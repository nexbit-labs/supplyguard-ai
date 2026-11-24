"""Risk case creation from a Boomi snapshot (POST /internal/risk-cases; tasks/UDAY.md U-06).

This module is the wrapper around N-04's `apps.intelligence.services.create_risk_case` (impact
engine + RiskCase + AffectedPO rows). It owns the callback duties; the creation logic is N-04's.

Order (each step is a security or idempotency rule):
  a. event lookup filtered by the credential's tenant; another tenant's event = the same 404 as
     an unknown id, plus an audit row in the CALLER's tenant (outside the transaction)
  then ONE transaction, with the event row locked (select_for_update) so concurrent callbacks
  cannot both flag the event and create a case:
  b. event flagged for data correction -> 409, no case
  c. a case already exists -> 200 with it (a case still in RECEIVED is moved to ANALYZING)
  d. snapshot.supplier null -> flag the event, 200 {risk_case_id: null, flagged: true}
  e. range checks, then N-04 create_risk_case; bad snapshot -> 400 (message only, never the data);
     nothing to create (no affected PO line) -> 200 {risk_case_id: null, no_impact: true}
  f. RECEIVED -> ANALYZING via workflow transition() + audit `risk_case.created` (counts only)
"""
import logging
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from django.db import DataError, transaction
from rest_framework.exceptions import APIException, NotFound, ValidationError

from apps.audit import record
from apps.core.models import DisruptionEvent, RiskCase
from apps.intelligence.impact import SnapshotError
from apps.intelligence.services import TenantMismatch, create_risk_case

from .snapshot_checks import AMOUNT_LIMIT, check_snapshot_ranges

logger = logging.getLogger(__name__)

CROSS_TENANT_ACTION = "security.cross_tenant_denied"
GENERIC_RANGE_ERROR = "snapshot contains a value that is out of range"


class Conflict(APIException):
    status_code = 409
    default_detail = "Conflict."
    default_code = "conflict"


@dataclass(frozen=True)
class CaseResult:
    http_status: int
    body: dict


def _money_str(value) -> str:
    return str(Decimal(value).quantize(Decimal("0.01")))


def case_body(case: RiskCase, affected_po_count: int | None = None) -> dict:
    if affected_po_count is None:
        affected_po_count = case.affected_pos.count()
    return {
        "risk_case_id": case.id,
        "status": case.status,
        "risk_level": case.risk_level,
        "risk_score": case.risk_score,
        "exposure": {"amount": _money_str(case.exposure_amount), "currency": case.currency},
        "affected_po_count": affected_po_count,
        "data_quality_flags": list(case.data_quality_flags or []),
        "correlation_id": str(case.correlation_id),
    }


def move_to_analyzing(case: RiskCase, event: DisruptionEvent, actor) -> None:
    """RECEIVED -> ANALYZING (Trigger SYS_IMPACT) through the workflow state machine."""
    from apps.workflow.services import transition

    transition(
        case,
        RiskCase.Status.ANALYZING,
        actor=actor,
        reason="impact computed",
        correlation_id=event.correlation_id,
    )


def _heal(case: RiskCase, event: DisruptionEvent, actor) -> RiskCase:
    """An existing case still in RECEIVED (the move failed earlier) is moved on now; transition
    is idempotent for a case that is already ANALYZING."""
    if case.status == RiskCase.Status.RECEIVED:
        move_to_analyzing(case, event, actor)
    return case


def _find_event(tenant, event_id) -> DisruptionEvent:
    event = DisruptionEvent.objects.filter(tenant=tenant, event_id=event_id).first()
    if event is not None:
        return event
    if DisruptionEvent.objects.filter(event_id=event_id).exclude(tenant=tenant).exists():
        record(
            "SYSTEM",
            CROSS_TENANT_ACTION,
            ("DisruptionEvent", str(event_id)[:100]),
            tenant=tenant,
            data={"endpoint": "internal.risk-cases"},
        )
    raise NotFound()  # identical for "unknown" and "another tenant's"


def _check_extra_tenants(tenant, snapshot) -> None:
    """shipments / supplier_history are stored with the snapshot but not read by the engine, so
    N-04 does not check them: every row must still carry this tenant's id."""
    for key in ("shipments", "supplier_history"):
        for row in snapshot.get(key) or []:
            if row.get("tenant_id") != tenant.id:
                raise ValidationError(
                    {"snapshot": [f"{key} row does not belong to the credential's tenant"]}
                )


def create_risk_case_from_snapshot(
    tenant, event_id, snapshot, captured_at=None, actor="SYSTEM"
) -> CaseResult:
    found = _find_event(tenant, event_id)  # (a)

    with transaction.atomic():
        event = DisruptionEvent.objects.select_for_update().get(pk=found.pk)  # lock the row

        if event.needs_data_correction:  # (b)
            raise Conflict("event is flagged for data correction")

        existing = RiskCase.objects.filter(tenant=tenant, event=event).first()  # (c)
        if existing is not None:
            return CaseResult(200, case_body(_heal(existing, event, actor)))

        if snapshot.get("supplier") is None:  # (d)
            DisruptionEvent.objects.filter(pk=event.pk).update(needs_data_correction=True)
            record(
                actor,
                "event.flagged_data_correction",
                ("DisruptionEvent", event.event_id),
                correlation_id=event.correlation_id,
                tenant=tenant,
                data={"reason": "supplier_not_in_snapshot"},
            )
            return CaseResult(200, {"risk_case_id": None, "flagged": True})

        check_snapshot_ranges(snapshot)  # (e)
        _check_extra_tenants(tenant, snapshot)
        try:
            case, created = create_risk_case(tenant=tenant, event=event, snapshot=snapshot)
        except (TenantMismatch, SnapshotError, ValueError) as exc:
            raise ValidationError({"snapshot": [str(exc)]}) from None
        except (DataError, InvalidOperation, OverflowError):
            raise ValidationError({"snapshot": [GENERIC_RANGE_ERROR]}) from None

        if case is None:  # nothing to create: no affected PO line
            return CaseResult(200, {"risk_case_id": None, "no_impact": True})
        if not created:  # lost a race inside N-04: the winner's case
            return CaseResult(200, case_body(_heal(case, event, actor)))

        # SQLite stores any number; PostgreSQL would have raised DataError on the insert.
        # Either way a total that does not fit Decimal(14, 2) is a 400 and nothing is kept.
        if abs(case.exposure_amount) >= AMOUNT_LIMIT:
            raise ValidationError({"snapshot": [GENERIC_RANGE_ERROR]})
        move_to_analyzing(case, event, actor)  # (f)
        record(
            actor,
            "risk_case.created",
            case,
            correlation_id=event.correlation_id,
            tenant=tenant,
            data={
                "risk_level": case.risk_level,
                "risk_score": case.risk_score,
                "affected_po_count": case.affected_pos.count(),
            },
        )
        return CaseResult(201, case_body(case))
