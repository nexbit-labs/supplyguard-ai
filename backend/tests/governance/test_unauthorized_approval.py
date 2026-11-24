# ruff: noqa: E501  (long evidence literals / table rows read better unwrapped)
"""Only a PROCUREMENT_MANAGER of the case's tenant may approve; everything else is denied with
no Approval, no Execution and no trigger call. Driven through POST /recommendations/{id}/decision
on a case built by the real pipeline (event -> case -> demo plan -> PENDING_APPROVAL)."""
from __future__ import annotations

import importlib
import threading

import pytest
from django.apps import apps as django_apps
from django.db import connection, connections
from rest_framework.test import APIClient

from apps.audit.models import AuditEvent
from apps.core.models import Approval, Execution, Recommendation, RiskCase
from apps.tenants.models import Tenant
from tests.factories import make_user
from tests.helpers import api_key_client, jwt_client

from .support import decision_url, demo_plan, open_case, post_analysis

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("triggers")]
S = RiskCase.Status


@pytest.fixture
def pending(system):
    """(case, [recs rank 1..4]) in PENDING_APPROVAL, built through the HTTP API."""
    case = RiskCase.objects.get(pk=open_case(system, "demo")["risk_case_id"])
    r = post_analysis(system, case.id, "RECOVERY_PLAN", demo_plan(case.id))
    assert r.status_code == 201 and r.json()["case_status"] == S.PENDING_APPROVAL
    return case, list(Recommendation.objects.filter(risk_case=case).order_by("rank"))


def approve(client, rec, comments="go"):
    return client.post(
        decision_url(rec.id), {"decision": "APPROVE", "comments": comments}, format="json"
    )


def untouched(case, rec, triggers):
    case.refresh_from_db()
    rec.refresh_from_db()
    assert case.status == S.PENDING_APPROVAL and rec.status == "PENDING"
    assert not Approval.objects.exists() and not Execution.objects.exists()
    assert not triggers.exec


@pytest.mark.parametrize("role", ["OPS_MANAGER", "VIEWER", "ADMIN"])
def test_non_pm_roles_get_403(acme, pending, triggers, role):
    case, (rec, *_) = pending
    r = approve(jwt_client(make_user(acme, role=role)), rec)
    assert r.status_code == 403 and r.json()["error"]["code"] == "FORBIDDEN"
    untouched(case, rec, triggers)


def test_system_api_key_gets_403(system, pending, triggers):
    case, (rec, *_) = pending
    r = approve(system, rec)
    assert r.status_code == 403
    untouched(case, rec, triggers)


def test_anonymous_gets_401(pending, triggers):
    case, (rec, *_) = pending
    assert approve(APIClient(), rec).status_code == 401
    untouched(case, rec, triggers)


def test_other_tenants_pm_gets_404_and_cross_tenant_attempt_is_audited(db, pending, triggers):
    case, (rec, *_) = pending
    globex = Tenant.objects.get(pk="TEN-GLOBEX")  # seeded second tenant
    stranger = jwt_client(make_user(globex, role="PROCUREMENT_MANAGER"))
    r = approve(stranger, rec)
    assert r.status_code == 404
    row = AuditEvent.objects.get(action="security.cross_tenant_denied")  # TC-10: spec'd audit row
    assert row.tenant_id == globex.id
    untouched(case, rec, triggers)


def test_unsupported_option_cannot_be_approved_422(system, pm_client, pending, triggers):
    case, (rec, *_) = pending
    Recommendation.objects.filter(pk=rec.pk).update(
        unsupported=True, unsupported_reasons=[{"code": "PRICE_MISMATCH", "message": "x"}]
    )
    r = approve(pm_client, rec)
    assert r.status_code == 422
    assert r.json()["error"]["details"]["reason"] == "UNSUPPORTED_OPTION"
    untouched(case, rec, triggers)


def test_stale_round_gets_409(pm_client, pending, triggers):
    case, (rec, *_) = pending
    RiskCase.objects.filter(pk=case.pk).update(analysis_round=2)
    r = approve(pm_client, rec)
    assert r.status_code == 409 and r.json()["error"]["details"]["reason"] == "STALE_ROUND"
    assert not Approval.objects.exists() and not Execution.objects.exists()
    assert not triggers.exec


@pytest.mark.parametrize("state", [s for s in S.values if s != S.PENDING_APPROVAL])
def test_case_not_pending_approval_gets_409(pm_client, pending, triggers, state):
    from tests.workflow_support import set_status

    case, (rec, *_) = pending
    set_status(case, state)
    r = approve(pm_client, rec)
    assert r.status_code == 409
    assert r.json()["error"]["details"]["reason"] == "CASE_NOT_PENDING_APPROVAL"
    assert not Approval.objects.exists() and not Execution.objects.exists()
    assert not triggers.exec


def test_double_approve_one_approval_one_execution(
    pm_client, pending, triggers, django_capture_on_commit_callbacks
):
    case, (rec, other, *_) = pending
    with django_capture_on_commit_callbacks(execute=True):
        first = approve(pm_client, rec)
    assert first.status_code == 201
    again = approve(pm_client, rec)  # same option
    second = approve(pm_client, other)  # another option of the now-APPROVED case
    assert again.status_code == 409 and second.status_code == 409
    assert {again.json()["error"]["details"]["reason"], second.json()["error"]["details"]["reason"]} == {
        "CASE_NOT_PENDING_APPROVAL"
    }
    assert Approval.objects.count() == 1 and Execution.objects.count() == 1
    assert len(triggers.exec) == 1


def test_denied_attempt_is_never_recorded_as_a_decision(acme, pending):
    """Observed audit behaviour of denied decision attempts (recorded in the test report):
    only the cross-tenant 404 is specified to leave a row; 403 / 409 / 422 do not."""
    case, (rec, *_) = pending
    approve(jwt_client(make_user(acme, role="VIEWER")), rec)
    decided = AuditEvent.objects.filter(action="recommendation.decided").count()
    assert decided == 0  # a denied attempt is never recorded as a decision


# ------------------------------------------------------------ concurrency (Postgres only, CI)
@pytest.mark.django_db(transaction=True)
@pytest.mark.skipif(connection.vendor != "postgresql", reason="needs Postgres row locks")
def test_parallel_double_approve_over_http_one_winner(transactional_db):
    migration = importlib.import_module("apps.core.migrations.0002_seed_id_sequences")
    migration.create_sequences(django_apps, None)  # flush wiped the IdSequence rows
    from django.core.management import call_command

    from tests import workflow_support as ws

    ws.EXEC_REQUESTS.clear()
    call_command("load_seed", verbosity=0)
    acme = Tenant.objects.get(pk="TEN-ACME")
    system = api_key_client(acme)
    case = RiskCase.objects.get(pk=open_case(system, "demo")["risk_case_id"])
    assert post_analysis(system, case.id, "RECOVERY_PLAN", demo_plan(case.id)).status_code == 201
    rec = Recommendation.objects.filter(risk_case=case).order_by("rank").first()
    pm = make_user(acme, role="PROCUREMENT_MANAGER")
    barrier, codes = threading.Barrier(2), []

    def worker():
        try:
            client = jwt_client(pm)
            barrier.wait(timeout=10)
            codes.append(approve(client, rec).status_code)
        finally:
            connections.close_all()

    threads = [threading.Thread(target=worker) for _ in range(2)]
    [t.start() for t in threads]
    [t.join(timeout=30) for t in threads]
    assert sorted(codes) == [201, 409]
    assert Approval.objects.count() == 1 and Execution.objects.count() == 1
