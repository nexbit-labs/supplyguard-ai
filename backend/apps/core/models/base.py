from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from .sequences import next_id

_MISSING = object()


class TenantOwned(models.Model):
    """Abstract base for every tenant-owned row (docs/CONTRACTS.md s10).

    Same-tenant guard: `save()` checks that every FK target that has a `tenant_id` (including
    `Approval.decided_by`, a User) carries the same tenant as the row, and raises
    `ValidationError` otherwise. Child models set `tenant_parent = "<fk name>"`; when `tenant`
    is not given it is copied from that parent. `bulk_create` and `QuerySet.update` skip this
    guard, so callers using them must check the tenant themselves.

    Models with a generated string pk set `id_prefix` / `id_width`; the id comes from
    `next_id()` on first save.
    """

    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(default=timezone.now)

    tenant_parent: str | None = None
    id_prefix: str | None = None
    id_width: int = 6

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        self.check_same_tenant()
        if self.id_prefix and not self.pk:
            self.pk = next_id(self.id_prefix, self.id_width)
        if self._state.adding:
            # A pk is set (generated or explicit): without this Django tries UPDATE first and a
            # colliding id would silently overwrite another row.
            if args:
                args = (True, *args[1:])
            else:
                kwargs["force_insert"] = True
        super().save(*args, **kwargs)
        self._loaded_tenant_id = self.tenant_id

    def clean(self):
        super().clean()
        # Same guards as save(), so admin / ModelForms show a form error instead of a 500.
        self.check_same_tenant()

    @classmethod
    def from_db(cls, db, field_names, values):
        instance = super().from_db(db, field_names, values)
        instance._loaded_tenant_id = instance.__dict__.get("tenant_id", _MISSING)
        return instance

    def refresh_from_db(self, using=None, fields=None, **kwargs):
        super().refresh_from_db(using=using, fields=fields, **kwargs)
        if fields is None or "tenant" in fields or "tenant_id" in fields:
            self._loaded_tenant_id = self.__dict__.get("tenant_id", _MISSING)

    def _check_tenant_unchanged(self):
        if self._state.adding:
            return
        loaded = getattr(self, "_loaded_tenant_id", _MISSING)
        if loaded is _MISSING:
            loaded = (
                type(self)
                ._base_manager.filter(pk=self.pk)
                .values_list("tenant_id", flat=True)
                .first()
            )
            if loaded is None:
                return
        if loaded != self.tenant_id:
            raise ValidationError(
                f"{self._meta.label}: tenant cannot be changed ({loaded!r} -> {self.tenant_id!r})."
            )

    def _fk_targets(self):
        for field in self._meta.concrete_fields:
            if field.many_to_one or field.one_to_one:
                if field.name == "tenant":
                    continue
                target = getattr(self, field.name)
                if target is not None:
                    yield field.name, target

    def check_same_tenant(self):
        if self.tenant_id is None and self.tenant_parent:
            parent = getattr(self, self.tenant_parent, None)
            if parent is not None:
                self.tenant_id = parent.tenant_id
        if self.tenant_id is None:
            raise ValidationError(f"{self._meta.label}: tenant is required.")
        self._check_tenant_unchanged()
        for name, target in self._fk_targets():
            target_tenant = getattr(target, "tenant_id", _MISSING)
            if target_tenant is _MISSING:
                continue
            if target_tenant != self.tenant_id:
                raise ValidationError(
                    f"{self._meta.label}.{name} belongs to another tenant "
                    f"({target_tenant!r} != {self.tenant_id!r})."
                )
