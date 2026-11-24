"""Regression tests for the opus review of U-03 (id collisions, append-only rows, status guard
bypasses, approval immutability, read-only admins, tenant changes, form errors)."""
from decimal import Decimal

import pytest
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError

from apps.audit.models import AuditEvent
from apps.core.models import (
    AffectedPO,
    AgentRun,
    AgentRunAppendOnly,
    Approval,
    ApprovalImmutable,
    Execution,
    IdSequence,
    PurchaseOrder,
    Recommendation,
    RiskCase,
    StatusChangeNotAllowed,
    Supplier,
    status_change_allowed,
)
from tests.factories import (
    BUILDERS,
    make_agent_run,
    make_approval,
    make_case,
    make_event,
    make_execution,
    make_po,
    make_rec,
    make_supplier,
    make_user,
)

pytestmark = pytest.mark.django_db
User = get_user_model()


def _row(model, pk):
    return model.objects.filter(pk=pk).values().get()


# --- 1. generated ids never overwrite another row ----------------------------------------------
def _unsaved_case(t):
    ev = make_event(t, make_supplier(t))
    return RiskCase(
        tenant=t, event=ev, supplier=ev.supplier, risk_level="HIGH", risk_score=80,
        exposure_amount=Decimal("1.00"), currency="USD", correlation_id=ev.correlation_id,
    )


def _unsaved_rec(t):
    return Recommendation(
        tenant=t, risk_case=make_case(t), rank=1, option_type="REVISED_SCHEDULE"
    )


def _unsaved_approval(t):
    rec = make_rec(t)
    return Approval(
        tenant=t, risk_case=rec.risk_case, recommendation=rec, decided_by=make_user(t),
        decision="APPROVE",
    )


def _unsaved_execution(t):
    return Execution(tenant=t, approval=make_approval(t))


@pytest.mark.parametrize(
    "prefix, model, unsaved",
    [
        ("RC", RiskCase, _unsaved_case),
        ("REC", Recommendation, _unsaved_rec),
        ("APR", Approval, _unsaved_approval),
        ("EXE", Execution, _unsaved_execution),
    ],
)
def test_colliding_generated_id_does_not_overwrite_another_tenants_row(
    tenant_a, tenant_b, prefix, model, unsaved
):
    first = unsaved(tenant_a)
    first.save()
    before = _row(model, first.pk)
    IdSequence.objects.filter(prefix=prefix).update(last_value=0)  # next id collides
    second = unsaved(tenant_b)
    with pytest.raises(IntegrityError), transaction.atomic():
        second.save()
    assert _row(model, first.pk) == before
    assert model.objects.filter(tenant=tenant_b).count() == 0


# --- 2. audit constructor tampering -------------------------------------------------------------
def test_audit_event_constructor_with_existing_pk_cannot_overwrite(tenant_a):
    ev = AuditEvent.objects.create(tenant=tenant_a, actor="SYSTEM", action="X", entity_type="t")
    before = _row(AuditEvent, ev.pk)
    with pytest.raises(IntegrityError), transaction.atomic():
        AuditEvent(pk=ev.pk, actor="evil", action="TAMPER", entity_type="t").save()
    assert _row(AuditEvent, ev.pk) == before


# --- 3. status guard bypasses -------------------------------------------------------------------
@pytest.mark.parametrize("update_fields", [None, ["status"]])
def test_deferred_status_cannot_be_changed_directly(tenant_a, update_fields):
    case = make_case(tenant_a)
    loaded = RiskCase.objects.defer("status").get(pk=case.pk)
    loaded.status = RiskCase.Status.CLOSED
    with pytest.raises(StatusChangeNotAllowed):
        loaded.save(update_fields=update_fields)
    assert RiskCase.objects.get(pk=case.pk).status == "RECEIVED"


def test_deferred_status_change_inside_context_manager_works(tenant_a):
    case = make_case(tenant_a)
    loaded = RiskCase.objects.defer("status").get(pk=case.pk)
    with status_change_allowed():
        loaded.status = RiskCase.Status.ANALYZING
        loaded.save(update_fields=["status"])
    assert RiskCase.objects.get(pk=case.pk).status == "ANALYZING"


def test_constructor_with_existing_pk_cannot_change_status(tenant_a):
    case = make_case(tenant_a)
    clone = RiskCase(
        pk=case.pk, tenant=tenant_a, event=case.event, supplier=case.supplier,
        status=RiskCase.Status.CLOSED, risk_level="HIGH", risk_score=80,
        exposure_amount=Decimal("1.00"), currency="USD", correlation_id=case.correlation_id,
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        clone.save()
    assert RiskCase.objects.get(pk=case.pk).status == "RECEIVED"


def test_normal_non_status_save_still_works(tenant_a):
    case = RiskCase.objects.get(pk=make_case(tenant_a).pk)
    case.title = "new title"
    case.save()
    assert RiskCase.objects.get(pk=case.pk).title == "new title"


# --- 4. Approval immutability, unsupported, read-only admins ------------------------------------
def test_approval_cannot_be_resaved_with_a_changed_decision(tenant_a):
    approval = make_approval(tenant_a)
    execution = make_execution(tenant_a, approval)
    before = _row(Approval, approval.pk)
    exec_before = _row(Execution, execution.pk)
    loaded = Approval.objects.get(pk=approval.pk)
    loaded.decision = Approval.Decision.REJECT
    with pytest.raises(ApprovalImmutable):
        loaded.save()
    assert _row(Approval, approval.pk) == before
    assert _row(Execution, execution.pk) == exec_before


def test_approval_constructor_with_existing_pk_cannot_overwrite(tenant_a):
    approval = make_approval(tenant_a)
    before = _row(Approval, approval.pk)
    clone = Approval(
        pk=approval.pk, tenant=tenant_a, risk_case=approval.risk_case,
        recommendation=approval.recommendation, decided_by=approval.decided_by, decision="REJECT",
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        clone.save()
    assert _row(Approval, approval.pk) == before


def test_unsupported_recommendation_cannot_be_approved(tenant_a):
    rec = make_rec(tenant_a, unsupported=True)
    with pytest.raises(ValidationError):
        make_approval(tenant_a, rec, decision="APPROVE")
    assert Approval.objects.count() == 0
    # rejecting it is fine
    assert make_approval(tenant_a, rec, decision="REJECT").decision == "REJECT"


@pytest.mark.parametrize(
    "model", [Approval, Execution, AgentRun, Recommendation, IdSequence]
)
def test_admin_is_read_only_for_workflow_models(rf, model):
    model_admin = admin.site._registry[model]
    request = rf.get("/")
    assert model_admin.has_add_permission(request) is False
    assert model_admin.has_change_permission(request) is False
    assert model_admin.has_delete_permission(request) is False


# --- 6. tenant cannot change on update ----------------------------------------------------------
def test_supplier_tenant_cannot_change(tenant_a, tenant_b):
    supplier = Supplier.objects.get(pk=make_supplier(tenant_a).pk)
    supplier.tenant = tenant_b
    with pytest.raises(ValidationError):
        supplier.save()
    assert Supplier.objects.get(pk=supplier.pk).tenant_id == tenant_a.id


def test_purchase_order_tenant_cannot_change_even_with_matching_supplier(tenant_a, tenant_b):
    po = PurchaseOrder.objects.get(pk=make_po(tenant_a).pk)
    po.tenant = tenant_b
    po.supplier = make_supplier(tenant_b)
    with pytest.raises(ValidationError):
        po.save()
    assert PurchaseOrder.objects.get(pk=po.pk).tenant_id == tenant_a.id


def test_risk_case_tenant_cannot_change_even_with_matching_parents(tenant_a, tenant_b):
    case = RiskCase.objects.get(pk=make_case(tenant_a).pk)
    supplier_b = make_supplier(tenant_b)
    case.tenant = tenant_b
    case.event = make_event(tenant_b, supplier_b)
    case.supplier = supplier_b
    with pytest.raises(ValidationError):
        case.save()
    assert RiskCase.objects.get(pk=case.pk).tenant_id == tenant_a.id


def test_tenant_baseline_resets_on_refresh(tenant_a, tenant_b):
    supplier = Supplier.objects.get(pk=make_supplier(tenant_a).pk)
    supplier.tenant = tenant_b
    supplier.refresh_from_db()
    supplier.name = "renamed"
    supplier.save()
    assert Supplier.objects.get(pk=supplier.pk).name == "renamed"


# --- 7. guards also run from clean() ------------------------------------------------------------
def test_clean_reports_cross_tenant_fk(tenant_a, tenant_b):
    supplier_a = make_supplier(tenant_a)
    po = PurchaseOrder(
        tenant=tenant_b, supplier=supplier_a, erp_id="PO-X", order_date="2026-11-01",
        expected_date="2026-12-01", amount=Decimal("1.00"), currency="USD",
    )
    with pytest.raises(ValidationError):
        po.clean()


def test_clean_reports_execution_without_approve(tenant_a):
    approval = make_approval(tenant_a, decision="REJECT")
    with pytest.raises(ValidationError):
        Execution(tenant=tenant_a, approval=approval).clean()


def test_clean_reports_unsupported_approval(tenant_a):
    rec = make_rec(tenant_a, unsupported=True)
    approval = Approval(
        tenant=tenant_a, risk_case=rec.risk_case, recommendation=rec,
        decided_by=make_user(tenant_a), decision="APPROVE",
    )
    with pytest.raises(ValidationError):
        approval.clean()


# --- 8. admin add-user form needs a tenant ------------------------------------------------------
def _add_form(rf, data):
    request = rf.get("/")
    request.user = User(username="adm", is_staff=True, is_superuser=True, is_active=True)
    form_class = admin.site._registry[User].get_form(request, None)
    return form_class(data=data)


def test_admin_add_user_form_requires_tenant(rf, tenant_a):
    data = {
        "username": "newuser", "password1": "Xk9#mP2vLq7!zR", "password2": "Xk9#mP2vLq7!zR",
        "role": "VIEWER",
    }
    form = _add_form(rf, data)
    assert not form.is_valid()
    assert "tenant" in form.errors
    form = _add_form(rf, {**data, "tenant": tenant_a.pk})
    assert form.is_valid(), form.errors


# --- 9. User ordering ---------------------------------------------------------------------------
def test_user_has_default_ordering():
    assert User._meta.ordering == ["id"]


# --- 10. AgentRun append-only -------------------------------------------------------------------
def test_agent_run_cannot_be_modified(tenant_a):
    run = make_agent_run(tenant_a)
    before = _row(AgentRun, run.pk)
    loaded = AgentRun.objects.get(pk=run.pk)
    loaded.raw = {"tampered": True}
    with pytest.raises(AgentRunAppendOnly):
        loaded.save()
    assert _row(AgentRun, run.pk) == before


def test_agent_run_constructor_with_existing_pk_cannot_overwrite(tenant_a):
    run = make_agent_run(tenant_a)
    before = _row(AgentRun, run.pk)
    with pytest.raises(IntegrityError), transaction.atomic():
        AgentRun(
            pk=run.pk, tenant=tenant_a, risk_case=run.risk_case, agent="RISK_ANALYST",
            raw={"x": 1},
        ).save()
    assert _row(AgentRun, run.pk) == before


# --- 12. missing coverage -----------------------------------------------------------------------
def test_tenant_is_copied_from_parent_for_case_run_affected_and_recommendation(tenant_a):
    case = _unsaved_case(tenant_a)
    case.tenant_id = None
    case.save()
    assert case.tenant_id == tenant_a.id

    run = AgentRun(risk_case=case, agent="RISK_ANALYST")
    run.save()
    assert run.tenant_id == tenant_a.id

    affected = AffectedPO(
        risk_case=case, po_id="PO-1", item_id="I-1", affected_qty=1,
        estimated_delay_days=Decimal("1"), exposure_amount=Decimal("1"), currency="USD",
        due_date="2026-12-01",
    )
    affected.save()
    assert affected.tenant_id == tenant_a.id

    rec = Recommendation(risk_case=case, rank=1, option_type="REVISED_SCHEDULE")
    rec.save()
    assert rec.tenant_id == tenant_a.id


def test_superuser_without_tenant_cannot_be_the_approver(tenant_a):
    superuser = User.objects.create_superuser(username="root", password="Xk9#mP2vLq7!zR")
    with pytest.raises(ValidationError):
        make_approval(tenant_a, user=superuser)


@pytest.mark.parametrize("name", sorted(BUILDERS))
def test_tenant_cannot_be_deleted_while_tenant_owned_rows_exist(tenant_a, name):
    BUILDERS[name](tenant_a)
    with pytest.raises(ProtectedError), transaction.atomic():
        tenant_a.delete()


def test_change_form_superuser_check_falls_back_to_instance(rf, tenant_a):
    from apps.accounts.admin import SupplyGuardUserChangeForm

    form = SupplyGuardUserChangeForm(instance=User(username="su", is_superuser=True))
    form.cleaned_data = {}
    assert form._superuser_allowed() is True
    form = SupplyGuardUserChangeForm(instance=User(username="x", is_superuser=False))
    form.cleaned_data = {}
    assert form._superuser_allowed() is False
