from django.db import models

from .base import TenantOwned
from .catalog import Supplier


class DisruptionEvent(TenantOwned):
    class EventType(models.TextChoices):
        FACTORY_OUTAGE = "FACTORY_OUTAGE"
        SHIPMENT_DELAY = "SHIPMENT_DELAY"
        QUALITY_HOLD = "QUALITY_HOLD"
        CAPACITY_REDUCTION = "CAPACITY_REDUCTION"
        LOGISTICS_DISRUPTION = "LOGISTICS_DISRUPTION"

    class Severity(models.TextChoices):
        LOW = "LOW"
        MEDIUM = "MEDIUM"
        HIGH = "HIGH"
        CRITICAL = "CRITICAL"

    class PublishStatus(models.TextChoices):
        RECEIVED = "RECEIVED"
        PUBLISHED = "PUBLISHED"
        PUBLISH_FAILED = "PUBLISH_FAILED"

    event_id = models.CharField(max_length=80)  # unique per tenant
    # Text copy of the payload supplier_id (named to not clash with the FK column below).
    supplier_erp_id = models.CharField(max_length=40)
    # Set only after a tenant-filtered lookup: null for an unknown or foreign supplier (TC-02).
    supplier = models.ForeignKey(
        Supplier, null=True, blank=True, on_delete=models.PROTECT, related_name="disruption_events"
    )
    event_type = models.CharField(max_length=24, choices=EventType.choices)
    severity = models.CharField(max_length=10, choices=Severity.choices)
    event_time = models.DateTimeField()
    expected_duration_hours = models.DecimalField(
        max_digits=8, decimal_places=2, null=True, blank=True
    )
    description = models.TextField(blank=True)
    source_system = models.CharField(max_length=100, blank=True)
    correlation_id = models.UUIDField(db_index=True)
    shipment_id = models.CharField(max_length=40, null=True, blank=True)  # noqa: DJ001
    po_id = models.CharField(max_length=40, null=True, blank=True)  # noqa: DJ001
    needs_data_correction = models.BooleanField(default=False)
    publish_status = models.CharField(
        max_length=16, choices=PublishStatus.choices, default=PublishStatus.RECEIVED
    )

    class Meta:
        ordering = ["-event_time", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "event_id"], name="uniq_event_tenant_eventid"
            ),
        ]
        indexes = [
            models.Index(fields=["tenant", "supplier"], name="event_tenant_supplier_idx"),
            models.Index(fields=["tenant", "event_time"], name="event_tenant_time_idx"),
        ]

    def __str__(self):
        return self.event_id
