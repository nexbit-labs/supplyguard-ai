"""Persist the impact engine's result (N-04, the "load" step after `compute_impact`).

`create_risk_case()` is called by `POST /internal/risk-cases` (Uday, U-06). It runs the pure
engine in `impact.py`, then writes one RiskCase and one AffectedPO per (po_id, item_id) in a
single transaction. It never sets or changes `RiskCase.status`: a new case keeps the model
default (RECEIVED) and later moves only through `apps.workflow.services.transition()`.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from django.db import IntegrityError, transaction

from apps.core.models import AffectedPO, DisruptionEvent, RiskCase

from .impact import (
    SnapshotError,
    TenantThresholds,
    compute_impact,
    rows,
    thresholds_from_tenant,
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
