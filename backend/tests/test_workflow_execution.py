"""Execution services, stubs, resume command (WORKFLOW_DESIGN.md s5.6)."""
from datetime import timedelta

import pytest
from django.core.management import call_command
from django.utils import timezone

from apps.audit.models import AuditEvent
from apps.core.models import Execution, RiskCase
from apps.workflow import hooks, services
from apps.workflow.exceptions import DecisionConflict, PreconditionFailed
from tests import workflow_support as ws
from tests.factories import make_approval, make_execution, make_rec, make_user
from tests.workflow_support import case_in, fake_triggers  # noqa: F401

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("fake_triggers")]
S = RiskCase.Status


def approved(tenant, **exe_kw):
    case = case_in(tenant, S.APPROVED)
    pm = make_user(tenant, role="PROCUREMENT_MANAGER")
    approval = make_approval(tenant, make_rec(tenant, case, proposed_action={"a": 1}), pm)
    return case, pm, make_execution(tenant, approval, **exe_kw)


def run_start(case, exe, cbs):
    with cbs(execute=True):
        services.start_execution(exe.pk, exe.tenant_id, case.correlation_id)


def test_start_execution_is_idempotent(tenant_a, django_capture_on_commit_callbacks):
    case, _, exe = approved(tenant_a)
    run_start(case, exe, django_capture_on_commit_callbacks)
    run_start(case, exe, django_capture_on_commit_callbacks)
    assert len(ws.EXEC_REQUESTS) == 1
    assert AuditEvent.objects.filter(action="risk_case.status_changed").count() == 1
    exe.refresh_from_db()
    assert exe.status == "RUNNING" and exe.started_at


def test_start_execution_unknown_id_is_ignored(tenant_a):
    services.start_execution("EXE-404", tenant_a.id, "6f1c2a3b-0000-4000-8000-000000000000")


def test_trigger_failure_fails_the_execution_as_downstream(
    tenant_a, settings, django_capture_on_commit_callbacks
):
    settings.WORKFLOW_EXECUTION_TRIGGER = "tests.workflow_support.RaisingExecutionTrigger"
    case, _, exe = approved(tenant_a)
    run_start(case, exe, django_capture_on_commit_callbacks)
    case.refresh_from_db()
    exe.refresh_from_db()
    assert case.status == S.EXECUTION_FAILED
    assert exe.status == "FAILED" and exe.error_class == "DOWNSTREAM" and exe.retryable
    assert "ConnectionError" in exe.error


def failed_case(tenant, **kw):
    case = case_in(tenant, S.EXECUTION_FAILED)
    pm = make_user(tenant, role="PROCUREMENT_MANAGER")
    approval = make_approval(tenant, make_rec(tenant, case), pm)
    kw.setdefault("status", "FAILED")
    kw.setdefault("error_class", "TRANSIENT")
    return case, pm, make_execution(tenant, approval, **kw)


def test_retry_increments_and_keeps_the_execution_id(
    tenant_a, django_capture_on_commit_callbacks
):
    case, pm, exe = failed_case(tenant_a)
    with django_capture_on_commit_callbacks(execute=True):
        result = services.retry_execution(exe, actor=pm)
    exe.refresh_from_db()
    assert result.changed and exe.retry_count == 1 and exe.status == "RUNNING"
    assert exe.error_class is None and exe.finished_at is None
    case.refresh_from_db()
    assert case.status == S.EXECUTING
    assert [(r.execution_id, r.attempt) for r in ws.EXEC_REQUESTS] == [(exe.pk, 2)]


def test_retry_not_retryable_is_422_and_changes_nothing(tenant_a):
    case, pm, exe = failed_case(tenant_a, error_class="VALIDATION")
    with pytest.raises(PreconditionFailed) as e:
        services.retry_execution(exe, actor=pm)
    assert e.value.status_code == 422
    exe.refresh_from_db()
    case.refresh_from_db()
    assert exe.retry_count == 0 and case.status == S.EXECUTION_FAILED


def test_retry_at_max_is_422(tenant_a, settings):
    case, pm, exe = failed_case(tenant_a, retry_count=settings.EXECUTION_MAX_RETRIES)
    with pytest.raises(PreconditionFailed):
        services.retry_execution(exe, actor=pm)


def test_second_retry_is_refused(tenant_a, django_capture_on_commit_callbacks):
    case, pm, exe = failed_case(tenant_a)
    with django_capture_on_commit_callbacks(execute=True):
        services.retry_execution(exe, actor=pm)
        with pytest.raises(DecisionConflict) as e:
            services.retry_execution(exe, actor=pm)
    assert e.value.details["reason"] == "EXECUTION_NOT_FAILED"
    exe.refresh_from_db()
    assert exe.retry_count == 1


def running_case(tenant):
    case = case_in(tenant, S.EXECUTING)
    pm = make_user(tenant, role="PROCUREMENT_MANAGER")
    approval = make_approval(tenant, make_rec(tenant, case), pm)
    return case, make_execution(tenant, approval, status="RUNNING")


def test_result_succeeded_recovers_and_sets_recovered_at(tenant_a):
    case, exe = running_case(tenant_a)
    result = services.record_execution_result(
        exe, outcome="SUCCEEDED", attempt=1, response_status=200, response_payload={"ok": True},
        actor="SYSTEM",
    )
    case.refresh_from_db()
    exe.refresh_from_db()
    assert result.changed and case.status == S.RECOVERED and case.recovered_at
    assert exe.status == "SUCCEEDED" and exe.finished_at and exe.response_status == 200


def test_result_failed_moves_to_execution_failed(tenant_a):
    case, exe = running_case(tenant_a)
    services.record_execution_result(
        exe, outcome="FAILED", attempt=1, error_class="BUSINESS", error="unverified",
        actor="SYSTEM",
    )
    case.refresh_from_db()
    exe.refresh_from_db()
    assert case.status == S.EXECUTION_FAILED and exe.error_class == "BUSINESS"
    assert not exe.retryable


def test_same_result_twice_is_a_noop(tenant_a):
    case, exe = running_case(tenant_a)
    services.record_execution_result(exe, outcome="SUCCEEDED", attempt=1, actor="SYSTEM")
    again = services.record_execution_result(exe, outcome="SUCCEEDED", attempt=1, actor="SYSTEM")
    assert again.changed is False and again.audit_event is None
    assert AuditEvent.objects.filter(action="risk_case.status_changed").count() == 1


def test_result_for_non_running_execution_is_409(tenant_a):
    case, _, exe = approved(tenant_a)  # PENDING
    with pytest.raises(DecisionConflict) as e:
        services.record_execution_result(exe, outcome="SUCCEEDED", attempt=1, actor="SYSTEM")
    assert e.value.details["reason"] == "EXECUTION_NOT_RUNNING"
    assert e.value.status_code == 409


def test_failed_result_without_error_class_changes_nothing(tenant_a):
    case, exe = running_case(tenant_a)
    with pytest.raises(PreconditionFailed):
        services.record_execution_result(exe, outcome="FAILED", attempt=1, actor="SYSTEM")
    exe.refresh_from_db()
    assert exe.status == "RUNNING"


def test_bad_outcome_is_value_error(tenant_a):
    case, exe = running_case(tenant_a)
    with pytest.raises(ValueError):
        services.record_execution_result(exe, outcome="MAYBE", attempt=1, actor="SYSTEM")


# --- stubs -------------------------------------------------------------------------------------
def test_stub_triggers_log_and_audit(tenant_a, settings, django_capture_on_commit_callbacks):
    settings.WORKFLOW_EXECUTION_TRIGGER = "apps.workflow.hooks.StubExecutionTrigger"
    settings.WORKFLOW_ANALYSIS_TRIGGER = "apps.workflow.hooks.StubAnalysisTrigger"
    assert isinstance(hooks.get_execution_trigger(), hooks.StubExecutionTrigger)
    assert isinstance(hooks.get_analysis_trigger(), hooks.StubAnalysisTrigger)
    case, _, exe = approved(tenant_a)
    run_start(case, exe, django_capture_on_commit_callbacks)
    case.refresh_from_db()
    assert case.status == S.EXECUTING  # the stub fakes no result
    assert AuditEvent.objects.filter(action="execution.dispatch_stubbed").count() == 1
    rejected = case_in(tenant_a, S.REJECTED)
    with django_capture_on_commit_callbacks(execute=True):
        services.reanalyze_case(
            rejected, actor=make_user(tenant_a, role="PROCUREMENT_MANAGER"), reason="x"
        )
    assert AuditEvent.objects.filter(action="analysis.request_stubbed").count() == 1


# --- resume command ----------------------------------------------------------------------------
def test_resume_pending_picks_up_old_pending_executions(tenant_a):
    case, _, exe = approved(tenant_a)
    call_command("workflow_resume_pending")  # too young: ignored
    exe.refresh_from_db()
    assert exe.status == "PENDING"
    Execution.objects.filter(pk=exe.pk).update(created_at=timezone.now() - timedelta(minutes=5))
    call_command("workflow_resume_pending")
    exe.refresh_from_db()
    case.refresh_from_db()
    assert exe.status == "RUNNING" and case.status == S.EXECUTING


def test_late_callback_of_attempt_1_cannot_fail_attempt_2(
    tenant_a, django_capture_on_commit_callbacks
):
    case, pm, exe = failed_case(tenant_a)
    with django_capture_on_commit_callbacks(execute=True):
        services.retry_execution(exe, actor=pm)  # attempt 2 is now RUNNING
    with pytest.raises(DecisionConflict) as e:
        services.record_execution_result(
            exe, outcome="FAILED", attempt=1, error_class="TRANSIENT", error="late",
            actor="SYSTEM",
        )
    assert e.value.status_code == 409
    assert e.value.details["reason"] == "STALE_EXECUTION_RESULT"
    assert e.value.details["current_attempt"] == 2
    case.refresh_from_db()
    exe.refresh_from_db()
    assert case.status == S.EXECUTING and exe.status == "RUNNING"
    services.record_execution_result(exe, outcome="SUCCEEDED", attempt=2, actor="SYSTEM")
    case.refresh_from_db()
    assert case.status == S.RECOVERED


def test_dispatch_skips_when_execution_is_not_running(tenant_a):
    case, _, exe = approved(tenant_a)  # PENDING, case APPROVED
    services.dispatch_execution(exe.pk, tenant_a.id, case.correlation_id)
    assert not ws.EXEC_REQUESTS
    case2, exe2 = running_case(tenant_a)
    services.dispatch_execution(exe2.pk, tenant_a.id, case2.correlation_id)
    assert len(ws.EXEC_REQUESTS) == 1
    ws.EXEC_REQUESTS.clear()
    services.record_execution_result(exe2, outcome="SUCCEEDED", attempt=1, actor="SYSTEM")
    services.dispatch_execution(exe2.pk, tenant_a.id, case2.correlation_id)  # case RECOVERED
    assert not ws.EXEC_REQUESTS
