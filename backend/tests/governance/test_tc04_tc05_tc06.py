# ruff: noqa: E501  (long evidence literals / table rows read better unwrapped)
"""TC-04 (no unsupported shortage), TC-05 (no alternates -> CLOSED), TC-06 (reject -> no ERP write)."""
from __future__ import annotations

import pytest

from apps.audit.models import AuditEvent
from apps.core.models import AgentRun, Approval, Execution, Recommendation, RiskCase

from .support import (
    RP_RUNS,
    decision_url,
    demo_plan,
    load_json,
    open_case,
    post_analysis,
    scenario_file,
)

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("triggers")]
S = RiskCase.Status


def _opt(rank, otype, po_id, change, *, lead, cost, evidence):
    return {
        "rank": rank,
        "option_type": otype,
        "proposed_action": {"po_id": po_id, "change": change},
        "action_steps": ["Review the proposed change."],
        "estimated_lead_time_days": lead,
        "estimated_cost_delta": cost,
        "trade_offs": [],
        "evidence": evidence,
        "assumptions": [],
        "data_gaps": [],
    }


def _plan(case_id, *options):
    return {"risk_case_id": case_id, "options": list(options), "no_viable_option_reason": None}


# ------------------------------------------------------------------------------------ TC-04
@pytest.fixture
def tc04(system):
    return RiskCase.objects.get(pk=open_case(system, "tc04_inventory_sufficient")["risk_case_id"])


def test_tc04_impact_reports_no_shortage(tc04):
    expected = scenario_file("tc04_inventory_sufficient")["expected"]
    assert expected["shortage_expected"] is False
    assert tc04.impact["shortage"] is False
    assert all(r["shortage"] is False for r in tc04.impact["affected_pos"])
    assert all(i["shortage"] is False for i in tc04.impact["items"])
    assert tc04.impact["affected_po_ids"] == expected["affected_po_ids"]


def test_tc04_plan_claiming_a_shortage_is_unsupported(system, pm_client, triggers, tc04):
    plan = _plan(
        tc04.id,
        _opt(
            1, "REVISED_SCHEDULE", "PO-1013", {"expected_date": "2026-12-06"},
            lead=3, cost={"amount": "0.00", "currency": "INR"},
            evidence=[
                {"source": "ERP", "entity": "PO-1013", "field": "due_date", "value": "2026-12-03", "item_id": "ITM-530"},
                {"source": "IMPACT", "entity": "PO-1013", "field": "estimated_delay_days", "value": 3},
                {"source": "IMPACT", "entity": "PO-1013", "field": "shortage", "value": True},
            ],
        ),
    )
    r = post_analysis(system, tc04.id, "RECOVERY_PLAN", plan)
    assert r.status_code == 201, r.content
    rec = Recommendation.objects.get(risk_case=tc04)
    assert rec.unsupported is True
    assert "EVIDENCE_MISMATCH" in [x["code"] for x in rec.unsupported_reasons]
    tc04.refresh_from_db()
    assert tc04.status == S.CLOSED  # nothing approvable
    d = pm_client.post(decision_url(rec.id), {"decision": "APPROVE"}, format="json")
    assert d.status_code in (409, 422)
    assert not Approval.objects.exists() and not Execution.objects.exists()


@pytest.mark.xfail(
    strict=True,
    reason="FINDING F-02: the validator checks an alternate-supplier option against supplier / "
    "price / capacity facts only. It does not flag an option that fixes a PO whose impact says "
    "shortage=false (stock covers the outage), so TC-04 is enforced for options that CLAIM a "
    "shortage (evidence), not for ones that merely imply it.",
)
def test_tc04_alternate_where_stock_covers_the_need_is_flagged(system, tc04):
    plan = _plan(
        tc04.id,
        _opt(
            1, "ALTERNATE_SUPPLIER", "PO-1013", {"supplier_id": "SUP-2005", "qty": 2000},
            lead=6, cost={"amount": "33580.00", "currency": "INR"},
            evidence=[
                {"source": "ERP", "entity": "SUP-2005", "field": "qualified", "value": True, "item_id": "ITM-530"},
                {"source": "ERP", "entity": "SUP-2005", "field": "unit_price", "value": "436.44", "item_id": "ITM-530"},
                {"source": "ERP", "entity": "SUP-2005", "field": "lead_time_days", "value": 6, "item_id": "ITM-530"},
                {"source": "ERP", "entity": "SUP-2005", "field": "capacity", "value": 6000, "item_id": "ITM-530"},
                {"source": "ERP", "entity": "PO-1013", "field": "unit_price", "value": "419.65", "item_id": "ITM-530"},
            ],
        ),
    )
    r = post_analysis(system, tc04.id, "RECOVERY_PLAN", plan)
    assert r.status_code == 201, r.content
    assert Recommendation.objects.get(risk_case=tc04).unsupported is True


# ------------------------------------------------------------------------------------ TC-05
def test_tc05_no_alternates_closes_case_never_pending_approval(system, triggers):
    case = RiskCase.objects.get(pk=open_case(system, "tc05_no_alternates")["risk_case_id"])
    assert case.impact["qualified_alternate_supplier_ids"] == []
    plan = load_json(RP_RUNS / "2026-11-20_RP-TC05_v1.3.0.json")  # real agent run: options []
    plan["risk_case_id"] = case.id
    assert plan["options"] == [] and plan["no_viable_option_reason"]
    r = post_analysis(system, case.id, "RECOVERY_PLAN", plan)
    assert r.status_code == 201, r.content
    assert r.json()["actionable_count"] == 0 and r.json()["case_status"] == S.CLOSED
    case.refresh_from_db()
    assert case.status == S.CLOSED
    assert case.no_viable_option_reason == plan["no_viable_option_reason"]
    assert not Recommendation.objects.filter(risk_case=case).exists()
    states = [
        e.data["to"]
        for e in AuditEvent.objects.filter(
            entity_id=case.id, action="risk_case.status_changed"
        ).order_by("id")
    ]
    assert S.PENDING_APPROVAL not in states and states[-1] == S.CLOSED
    assert not Approval.objects.exists() and not triggers.exec


def test_tc05_empty_options_without_reason_is_rejected(system):
    """Missing data must be stated: `options: []` with no reason is an invalid run."""
    case = RiskCase.objects.get(pk=open_case(system, "tc05_no_alternates")["risk_case_id"])
    r = post_analysis(system, case.id, "RECOVERY_PLAN", {"risk_case_id": case.id, "options": []})
    assert r.status_code == 422
    case.refresh_from_db()
    assert case.status == S.ANALYZING and AgentRun.objects.get(risk_case=case).valid is False


# ------------------------------------------------------------------------------------ TC-06
def test_tc06_reject_does_not_execute_and_is_audited(system, pm, pm_client, triggers):
    sc = scenario_file("tc06_reject")
    case = RiskCase.objects.get(pk=open_case(system, "tc06_reject")["risk_case_id"])
    r = post_analysis(system, case.id, "RECOVERY_PLAN", demo_plan(case.id))
    assert r.status_code == 201 and r.json()["case_status"] == S.PENDING_APPROVAL
    recs = list(Recommendation.objects.filter(risk_case=case).order_by("rank"))
    assert len(recs) == 4

    comments = sc["decision"]["comments"]
    d = pm_client.post(
        decision_url(recs[0].id),
        {"decision": sc["decision"]["decision"], "comments": comments},
        format="json",
    )
    assert d.status_code == 201, d.content
    body = d.json()
    assert body["decision"] == "REJECT" and body["execution_id"] is None

    # no ERP write path was started
    assert not triggers.exec and not Execution.objects.exists()
    approval = Approval.objects.get()
    assert approval.decision == "REJECT" and approval.comments == comments
    assert approval.decided_by_id == pm.pk and approval.recommendation_id == recs[0].id
    # N-10: rejecting one of several options leaves the case PENDING_APPROVAL; the rejected
    # option is REJECTED and the others stay decidable
    case.refresh_from_db()
    assert case.status == S.PENDING_APPROVAL
    statuses = {x.rank: x.status for x in Recommendation.objects.filter(risk_case=case)}
    assert statuses == {1: "REJECTED", 2: "PENDING", 3: "PENDING", 4: "PENDING"}
    # comments are in the audit trail
    ev = AuditEvent.objects.get(action="recommendation.decided", entity_id=recs[0].id)
    assert ev.data["decision"] == "REJECT" and ev.data["comments"] == comments


def test_tc06_rejecting_the_last_option_rejects_the_case_still_no_execution(
    system, pm_client, triggers
):
    case = RiskCase.objects.get(pk=open_case(system, "tc06_reject")["risk_case_id"])
    plan = demo_plan(case.id)
    plan["options"] = plan["options"][:1]
    assert post_analysis(system, case.id, "RECOVERY_PLAN", plan).status_code == 201
    rec = Recommendation.objects.get(risk_case=case)
    d = pm_client.post(
        decision_url(rec.id), {"decision": "REJECT", "comments": "Cost too high"}, format="json"
    )
    assert d.status_code == 201 and d.json()["case_status"] == S.REJECTED
    assert not Execution.objects.exists() and not triggers.exec
    case.refresh_from_db()
    assert case.status == S.REJECTED
