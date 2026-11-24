# ruff: noqa: F811  (pytest fixtures imported from test_analysis_persist)
"""N-07 persist review fixes (Opus review probes ported): duplicate rank, out-of-range numbers,
required round, apply failures, audit rows, body limit, concurrency."""
from __future__ import annotations

import importlib
import threading

import pytest
from django.apps import apps as django_apps
from django.db import connection, connections

from apps.audit.models import AuditEvent
from apps.core.models import AgentRun, Recommendation, RiskCase
from apps.intelligence.exceptions import AnalysisConflict
from apps.intelligence.services import record_analysis
from apps.intelligence.tests.test_analysis_persist import (  # noqa: F401  (fixtures)
    S,
    analysis,
    case,
    plan,
    post,
    system,
    url,
)
from apps.intelligence.tests.test_services import seeded  # noqa: F401
from tests.factories import make_user
from tests.helpers import jwt_client

pytestmark = pytest.mark.django_db


def test_duplicate_rank_is_422_not_500(system, case, plan):
    plan["options"][1]["rank"] = plan["options"][0]["rank"]
    for _ in range(2):  # a retry gives the same answer, not a stuck 500
        r = post(system, case, "RECOVERY_PLAN", plan)
        assert r.status_code == 422, r.content
        assert r.json()["error"]["details"]["reason"] == "AGENT_OUTPUT_INVALID"
    assert not Recommendation.objects.exists()
    assert AgentRun.objects.filter(risk_case=case, valid=False).count() == 2
    case.refresh_from_db()
    assert case.status == S.ANALYZING
    assert AuditEvent.objects.filter(entity_id=case.id, action="analysis.invalid").count() == 2


def test_out_of_range_numbers_are_nulled_and_unsupported(system, case, plan):
    plan["options"][0]["estimated_lead_time_days"] = 10**12
    plan["options"][0]["estimated_cost_delta"] = {"amount": "1000000000000.00", "currency": "INR"}
    r = post(system, case, "RECOVERY_PLAN", plan)
    assert r.status_code == 201, r.content
    rec = Recommendation.objects.get(risk_case=case, rank=1)
    assert rec.unsupported and rec.estimated_lead_time_days is None
    assert rec.estimated_cost_delta is None
    assert any("out of range" in g for g in rec.data_gaps)
    assert "VALUE_OUT_OF_RANGE" in {x["code"] for x in rec.unsupported_reasons}


def test_round_is_required(system, case, plan):
    r = system.post(url(case), {"kind": "RECOVERY_PLAN", "payload": plan}, format="json")
    assert r.status_code == 400
    assert not AgentRun.objects.exists()
    case.refresh_from_db()
    assert case.status == S.ANALYZING


def test_stale_round_409_writes_nothing(system, case, plan):
    r = post(system, case, "RECOVERY_PLAN", plan, round=2)
    assert r.status_code == 409
    assert r.json()["error"]["details"]["reason"] == "STALE_ROUND"
    assert not AgentRun.objects.exists() and not Recommendation.objects.exists()


def test_apply_failure_is_422_and_keeps_the_agent_run(system, case, plan, monkeypatch):
    from apps.intelligence import services

    def boom(*a, **k):
        raise services.IntegrityError("unique")

    monkeypatch.setattr(services, "_apply_valid", boom)
    r = post(system, case, "RECOVERY_PLAN", plan)
    assert r.status_code == 422
    assert r.json()["error"]["details"]["reason"] == "AGENT_OUTPUT_NOT_APPLIED"
    assert AgentRun.objects.filter(risk_case=case, valid=True).count() == 1
    assert not Recommendation.objects.exists()
    case.refresh_from_db()
    assert case.status == S.ANALYZING
    assert AuditEvent.objects.filter(entity_id=case.id, action="analysis.rejected").count() == 1


def test_422_error_list_is_truncated(system, case):
    bad = {"risk_case_id": case.id, "options": [{} for _ in range(200)]}
    r = post(system, case, "RECOVERY_PLAN", bad)
    d = r.json()["error"]["details"]
    assert r.status_code == 422 and len(d["errors"]) <= 50
    assert d["error_count"] >= len(d["errors"])


def test_oversized_body_413(system, case):
    big = {"kind": "RECOVERY_PLAN", "round": 1, "payload": {"x": "a" * (1024 * 1024 + 10)}}
    r = system.post(url(case), big, format="json")
    assert r.status_code == 413
    assert not AgentRun.objects.exists()
    assert AuditEvent.objects.filter(entity_id=case.id, action="analysis.rejected").count() == 1


def test_failures_are_audited_400_403_404_409(seeded, case, plan, system):  # noqa: F811
    def n():
        return AuditEvent.objects.filter(action="analysis.rejected").count()

    system.post(url(case), {"kind": "BAD"}, format="json")  # 400
    assert n() == 1
    pm = jwt_client(make_user(seeded, role="PROCUREMENT_MANAGER"))
    assert post(pm, case, "RECOVERY_PLAN", plan).status_code == 403
    assert n() == 2
    missing = system.post(
        "/api/v1/internal/risk-cases/RC-999999/analysis",
        {"kind": "RECOVERY_PLAN", "payload": plan, "round": 1},
        format="json",
    )
    assert missing.status_code == 404 and n() == 3
    assert post(system, case, "RECOVERY_PLAN", plan, round=9).status_code == 409
    assert n() == 4


def test_sequential_duplicate_plan_is_409_and_writes_nothing_more(system, case, plan):
    assert post(system, case, "RECOVERY_PLAN", plan).status_code == 201
    assert post(system, case, "RECOVERY_PLAN", plan).status_code == 409
    assert AgentRun.objects.filter(risk_case=case).count() == 1
    assert Recommendation.objects.filter(risk_case=case).count() == 4


@pytest.fixture
def reseed_ids(transactional_db):
    """transaction=True tests flush the DB, wiping the IdSequence rows; recreate them."""
    migration = importlib.import_module("apps.core.migrations.0002_seed_id_sequences")
    migration.create_sequences(django_apps, None)


@pytest.mark.skipif(connection.vendor != "postgresql", reason="needs Postgres row locks")
@pytest.mark.django_db(transaction=True)
def test_concurrent_duplicate_plan_one_winner(reseed_ids, seeded, case, plan):  # noqa: F811
    barrier = threading.Barrier(2)
    out = [None, None]

    def worker(i):
        try:
            barrier.wait(timeout=10)
            fresh = RiskCase.objects.get(pk=case.pk)
            result = record_analysis(
                tenant=seeded, case=fresh, kind="RECOVERY_PLAN", payload=plan,
                actor="SYSTEM", round=1,
            )
            out[i] = ("ok", result)
        except Exception as exc:  # noqa: BLE001
            out[i] = ("err", exc)
        finally:
            connections.close_all()

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert sorted(o[0] for o in out) == ["err", "ok"], out
    loser = next(o[1] for o in out if o[0] == "err")
    assert isinstance(loser, AnalysisConflict)
    assert loser.details["reason"] in {"CASE_NOT_ANALYZING", "ALREADY_RECORDED"}
    assert AgentRun.objects.filter(risk_case=case).count() == 1
    assert Recommendation.objects.filter(risk_case=case).count() == 4


# ---------------------------------------------------------------- parser hardening
def _raw(client, case, body: bytes):
    return client.post(url(case), data=body, content_type="application/json")


def test_deeply_nested_json_is_400_with_audit(system, case):
    deep = b"[" * 50000 + b"]" * 50000
    body = b'{"kind": "RECOVERY_PLAN", "round": 1, "payload": ' + deep + b"}"
    r = _raw(system, case, body)
    assert r.status_code == 400, r.content[:200]
    assert r.json()["error"]["details"]["reason"] == "INVALID_JSON"
    assert not AgentRun.objects.exists()
    assert AuditEvent.objects.filter(entity_id=case.id, action="analysis.rejected").count() == 1


def test_moderately_nested_json_over_limit_is_400(system, case):
    deep = b"[" * 100 + b"]" * 100
    r = _raw(system, case, b'{"kind": "RECOVERY_PLAN", "round": 1, "payload": ' + deep + b"}")
    assert r.status_code == 400
    assert r.json()["error"]["details"]["reason"] == "INVALID_JSON"


def test_malformed_json_is_400_invalid_json(system, case):
    r = _raw(system, case, b'{"kind": ')
    assert r.status_code == 400
    assert r.json()["error"]["details"]["reason"] == "INVALID_JSON"


def test_nul_character_is_422_and_stored_sanitised(system, case, plan):
    plan["options"][0]["action_steps"] = ["step\u0000one"]
    plan["bad\u0000key"] = 1
    r = post(system, case, "RECOVERY_PLAN", plan)
    assert r.status_code == 422, r.content
    d = r.json()["error"]["details"]
    assert d["reason"] == "AGENT_OUTPUT_INVALID"
    assert d["errors"][0]["code"] == "NUL_CHARACTER"
    run = AgentRun.objects.get(risk_case=case)
    assert run.valid is False and run.validation_errors[0]["code"] == "NUL_CHARACTER"
    assert run.raw["options"][0]["action_steps"] == ["step\ufffdone"]
    assert "bad\ufffdkey" in run.raw and "\x00" not in str(run.raw)
    assert not Recommendation.objects.exists()
    case.refresh_from_db()
    assert case.status == S.ANALYZING
