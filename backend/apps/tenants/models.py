from django.db import models
from django.utils import timezone


class Tenant(models.Model):
    """A customer organisation. Everything tenant-owned points here (docs/CONTRACTS.md s10)."""

    class Status(models.TextChoices):
        ACTIVE = "ACTIVE"
        INACTIVE = "INACTIVE"

    id = models.CharField(primary_key=True, max_length=40)  # e.g. TEN-ACME
    name = models.CharField(max_length=200)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)
    currency = models.CharField(max_length=3)
    locations = models.JSONField(default=list, blank=True)  # e.g. ["DC-PUNE"]
    # risk_thresholds, max_cost_increase_pct, require_qualified_alternates (U-04 checks)
    settings = models.JSONField(default=dict, blank=True)
    # default=now (not auto_now_add) so a seeded created_at is kept.
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return f"{self.id} ({self.name})"
