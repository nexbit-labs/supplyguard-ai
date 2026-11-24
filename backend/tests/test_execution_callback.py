"""POST /api/v1/internal/executions/<id>/result: validation + sanitising here, the Execution update
and the case move are N-10's record_execution_result (U-06 round 3)."""
import json
from unittest import mock

import pytest
from rest_framework.test import APIClient

from apps.audit.models import AuditEvent
from apps.core.models import Execution, RiskCase
from apps.integrations import executions
from tests.factories import make_approval, make_execution, make_rec, make_tenant, make_user
from tests.helpers import api_key_client, jwt_client, strip_cid
from tests.workflow_support import case_in

pytestmark = pytest.mark.django_db
S = RiskCase.Status
VERIFIED_OK = {"status": "SUCCEEDED", "attempt": 1, "verified": True, "response_status": 200}
FAILED = {"status": "FAILED", "attempt": 1, "error_class": "TRANSIENT", "error": "ERP timeout"}


def running_execution(tenant, **kw):
    """APPROVED decision, case EXECUTING, execution RUNNING (attempt 1)."""
    case = case_in(tenant, S.EXECUTING)
    pm = make_user(tenant, role="PROCUREMENT_MANAGER")
    approval = make_approval(tenant, make_rec(tenant, case), pm)
    kw.setdefault("status", "RUNNING")
    return make_execution(tenant, approval, **kw)


def url(execution_id):
    return f"/api/v1/internal/executions/{execution_id}/result"


def case_status(execution):
    return RiskCase.objects.get(pk=execution.approval.risk_case_id).status


@pytest.fixture
def acme():
    return make_tenant("TEN-ACME")


@pytest.fixture
def key(acme):
    return api_key_client(acme)


@pytest.fixture
def execution(acme):
    return running_execution(acme)


def send(client, execution_id, body):
    return client.post(url(execution_id), body, format="json")


# --- success / failure rules -------------------------------------------------------------------
@pytest.mark.parametrize(
    "extra", [{}, {"verified": False}, {"response_status": 200}], ids=["missing", "false", "status"]
)
def test_unverified_success_is_converted_to_a_business_failure(key, execution, extra):
    """Not a 400 (that would strand the execution RUNNING): N-10's rule, FAILED / BUSINESS."""
    response = send(key, execution.id, {"status": "SUCCEEDED", "attempt": 1, **extra})
    assert response.status_code == 200, response.content
    body = response.json()
    assert body["status"] == "FAILED" and body["error_class"] == "BUSINESS"
    assert body["case_status"] == "EXECUTION_FAILED" and body["retryable"] is False
    execution.refresh_from_db()
    assert execution.status == "FAILED" and execution.error == "ERP change not verified"
    assert case_status(execution) == S.EXECUTION_FAILED
    # the same callback again is an identical repeat
    again = send(key, execution.id, {"status": "SUCCEEDED", "attempt": 1, **extra})
    assert again.status_code == 200


def test_verified_must_be_a_boolean(key, execution):
    assert send(key, execution.id, {**VERIFIED_OK, "verified": "maybe"}).status_code == 400


def test_attempt_is_required_and_positive(key, execution):
    for body in (
        {"status": "SUCCEEDED", "verified": True},
        {**VERIFIED_OK, "attempt": 0},
        {**VERIFIED_OK, "attempt": "x"},
    ):
        assert send(key, execution.id, body).status_code == 400, body


def test_verified_success_recovers_the_case(key, execution):
    response = send(
        key,
        execution.id,
        {**VERIFIED_OK, "response_payload": {"po_id": "PO-1001", "new_due_date": "2026-12-09"}},
    )
    assert response.status_code == 200, response.content
    body = response.json()
    assert body["execution_id"] == execution.id and body["status"] == "SUCCEEDED"
    assert body["error_class"] is None and body["finished_at"]
    assert body["case_status"] == "RECOVERED" and body["retryable"] is False
    execution.refresh_from_db()
    assert execution.status == "SUCCEEDED" and execution.response_status == 200
    assert execution.finished_at is not None
    assert execution.response_payload == {"po_id": "PO-1001", "new_due_date": "2026-12-09"}
    assert execution.error is None and execution.error_class is None
    case = execution.approval.risk_case
    case.refresh_from_db()
    assert case.status == S.RECOVERED and case.recovered_at
    (row,) = AuditEvent.objects.filter(action="risk_case.status_changed")
    assert row.actor == "SYSTEM" and row.tenant_id == "TEN-ACME"
    assert row.data["from"] == "EXECUTING" and row.data["to"] == "RECOVERED"


def test_transient_failure_moves_case_to_execution_failed_and_is_retryable(key, execution):
    response = send(key, execution.id, {**FAILED, "response_status": 504})
    assert response.status_code == 200, response.content
    body = response.json()
    assert body["status"] == "FAILED" and body["error_class"] == "TRANSIENT"
    assert body["case_status"] == "EXECUTION_FAILED" and body["retryable"] is True
    execution.refresh_from_db()
    assert (execution.status, execution.error_class, execution.error) == (
        "FAILED", "TRANSIENT", "ERP timeout",
    )
    assert execution.response_status == 504 and execution.retryable


def test_business_failure_is_not_retryable(key, execution):
    body = send(key, execution.id, {**FAILED, "error_class": "BUSINESS"}).json()
    assert body["case_status"] == "EXECUTION_FAILED" and body["retryable"] is False


def test_response_payload_and_error_are_redacted(key, execution):
    payload = {
        "api_key": "sgk_live_abcdef",
        "nested": {"password": "hunter2", "Authorization": "Bearer abc.def", "ok": 1},
        "list": [{"token": "t0k3n"}],
        "po_id": "PO-1",
    }
    response = send(key, execution.id, {**VERIFIED_OK, "response_payload": payload})
    assert response.status_code == 200
    stored = Execution.objects.get(pk=execution.pk).response_payload
    assert stored["api_key"] == "[REDACTED]"
    assert stored["nested"] == {"password": "[REDACTED]", "Authorization": "[REDACTED]", "ok": 1}
    assert stored["list"] == [{"token": "[REDACTED]"}]
    assert stored["po_id"] == "PO-1"
    assert "hunter2" not in json.dumps(stored)


def test_error_text_is_sanitised_and_capped(acme, key):
    err = "upstream said password=hunter2 and token: abc123 " + "x" * 1900
    execution = running_execution(acme)
    assert send(key, execution.id, {**FAILED, "error": err}).status_code == 200
    stored = Execution.objects.get(pk=execution.pk).error
    assert "hunter2" not in stored and "abc123" not in stored
    assert len(stored) <= 2000


def test_error_longer_than_2000_is_400(key, execution):
    assert send(key, execution.id, {**FAILED, "error": "x" * 2001}).status_code == 400


def test_failed_needs_error_class_and_error(key, execution):
    assert send(key, execution.id, {"status": "FAILED", "attempt": 1, "error": "b"}
                ).status_code == 400
    assert send(key, execution.id, {"status": "FAILED", "attempt": 1, "error_class": "AUTH"}
                ).status_code == 400
    bad = {"status": "FAILED", "attempt": 1, "error_class": "NOPE", "error": "x"}
    assert send(key, execution.id, bad).status_code == 400
    assert Execution.objects.get(pk=execution.pk).status == "RUNNING"


@pytest.mark.parametrize("status", [99, 600, "x"])
def test_response_status_range(key, execution, status):
    assert send(key, execution.id, {**VERIFIED_OK, "response_status": status}).status_code == 400


def test_non_terminal_status_is_400(key, execution):
    assert send(key, execution.id, {"status": "RUNNING", "attempt": 1}).status_code == 400


def test_payload_must_be_an_object(key, execution):
    assert send(key, execution.id, {**VERIFIED_OK, "response_payload": [1]}).status_code == 400


# --- N-10 rules surfaced as contract errors ----------------------------------------------------
def test_stale_attempt_is_409(key, acme):
    execution = running_execution(acme, retry_count=1)  # attempt 2 is the current one
    response = send(key, execution.id, FAILED)  # a late callback of attempt 1
    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "CONFLICT"
    assert error["details"]["reason"] == "STALE_EXECUTION_RESULT"
    assert error["details"]["current_attempt"] == 2
    assert Execution.objects.get(pk=execution.pk).status == "RUNNING"
    assert send(key, execution.id, {**VERIFIED_OK, "attempt": 2}).status_code == 200


def test_not_running_execution_is_409(key, acme):
    pending = running_execution(acme, status="PENDING")
    response = send(key, pending.id, VERIFIED_OK)
    assert response.status_code == 409
    assert response.json()["error"]["details"]["reason"] == "EXECUTION_NOT_RUNNING"
    assert Execution.objects.get(pk=pending.pk).status == "PENDING"


def test_identical_repeat_is_200_without_change(key, execution):
    first = send(key, execution.id, VERIFIED_OK)
    stamp = Execution.objects.get(pk=execution.pk).finished_at
    second = send(key, execution.id, VERIFIED_OK)
    assert second.status_code == 200
    assert second.json() == first.json()
    assert Execution.objects.get(pk=execution.pk).finished_at == stamp
    assert AuditEvent.objects.filter(action="risk_case.status_changed").count() == 1


def test_conflicting_terminal_result_is_409(key, execution):
    send(key, execution.id, VERIFIED_OK)
    response = send(key, execution.id, FAILED)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CONFLICT"
    assert Execution.objects.get(pk=execution.pk).status == "SUCCEEDED"
    assert case_status(execution) == S.RECOVERED


# --- tenancy / roles ---------------------------------------------------------------------------
def test_foreign_execution_is_the_same_404_as_missing_and_audited(execution):
    other = make_tenant("TEN-OTHER")
    foreign = api_key_client(other)
    hit = send(foreign, execution.id, VERIFIED_OK)
    miss = send(foreign, "EXE-999999", VERIFIED_OK)
    assert hit.status_code == miss.status_code == 404
    assert strip_cid(hit) == strip_cid(miss)
    assert Execution.objects.get(pk=execution.pk).status == "RUNNING"
    (row,) = AuditEvent.objects.filter(action="security.cross_tenant_denied")
    assert row.tenant_id == "TEN-OTHER" and row.actor == "SYSTEM"
    assert (row.entity_type, row.entity_id) == ("Execution", execution.id)


def test_tenant_in_body_is_ignored(key, acme):
    other = make_tenant("TEN-OTHER")
    foreign = running_execution(other)
    assert send(key, foreign.id, {**VERIFIED_OK, "tenant_id": "TEN-OTHER"}).status_code == 404
    assert Execution.objects.get(pk=foreign.pk).status == "RUNNING"


@pytest.mark.parametrize("role", ["ADMIN", "PROCUREMENT_MANAGER", "OPS_MANAGER", "VIEWER"])
def test_user_tokens_are_403(acme, execution, role):
    client = jwt_client(make_user(acme, role=role))
    assert send(client, execution.id, VERIFIED_OK).status_code == 403
    assert Execution.objects.get(pk=execution.pk).status == "RUNNING"


def test_anonymous_is_401(execution):
    assert send(APIClient(), execution.id, VERIFIED_OK).status_code == 401


# --- size cap ----------------------------------------------------------------------------------
def test_large_payload_is_truncated_with_a_marker_not_rejected(key, execution):
    payload = {"rows": ["x" * 100 for _ in range(1000)], "api_key": "sgk_secret"}
    response = send(key, execution.id, {**VERIFIED_OK, "response_payload": payload})
    assert response.status_code == 200
    stored = Execution.objects.get(pk=execution.pk).response_payload
    assert stored["_truncated"] is True
    assert stored["original_size_bytes"] > executions.PAYLOAD_MAX_BYTES
    assert len(json.dumps(stored).encode()) <= executions.PAYLOAD_MAX_BYTES
    assert "sgk_secret" not in json.dumps(stored)


def test_payload_just_under_the_cap_is_kept(key, execution):
    payload = {"blob": "y" * 19_000}
    send(key, execution.id, {**VERIFIED_OK, "response_payload": payload})
    assert Execution.objects.get(pk=execution.pk).response_payload == payload


# --- delegation --------------------------------------------------------------------------------
def test_delegates_to_record_execution_result_with_attempt(key, execution):
    from apps.workflow import services

    with mock.patch.object(
        services, "record_execution_result", wraps=services.record_execution_result
    ) as spy:
        assert send(key, execution.id, {**VERIFIED_OK, "attempt": 1}).status_code == 200
    spy.assert_called_once()
    kwargs = spy.call_args.kwargs
    assert kwargs["attempt"] == 1 and kwargs["outcome"] == "SUCCEEDED"
    assert kwargs["correlation_id"] == execution.approval.risk_case.correlation_id
