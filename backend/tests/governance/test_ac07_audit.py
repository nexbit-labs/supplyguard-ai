"""AC-07: event -> recommendation -> decision -> execution can be reviewed in the audit history.
Read back through GET /api/v1/audit with a human JWT, filtered by the case's correlation id."""
from __future__ import annotations

import pytest

from apps.core.models import Execution, Recommendation, RiskCase

from .support import decision_url, demo_plan, open_case, post_analysis

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("triggers")]


def test_ac07_full_trail_is_readable_in_order(
    system, pm_client, triggers, django_capture_on_commit_callbacks
):
    body = open_case(system, "demo")
    case = RiskCase.objects.get(pk=body["risk_case_id"])
    assert post_analysis(system, case.id, "RECOVERY_PLAN", demo_plan(case.id)).status_code == 201
    rec = Recommendation.objects.filter(risk_case=case).order_by("rank").first()
    with django_capture_on_commit_callbacks(execute=True):
        d = pm_client.post(
            decision_url(rec.id), {"decision": "APPROVE", "comments": "go ahead"}, format="json"
        )
    assert d.status_code == 201 and d.json()["execution_id"]
    assert Execution.objects.count() == 1 and len(triggers.exec) == 1

    rows = []
    for etype, eid in (("RiskCase", case.id), ("Recommendation", rec.id)):
        r = pm_client.get("/api/v1/audit", {"entity_type": etype})
        assert r.status_code == 200, r.content
        page = r.json()
        rows += [x for x in (page["results"] if isinstance(page, dict) else page)
                 if x["entity_id"] == eid]
    rows.sort(key=lambda x: x["id"], reverse=True)  # newest first, like the API
    actions = [row["action"] for row in reversed(rows)]  # API is newest first
    expected_in_order = [
        "risk_case.created",
        "analysis.recorded",
        "recommendation.decided",
    ]
    positions = [actions.index(a) for a in expected_in_order]
    assert positions == sorted(positions), actions
    moves = [
        row["data"]["to"] for row in reversed(rows) if row["action"] == "risk_case.status_changed"
    ]
    assert moves[:4] == ["ANALYZING", "RECOMMENDATION_READY", "PENDING_APPROVAL", "APPROVED"]
    assert moves[-1] == "EXECUTING"
    decided = next(row for row in rows if row["action"] == "recommendation.decided")
    assert decided["data"]["decision"] == "APPROVE" and decided["data"]["comments"] == "go ahead"
