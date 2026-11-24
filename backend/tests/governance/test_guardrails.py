# ruff: noqa: E501  (long evidence literals / table rows read better unwrapped)
"""Hallucination / guardrail suite (AI governance: "AI never invents facts").

Each row is a real agent output (the saved Recovery Planner / Risk Analyst v1.3.0 DEMO runs)
with exactly ONE invented fact, sent to POST /internal/risk-cases/{id}/analysis with a SYSTEM
API key. Expected outcome per row:
  mode "option": 201, that option is `unsupported` with the expected reason code, and the
                 decision API answers 422 UNSUPPORTED_OPTION (nothing approvable);
  mode "run"   : 422 AGENT_OUTPUT_INVALID with the expected code, the AgentRun is stored
                 invalid, no Recommendation exists and the case stays ANALYZING.
"""
from __future__ import annotations

import copy

import pytest

from apps.audit.models import AuditEvent
from apps.core.models import AgentRun, Approval, Execution, Recommendation, RiskCase

from .support import (
    decision_url,
    demo_analysis,
    demo_plan,
    open_case,
    post_analysis,
    unqualified_event,
)

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("triggers")]
S = RiskCase.Status


# ----------------------------------------------------------------------------- mutations
def _opt(plan, rank):
    return next(o for o in plan["options"] if o["rank"] == rank)


def _ev(option, entity, field):
    return next(e for e in option["evidence"] if e["entity"] == entity and e["field"] == field)


def invented_capacity_evidence(p):  # SUP-2001 capacity is 5000 in the ERP
    _ev(_opt(p, 1), "SUP-2001", "capacity")["value"] = 9000


def invented_capacity_for_unknown(p):  # SUP-2002 capacity is null (unknown) in the ERP
    _ev(_opt(p, 3), "SUP-2002", "capacity")["value"] = 8000


def invented_capacity_free_text(p):  # the capacity of SUP-2002 is unknown; text states a figure
    _opt(p, 3)["assumptions"].append("SUP-2002 can supply 8000 units per week.")


def invented_price(p):
    _ev(_opt(p, 1), "SUP-2001", "unit_price")["value"] = "70.00"


def unknown_supplier(p):
    _opt(p, 1)["proposed_action"]["change"]["supplier_id"] = "SUP-9999"


def disrupted_supplier(p):
    _opt(p, 1)["proposed_action"]["change"]["supplier_id"] = "SUP-1008"


def qty_above_affected(p):
    _opt(p, 1)["proposed_action"]["change"]["qty"] = 9000


def transfer_above_available(p):  # 800 on hand - 100 reserved - 200 safety = 500 available
    _opt(p, 2)["proposed_action"]["change"]["qty"] = 900


def wrong_cost_delta(p):
    _opt(p, 1)["estimated_cost_delta"]["amount"] = "1.00"


def wrong_date(p):  # due 2026-12-03 + 2 days delay = 2026-12-05
    _opt(p, 4)["proposed_action"]["change"]["expected_date"] = "2026-12-31"


def date_not_derivable(p):  # PO-1003 is open but not affected: nothing to derive a date from
    _opt(p, 4)["proposed_action"]["po_id"] = "PO-1003"


def wrong_change_field(p):
    _opt(p, 1)["proposed_action"]["change"]["expected_date"] = "2026-12-05"


def duplicate_option(p):
    dup = copy.deepcopy(_opt(p, 1))
    dup["rank"] = 5
    p["options"].append(dup)


def duplicate_rank(p):
    _opt(p, 3)["rank"] = 1


def risk_case_mismatch(p):
    p["risk_case_id"] = "RC-999999"


# risk analysis
def analysis_capacity_not_in_missing_data(a):
    a["missing_data"] = [m for m in a["missing_data"] if "capacity" not in m.lower()]


def analysis_invented_capacity_text(a):  # SUP-1008 capacity is null in the ERP
    a["summary"] += " SUP-1008 capacity is 5000 units per week."


def analysis_invented_price(a):
    e = next(x for x in a["evidence"] if x["field"] == "unit_price")
    e["value"] = "1.00"


def analysis_unknown_entity(a):
    a["affected_entities"].append({"type": "SUPPLIER", "id": "SUP-9999"})


def analysis_wrong_score(a):
    a["risk_score"] = 99


# id, mutate, mode, rank (option mode), expected code
PLAN_ROWS = [
    ("invented-capacity-evidence", invented_capacity_evidence, "option", 1, "CAPACITY_MISMATCH"),
    ("invented-capacity-unknown-supplier", invented_capacity_for_unknown, "option", 3, "CAPACITY_MISMATCH"),
    ("invented-price", invented_price, "option", 1, "PRICE_MISMATCH"),
    ("unknown-supplier", unknown_supplier, "option", 1, "SUPPLIER_NOT_FOUND"),
    ("disrupted-supplier-itself", disrupted_supplier, "option", 1, "SUPPLIER_DISRUPTED"),
    ("qty-above-affected", qty_above_affected, "option", 1, "QTY_EXCEEDS_AFFECTED"),
    ("transfer-above-available", transfer_above_available, "option", 2, "TRANSFER_EXCEEDS_AVAILABLE"),
    ("wrong-cost-delta", wrong_cost_delta, "option", 1, "COST_DELTA_MISMATCH"),
    ("wrong-revised-date", wrong_date, "option", 4, "REVISED_DATE_MISMATCH"),
    ("date-not-derivable", date_not_derivable, "option", 4, "REVISED_DATE_UNSUPPORTED"),
    ("wrong-change-field", wrong_change_field, "option", 1, "CHANGE_FIELD_NOT_ALLOWED"),
    ("duplicate-option", duplicate_option, "option", 5, "DUPLICATE_OPTION"),
    ("duplicate-rank", duplicate_rank, "run", None, "DUPLICATE_RANK"),
    ("risk-case-id-mismatch", risk_case_mismatch, "run", None, "RISK_CASE_MISMATCH"),
]
ANALYSIS_ROWS = [
    ("unknown-capacity-not-in-missing-data", analysis_capacity_not_in_missing_data, "CAPACITY_NOT_IN_MISSING_DATA"),
    ("invented-capacity-free-text", analysis_invented_capacity_text, "CAPACITY_INVENTED"),
    ("invented-price-evidence", analysis_invented_price, "PRICE_MISMATCH"),
    ("unknown-entity", analysis_unknown_entity, "ENTITY_NOT_FOUND"),
    ("wrong-risk-score", analysis_wrong_score, "RISK_SCORE_MISMATCH"),
    ("risk-case-id-mismatch", lambda a: a.update(risk_case_id="RC-999999"), "RISK_CASE_MISMATCH"),
]


# ----------------------------------------------------------------------------- helpers
def error_codes(response) -> list[str]:
    return [e["code"] for e in response.json()["error"]["details"]["errors"]]


def assert_nothing_executed(triggers):
    assert not Approval.objects.exists() and not Execution.objects.exists()
    assert not triggers.exec


def assert_invalid_run(response, case, code, agent, triggers):
    assert response.status_code == 422, response.content
    err = response.json()["error"]
    assert err["details"]["reason"] == "AGENT_OUTPUT_INVALID" and err["details"]["valid"] is False
    assert code in error_codes(response)
    run = AgentRun.objects.get(risk_case=case)  # stored for the audit trail, flagged invalid
    assert run.valid is False and run.agent == agent
    assert not Recommendation.objects.filter(risk_case=case).exists()
    case.refresh_from_db()
    assert case.status == S.ANALYZING  # never reaches PENDING_APPROVAL
    assert AuditEvent.objects.filter(entity_id=case.id, action="analysis.invalid").count() == 1
    assert_nothing_executed(triggers)


@pytest.fixture
def case(system):
    return RiskCase.objects.get(pk=open_case(system, "demo")["risk_case_id"])


# ----------------------------------------------------------------------------- recovery plan
@pytest.mark.parametrize(
    "mutate,mode,rank,code",
    [pytest.param(*row[1:], id=row[0]) for row in PLAN_ROWS],
)
def test_invented_fact_in_recovery_plan(system, pm_client, triggers, case, mutate, mode, rank, code):
    plan = demo_plan(case.id)
    mutate(plan)
    r = post_analysis(system, case.id, "RECOVERY_PLAN", plan)
    if mode == "run":
        assert_invalid_run(r, case, code, "RECOVERY_PLANNER", triggers)
        return

    assert r.status_code == 201, r.content  # the run is valid; only the option is unsupported
    bad = Recommendation.objects.get(risk_case=case, rank=rank)
    assert bad.unsupported is True
    assert code in [x["code"] for x in bad.unsupported_reasons]
    # the guard is per option: the untouched options stay actionable
    ok = Recommendation.objects.filter(risk_case=case, unsupported=False)
    assert ok.exists() and not ok.filter(rank=rank).exists()
    # ... and the invented one cannot be approved, rejected or sent back
    for decision in ("APPROVE", "REJECT", "REQUEST_CHANGES"):
        d = pm_client.post(
            decision_url(bad.id), {"decision": decision, "comments": "x"}, format="json"
        )
        assert d.status_code == 422, d.content
        assert d.json()["error"]["details"]["reason"] == "UNSUPPORTED_OPTION"
    bad.refresh_from_db()
    assert bad.status == "PENDING"
    assert_nothing_executed(triggers)


@pytest.mark.xfail(
    strict=True,
    reason="FINDING F-01: capacity figures stated in a recovery option's free text "
    "(assumptions / trade_offs / action_steps) are not fact-checked; only the risk analysis "
    "text is scanned (CAPACITY_INVENTED). The saved v1.3.0 DEMO run itself carries such a line.",
)
def test_invented_capacity_in_recovery_plan_free_text(system, case):
    plan = demo_plan(case.id)
    invented_capacity_free_text(plan)
    r = post_analysis(system, case.id, "RECOVERY_PLAN", plan)
    assert r.status_code == 201, r.content
    assert Recommendation.objects.get(risk_case=case, rank=3).unsupported is True


def test_every_option_invented_closes_the_case_nothing_approvable(system, triggers, case):
    plan = demo_plan(case.id)
    for option in plan["options"]:  # same lie in all of them: an unknown PO
        option["proposed_action"]["po_id"] = "PO-9999"
    r = post_analysis(system, case.id, "RECOVERY_PLAN", plan)
    assert r.status_code == 201 and r.json()["actionable_count"] == 0
    case.refresh_from_db()
    assert case.status == S.CLOSED and case.no_viable_option_reason
    assert not Recommendation.objects.filter(risk_case=case, unsupported=False).exists()
    assert_nothing_executed(triggers)


def test_unqualified_supplier_is_unsupported(system, pm_client, triggers):
    case = RiskCase.objects.get(pk=open_case(system, unqualified_event())["risk_case_id"])
    plan = {
        "risk_case_id": case.id,
        "options": [
            {
                "rank": 1,
                "option_type": "ALTERNATE_SUPPLIER",
                "proposed_action": {
                    "po_id": "PO-1103",
                    "change": {"supplier_id": "SUP-2003", "qty": 1000},
                },
                "action_steps": ["Confirm with SUP-2003 that it can supply 1000 units of ITM-518."],
                "estimated_lead_time_days": 8,
                "estimated_cost_delta": {"amount": "24020.00", "currency": "INR"},
                "trade_offs": ["Unit price differs from the PO price."],
                "evidence": [
                    {"source": "ERP", "entity": "SUP-2003", "field": "qualified", "value": False, "item_id": "ITM-518"},
                    {"source": "ERP", "entity": "SUP-2003", "field": "unit_price", "value": "197.86", "item_id": "ITM-518"},
                    {"source": "ERP", "entity": "SUP-2003", "field": "lead_time_days", "value": 8, "item_id": "ITM-518"},
                    {"source": "ERP", "entity": "PO-1103", "field": "unit_price", "value": "173.84", "item_id": "ITM-518"},
                ],
                "assumptions": [],
                "data_gaps": [],
            }
        ],
        "no_viable_option_reason": None,
    }
    r = post_analysis(system, case.id, "RECOVERY_PLAN", plan)
    assert r.status_code == 201, r.content
    rec = Recommendation.objects.get(risk_case=case, rank=1)
    assert rec.unsupported
    assert "SUPPLIER_NOT_QUALIFIED" in [x["code"] for x in rec.unsupported_reasons]
    case.refresh_from_db()
    assert case.status == S.CLOSED  # the only option was unsupported: nothing approvable
    d = pm_client.post(decision_url(rec.id), {"decision": "APPROVE"}, format="json")
    assert d.status_code in (409, 422)  # closed case / unsupported option: never 201
    assert_nothing_executed(triggers)


def test_control_valid_demo_plan_is_fully_supported(system, case):
    """Without an invented fact the same plan is clean: the rows above fail for their one lie."""
    r = post_analysis(system, case.id, "RECOVERY_PLAN", demo_plan(case.id))
    assert r.status_code == 201 and r.json()["actionable_count"] == 4
    assert not Recommendation.objects.filter(risk_case=case, unsupported=True).exists()


# ----------------------------------------------------------------------------- risk analysis
@pytest.mark.parametrize(
    "mutate,code", [pytest.param(*row[1:], id=row[0]) for row in ANALYSIS_ROWS]
)
def test_invented_fact_in_risk_analysis(system, triggers, case, mutate, code):
    analysis = demo_analysis(case.id, case.impact)
    mutate(analysis)
    r = post_analysis(system, case.id, "RISK_ANALYSIS", analysis)
    assert_invalid_run(r, case, code, "RISK_ANALYST", triggers)
    assert not case.analysis  # nothing invented is shown as the case's analysis


def test_control_valid_demo_analysis_is_accepted(system, case):
    r = post_analysis(system, case.id, "RISK_ANALYSIS", demo_analysis(case.id, case.impact))
    assert r.status_code == 201, r.content
