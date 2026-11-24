from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.forms import AdminUserCreationForm, UserChangeForm

from .models import ApiKey, User


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


@admin.register(ApiKey)
class ApiKeyAdmin(admin.ModelAdmin):
    """Read-only except is_active. Keys are created with `manage.py create_api_key`."""

    list_display = ("label", "prefix", "tenant", "is_active", "created_at", "last_used_at")
    list_filter = ("tenant", "is_active")
    fields = ("tenant", "label", "prefix", "key_hash", "is_active", "created_at", "last_used_at")
    readonly_fields = ("tenant", "label", "prefix", "key_hash", "created_at", "last_used_at")

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
