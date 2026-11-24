"""POST /recommendations/{id}/decision (WORKFLOW_DESIGN.md s5.2 - s5.4)."""
from unittest import mock

import pytest

from apps.audit.models import AuditEvent
from apps.core.models import Approval, Execution, Recommendation, RiskCase
from apps.workflow import services
from tests import workflow_support as ws
from tests.factories import make_rec, make_user
from tests.helpers import api_key_client, jwt_client, strip_cid
from tests.workflow_support import case_in, fake_triggers, pending_case  # noqa: F401

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("fake_triggers")]
S = RiskCase.Status


def url(rec):
    return f"/api/v1/recommendations/{rec.pk}/decision"


def post(client, rec, decision="APPROVE", comments="", **extra):
    body = {"decision": decision, "comments": comments, **extra}
    return client.post(url(rec), body, format="json")


@pytest.fixture
def pm(tenant_a):
    return make_user(tenant_a, role="PROCUREMENT_MANAGER")


def no_side_effects(case, rec):
    case.refresh_from_db()
    rec.refresh_from_db()
    assert case.status == S.PENDING_APPROVAL and rec.status == Recommendation.Status.PENDING
    assert not Approval.objects.exists() and not Execution.objects.exists()


# --- roles and tenant --------------------------------------------------------------------------
@pytest.mark.parametrize("role", ["OPS_MANAGER", "VIEWER", "ADMIN"])
def test_other_roles_get_403(tenant_a, role):
    case, (rec,) = pending_case(tenant_a)
    r = post(jwt_client(make_user(tenant_a, role=role)), rec)
    assert r.status_code == 403 and r.json()["error"]["code"] == "FORBIDDEN"
    no_side_effects(case, rec)


def test_system_api_key_gets_403(tenant_a):
    case, (rec,) = pending_case(tenant_a)
    r = post(api_key_client(tenant_a), rec)
    assert r.status_code == 403
    no_side_effects(case, rec)


def test_anonymous_gets_401(tenant_a):
    from rest_framework.test import APIClient

    case, (rec,) = pending_case(tenant_a)
    r = post(APIClient(), rec)
    assert r.status_code == 401 and r.json()["error"]["code"] == "UNAUTHENTICATED"


def test_other_tenant_gets_identical_404_and_is_audited(tenant_a, tenant_b):
    case, (rec,) = pending_case(tenant_a)
    stranger = jwt_client(make_user(tenant_b, role="PROCUREMENT_MANAGER"))
    foreign = post(stranger, rec)
    missing = stranger.post(
        "/api/v1/recommendations/REC-999999/decision", {"decision": "APPROVE"}, format="json"
    )
    assert foreign.status_code == 404
    assert strip_cid(foreign) == strip_cid(missing)
    assert AuditEvent.objects.filter(
        action="security.cross_tenant_denied", tenant=tenant_b
    ).count() == 1
    no_side_effects(case, rec)


def test_inactive_tenant_gets_403():
    from tests.factories import make_tenant

    off = make_tenant("TEN-OFF", status="INACTIVE")
    r = post(jwt_client(make_user(off, role="PROCUREMENT_MANAGER")), make_rec_stub())
    assert r.status_code == 403


def make_rec_stub():
    class R:
        pk = "REC-1"

    return R()


def test_tenant_and_decider_in_body_are_ignored(tenant_a, tenant_b, pm):
    case, (rec,) = pending_case(tenant_a)
    other = make_user(tenant_b, role="PROCUREMENT_MANAGER")
    r = post(jwt_client(pm), rec, tenant_id=tenant_b.id, decided_by=other.pk, round=9)
    assert r.status_code == 201
    approval = Approval.objects.get()
    assert approval.tenant_id == tenant_a.id and approval.decided_by_id == pm.pk
    assert approval.round == 1


# --- validation --------------------------------------------------------------------------------
@pytest.mark.parametrize("decision", ["REJECT", "REQUEST_CHANGES"])
def test_comments_required_for_reject_and_request_changes(tenant_a, pm, decision):
    case, (rec,) = pending_case(tenant_a)
    for comments in ("", "   "):
        r = post(jwt_client(pm), rec, decision, comments)
        assert r.status_code == 400 and r.json()["error"]["code"] == "VALIDATION_ERROR"
    no_side_effects(case, rec)


def test_bad_or_missing_decision_and_long_comments(tenant_a, pm):
    case, (rec,) = pending_case(tenant_a)
    c = jwt_client(pm)
    assert c.post(url(rec), {}, format="json").status_code == 400
    assert post(c, rec, "MAYBE").status_code == 400
    assert post(c, rec, "APPROVE", "x" * 2001).status_code == 400
    no_side_effects(case, rec)


# --- state rules -------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "state", [s for s in S.values if s != S.PENDING_APPROVAL]
)
def test_case_not_pending_approval_is_409(tenant_a, pm, state):
    case, (rec,) = pending_case(tenant_a)
    ws.set_status(case, state)
    r = post(jwt_client(pm), rec)
    assert r.status_code == 409
    err = r.json()["error"]
    assert err["code"] == "CONFLICT"
    assert err["details"]["reason"] == "CASE_NOT_PENDING_APPROVAL"
    assert err["details"]["current_status"] == state


def test_stale_round_is_409(tenant_a, pm):
    case, (rec,) = pending_case(tenant_a)
    RiskCase.objects.filter(pk=case.pk).update(analysis_round=2)
    r = post(jwt_client(pm), rec)
    assert r.status_code == 409 and r.json()["error"]["details"]["reason"] == "STALE_ROUND"


@pytest.mark.parametrize("decision", ["APPROVE", "REJECT", "REQUEST_CHANGES"])
def test_unsupported_option_is_422(tenant_a, pm, decision):
    case, _ = pending_case(tenant_a, 0)
    rec = make_rec(tenant_a, case, unsupported=True, unsupported_reasons=["NO_EVIDENCE"])
    r = post(jwt_client(pm), rec, decision, "because")
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "BUSINESS_RULE" and err["details"]["reason"] == "UNSUPPORTED_OPTION"
    assert err["details"]["unsupported_reasons"] == ["NO_EVIDENCE"]


@pytest.mark.parametrize("status", ["APPROVED", "REJECTED", "CHANGES_REQUESTED"])
def test_already_decided_recommendation_is_409(tenant_a, pm, status):
    case, _ = pending_case(tenant_a, 0)
    rec = make_rec(tenant_a, case, status=status)
    r = post(jwt_client(pm), rec)
    assert r.status_code == 409 and r.json()["error"]["details"]["reason"] == "ALREADY_DECIDED"


# --- APPROVE -----------------------------------------------------------------------------------
def test_approve_creates_approval_execution_and_triggers_once(
    tenant_a, pm, django_capture_on_commit_callbacks
):
    case, (rec,) = pending_case(tenant_a)
    with django_capture_on_commit_callbacks(execute=False) as cbs:
        r = post(jwt_client(pm), rec, "APPROVE", "go")
    assert r.status_code == 201
    body = r.json()
    approval = Approval.objects.get()
    execution = Execution.objects.get()
    assert body["approval_id"] == approval.pk and body["execution_id"] == execution.pk
    assert body["decision"] == "APPROVE" and body["recommendation_status"] == "APPROVED"
    assert body["case_status"] == "APPROVED" and body["analysis_round"] == 1
    assert body["decided_by"] == f"USR-{pm.pk}" and body["correlation_id"] == str(
        case.correlation_id
    )
    assert execution.status == "PENDING" and execution.approval_id == approval.pk
    assert execution.request_payload == rec.proposed_action
    assert len(cbs) == 1 and not ws.EXEC_REQUESTS  # nothing runs before commit

    with django_capture_on_commit_callbacks(execute=True):
        services.start_execution(execution.pk, tenant_a.id, case.correlation_id)
    case.refresh_from_db()
    execution.refresh_from_db()
    assert case.status == S.EXECUTING and execution.status == "RUNNING"
    assert len(ws.EXEC_REQUESTS) == 1
    req = ws.EXEC_REQUESTS[0]
    assert req.execution_id == execution.pk and req.approval_id == approval.pk
    assert req.recommendation_id == rec.pk and req.correlation_id == str(case.correlation_id)
    assert req.attempt == 1 and req.proposed_action == rec.proposed_action


def test_approve_full_callbacks_reach_executing(tenant_a, pm, django_capture_on_commit_callbacks):
    case, (rec,) = pending_case(tenant_a)
    with django_capture_on_commit_callbacks(execute=True):
        assert post(jwt_client(pm), rec).status_code == 201
    case.refresh_from_db()
    assert case.status == S.EXECUTING and len(ws.EXEC_REQUESTS) == 1


def test_double_approve_is_409_and_creates_nothing_more(
    tenant_a, pm, django_capture_on_commit_callbacks
):
    case, (rec, rec2) = pending_case(tenant_a, 2)
    c = jwt_client(pm)
    with django_capture_on_commit_callbacks(execute=True):
        assert post(c, rec).status_code == 201
    again = post(c, rec)
    assert again.status_code == 409
    assert again.json()["error"]["details"]["reason"] == "CASE_NOT_PENDING_APPROVAL"
    other = post(c, rec2)
    assert other.status_code == 409
    assert other.json()["error"]["details"]["reason"] == "CASE_NOT_PENDING_APPROVAL"
    assert Approval.objects.filter(decision="APPROVE").count() == 1
    assert Execution.objects.count() == 1 and len(ws.EXEC_REQUESTS) == 1


def test_already_approved_backstop_is_409(tenant_a, pm):
    from django.db import IntegrityError

    case, (rec,) = pending_case(tenant_a)
    with mock.patch.object(Approval.objects, "create", side_effect=IntegrityError("dup")):
        with pytest.raises(services.DecisionConflict) as exc:
            services.record_decision(rec, "APPROVE", "", user=pm)
    assert exc.value.details["reason"] == "ALREADY_APPROVED"


# --- REJECT / REQUEST_CHANGES / TC-06 ----------------------------------------------------------
def test_reject_two_options_case_rejected_only_after_the_last(
    tenant_a, pm, django_capture_on_commit_callbacks
):
    case, (a, b) = pending_case(tenant_a, 2)
    make_rec(tenant_a, case, unsupported=True)  # an unsupported sibling is not actionable
    c = jwt_client(pm)
    with django_capture_on_commit_callbacks(execute=True):
        r1 = post(c, a, "REJECT", "too slow")
        assert r1.status_code == 201 and r1.json()["case_status"] == "PENDING_APPROVAL"
        r2 = post(c, b, "REJECT", "too costly")
        assert r2.status_code == 201 and r2.json()["case_status"] == "REJECTED"
    # TC-06: no execution call, no Execution row, comments audited
    assert not ws.EXEC_REQUESTS and not Execution.objects.exists() and not ws.ANALYSIS_REQUESTS
    decided = AuditEvent.objects.filter(action="recommendation.decided").order_by("id")
    assert [e.data["comments"] for e in decided] == ["too slow", "too costly"]
    assert all(e.data["decision"] == "REJECT" for e in decided)
    assert all(e.correlation_id == case.correlation_id for e in decided)


def test_request_changes_bumps_round_and_asks_for_analysis(
    tenant_a, pm, django_capture_on_commit_callbacks
):
    case, (rec,) = pending_case(tenant_a)
    with django_capture_on_commit_callbacks(execute=True):
        r = post(jwt_client(pm), rec, "REQUEST_CHANGES", "find a cheaper one")
    assert r.status_code == 201
    body = r.json()
    assert body["case_status"] == "ANALYZING" and body["analysis_round"] == 2
    assert body["execution_id"] is None and body["recommendation_status"] == "CHANGES_REQUESTED"
    assert not ws.EXEC_REQUESTS and not Execution.objects.exists()
    assert len(ws.ANALYSIS_REQUESTS) == 1
    assert ws.ANALYSIS_REQUESTS[0].feedback == "find a cheaper one"
    assert ws.ANALYSIS_REQUESTS[0].round == 2
    # old-round recommendation is stale now (case also left PENDING_APPROVAL)
    assert post(jwt_client(pm), rec, "APPROVE").status_code == 409
    ev = AuditEvent.objects.get(action="recommendation.decided")
    assert ev.data["comments"] == "find a cheaper one"
    assert AuditEvent.objects.filter(action="risk_case.status_changed").count() == 1


def test_stale_round_after_request_changes_with_pending_again(tenant_a, pm):
    case, (rec,) = pending_case(tenant_a)
    post(jwt_client(pm), rec, "REQUEST_CHANGES", "again")
    ws.set_status(case, S.PENDING_APPROVAL)  # the next round got validated
    r = post(jwt_client(pm), rec, "APPROVE")
    assert r.status_code == 409 and r.json()["error"]["details"]["reason"] == "STALE_ROUND"


def test_audit_rows_carry_the_case_correlation_id(tenant_a, pm):
    case, (rec,) = pending_case(tenant_a)
    post(jwt_client(pm), rec, "APPROVE", "ok")
    rows = AuditEvent.objects.filter(
        action__in=["recommendation.decided", "risk_case.status_changed"]
    )
    assert rows.count() == 2
    assert {r.correlation_id for r in rows} == {case.correlation_id}
    status_row = rows.get(action="risk_case.status_changed")
    assert status_row.data["approval_id"] == Approval.objects.get().pk


def test_rolled_back_approve_triggers_nothing(tenant_a, pm, django_capture_on_commit_callbacks):
    case, (rec,) = pending_case(tenant_a)
    with mock.patch.object(services, "transition", side_effect=RuntimeError("boom")):
        with django_capture_on_commit_callbacks(execute=True) as cbs:
            with pytest.raises(RuntimeError):
                services.record_decision(rec, "APPROVE", "", user=pm)
    assert cbs == [] and not ws.EXEC_REQUESTS
    assert not Approval.objects.exists() and not Execution.objects.exists()
    no_side_effects(case, rec)


def test_execution_cannot_exist_for_a_reject_approval(tenant_a, pm):
    from django.core.exceptions import ValidationError

    case, (rec,) = pending_case(tenant_a)
    services.record_decision(rec, "REJECT", "no", user=pm)
    with pytest.raises(ValidationError):
        Execution.objects.create(tenant=tenant_a, approval=Approval.objects.get())


def test_error_body_has_contract_shape(tenant_a, pm):
    case, (rec,) = pending_case(tenant_a)
    ws.set_status(case, S.APPROVED)
    err = post(jwt_client(pm), rec).json()["error"]
    assert set(err) == {"code", "message", "details", "correlation_id"}
    assert case_in  # keep import used


@pytest.mark.parametrize("role", ["OPS_MANAGER", "VIEWER", "ADMIN"])
def test_service_itself_refuses_non_pm_even_for_a_non_final_reject(tenant_a, role):
    case, (a, b) = pending_case(tenant_a, 2)  # REJECT of a would not call transition()
    with pytest.raises(services.DecisionNotPermitted) as exc:
        services.record_decision(a, "REJECT", "no", user=make_user(tenant_a, role=role))
    assert exc.value.status_code == 403
    with pytest.raises(services.DecisionNotPermitted):
        services.record_decision(a, "APPROVE", "", user="SYSTEM")
    no_side_effects(case, a)
    assert not AuditEvent.objects.filter(action="recommendation.decided").exists()


def test_failure_after_commit_does_not_turn_a_saved_approval_into_a_500(
    tenant_a, pm, django_capture_on_commit_callbacks
):
    case, (rec,) = pending_case(tenant_a)
    with mock.patch.object(services, "start_execution", side_effect=RuntimeError("late")):
        with django_capture_on_commit_callbacks(execute=True):
            r = post(jwt_client(pm), rec)
    assert r.status_code == 201
    assert Approval.objects.count() == 1
    execution = Execution.objects.get()
    assert execution.status == "PENDING"  # left for workflow_resume_pending
