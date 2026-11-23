"""Same-tenant guard, tenant copy from parent, per-tenant unique keys (CONTRACTS s10)."""
import uuid

import pytest
from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from apps.core.models import DisruptionEvent, PurchaseOrder, Supplier, TenantOwned
from tests.factories import (
    BUILDERS,
    BUILDERS_BY_TARGET,
    make_approval,
    make_case,
    make_event,
    make_line,
    make_po,
    make_supplier,
)

pytestmark = pytest.mark.django_db

TENANT_OWNED = [m for m in apps.get_models() if issubclass(m, TenantOwned)]


def _fk_fields(model):
    return [
        f
        for f in model._meta.concrete_fields
        if (f.many_to_one or f.one_to_one) and f.name != "tenant"
    ]


CROSS_TENANT_CASES = [(m, f.name) for m in TENANT_OWNED for f in _fk_fields(m)]


def test_every_tenant_owned_model_has_a_builder():
    assert {m.__name__ for m in TENANT_OWNED} == set(BUILDERS)


def test_the_tenant_owned_set_is_what_the_spec_lists():
    assert {m.__name__ for m in TENANT_OWNED} == {
        "Supplier", "PurchaseOrder", "PurchaseOrderLine", "Inventory", "Shipment",
        "DisruptionEvent", "RiskCase", "AgentRun", "AffectedPO", "Recommendation",
        "Approval", "Execution",
    }


def _guard(obj):
    """Update path through check_same_tenant; Approval and AgentRun are insert-only."""
    if type(obj).__name__ in {"Approval", "AgentRun"}:
        obj.clean()
    else:
        obj.save()


@pytest.mark.parametrize(
    ("model", "fk_name"),
    CROSS_TENANT_CASES,
    ids=[f"{m.__name__}.{n}" for m, n in CROSS_TENANT_CASES],
)
def test_cross_tenant_fk_is_rejected(tenant_a, tenant_b, model, fk_name):
    obj = BUILDERS[model.__name__](tenant_a)
    related = model._meta.get_field(fk_name).related_model
    foreign = BUILDERS_BY_TARGET[related.__name__](tenant_b)
    setattr(obj, fk_name, foreign)
    with pytest.raises(ValidationError):
        _guard(obj)


def test_same_tenant_fks_save_fine_for_every_model(tenant_a):
    for name, builder in BUILDERS.items():
        obj = builder(tenant_a)
        _guard(obj)
        assert obj.tenant_id == tenant_a.id, name


def test_missing_tenant_without_parent_raises(tenant_a):
    with pytest.raises(ValidationError):
        Supplier(erp_id="SUP-X", name="x", on_time_delivery_pct=1, quality_score=1).save()


def test_child_copies_tenant_from_parent(tenant_a):
    po = make_po(tenant_a)
    from apps.core.models import PurchaseOrderLine

    line = PurchaseOrderLine.objects.create(
        po=po, line_no=1, item_id="I", qty=1, due_date="2026-12-01", unit_price="1.0000"
    )
    assert line.tenant_id == tenant_a.id


def test_approval_and_execution_copy_tenant_from_parent(tenant_a):
    from apps.core.models import Execution

    approval = make_approval(tenant_a)
    execution = Execution.objects.create(approval=approval)
    assert execution.tenant_id == tenant_a.id


def test_approver_must_belong_to_the_same_tenant(tenant_a, tenant_b):
    from tests.factories import make_rec, make_user

    rec = make_rec(tenant_a)
    with pytest.raises(ValidationError):
        make_approval(tenant_a, rec=rec, user=make_user(tenant_b))


def test_same_erp_id_in_two_tenants_is_allowed(tenant_a, tenant_b):
    make_supplier(tenant_a, erp_id="SUP-1001")
    make_supplier(tenant_b, erp_id="SUP-1001")
    make_po(tenant_a, erp_id="PO-1")
    make_po(tenant_b, erp_id="PO-1")
    assert Supplier.objects.filter(erp_id="SUP-1001").count() == 2


def test_same_erp_id_twice_in_one_tenant_fails(tenant_a):
    make_supplier(tenant_a, erp_id="SUP-1001")
    with pytest.raises(IntegrityError), transaction.atomic():
        make_supplier(tenant_a, erp_id="SUP-1001")
    make_po(tenant_a, erp_id="PO-1")
    with pytest.raises(IntegrityError), transaction.atomic():
        make_po(tenant_a, erp_id="PO-1")


def test_event_id_unique_per_tenant(tenant_a, tenant_b):
    make_event(tenant_a, event_id="EVT-1")
    make_event(tenant_b, event_id="EVT-1")
    with pytest.raises(IntegrityError), transaction.atomic():
        make_event(tenant_a, event_id="EVT-1")
    assert DisruptionEvent.objects.get(tenant=tenant_b, event_id="EVT-1").tenant_id == "TEN-B"


def test_event_with_unknown_supplier_saves_with_null_fk(tenant_a):
    event = DisruptionEvent.objects.create(
        tenant=tenant_a,
        event_id="EVT-TC02",
        supplier_erp_id="SUP-DOES-NOT-EXIST",
        event_type="FACTORY_OUTAGE",
        severity="HIGH",
        event_time="2026-11-20T10:00:00Z",
        correlation_id=uuid.uuid4(),
        needs_data_correction=True,
    )
    event.refresh_from_db()
    assert event.supplier is None
    assert event.supplier_erp_id == "SUP-DOES-NOT-EXIST"
    assert event.needs_data_correction is True


def test_event_cannot_point_at_another_tenants_supplier(tenant_a, tenant_b):
    foreign = make_supplier(tenant_b)
    with pytest.raises(ValidationError):
        make_event(tenant_a, supplier=foreign)


def test_line_number_unique_per_po(tenant_a):
    po = make_po(tenant_a)
    make_line(tenant_a, po=po, line_no=1)
    with pytest.raises(IntegrityError), transaction.atomic():
        make_line(tenant_a, po=po, line_no=1)


def test_affected_po_unique_key(tenant_a):
    from tests.factories import make_affected

    case = make_case(tenant_a)
    make_affected(tenant_a, case, po_id="PO-1", item_id="I-1")
    make_affected(tenant_a, case, po_id="PO-1", item_id="I-2")
    with pytest.raises(IntegrityError), transaction.atomic():
        make_affected(tenant_a, case, po_id="PO-1", item_id="I-1")


def test_purchase_order_model_is_exported():
    assert PurchaseOrder._meta.app_label == "core"
