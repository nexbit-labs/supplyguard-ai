"""RiskCase status guard, Approval/Execution/Recommendation rules, id sequences."""
import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import override_settings

from apps.core.models import (
    Approval,
    Execution,
    IdSequence,
    Recommendation,
    RiskCase,
    StatusChangeNotAllowed,
    next_id,
    status_change_allowed,
)
from tests.factories import make_approval, make_case, make_execution, make_rec, make_user

pytestmark = pytest.mark.django_db


# --- ids ---------------------------------------------------------------------------------------
def test_data_migration_seeded_the_prefixes():
    assert set(IdSequence.objects.values_list("prefix", flat=True)) == {"RC", "REC", "APR", "EXE"}


def test_next_id_counts_up_and_pads():
    assert next_id("RC", 6) == "RC-000001"
    assert next_id("RC", 6) == "RC-000002"
    assert next_id("REC", 6) == "REC-000001"


def test_generated_pks_use_the_prefixes(tenant_a):
    case = make_case(tenant_a)
    rec = make_rec(tenant_a, case)
    approval = make_approval(tenant_a, rec)
    execution = make_execution(tenant_a, approval)
    assert case.id == "RC-000001"
    assert rec.id == "REC-000001"
    assert approval.id == "APR-000001"
    assert execution.id == "EXE-000001"
    assert make_case(tenant_a).id == "RC-000002"


# --- RiskCase.status guard ---------------------------------------------------------------------
def test_direct_status_change_raises(tenant_a):
    case = make_case(tenant_a)
    case.status = RiskCase.Status.ANALYZING
    with pytest.raises(StatusChangeNotAllowed):
        case.save()
    assert RiskCase.objects.get(pk=case.pk).status == "RECEIVED"


def test_status_change_inside_context_manager_works(tenant_a):
    case = make_case(tenant_a)
    with status_change_allowed():
        case.status = RiskCase.Status.ANALYZING
        case.save(update_fields=["status", "updated_at"])
    assert RiskCase.objects.get(pk=case.pk).status == "ANALYZING"
    # the guard is back on afterwards and the new value is the baseline
    case.status = RiskCase.Status.CLOSED
    with pytest.raises(StatusChangeNotAllowed):
        case.save()


def test_loaded_instance_cannot_change_status_but_can_change_other_fields(tenant_a):
    make_case(tenant_a)
    case = RiskCase.objects.get()
    case.title = "new title"
    case.save()
    case.status = RiskCase.Status.CLOSED
    with pytest.raises(StatusChangeNotAllowed):
        case.save()


def test_queryset_update_with_status_is_rejected(tenant_a):
    make_case(tenant_a)
    with pytest.raises(StatusChangeNotAllowed):
        RiskCase.objects.all().update(status="CLOSED")
    with status_change_allowed(), pytest.raises(StatusChangeNotAllowed):
        RiskCase.objects.all().update(status="CLOSED")
    assert RiskCase.objects.all().update(title="ok") == 1


def test_bulk_update_status_is_rejected(tenant_a):
    case = make_case(tenant_a)
    with pytest.raises(StatusChangeNotAllowed):
        RiskCase.objects.bulk_update([case], ["status"])


def test_refresh_from_db_resets_the_baseline(tenant_a):
    case = make_case(tenant_a)
    other = RiskCase.objects.get(pk=case.pk)
    with status_change_allowed():
        other.status = RiskCase.Status.ANALYZING
        other.save()
    case.refresh_from_db()
    case.save()  # no change relative to the refreshed value: no error
    assert case.status == "ANALYZING"


def test_admin_shows_status_read_only():
    from django.contrib import admin

    assert "status" in admin.site._registry[RiskCase].readonly_fields


# --- Approval ----------------------------------------------------------------------------------
def test_second_approve_in_same_case_and_round_fails(tenant_a):
    case = make_case(tenant_a)
    r1 = make_rec(tenant_a, case, rank=1)
    r2 = make_rec(tenant_a, case, rank=2)
    make_approval(tenant_a, r1, decision="APPROVE")
    with pytest.raises(IntegrityError), transaction.atomic():
        make_approval(tenant_a, r2, decision="APPROVE")


def test_reject_does_not_conflict_with_approve_and_next_round_is_free(tenant_a):
    case = make_case(tenant_a)
    r1 = make_rec(tenant_a, case, rank=1)
    r2 = make_rec(tenant_a, case, rank=2)
    make_approval(tenant_a, r1, decision="REJECT")
    make_approval(tenant_a, r2, decision="REJECT")
    make_approval(tenant_a, r1, decision="APPROVE")
    r_round2 = make_rec(tenant_a, case, round=2, rank=1)
    make_approval(tenant_a, r_round2, decision="APPROVE", round=2)
    assert Approval.objects.filter(risk_case=case).count() == 4


# --- Execution ---------------------------------------------------------------------------------
@pytest.mark.parametrize("decision", ["REJECT", "REQUEST_CHANGES"])
def test_execution_requires_an_approve_decision(tenant_a, decision):
    approval = make_approval(tenant_a, decision=decision)
    with pytest.raises(ValidationError):
        Execution.objects.create(tenant=tenant_a, approval=approval)
    assert Execution.objects.count() == 0


def test_execution_is_one_per_approval(tenant_a):
    approval = make_approval(tenant_a)
    make_execution(tenant_a, approval)
    with pytest.raises(IntegrityError), transaction.atomic():
        make_execution(tenant_a, approval)


@pytest.mark.parametrize(
    ("status", "error_class", "retry_count", "expected"),
    [
        ("FAILED", "TRANSIENT", 0, True),
        ("FAILED", "DOWNSTREAM", 2, True),
        ("FAILED", "VALIDATION", 0, False),
        ("FAILED", "AUTH", 0, False),
        ("FAILED", "BUSINESS", 0, False),
        ("FAILED", None, 0, False),
        ("FAILED", "TRANSIENT", 3, False),
        ("FAILED", "TRANSIENT", 5, False),
        ("SUCCEEDED", "TRANSIENT", 0, False),
        ("RUNNING", "TRANSIENT", 0, False),
        ("PENDING", None, 0, False),
    ],
)
def test_retryable_truth_table(tenant_a, status, error_class, retry_count, expected):
    execution = make_execution(
        tenant_a, status=status, error_class=error_class, retry_count=retry_count
    )
    assert execution.retryable is expected


def test_retryable_follows_the_setting(tenant_a):
    execution = make_execution(
        tenant_a, status="FAILED", error_class="TRANSIENT", retry_count=3
    )
    assert execution.retryable is False
    with override_settings(EXECUTION_MAX_RETRIES=5):
        assert execution.retryable is True


# --- Recommendation ----------------------------------------------------------------------------
def test_unsupported_recommendation_cannot_be_approved(tenant_a):
    case = make_case(tenant_a)
    with pytest.raises(IntegrityError), transaction.atomic():
        make_rec(tenant_a, case, unsupported=True, status="APPROVED")
    make_rec(tenant_a, case, rank=2, unsupported=True, status="REJECTED")
    make_rec(tenant_a, case, rank=3, unsupported=False, status="APPROVED")


def test_recommendation_unique_case_round_rank(tenant_a):
    case = make_case(tenant_a)
    make_rec(tenant_a, case, round=1, rank=1)
    make_rec(tenant_a, case, round=2, rank=1)
    with pytest.raises(IntegrityError), transaction.atomic():
        make_rec(tenant_a, case, round=1, rank=1)
    assert Recommendation.objects.count() == 2


# --- AgentRun ----------------------------------------------------------------------------------
def test_agent_runs_are_kept_per_call(tenant_a):
    from tests.factories import make_agent_run

    case = make_case(tenant_a)
    make_agent_run(tenant_a, case, raw={"a": 1}, valid=False, validation_errors=["bad"])
    make_agent_run(tenant_a, case, raw={"a": 2}, valid=True)
    assert case.agent_runs.count() == 2
    assert make_user(tenant_a).tenant_id == tenant_a.id


# --- Approval integrity ------------------------------------------------------------------------
def test_approval_risk_case_must_match_recommendation(tenant_a):
    rec = make_rec(tenant_a)
    other_case = make_case(tenant_a)
    with pytest.raises(ValidationError):
        Approval.objects.create(
            risk_case=other_case,
            recommendation=rec,
            decided_by=make_user(tenant_a),
            decision="REJECT",
            round=rec.round,
        )


def test_approval_round_must_match_recommendation(tenant_a):
    rec = make_rec(tenant_a, round=2)
    with pytest.raises(ValidationError):
        make_approval(tenant_a, rec, round=1)


def test_approval_round_defaults_from_recommendation(tenant_a):
    rec = make_rec(tenant_a, round=3)
    approval = Approval.objects.create(
        risk_case=rec.risk_case,
        recommendation=rec,
        decided_by=make_user(tenant_a),
        decision="APPROVE",
    )
    assert approval.round == 3
    assert approval.tenant_id == tenant_a.id


def test_approval_happy_path(tenant_a):
    rec = make_rec(tenant_a)
    approval = make_approval(tenant_a, rec, round=rec.round)
    assert (approval.risk_case_id, approval.round) == (rec.risk_case_id, rec.round)
