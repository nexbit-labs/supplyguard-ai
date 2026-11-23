from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.forms import AdminUserCreationForm, UserChangeForm

from .models import User


class TenantRequiredMixin:
    """Non-superusers need a tenant (DB check constraint); give a form error, not a 500."""

    def clean(self):
        cleaned = super().clean()
        if self._superuser_allowed() or cleaned.get("tenant"):
            return cleaned
        self.add_error("tenant", "A tenant is required unless the user is a superuser.")
        return cleaned

    def _superuser_allowed(self):
        return False


class SupplyGuardUserCreationForm(TenantRequiredMixin, AdminUserCreationForm):
    """Add form: superusers are created with `createsuperuser`, so a tenant is always required."""


class SupplyGuardUserChangeForm(TenantRequiredMixin, UserChangeForm):
    def _superuser_allowed(self):
        return bool(self.cleaned_data.get("is_superuser", self.instance.is_superuser))


@admin.register(User)
class SupplyGuardUserAdmin(UserAdmin):
    add_form = SupplyGuardUserCreationForm
    form = SupplyGuardUserChangeForm
    list_display = ("username", "email", "name", "role", "tenant", "is_active", "is_superuser")
    list_filter = ("tenant", "role", "is_active", "is_superuser")
    search_fields = ("username", "email", "name")
    fieldsets = (
        *UserAdmin.fieldsets,
        ("SupplyGuard", {"fields": ("tenant", "role", "name")}),
    )
    add_fieldsets = (
        *UserAdmin.add_fieldsets,
        ("SupplyGuard", {"fields": ("tenant", "role", "name", "email")}),
    )
