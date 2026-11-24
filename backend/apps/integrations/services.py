"""Event intake (docs/CONTRACTS.md s3/s4; tasks/UDAY.md U-06).

Order inside `intake_event` (each step is a security or idempotency rule):
  a. validate the payload (400 via DRF ValidationError, nothing written)
  b. tenant check: a payload tenant_id that differs from the credential's tenant -> 403, audited,
     BEFORE any write
  c. dedupe on (tenant, event_id): duplicate -> 200 with the stored correlation id, no new rows,
     no Boomi call
  d. create the event (+ audit) in one transaction; a lost insert race = duplicate
  e. unknown or foreign supplier -> flagged for data correction, no Boomi call, same response
  f. known supplier -> publish to Boomi AFTER the transaction (no DB transaction during HTTP)
"""
import logging
import uuid
from dataclasses import dataclass
from decimal import Decimal

from django.db import IntegrityError, transaction

from apps.audit import record
from apps.core.models import DisruptionEvent, RiskCase, Supplier

from . import boomi
from .serializers import DisruptionEventInputSerializer

logger = logging.getLogger(__name__)

STATUS_RECEIVED = "RECEIVED"


@dataclass(frozen=True)
class IntakeResult:
    http_status: int
    body: dict


def _json_number(value):
    if value is None:
        return None
    value = Decimal(value)
    return int(value) if value == value.to_integral_value() else float(value)


def _iso_utc(value) -> str:
    from datetime import UTC

    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def canonical_event(event: DisruptionEvent) -> dict:
    """The CONTRACTS s3 shape sent to Boomi. tenant_id is the CREDENTIAL's tenant."""
    out = {
        "event_id": event.event_id,
        "tenant_id": event.tenant_id,
        "supplier_id": event.supplier_erp_id,
        "event_type": event.event_type,
        "severity": event.severity,
        "event_time": _iso_utc(event.event_time),
        "expected_duration_hours": _json_number(event.expected_duration_hours),
        "description": event.description,
        "source_system": event.source_system,
        "correlation_id": str(event.correlation_id),
    }
    if event.shipment_id:
        out["shipment_id"] = event.shipment_id
    if event.po_id:
        out["po_id"] = event.po_id
    return out


def _risk_case_id(event: DisruptionEvent):
    return RiskCase.objects.filter(event=event).values_list("id", flat=True).first()


def _accepted_body(event: DisruptionEvent, risk_case_id=None) -> dict:
    return {
        "event_id": event.event_id,
        "correlation_id": str(event.correlation_id),
        "status": STATUS_RECEIVED,
        "risk_case_id": risk_case_id,
    }


def _duplicate(tenant, event, actor, risk_case_id=None, republish=True) -> IntakeResult:
    record(
        actor,
        "event.duplicate",
        ("DisruptionEvent", event.event_id),
        correlation_id=event.correlation_id,
        tenant=tenant,
    )
    # An event whose publish never succeeded is sent again (the Idempotency-Key makes that
    # safe). A PUBLISHED event, or one with an unknown supplier (nothing to send), is not.
    if (
        republish
        and event.supplier_id is not None
        and not event.needs_data_correction
        and event.publish_status != DisruptionEvent.PublishStatus.PUBLISHED
    ):
        _publish(tenant, actor, event)
    return IntakeResult(200, _accepted_body(event, risk_case_id or _risk_case_id(event)))


def _forbidden_body(correlation_id) -> dict:
    return {
        "error": {
            "code": "FORBIDDEN",
            "message": "The tenant in the payload does not match the credential.",
            "details": {},
            "correlation_id": str(correlation_id),
        }
    }


def _optional_text(value):
    return value or None


def intake_event(*, tenant, actor, payload, request_correlation_id) -> IntakeResult:
    """Process one disruption event for `tenant` (always the credential's tenant)."""
    # (a) validate; a DRF ValidationError becomes the 400 contract body in the handler
    serializer = DisruptionEventInputSerializer(data=payload)
    serializer.is_valid(raise_exception=True)
    data = serializer.validated_data

    # (b) tenant check before any write; the audit row carries no payload data
    claimed = data.get("tenant_id")
    if claimed and claimed != tenant.id:
        record(
            actor,
            "security.tenant_mismatch",
            tenant=tenant,
            correlation_id=request_correlation_id,
            data={"endpoint": "events.create"},
        )
        return IntakeResult(403, _forbidden_body(request_correlation_id))

    event_id = data["event_id"]

    # (c) duplicate: same (tenant, event_id) -> answer with the stored event
    existing = DisruptionEvent.objects.filter(tenant=tenant, event_id=event_id).first()
    if existing is not None:
        return _duplicate(tenant, existing, actor)

    # (d) create inside one transaction. The supplier lookup is tenant-filtered, so a supplier
    # that exists only in another tenant is "unknown" here.
    supplier = Supplier.objects.filter(tenant=tenant, erp_id=data["supplier_id"]).first()
    correlation_id = data.get("correlation_id") or uuid.UUID(str(request_correlation_id))
    try:
        with transaction.atomic():
            event = DisruptionEvent.objects.create(
                tenant=tenant,
                event_id=event_id,
                supplier=supplier,
                supplier_erp_id=data["supplier_id"],
                event_type=data["event_type"],
                severity=data["severity"],
                event_time=data["event_time"],
                expected_duration_hours=data.get("expected_duration_hours"),
                description=data.get("description") or "",
                source_system=data.get("source_system") or "",
                correlation_id=correlation_id,
                shipment_id=_optional_text(data.get("shipment_id")),
                po_id=_optional_text(data.get("po_id")),
                needs_data_correction=supplier is None,
            )
            record(
                actor,
                "event.received",
                ("DisruptionEvent", event.event_id),
                correlation_id=correlation_id,
                tenant=tenant,
                data={
                    "event_type": event.event_type,
                    "severity": event.severity,
                    "needs_data_correction": event.needs_data_correction,
                },
            )
    except IntegrityError:
        # Lost the race on uniq_event_tenant_eventid: the other request created it.
        winner = DisruptionEvent.objects.filter(tenant=tenant, event_id=event_id).first()
        if winner is None:
            raise
        return _duplicate(tenant, winner, actor, republish=False)

    body = _accepted_body(event)

    # (e) unknown or foreign supplier: nothing is sent; the response is identical to (f)
    if supplier is None:
        record(
            actor,
            "event.flagged_data_correction",
            ("DisruptionEvent", event.event_id),
            correlation_id=correlation_id,
            tenant=tenant,
            data={"reason": "supplier_not_found"},
        )
        return IntakeResult(202, body)

    # (f) publish after the commit
    _publish(tenant, actor, event)
    return IntakeResult(202, body)


def _publish(tenant, actor, event: DisruptionEvent) -> None:
    def audit(action, data=None):
        record(
            actor,
            action,
            ("DisruptionEvent", event.event_id),
            correlation_id=event.correlation_id,
            tenant=tenant,
            data=data,
        )

    try:
        client = boomi.get_boomi_client()
    except boomi.BoomiNotConfigured:
        audit("event.publish_skipped", {"reason": "boomi_not_configured"})
        return
    try:
        client.publish_event(canonical_event(event), str(event.correlation_id))
    except boomi.BoomiError as exc:
        DisruptionEvent.objects.filter(pk=event.pk).update(
            publish_status=DisruptionEvent.PublishStatus.PUBLISH_FAILED
        )
        audit(
            "event.publish_failed",
            {"kind": exc.kind, "status": exc.status, "attempts": exc.attempts},
        )
        return
    DisruptionEvent.objects.filter(pk=event.pk).update(
        publish_status=DisruptionEvent.PublishStatus.PUBLISHED
    )
