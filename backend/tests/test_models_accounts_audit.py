"""User, AuditEvent, admin wiring, migrations and spec field names (CONTRACTS s10)."""
import pytest
from django.apps import apps
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import IntegrityError, transaction

from apps.accounts.ids import format_user_id, parse_user_id
from apps.audit.models import AuditAppendOnly, AuditEvent
from tests.factories import make_tenant, make_user

pytestmark = pytest.mark.django_db
User = get_user_model()


# --- User --------------------------------------------------------------------------------------
def test_user_defaults_and_full_name(tenant_a):
    user = make_user(tenant_a, name="Asha Rao", role="VIEWER")
    assert user.get_full_name() == "Asha Rao"
    assert User(username="x").role == "VIEWER"


def test_non_superuser_without_tenant_fails():
    with pytest.raises(IntegrityError), transaction.atomic():
        User.objects.create_user(username="orphan", password="pw-not-secret-1")


def test_superuser_without_tenant_is_ok():
    admin_user = User.objects.create_superuser(username="root", password="pw-not-secret-1")
    assert admin_user.tenant_id is None


def test_tenant_cannot_be_deleted_while_users_exist(tenant_a):
    make_user(tenant_a)
    with pytest.raises(Exception, match="protected|PROTECT|Protected"):
        tenant_a.delete()


def test_user_id_round_trip():
    assert format_user_id(42) == "USR-42"
    assert parse_user_id("USR-42") == 42
    assert parse_user_id(format_user_id(7)) == 7


@pytest.mark.parametrize("bad", ["", "42", "usr-1", "USR-", "USR-1x", "USR--1", None])
def test_parse_user_id_rejects_garbage(bad):
    with pytest.raises(ValueError):
        parse_user_id(bad)


def test_admin_add_form_includes_tenant_and_role():
    user_admin = admin.site._registry[User]
    fields = [f for _, opts in user_admin.add_fieldsets for f in opts["fields"]]
    assert "tenant" in fields
    assert "role" in fields


# --- Tenant ------------------------------------------------------------------------------------
def test_tenant_created_at_keeps_the_seeded_value():
    from django.utils.dateparse import parse_datetime

    seeded = parse_datetime("2026-01-02T03:04:05+00:00")
    tenant = make_tenant("TEN-SEED", created_at=seeded)
    tenant.refresh_from_db()
    assert tenant.created_at == seeded


# --- AuditEvent --------------------------------------------------------------------------------
def _event(tenant=None):
    return AuditEvent.objects.create(
        tenant=tenant, actor="SYSTEM", action="event.received", entity_type="DisruptionEvent"
    )


def test_audit_event_can_be_created_with_and_without_tenant(tenant_a):
    assert _event(tenant_a).pk
    assert _event(None).tenant_id is None


def test_audit_event_cannot_be_modified(tenant_a):
    event = _event(tenant_a)
    event.action = "tampered"
    with pytest.raises(AuditAppendOnly):
        event.save()
    assert AuditEvent.objects.get(pk=event.pk).action == "event.received"


def test_audit_event_cannot_be_deleted(tenant_a):
    event = _event(tenant_a)
    with pytest.raises(AuditAppendOnly):
        event.delete()
    assert AuditEvent.objects.count() == 1


def test_audit_queryset_update_and_delete_are_rejected(tenant_a):
    _event(tenant_a)
    with pytest.raises(AuditAppendOnly):
        AuditEvent.objects.all().update(action="x")
    with pytest.raises(AuditAppendOnly):
        AuditEvent.objects.all().delete()
    with pytest.raises(AuditAppendOnly):
        AuditEvent.objects.bulk_update(list(AuditEvent.objects.all()), ["action"])
    assert AuditEvent.objects.count() == 1


def test_audit_admin_is_read_only(rf):
    model_admin = admin.site._registry[AuditEvent]
    request = rf.get("/")
    assert model_admin.has_add_permission(request) is False
    assert model_admin.has_change_permission(request) is False
    assert model_admin.has_delete_permission(request) is False


# --- migrations / registry ---------------------------------------------------------------------
def test_makemigrations_check_is_clean():
    call_command("makemigrations", "--check", "--dry-run")


def test_every_model_is_registered_in_the_admin():
    labels = {"tenants", "accounts", "core", "audit"}
    ours = [m for m in apps.get_models() if m._meta.app_label in labels]
    assert ours
    missing = [m.__name__ for m in ours if m not in admin.site._registry]
    assert not missing


# --- field names from the spec -----------------------------------------------------------------
def _names(label):
    return {f.name for f in apps.get_model(label)._meta.get_fields() if not f.auto_created}


EXPECTED = {
    "core.RiskCase": {
        "id", "tenant", "created_at", "event", "supplier", "status", "risk_level", "risk_score",
        "title", "impact_summary", "exposure_amount", "currency", "analysis_round", "snapshot",
        "snapshot_captured_at", "impact", "data_quality_flags", "analysis",
        "no_viable_option_reason", "recovered_at", "correlation_id", "updated_at",
    },
    "core.Execution": {
        "id", "tenant", "created_at", "approval", "status", "retry_count", "error_class", "error",
        "target_system", "request_payload", "response_status", "response_payload", "started_at",
        "finished_at",
    },
    "core.AffectedPO": {
        "tenant", "created_at", "risk_case", "po_id", "item_id", "item_name", "affected_qty",
        "estimated_delay_days", "exposure_amount", "currency", "due_date",
        "inventory_coverage_days", "shortage",
    },
    "core.Recommendation": {
        "id", "tenant", "created_at", "risk_case", "round", "rank", "option_type",
        "proposed_action", "action_steps", "trade_offs", "evidence", "assumptions", "data_gaps",
        "estimated_lead_time_days", "estimated_cost_delta", "currency", "status", "unsupported",
        "unsupported_reasons",
    },
    "core.Approval": {
        "id", "tenant", "created_at", "risk_case", "round", "recommendation", "decided_by",
        "decision", "comments", "decided_at",
    },
    "core.DisruptionEvent": {
        "tenant", "created_at", "event_id", "supplier_erp_id", "supplier", "event_type",
        "severity", "event_time", "expected_duration_hours", "description", "source_system",
        "correlation_id", "shipment_id", "po_id", "needs_data_correction", "publish_status",
    },
    "audit.AuditEvent": {
        "tenant", "timestamp", "actor", "action", "entity_type", "entity_id", "correlation_id",
        "data",
    },
}


@pytest.mark.parametrize("label", sorted(EXPECTED))
def test_field_names_match_section_10(label):
    assert _names(label) == EXPECTED[label]
