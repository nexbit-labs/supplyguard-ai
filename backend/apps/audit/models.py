from django.db import models
from django.utils import timezone


class AuditAppendOnly(RuntimeError):
    """The audit log is append-only."""


class AuditQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise AuditAppendOnly("AuditEvent rows cannot be updated.")

    def bulk_update(self, *args, **kwargs):
        raise AuditAppendOnly("AuditEvent rows cannot be updated.")

    def delete(self):
        raise AuditAppendOnly("AuditEvent rows cannot be deleted.")


class AuditEvent(models.Model):
    """Append-only audit trail (docs/CONTRACTS.md s10). Not TenantOwned: tenant is nullable
    (superuser actions and unknown-key attempts have none); GET /audit never returns those."""

    tenant = models.ForeignKey(
        "tenants.Tenant", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    timestamp = models.DateTimeField(default=timezone.now)
    # `USR-<n>`, `SYSTEM` or an external source; text so the log outlives users.
    actor = models.CharField(max_length=100)
    action = models.CharField(max_length=100)
    entity_type = models.CharField(max_length=50)
    entity_id = models.CharField(max_length=80, blank=True)
    correlation_id = models.UUIDField(null=True, blank=True)
    data = models.JSONField(default=dict, blank=True)

    objects = AuditQuerySet.as_manager()

    class Meta:
        ordering = ["-timestamp", "-id"]
        indexes = [
            models.Index(fields=["tenant", "timestamp"], name="audit_tenant_ts_idx"),
            models.Index(
                fields=["tenant", "entity_type", "entity_id"], name="audit_tenant_entity_idx"
            ),
            models.Index(fields=["tenant", "correlation_id"], name="audit_tenant_corr_idx"),
        ]

    def __str__(self):
        return f"{self.timestamp:%Y-%m-%d %H:%M:%S} {self.actor} {self.action}"

    def save(self, *args, **kwargs):
        if self.pk is not None and not self._state.adding:
            raise AuditAppendOnly("AuditEvent rows cannot be modified.")
        # Always INSERT: a constructor with an existing pk must collide, not UPDATE.
        if args:
            args = (True, *args[1:])
        else:
            kwargs["force_insert"] = True
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise AuditAppendOnly("AuditEvent rows cannot be deleted.")
