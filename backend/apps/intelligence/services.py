"""Persist the impact engine's result (N-04, the "load" step after `compute_impact`).

`create_risk_case()` is called by `POST /internal/risk-cases` (Uday, U-06). It runs the pure
engine in `impact.py`, then writes one RiskCase and one AffectedPO per (po_id, item_id) in a
single transaction. It never sets or changes `RiskCase.status`: a new case keeps the model
default (RECEIVED) and later moves only through `apps.workflow.services.transition()`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal

from django.db import DataError, IntegrityError, transaction

from apps.core.models import (
    AffectedPO,
    AgentRun,
    DisruptionEvent,
    Recommendation,
    RiskCase,
)
from apps.workflow.exceptions import CaseNotFound

from .exceptions import AnalysisApplyFailed, AnalysisConflict
from .impact import (
    SnapshotError,
    TenantThresholds,
    compute_impact,
    rows,
    thresholds_from_tenant,
)
from .validation import (
    MAX_COST_AMOUNT,
    MAX_LEAD_DAYS,
    RECOVERY_PLAN,
    RISK_ANALYSIS,
    validate_agent_output,
)

COVERAGE_MAX = Decimal("9999999.99")  # AffectedPO.inventory_coverage_days is Decimal(9, 2)
COVERAGE_OUT_OF_RANGE = "COVERAGE_OUT_OF_RANGE"
CURRENCY_MISMATCH = "CURRENCY_MISMATCH"


class TenantMismatch(ValueError):
    """The event or the snapshot does not belong to the tenant the case is created for."""


def _utc_iso(dt: datetime) -> str:
    dt = dt.astimezone(UTC)
    frac = f".{dt.microsecond:06d}" if dt.microsecond else ""
    return dt.strftime("%Y-%m-%dT%H:%M:%S") + frac + "Z"


def _event_dict(event: DisruptionEvent) -> dict:
    """The CONTRACTS s3 canonical event, as `compute_impact` reads it."""
    return {
        "event_id": event.event_id,
        "tenant_id": event.tenant_id,
        "supplier_id": event.supplier_erp_id,
        "event_type": event.event_type,
        "severity": event.severity,
        "event_time": _utc_iso(event.event_time),
        "expected_duration_hours": event.expected_duration_hours,
        "description": event.description,
        "source_system": event.source_system,
        "correlation_id": str(event.correlation_id),
        "shipment_id": event.shipment_id,
        "po_id": event.po_id,
    }


def _check_snapshot_tenant(tenant_id: str, snapshot: dict) -> None:
    """Every snapshot row must carry this tenant's id (rows without one are rejected)."""
    if not isinstance(snapshot, dict):
        raise SnapshotError("snapshot must be a JSON object")
    try:
        supplier = snapshot.get("supplier")
        candidates = [supplier] if supplier else []
        for key in ("purchase_orders", "inventory", "alternate_suppliers"):
            candidates += rows(snapshot.get(key))
        for row in candidates:
            tid = row.get("tenant_id")
            if tid != tenant_id:
                raise TenantMismatch(f"snapshot row has tenant {tid!r}, case is for {tenant_id!r}")
    except (AttributeError, TypeError) as exc:
        raise SnapshotError(f"malformed snapshot: {exc!r}") from exc


def _captured_at(snapshot: dict) -> datetime | None:
    raw = snapshot.get("captured_at")
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError as exc:
        raise SnapshotError(f"invalid snapshot captured_at {raw!r}") from exc
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt


def _item_names(snapshot: dict) -> dict[str, str]:
    names: dict[str, str] = {}
    for po in rows(snapshot.get("purchase_orders")):
        for line in po.get("lines") or []:
            if line.get("item_name"):
                names.setdefault(line["item_id"], line["item_name"])
    for row in rows(snapshot.get("inventory")):
        if row.get("item_name"):
            names.setdefault(row["item_id"], row["item_name"])
    return names


def _summary(result: dict, supplier_label: str, event: DisruptionEvent) -> tuple[str, str]:
    n_po = len(result["affected_po_ids"])
    n_lines = len(result["affected_pos"])
    exposure = result["total_exposure"]
    title = f"{supplier_label}: {event.event_type} affects {n_po} open PO(s)"
    shortage = "shortage expected" if result["shortage"] else "no shortage expected"
    summary = (
        f"{event.event_type} at {supplier_label} ({event.event_id}) affects {n_po} open PO(s) "
        f"across {n_lines} PO line(s) and {len(result['affected_item_ids'])} item(s). "
        f"Exposure {exposure['currency']} {exposure['amount']}, "
        f"estimated delay up to {result['estimated_delay_days']} day(s), {shortage}. "
        f"Risk score {result['risk_score']} ({result['risk_level']})."
    )
    return title[:300], summary


def _currency(value, what: str) -> str:
    if not isinstance(value, str) or len(value) != 3:
        raise SnapshotError(f"missing or invalid currency on {what}: {value!r}")
    return value


def _build_rows(result: dict, snapshot: dict) -> tuple[list[dict], list[str]]:
    """Convert the engine's affected rows to AffectedPO field values (no DB access).

    Returns (rows, extra data-quality flags). Raises SnapshotError on a missing currency.
    """
    names = _item_names(snapshot)
    flags: list[str] = []
    out = []
    for row in result["affected_pos"]:
        cov = row["inventory_coverage_days"]
        cov_dec = None if cov is None else Decimal(str(cov))
        if cov_dec is not None and (not cov_dec.is_finite() or abs(cov_dec) > COVERAGE_MAX):
            flags.append(
                f"{COVERAGE_OUT_OF_RANGE}: {row['po_id']} / {row['item_id']} "
                f"coverage {cov} days stored as null"
            )
            cov_dec = None
        out.append(
            {
                "po_id": row["po_id"],
                "item_id": row["item_id"],
                "item_name": names.get(row["item_id"], ""),
                "affected_qty": row["affected_qty"],
                "estimated_delay_days": Decimal(str(row["estimated_delay_days"])),
                "exposure_amount": Decimal(row["exposure_amount"]["amount"]),
                "currency": _currency(row["exposure_amount"]["currency"], row["po_id"]),
                "due_date": date.fromisoformat(row["due_date"]),
                "inventory_coverage_days": cov_dec,
                "shortage": row["shortage"],
            }
        )
    return out, flags


def create_risk_case(
    *,
    tenant,
    event: DisruptionEvent,
    snapshot: dict,
    thresholds: TenantThresholds | None = None,
) -> tuple[RiskCase | None, bool]:
    """Run the impact engine and save RiskCase + AffectedPO rows.

    Returns `(case, created)`:
    - `(new_case, True)` when a case was written;
    - `(existing_case, False)` when the event already has a case (returned unchanged);
    - `(None, False)` when no case applies: unknown supplier (TC-02) or no affected PO line.
      Nothing is written.

    Errors (nothing is written for any of them): `TenantMismatch` if the event or a snapshot
    row is another tenant's or has no tenant_id; `SnapshotError` for a malformed snapshot, a
    snapshot supplier that is not the event's supplier, or a missing currency; `ValueError`
    for bad event/threshold values. `TenantMismatch` and `SnapshotError` are `ValueError`s.
    """
    if event.tenant_id != tenant.id:
        raise TenantMismatch(f"event {event.event_id} belongs to tenant {event.tenant_id}")
    _check_snapshot_tenant(tenant.id, snapshot)

    existing = RiskCase.objects.filter(event=event).first()
    if existing is not None:
        return existing, False
    if event.supplier_id is None:
        return None, False  # unknown or foreign supplier: no case (TC-02)

    supplier = snapshot.get("supplier")
    if supplier and supplier.get("supplier_id") != event.supplier_erp_id:
        raise SnapshotError(
            f"snapshot supplier {supplier.get('supplier_id')!r} does not match the "
            f"event supplier {event.supplier_erp_id!r}"
        )

    if thresholds is None:
        thresholds = thresholds_from_tenant(
            {"settings": tenant.settings, "currency": tenant.currency}
        )
    try:
        result = compute_impact(_event_dict(event), snapshot, thresholds)
        if not result["affected_pos"]:
            return None, False
        affected, extra_flags = _build_rows(result, snapshot)
        exposure = result["total_exposure"]
        currency = _currency(exposure["currency"], "total_exposure")
        exposure_amount = Decimal(exposure["amount"])
        captured_at = _captured_at(snapshot)
    except (KeyError, TypeError, AttributeError, ArithmeticError) as exc:
        raise SnapshotError(f"malformed snapshot: {exc!r}") from exc

    if tenant.currency and currency != tenant.currency:
        extra_flags.append(
            f"{CURRENCY_MISMATCH}: snapshot currency {currency}, tenant currency {tenant.currency}"
        )
    flags = [*result["data_quality_flags"], *extra_flags]
    result["data_quality_flags"] = flags

    supplier_label = (supplier or {}).get("name") or event.supplier_erp_id
    title, summary = _summary(result, supplier_label, event)

    try:
        with transaction.atomic():
            case = RiskCase.objects.create(
                tenant=tenant,
                event=event,
                supplier=event.supplier,
                risk_level=result["risk_level"],
                risk_score=result["risk_score"],
                title=title,
                impact_summary=summary,
                exposure_amount=exposure_amount,
                currency=currency,
                snapshot=snapshot,
                snapshot_captured_at=captured_at,
                impact=result,
                data_quality_flags=flags,
                correlation_id=event.correlation_id,
            )
            for fields in affected:
                AffectedPO.objects.create(tenant=tenant, risk_case=case, **fields)
    except IntegrityError:
        # Lost a race with a concurrent call for the same event: return the winner's case.
        winner = RiskCase.objects.filter(event=event).first()
        if winner is None:
            raise
        return winner, False
    return case, True


# ---------------------------------------------------------------------------------- N-07
_AGENT_BY_KIND = {
    RISK_ANALYSIS: AgentRun.Agent.RISK_ANALYST,
    RECOVERY_PLAN: AgentRun.Agent.RECOVERY_PLANNER,
}


@dataclass(frozen=True)
class AnalysisResult:
    agent_run: AgentRun
    valid: bool
    validation_errors: list
    actionable_count: int
    status: str  # the case's status after the call
    recommendations: list = field(default_factory=list)


def _lock_case(case: RiskCase, tenant) -> RiskCase:
    try:
        return (
            RiskCase.objects.select_for_update(of=("self",))
            .select_related("event")
            .get(pk=case.pk, tenant=tenant)
        )
    except RiskCase.DoesNotExist:
        raise CaseNotFound() from None


def _check_open(locked: RiskCase, kind: str, round_) -> None:
    """409 unless the case is waiting for this output (nothing is written)."""
    if locked.status != RiskCase.Status.ANALYZING:
        raise AnalysisConflict(
            "CASE_NOT_ANALYZING",
            f"The case is {locked.status}, not ANALYZING; the agent output was not applied.",
            case_status=locked.status,
        )
    if round_ is not None and round_ != locked.analysis_round:
        raise AnalysisConflict(
            "STALE_ROUND",
            f"The output is for round {round_}; the case is on round {locked.analysis_round}.",
            round=round_,
            current_round=locked.analysis_round,
        )
    if kind == RECOVERY_PLAN:
        done = Recommendation.objects.filter(risk_case=locked, round=locked.analysis_round)
    else:
        done = AgentRun.objects.filter(
            risk_case=locked,
            agent=_AGENT_BY_KIND[kind],
            round=locked.analysis_round,
            valid=True,
        )
    if done.exists():
        raise AnalysisConflict(
            "ALREADY_RECORDED",
            f"A valid {kind} was already recorded for round {locked.analysis_round}.",
            round=locked.analysis_round,
        )


def has_nul(value) -> bool:
    """True if any string or dict key anywhere in the JSON value holds U+0000 (no recursion)."""
    stack = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, str):
            if "\x00" in item:
                return True
        elif isinstance(item, dict):
            for k, v in item.items():
                if isinstance(k, str) and "\x00" in k:
                    return True
                stack.append(v)
        elif isinstance(item, list):
            stack.extend(item)
    return False


def strip_nul(value):
    """Copy of the JSON value with U+0000 replaced by U+FFFD (depth is capped by the parser)."""
    if isinstance(value, str):
        return value.replace("\x00", "�")
    if isinstance(value, dict):
        return {strip_nul(k): strip_nul(v) for k, v in value.items()}
    if isinstance(value, list):
        return [strip_nul(v) for v in value]
    return value


def _money(value):
    """-> (amount quantised to 2 dp or None when missing / out of range, currency)."""
    if not isinstance(value, dict):
        return None, ""
    return _bounded(value.get("amount"), MAX_COST_AMOUNT), value.get("currency") or ""


def _bounded(value, limit: Decimal) -> Decimal | None:
    if value is None:
        return None
    try:
        d = Decimal(str(value))
    except (ArithmeticError, ValueError):
        return None
    if not d.is_finite() or abs(d) > limit:
        return None
    return d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _recommendation_fields(option: dict, verdict: dict) -> dict:
    cost, currency = _money(option.get("estimated_cost_delta"))
    lead = _bounded(option.get("estimated_lead_time_days"), MAX_LEAD_DAYS)
    data_gaps = list(option.get("data_gaps") or [])
    if option.get("estimated_lead_time_days") is not None and lead is None:
        data_gaps.append("estimated_lead_time_days was out of range and is not stored.")
    if option.get("estimated_cost_delta") is not None and cost is None:
        data_gaps.append("estimated_cost_delta was out of range and is not stored.")
    return {
        "rank": option["rank"],
        "option_type": option["option_type"],
        "proposed_action": option.get("proposed_action") or {},
        "action_steps": option.get("action_steps") or [],
        "trade_offs": option.get("trade_offs") or [],
        "evidence": option.get("evidence") or [],
        "assumptions": option.get("assumptions") or [],
        "data_gaps": data_gaps,
        "estimated_lead_time_days": lead,
        "estimated_cost_delta": cost,
        "currency": currency,
        "unsupported": bool(verdict["unsupported"]),
        "unsupported_reasons": verdict["unsupported_reasons"],
        "status": Recommendation.Status.PENDING,
    }


def _apply_valid(locked, tenant, kind, payload, result, run, actor, correlation_id):
    """Phase 2 body: store the validated output and move the case (inside the caller's lock)."""
    from apps.workflow import services as workflow
    from apps.workflow.states import actionable_recommendations

    recs: list[Recommendation] = []
    if kind == RISK_ANALYSIS:
        locked.analysis = payload
        locked.save(update_fields=["analysis", "updated_at"])
        return locked.status, recs
    verdicts = {o["rank"]: o for o in result["options"]}  # ranks are unique: validated
    for option in payload["options"]:
        recs.append(
            Recommendation.objects.create(
                tenant=tenant,
                risk_case=locked,
                round=locked.analysis_round,
                **_recommendation_fields(option, verdicts[option["rank"]]),
            )
        )
    no_viable = payload.get("no_viable_option_reason")
    if not no_viable and not result["actionable_count"]:
        no_viable = f"All {len(recs)} option(s) failed the fact check (unsupported)."
    locked.no_viable_option_reason = no_viable
    locked.save(update_fields=["no_viable_option_reason", "updated_at"])

    cid = correlation_id or locked.correlation_id
    workflow.transition(
        locked,
        RiskCase.Status.RECOMMENDATION_READY,
        actor=actor,
        reason="analysis validated",
        correlation_id=cid,
        actionable_count=result["actionable_count"],
        agent_run=run,
    )
    if actionable_recommendations(locked).exists():
        workflow.transition(
            locked,
            RiskCase.Status.PENDING_APPROVAL,
            actor=actor,
            reason="options ready for approval",
            correlation_id=cid,
        )
    else:
        workflow.transition(
            locked,
            RiskCase.Status.CLOSED,
            actor=actor,
            reason=f"no viable option: {no_viable}",
            correlation_id=cid,
        )
    return locked.status, recs


_APPLY_ERRORS = (IntegrityError, DataError, ArithmeticError, ValueError, KeyError, TypeError)


def record_analysis(
    *, tenant, case: RiskCase, kind: str, payload, actor, correlation_id=None, round=None
) -> AnalysisResult:
    """Validate one agent output, store it, and move the case on (N-07, AC-07).

    One transaction holds the case row lock from the checks to the end, so two concurrent
    submissions cannot both pass: the loser sees the new state and gets a 409 that writes nothing.
    Inside it: 409 unless the case is ANALYZING / the round matches / no output of this kind was
    already recorded, then ONE append-only AgentRun (`raw` = payload as received, even when
    invalid). A valid output is then applied in a savepoint (RISK_ANALYSIS -> `case.analysis`;
    RECOVERY_PLAN -> Recommendation rows, then `transition()` only: ANALYZING ->
    RECOMMENDATION_READY -> PENDING_APPROVAL, or CLOSED when nothing is actionable, TC-05). If the
    savepoint fails, only it is rolled back: the AgentRun is committed (AC-07) and the error is
    raised afterwards (`AnalysisApplyFailed` for data errors). An invalid output changes no status.
    `round` is the caller's round check; `actor` is the SystemPrincipal (or "SYSTEM").
    """
    if kind not in _AGENT_BY_KIND:
        raise ValueError(f"unknown kind {kind!r}")

    failure = None
    with transaction.atomic():
        locked = _lock_case(case, tenant)
        _check_open(locked, kind, round)
        if has_nul(payload):
            # Postgres jsonb cannot hold U+0000: keep a sanitised copy so the call is still on
            # record (AC-07), and never validate or apply it.
            payload = strip_nul(payload)
            result = {"valid": False, "schema_errors": [], "fact_errors": [], "options": [],
                      "actionable_count": 0}
            errors = [{
                "code": "NUL_CHARACTER",
                "message": "The payload contained NUL (U+0000) characters; they were replaced "
                "with U+FFFD in the stored copy and the output was not applied.",
            }]
        else:
            result = validate_agent_output(
                kind,
                payload,
                locked.snapshot,
                locked.impact,
                event=_event_dict(locked.event),
                risk_case_id=locked.id,
            )
            errors = [*result["schema_errors"], *result["fact_errors"]]
        run = AgentRun.objects.create(
            tenant=tenant,
            risk_case=locked,
            agent=_AGENT_BY_KIND[kind],
            round=locked.analysis_round,
            raw=payload,
            valid=result["valid"],
            validation_errors=errors,
        )
        final_status, recs = locked.status, []
        if result["valid"]:
            try:
                with transaction.atomic():
                    final_status, recs = _apply_valid(
                        locked, tenant, kind, payload, result, run, actor, correlation_id
                    )
            except Exception as exc:  # the AgentRun must still commit
                failure = exc
    if failure is not None:
        if isinstance(failure, _APPLY_ERRORS):
            raise AnalysisApplyFailed(run.pk, failure) from failure
        raise failure
    case.refresh_from_db(fields=["status", "analysis_round", "updated_at"])
    if not result["valid"]:
        return AnalysisResult(run, False, errors, 0, case.status)
    return AnalysisResult(run, True, [], result["actionable_count"], final_status, recs)
