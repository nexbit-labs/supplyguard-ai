"""N-07 persistence: `record_analysis()` and POST /internal/risk-cases/{id}/analysis, on the real
seed demo case (built by `create_risk_case`) and real v1.3.0 agent outputs."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from apps.audit.models import AuditEvent
from apps.core.models import AgentRun, Recommendation, RiskCase
from apps.core.models.cases import AgentRunAppendOnly
from apps.intelligence.exceptions import AnalysisConflict
from apps.intelligence.services import create_risk_case, record_analysis
from apps.intelligence.tests.test_services import scenario, seeded  # noqa: F401
from apps.workflow import services as workflow
from tests.factories import make_tenant, make_user
from tests.helpers import api_key_client, jwt_client

ROOT = Path(__file__).resolve().parents[4]
RP = ROOT / "boomi" / "agents" / "recovery_planner" / "eval" / "runs"
RA = ROOT / "boomi" / "agents" / "risk_analyst" / "eval" / "runs"
S = RiskCase.Status

pytestmark = pytest.mark.django_db


def _load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def url(case):
    return f"/api/v1/internal/risk-cases/{case.id}/analysis"


@pytest.fixture
def case(seeded):  # noqa: F811
    event, snap = scenario("demo", seeded)
    c, created = create_risk_case(tenant=seeded, event=event, snapshot=snap)
    assert created
    workflow.transition(c, S.ANALYZING, actor="SYSTEM", reason="impact computed")
    return c


@pytest.fixture
def plan(case):
    p = _load(RP / "2026-11-20_RP-DEMO_v1.3.0.json")
    p["risk_case_id"] = case.id
    return p


@pytest.fixture
def analysis(case):
    a = _load(RA / "2026-11-20_DEMO_v1.3.0.json")
    a["risk_case_id"] = case.id
    # The saved v1.3.0 run was graded on a hand-built input (score 90, coverage 1.5); the live
    # engine on the seed gives 67 / 1.0, and the validator rightly rejects the old numbers.
    # Align the cited numbers with the case so the test exercises persistence, not that gap.
    imp = case.impact
    a["risk_score"], a["risk_level"] = imp["risk_score"], imp["risk_level"]
    cov = {r["po_id"]: r["inventory_coverage_days"] for r in imp["affected_pos"]}
    for e in a["evidence"]:
        if e.get("source") == "IMPACT" and e["field"] == "risk_score":
            e["value"] = imp["risk_score"]
        elif e.get("source") == "IMPACT" and e["field"] == "inventory_coverage_days":
            e["value"] = cov[e["entity"]]
    return a


@pytest.fixture
def system(seeded):  # noqa: F811
    return api_key_client(seeded)


def post(client, case, kind, payload, **extra):
    body = {"kind": kind, "payload": payload, "round": 1, **extra}
    return client.post(url(case), body, format="json")


# ------------------------------------------------------------------------------ service
def test_demo_plan_stores_four_recs_and_reaches_pending_approval(seeded, case, plan):  # noqa: F811
    r = record_analysis(
        tenant=seeded, case=case, kind="RECOVERY_PLAN", payload=plan, actor="SYSTEM"
    )
    assert r.valid and r.actionable_count == 4 and r.status == S.PENDING_APPROVAL
    recs = list(Recommendation.objects.filter(risk_case=case).order_by("rank"))
    assert [x.rank for x in recs] == [1, 2, 3, 4]
    assert all(x.round == 1 and x.status == "PENDING" and not x.unsupported for x in recs)
    first = recs[0]
    assert first.option_type == "ALTERNATE_SUPPLIER" and first.currency == "INR"
    assert str(first.estimated_cost_delta) == "33600.00"
    assert str(first.estimated_lead_time_days) == "2.00"
    case.refresh_from_db()
    assert case.status == S.PENDING_APPROVAL
    moves = AuditEvent.objects.filter(entity_id=case.id, action="risk_case.status_changed")
    assert [m.data["to"] for m in moves.order_by("id")][-2:] == [
        "RECOMMENDATION_READY",
        "PENDING_APPROVAL",
    ]


def test_tc05_empty_options_closes_case(seeded, case):  # noqa: F811
    payload = {
        "risk_case_id": case.id,
        "options": [],
        "no_viable_option_reason": "No qualified alternate supplier has capacity.",
    }
    r = record_analysis(
        tenant=seeded, case=case, kind="RECOVERY_PLAN", payload=payload, actor="SYSTEM"
    )
    assert r.valid and r.actionable_count == 0 and r.status == S.CLOSED
    case.refresh_from_db()
    assert case.status == S.CLOSED
    assert case.no_viable_option_reason == payload["no_viable_option_reason"]
    assert not Recommendation.objects.filter(risk_case=case).exists()


def _unsupported_plan(plan, keep_others=True):
    p = copy.deepcopy(plan)
    p["options"][0]["proposed_action"]["change"]["supplier_id"] = "SUP-9999"
    for e in p["options"][0]["evidence"]:
        if e["entity"] == "SUP-2001":
            e["entity"] = "SUP-9999"
    if not keep_others:
        p["options"] = p["options"][:1]
    return p


def test_nonexistent_supplier_option_is_unsupported_and_not_approvable(seeded, case, plan):  # noqa: F811
    r = record_analysis(
        tenant=seeded,
        case=case,
        kind="RECOVERY_PLAN",
        payload=_unsupported_plan(plan),
        actor="SYSTEM",
    )
    assert r.valid and r.actionable_count == 3 and r.status == S.PENDING_APPROVAL
    bad = Recommendation.objects.get(risk_case=case, rank=1)
    assert bad.unsupported and bad.unsupported_reasons
    pm = make_user(seeded, role="PROCUREMENT_MANAGER")
    resp = jwt_client(pm).post(
        f"/api/v1/recommendations/{bad.id}/decision", {"decision": "APPROVE"}, format="json"
    )
    assert resp.status_code == 422, resp.content
    bad.refresh_from_db()
    assert bad.status == "PENDING"


def test_all_unsupported_closes_case(seeded, case, plan):  # noqa: F811
    r = record_analysis(
        tenant=seeded,
        case=case,
        kind="RECOVERY_PLAN",
        payload=_unsupported_plan(plan, keep_others=False),
        actor="SYSTEM",
    )
    assert r.valid and r.actionable_count == 0 and r.status == S.CLOSED
    case.refresh_from_db()
    assert case.no_viable_option_reason


def test_schema_invalid_payload_stores_run_only(seeded, case):  # noqa: F811
    bad = {"risk_case_id": case.id, "options": "nope"}
    r = record_analysis(
        tenant=seeded, case=case, kind="RECOVERY_PLAN", payload=bad, actor="SYSTEM"
    )
    assert not r.valid and r.validation_errors and r.status == S.ANALYZING
    run = AgentRun.objects.get(risk_case=case)
    assert run.valid is False and run.validation_errors and run.raw == bad
    assert run.agent == "RECOVERY_PLANNER" and run.round == 1
    assert not Recommendation.objects.filter(risk_case=case).exists()
    case.refresh_from_db()
    assert case.status == S.ANALYZING
    # the agent may try again after an invalid output
    again = record_analysis(
        tenant=seeded, case=case, kind="RECOVERY_PLAN", payload=bad, actor="SYSTEM"
    )
    assert not again.valid and AgentRun.objects.filter(risk_case=case).count() == 2


def test_fact_mismatch_plan_is_invalid(seeded, case, plan):  # noqa: F811
    plan["risk_case_id"] = "RC-999999"
    r = record_analysis(
        tenant=seeded, case=case, kind="RECOVERY_PLAN", payload=plan, actor="SYSTEM"
    )
    assert not r.valid and r.status == S.ANALYZING
    assert AgentRun.objects.filter(risk_case=case, valid=False).count() == 1


def test_risk_analysis_stored_on_case_without_status_change(seeded, case, analysis):  # noqa: F811
    r = record_analysis(
        tenant=seeded, case=case, kind="RISK_ANALYSIS", payload=analysis, actor="SYSTEM"
    )
    assert r.valid and r.status == S.ANALYZING
    case.refresh_from_db()
    assert case.analysis == analysis and case.status == S.ANALYZING
    run = AgentRun.objects.get(risk_case=case)
    assert run.agent == "RISK_ANALYST" and run.valid
    with pytest.raises(AnalysisConflict) as exc:  # duplicate valid analysis, same round
        record_analysis(
            tenant=seeded, case=case, kind="RISK_ANALYSIS", payload=analysis, actor="SYSTEM"
        )
    assert exc.value.details["reason"] == "ALREADY_RECORDED"


def test_wrong_state_stale_round_and_duplicate(seeded, case, plan):  # noqa: F811
    with pytest.raises(AnalysisConflict) as exc:
        record_analysis(
            tenant=seeded, case=case, kind="RECOVERY_PLAN", payload=plan, actor="SYSTEM",
            round=2,
        )
    assert exc.value.details["reason"] == "STALE_ROUND"
    assert not AgentRun.objects.exists()
    record_analysis(tenant=seeded, case=case, kind="RECOVERY_PLAN", payload=plan, actor="SYSTEM")
    with pytest.raises(AnalysisConflict) as exc:  # duplicate: the case has moved on
        record_analysis(
            tenant=seeded, case=case, kind="RECOVERY_PLAN", payload=plan, actor="SYSTEM"
        )
    assert exc.value.details["reason"] == "CASE_NOT_ANALYZING"
    assert Recommendation.objects.filter(risk_case=case).count() == 4
    assert AgentRun.objects.filter(risk_case=case).count() == 1


def test_agent_run_append_only_and_raw_equal_to_input(seeded, case, plan):  # noqa: F811
    record_analysis(tenant=seeded, case=case, kind="RECOVERY_PLAN", payload=plan, actor="SYSTEM")
    run = AgentRun.objects.get(risk_case=case)
    assert run.raw == plan
    assert json.dumps(run.raw, sort_keys=True) == json.dumps(plan, sort_keys=True)
    run.valid = False
    with pytest.raises(AgentRunAppendOnly):
        run.save()


def test_agent_run_survives_phase_two_failure(seeded, case, plan, monkeypatch):  # noqa: F811
    def boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(workflow, "transition", boom)
    with pytest.raises(RuntimeError):
        record_analysis(
            tenant=seeded, case=case, kind="RECOVERY_PLAN", payload=plan, actor="SYSTEM"
        )
    assert AgentRun.objects.filter(risk_case=case, valid=True).count() == 1
    case.refresh_from_db()
    assert case.status == S.ANALYZING


# ------------------------------------------------------------------------------ endpoint
def test_endpoint_201_demo_plan(system, case, plan):
    r = post(system, case, "RECOVERY_PLAN", plan)
    assert r.status_code == 201, r.content
    body = r.json()
    assert body["valid"] is True and body["actionable_count"] == 4
    assert body["case_status"] == "PENDING_APPROVAL" and body["agent_run_id"]
    assert len(body["recommendation_ids"]) == 4
    assert AuditEvent.objects.filter(entity_id=case.id, action="analysis.recorded").count() == 1


def test_endpoint_422_invalid_still_stores_run(system, case):
    r = post(system, case, "RECOVERY_PLAN", {"risk_case_id": case.id, "options": "x"})
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "BUSINESS_RULE"
    assert err["details"]["reason"] == "AGENT_OUTPUT_INVALID"
    assert err["details"]["errors"] and err["details"]["agent_run_id"]
    assert AgentRun.objects.filter(risk_case=case, valid=False).count() == 1
    assert AuditEvent.objects.filter(entity_id=case.id, action="analysis.invalid").count() == 1


def test_endpoint_409_wrong_state_and_stale_round(system, case, plan):
    assert post(system, case, "RECOVERY_PLAN", plan, round=3).status_code == 409
    assert post(system, case, "RECOVERY_PLAN", plan).status_code == 201
    r = post(system, case, "RECOVERY_PLAN", plan)
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "CONFLICT"
    assert r.json()["error"]["details"]["reason"] == "CASE_NOT_ANALYZING"
    assert Recommendation.objects.filter(risk_case=case).count() == 4
    assert AuditEvent.objects.filter(entity_id=case.id, action="analysis.rejected").count() == 2


def test_endpoint_tenant_isolation(case):
    other = make_tenant("TEN-OTHER")
    r = post(api_key_client(other), case, "RECOVERY_PLAN", {})
    assert r.status_code == 404
    case.refresh_from_db()
    assert case.status == S.ANALYZING and not AgentRun.objects.exists()


def test_endpoint_non_system_callers_get_403(seeded, case, plan):  # noqa: F811
    for role in ("ADMIN", "PROCUREMENT_MANAGER", "OPS_MANAGER", "VIEWER"):
        client = jwt_client(make_user(seeded, role=role))
        assert post(client, case, "RECOVERY_PLAN", plan).status_code == 403
    assert not AgentRun.objects.exists()


def test_endpoint_bad_body_400(system, case):
    r = system.post(url(case), {"kind": "NOPE", "payload": {}}, format="json")
    assert r.status_code == 400
    assert not AgentRun.objects.exists()
