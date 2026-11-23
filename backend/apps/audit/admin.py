from django.contrib import admin

from .models import AuditEvent


@admin.register(AuditEvent)
class AuditEventAdmin(admin.ModelAdmin):
    """Read-only: the audit log is append-only (no add, change or delete)."""

    list_display = ("timestamp", "tenant", "actor", "action", "entity_type", "entity_id")
    list_filter = ("tenant", "entity_type", "action")
    search_fields = ("actor", "action", "entity_id", "correlation_id")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
