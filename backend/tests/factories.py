"""Small hand-written builders for model tests. Each `make_*` creates a valid row (and its
parents) inside the given tenant; keyword overrides win."""
import itertools
import uuid
from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.utils import timezone

from apps.core.models import (
    AffectedPO,
    AgentRun,
    Approval,
    DisruptionEvent,
    Execution,
    Inventory,
    PurchaseOrder,
    PurchaseOrderLine,
    Recommendation,
    RiskCase,
    Shipment,
    Supplier,
)
from apps.tenants.models import Tenant

_n = itertools.count(1)


def make_tenant(id="TEN-A", **kw):
    kw.setdefault("name", id)
    kw.setdefault("currency", "USD")
    kw.setdefault("locations", ["DC-1"])
    return Tenant.objects.create(id=id, **kw)


def make_user(tenant, **kw):
    kw.setdefault("username", f"user{next(_n)}")
    kw.setdefault("role", "PROCUREMENT_MANAGER")
    return get_user_model().objects.create_user(password="pw-not-secret-1", tenant=tenant, **kw)


def make_supplier(tenant, **kw):
    kw.setdefault("erp_id", f"SUP-{next(_n)}")
    kw.setdefault("name", "Acme Parts")
    kw.setdefault("on_time_delivery_pct", Decimal("95.50"))
    kw.setdefault("quality_score", Decimal("4.5"))
    return Supplier.objects.create(tenant=tenant, **kw)


def make_po(tenant, supplier=None, **kw):
    kw.setdefault("erp_id", f"PO-{next(_n)}")
    kw.setdefault("order_date", date(2026, 11, 1))
    kw.setdefault("expected_date", date(2026, 12, 1))
    kw.setdefault("amount", Decimal("1000.00"))
    kw.setdefault("currency", tenant.currency)
    return PurchaseOrder.objects.create(
        tenant=tenant, supplier=supplier or make_supplier(tenant), **kw
    )


def make_line(tenant, po=None, **kw):
    kw.setdefault("line_no", next(_n))
    kw.setdefault("item_id", "ITEM-1")
    kw.setdefault("qty", 10)
    kw.setdefault("due_date", date(2026, 12, 1))
    kw.setdefault("unit_price", Decimal("12.3456"))
    return PurchaseOrderLine.objects.create(tenant=tenant, po=po or make_po(tenant), **kw)


def make_inventory(tenant, **kw):
    kw.setdefault("item_id", f"ITEM-{next(_n)}")
    kw.setdefault("location_id", "DC-1")
    kw.setdefault("on_hand", 100)
    kw.setdefault("daily_demand", Decimal("5.00"))
    return Inventory.objects.create(tenant=tenant, **kw)


def make_shipment(tenant, po=None, **kw):
    kw.setdefault("erp_id", f"SHP-{next(_n)}")
    kw.setdefault("eta", date(2026, 12, 5))
    return Shipment.objects.create(tenant=tenant, po=po or make_po(tenant), **kw)


def make_event(tenant, supplier=None, **kw):
    kw.setdefault("event_id", f"EVT-{next(_n)}")
    kw.setdefault("supplier_erp_id", supplier.erp_id if supplier else "SUP-UNKNOWN")
    kw.setdefault("event_type", "FACTORY_OUTAGE")
    kw.setdefault("severity", "HIGH")
    kw.setdefault("event_time", timezone.now())
    kw.setdefault("correlation_id", uuid.uuid4())
    return DisruptionEvent.objects.create(tenant=tenant, supplier=supplier, **kw)


def make_case(tenant, event=None, **kw):
    if event is None:
        supplier = make_supplier(tenant)
        event = make_event(tenant, supplier=supplier)
    kw.setdefault("supplier", event.supplier)
    kw.setdefault("risk_level", "HIGH")
    kw.setdefault("risk_score", 80)
    kw.setdefault("exposure_amount", Decimal("5000.00"))
    kw.setdefault("currency", tenant.currency)
    kw.setdefault("correlation_id", event.correlation_id)
    return RiskCase.objects.create(tenant=tenant, event=event, **kw)


def make_agent_run(tenant, risk_case=None, **kw):
    kw.setdefault("agent", "RISK_ANALYST")
    return AgentRun.objects.create(tenant=tenant, risk_case=risk_case or make_case(tenant), **kw)


def make_affected(tenant, risk_case=None, **kw):
    kw.setdefault("po_id", f"PO-{next(_n)}")
    kw.setdefault("item_id", "ITEM-1")
    kw.setdefault("affected_qty", 10)
    kw.setdefault("estimated_delay_days", Decimal("3.00"))
    kw.setdefault("exposure_amount", Decimal("100.00"))
    kw.setdefault("currency", tenant.currency)
    kw.setdefault("due_date", date(2026, 12, 1))
    return AffectedPO.objects.create(
        tenant=tenant, risk_case=risk_case or make_case(tenant), **kw
    )


def make_rec(tenant, risk_case=None, **kw):
    kw.setdefault("rank", next(_n))
    kw.setdefault("option_type", "REVISED_SCHEDULE")
    return Recommendation.objects.create(
        tenant=tenant, risk_case=risk_case or make_case(tenant), **kw
    )


def make_approval(tenant, rec=None, user=None, **kw):
    rec = rec or make_rec(tenant)
    kw.setdefault("decision", "APPROVE")
    return Approval.objects.create(
        tenant=tenant,
        risk_case=rec.risk_case,
        recommendation=rec,
        decided_by=user or make_user(tenant),
        **kw,
    )


def make_execution(tenant, approval=None, **kw):
    return Execution.objects.create(
        tenant=tenant, approval=approval or make_approval(tenant), **kw
    )


# Builder per tenant-owned model (tests assert this covers every TenantOwned subclass).
BUILDERS = {
    "Supplier": make_supplier,
    "PurchaseOrder": make_po,
    "PurchaseOrderLine": make_line,
    "Inventory": make_inventory,
    "Shipment": make_shipment,
    "DisruptionEvent": make_event,
    "RiskCase": make_case,
    "AgentRun": make_agent_run,
    "AffectedPO": make_affected,
    "Recommendation": make_rec,
    "Approval": make_approval,
    "Execution": make_execution,
}
BUILDERS_BY_TARGET = {**BUILDERS, "User": make_user}
