from django.contrib.auth.models import AbstractUser
from django.db import models
from django.db.models import Q
from django.db.models.functions import Lower
from django.utils import timezone

from .ids import format_user_id


class User(AbstractUser):
    """Project user (docs/CONTRACTS.md s10). Non-superusers always belong to a tenant."""

    class Role(models.TextChoices):
        ADMIN = "ADMIN"
        PROCUREMENT_MANAGER = "PROCUREMENT_MANAGER"
        OPS_MANAGER = "OPS_MANAGER"
        VIEWER = "VIEWER"

    tenant = models.ForeignKey(
        "tenants.Tenant", null=True, blank=True, on_delete=models.PROTECT, related_name="users"
    )
    role = models.CharField(max_length=24, choices=Role.choices, default=Role.VIEWER)
    name = models.CharField(max_length=200, blank=True)

    class Meta(AbstractUser.Meta):
        ordering = ["id"]
        constraints = [
            models.CheckConstraint(
                condition=Q(is_superuser=True) | Q(tenant__isnull=False),
                name="user_tenant_unless_superuser",
            ),
            # 'alice' and 'Alice' can never both exist, even under concurrent creates.
            models.UniqueConstraint(Lower("username"), name="accounts_user_username_lower_uniq"),
        ]

    @property
    def api_id(self) -> str:
        return format_user_id(self.pk)

    def get_full_name(self):
        return self.name


class ApiKey(models.Model):
    """Credential for SYSTEM callers (Boomi). Only the sha256 hash and an 8-char prefix are
    stored; the raw key is shown once by `manage.py create_api_key` and is not recoverable."""

    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.PROTECT, related_name="api_keys")
    label = models.CharField(max_length=100)
    prefix = models.CharField(max_length=8, db_index=True)
    key_hash = models.CharField(max_length=64, unique=True)  # unique implies an index
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(default=timezone.now)
    last_used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return f"{self.label} ({self.prefix}...) for {self.tenant_id}"
