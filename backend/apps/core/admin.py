from django.contrib import admin

from .models import (
    AffectedPO,
    AgentRun,
    Approval,
    DisruptionEvent,
    Execution,
    IdSequence,
    Inventory,
    PurchaseOrder,
    PurchaseOrderLine,
    Recommendation,
    RiskCase,
    Shipment,
    Supplier,
)


class ReadOnlyAdmin(admin.ModelAdmin):
    """View only: these rows are written by the workflow / AI / ERP code paths, never by hand."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Supplier)
class SupplierAdmin(admin.ModelAdmin):
    list_display = ("erp_id", "name", "tenant", "status", "risk_level", "country")
    list_filter = ("tenant", "status", "risk_level")
    search_fields = ("erp_id", "name", "tax_id")


@admin.register(PurchaseOrder)
class PurchaseOrderAdmin(admin.ModelAdmin):
    list_display = ("erp_id", "tenant", "supplier", "status", "expected_date", "amount", "currency")
    list_filter = ("tenant", "status")
    search_fields = ("erp_id", "supplier__name")


@admin.register(PurchaseOrderLine)
class PurchaseOrderLineAdmin(admin.ModelAdmin):
    list_display = ("po", "line_no", "tenant", "item_id", "qty", "due_date", "unit_price")
    list_filter = ("tenant",)
    search_fields = ("po__erp_id", "item_id", "item_name")


@admin.register(Inventory)
class InventoryAdmin(admin.ModelAdmin):
    list_display = ("item_id", "location_id", "tenant", "on_hand", "reserved", "safety_stock")
    list_filter = ("tenant", "location_id")
    search_fields = ("item_id", "item_name")


@admin.register(Shipment)
class ShipmentAdmin(admin.ModelAdmin):
    list_display = ("erp_id", "tenant", "po", "status", "eta", "carrier")
    list_filter = ("tenant", "status")
    search_fields = ("erp_id", "po__erp_id")


@admin.register(DisruptionEvent)
class DisruptionEventAdmin(admin.ModelAdmin):
    list_display = (
        "event_id", "tenant", "supplier_erp_id", "event_type", "severity", "event_time",
        "publish_status", "needs_data_correction",
    )
    list_filter = ("tenant", "event_type", "severity", "publish_status", "needs_data_correction")
    search_fields = ("event_id", "supplier_erp_id", "correlation_id")


@admin.register(RiskCase)
class RiskCaseAdmin(admin.ModelAdmin):
    list_display = ("id", "tenant", "supplier", "status", "risk_level", "risk_score", "created_at")
    list_filter = ("tenant", "status", "risk_level")
    search_fields = ("id", "title", "supplier__name")
    # Only workflow.services.transition() may change the status (see models/cases.py).
    readonly_fields = ("status",)


@admin.register(AgentRun)
class AgentRunAdmin(ReadOnlyAdmin):
    list_display = ("id", "tenant", "risk_case", "agent", "round", "valid", "created_at")
    list_filter = ("tenant", "agent", "valid")
    search_fields = ("risk_case__id",)


@admin.register(AffectedPO)
class AffectedPOAdmin(admin.ModelAdmin):
    list_display = ("risk_case", "po_id", "item_id", "tenant", "affected_qty", "shortage")
    list_filter = ("tenant", "shortage")
    search_fields = ("risk_case__id", "po_id", "item_id")


@admin.register(Recommendation)
class RecommendationAdmin(ReadOnlyAdmin):
    list_display = ("id", "tenant", "risk_case", "round", "rank", "option_type", "status")
    list_filter = ("tenant", "option_type", "status", "unsupported")
    search_fields = ("id", "risk_case__id")


@admin.register(Approval)
class ApprovalAdmin(ReadOnlyAdmin):
    list_display = ("id", "tenant", "risk_case", "round", "decision", "decided_by", "decided_at")
    list_filter = ("tenant", "decision")
    search_fields = ("id", "risk_case__id")


@admin.register(Execution)
class ExecutionAdmin(ReadOnlyAdmin):
    list_display = ("id", "tenant", "approval", "status", "error_class", "retry_count")
    list_filter = ("tenant", "status", "error_class")
    search_fields = ("id", "approval__id")


@admin.register(IdSequence)
class IdSequenceAdmin(ReadOnlyAdmin):
    list_display = ("prefix", "last_value")
