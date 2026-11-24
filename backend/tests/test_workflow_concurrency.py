"""Concurrency (WORKFLOW_DESIGN.md s5.5). Needs real row locks, so Postgres only:
    DATABASE_URL=postgres://... python -m pytest tests/test_workflow_concurrency.py
Skipped on SQLite."""
import importlib
import threading

import pytest
from django.apps import apps as django_apps
from django.db import connection, connections

from apps.audit.models import AuditEvent
from apps.core.models import Approval, Execution, RiskCase
from apps.workflow import services
from apps.workflow.exceptions import DecisionConflict
from tests import workflow_support as ws
from tests.factories import make_user
from tests.workflow_support import case_in, fake_triggers, pending_case  # noqa: F401

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.usefixtures("fake_triggers"),
    pytest.mark.skipif(connection.vendor != "postgresql", reason="needs Postgres row locks"),
]


@pytest.fixture(autouse=True)
def _reseed_id_sequences(transactional_db):
    """transaction=True tests flush the DB, which wipes the IdSequence rows that data migration
    core/0002 creates; recreate them with the migration's own function before each test."""
    migration = importlib.import_module("apps.core.migrations.0002_seed_id_sequences")
    migration.create_sequences(django_apps, None)


def run_parallel(*jobs):
    """Run each callable in its own thread (own DB connection) released by a barrier."""
    barrier = threading.Barrier(len(jobs))
    results = [None] * len(jobs)

    def worker(i, job):
        try:
            barrier.wait(timeout=10)
            results[i] = ("ok", job())
        except Exception as exc:  # noqa: BLE001 - the test inspects it
            results[i] = ("err", exc)
        finally:
            connections.close_all()

    threads = [threading.Thread(target=worker, args=(i, j)) for i, j in enumerate(jobs)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    return results


def assert_one_winner(results):
    oks = [r for r in results if r[0] == "ok"]
    errs = [r for r in results if r[0] == "err"]
    assert len(oks) == 1 and len(errs) == len(results) - 1, results
    assert all(isinstance(e[1], DecisionConflict) for e in errs), errs


def test_two_approves_on_different_recs_of_one_case(tenant_a):
    case, (a, b) = pending_case(tenant_a, 2)
    pm = make_user(tenant_a, role="PROCUREMENT_MANAGER")
    results = run_parallel(
        lambda: services.record_decision(a, "APPROVE", "", user=pm),
        lambda: services.record_decision(b, "APPROVE", "", user=pm),
    )
    assert_one_winner(results)
    assert Approval.objects.filter(decision="APPROVE").count() == 1
    assert Execution.objects.count() == 1 and len(ws.EXEC_REQUESTS) == 1


def test_two_approves_on_the_same_rec(tenant_a):
    case, (rec,) = pending_case(tenant_a)
    pm = make_user(tenant_a, role="PROCUREMENT_MANAGER")
    results = run_parallel(
        lambda: services.record_decision(rec, "APPROVE", "", user=pm),
        lambda: services.record_decision(rec, "APPROVE", "", user=pm),
    )
    assert_one_winner(results)
    assert Approval.objects.count() == 1 and Execution.objects.count() == 1
    assert len(ws.EXEC_REQUESTS) == 1


def test_approve_racing_request_changes_ends_consistent(tenant_a):
    case, (a, b) = pending_case(tenant_a, 2)
    pm = make_user(tenant_a, role="PROCUREMENT_MANAGER")
    results = run_parallel(
        lambda: services.record_decision(a, "APPROVE", "", user=pm),
        lambda: services.record_decision(b, "REQUEST_CHANGES", "redo", user=pm),
    )
    assert_one_winner(results)
    case.refresh_from_db()
    if case.status in (RiskCase.Status.APPROVED, RiskCase.Status.EXECUTING):
        assert Execution.objects.count() == 1 and case.analysis_round == 1
    else:
        assert case.status == RiskCase.Status.ANALYZING and case.analysis_round == 2
        assert Execution.objects.count() == 0 and not ws.EXEC_REQUESTS


def test_concurrent_transitions_to_the_same_state_are_serialised(tenant_a):
    case = case_in(tenant_a, RiskCase.Status.RECEIVED)
    results = run_parallel(
        *[
            (lambda: services.transition(case, "ANALYZING", actor="SYSTEM", reason="x"))
            for _ in range(3)
        ]
    )
    assert all(r[0] == "ok" for r in results), results
    assert sorted(r[1].changed for r in results) == [False, False, True]
    assert AuditEvent.objects.filter(action="risk_case.status_changed").count() == 1
