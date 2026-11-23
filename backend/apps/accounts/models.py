from django.contrib.auth.models import AbstractUser
from django.db import models
from django.db.models import Q

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
            )
        ]

    @property
    def api_id(self) -> str:
        return format_user_id(self.pk)

    def get_full_name(self):
        return self.name
