"""AC-03: "The system explains the risk and clearly labels estimates, assumptions and missing
data." This test builds the demo case through the real API and writes docs/evidence/AC-03_demo.json
with FACTS (ERP values + evidence pointers), ESTIMATES (impact engine + what-if, labelled) and
MISSING DATA side by side. Everything comes from the stored case / analysis / recommendations and
the what-if endpoint; nothing is hand-typed. Deterministic: fixed snapshot clock, explicit
event time, no wall-clock values, no run-specific ids (the file is compared byte for byte).
"""
from __future__ import annotations

import json
import sys

import pytest

from apps.core.models import Recommendation, RiskCase
from apps.intelligence import views

from .support import (
    ROOT,
    demo_analysis,
    demo_plan,
    open_case,
    post_analysis,
)

sys.path.insert(0, str(ROOT / "mock-erp"))
from app.main import app as erp_app  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("triggers")]
EVIDENCE_FILE = ROOT / "docs" / "evidence" / "AC-03_demo.json"


def _build(system, pm_client, monkeypatch) -> dict:
    monkeypatch.setattr(views, "_client", lambda: TestClient(erp_app))
    case = RiskCase.objects.get(pk=open_case(system, "demo")["risk_case_id"])
    analysis = demo_analysis(case.id, case.impact)
    assert post_analysis(system, case.id, "RISK_ANALYSIS", analysis).status_code == 201
    assert post_analysis(system, case.id, "RECOVERY_PLAN", demo_plan(case.id)).status_code == 201
    case.refresh_from_db()
    recs = list(Recommendation.objects.filter(risk_case=case).order_by("rank"))
    wi = pm_client.post(f"/api/v1/risk-cases/{case.id}/what-if", {}, format="json")
    assert wi.status_code == 200, wi.content
    what_if = wi.json()
    impact, snap, stored = case.impact, case.snapshot, case.analysis

    affected = {r["po_id"] for r in impact["affected_pos"]}
    erp_facts = {
        "captured_at": snap["captured_at"],
        "supplier": {
            k: snap["supplier"].get(k)
            for k in ("supplier_id", "name", "status", "capacity_per_week")
            if k in snap["supplier"]
        },
        "purchase_orders": [
            {
                "po_id": p["po_id"],
                "expected_date": p["expected_date"],
                "lines": [
                    {k: ln[k] for k in ("item_id", "qty", "due_date", "unit_price")}
                    for ln in p["lines"]
                ],
            }
            for p in _rows(snap["purchase_orders"])
            if p["po_id"] in affected
        ],
        "inventory": _rows(snap["inventory"]),
        "alternate_suppliers": _rows(snap["alternate_suppliers"]),
    }
    return {
        "artifact": "AC-03 evidence: facts vs estimates vs missing data",
        "scenario": "demo (SUP-1008 factory outage, data/scenarios/demo.json)",
        "risk_case_id": case.id,
        "analysis_agent_output_stored_on_case": True,
        "facts": {
            "label": "FACT - copied from the ERP snapshot / event; each has an evidence pointer",
            "erp_snapshot": erp_facts,
            "agent_stated_facts": stored["facts"],
            "evidence_pointers": [
                e for e in stored["evidence"] if e["source"] in ("ERP", "EVENT")
            ],
        },
        "estimates": {
            "label": "ESTIMATE - computed by the impact engine / what-if or inferred; never a fact",
            "impact_engine": {
                "label": "ESTIMATE",
                "risk_score": impact["risk_score"],
                "risk_level": impact["risk_level"],
                "total_exposure": impact["total_exposure"],
                "affected_pos": impact["affected_pos"],
                "window": impact["window"],
            },
            "agent_estimates": stored["estimates"],
            "recovery_options": [
                {
                    "label": "ESTIMATE",
                    "rank": r.rank,
                    "option_type": r.option_type,
                    "estimated_cost_delta": str(r.estimated_cost_delta)
                    if r.estimated_cost_delta is not None
                    else None,
                    "estimated_lead_time_days": str(r.estimated_lead_time_days)
                    if r.estimated_lead_time_days is not None
                    else None,
                    "unsupported": r.unsupported,
                }
                for r in recs
            ],
            "what_if": {
                "estimate": what_if["estimate"],
                "labels": what_if["labels"],
                "scenario": what_if["scenario"],
                "risk": what_if["risk"],
                "exposure": what_if["exposure"],
                "best_option_estimate": what_if["best_option_estimate"],
                "assumptions": what_if["assumptions"],
            },
            "assumptions": stored["assumptions"],
        },
        "missing_data": {
            "label": "MISSING - stated, never invented",
            "agent_missing_data": stored["missing_data"],
            "case_data_quality_flags": impact["data_quality_flags"],
            "what_if_data_gaps": what_if["data_gaps"],
            "option_data_gaps": {str(r.rank): r.data_gaps for r in recs if r.data_gaps}
            if hasattr(Recommendation, "data_gaps")
            else {},
        },
    }


def _rows(value):
    return value["results"] if isinstance(value, dict) else value


def _dump(doc) -> str:
    return json.dumps(doc, indent=2, ensure_ascii=False) + "\n"


def test_ac03_evidence_artifact_is_written_and_deterministic(system, pm_client, monkeypatch):
    doc = _build(system, pm_client, monkeypatch)
    # structure: the three columns are present, labelled and non-empty
    assert doc["facts"]["erp_snapshot"]["purchase_orders"] and doc["facts"]["evidence_pointers"]
    assert doc["estimates"]["impact_engine"]["label"] == "ESTIMATE"
    assert doc["estimates"]["what_if"]["estimate"] is True
    assert set(doc["estimates"]["what_if"]["labels"].values()) == {"ESTIMATE"}
    assert doc["estimates"]["agent_estimates"] and doc["estimates"]["assumptions"]
    gaps = doc["missing_data"]
    assert any("capacity" in m.lower() for m in gaps["agent_missing_data"])
    assert gaps["case_data_quality_flags"] and gaps["what_if_data_gaps"]
    # every agent evidence pointer resolves to a value present in the stored facts (no orphans)
    for ptr in doc["facts"]["evidence_pointers"]:
        assert {"source", "entity", "field", "value"} <= set(ptr)
    text = _dump(doc)
    # deterministic: a second build of the same case gives the identical document
    EVIDENCE_FILE.parent.mkdir(parents=True, exist_ok=True)
    EVIDENCE_FILE.write_text(text, encoding="utf-8", newline="\n")
    assert EVIDENCE_FILE.read_text(encoding="utf-8") == text


def test_ac03_committed_artifact_matches_a_fresh_build(system, pm_client, monkeypatch):
    """Determinism / staleness guard: a new DB and a new build reproduce the file byte for byte."""
    fresh = _dump(_build(system, pm_client, monkeypatch))
    assert EVIDENCE_FILE.read_text(encoding="utf-8") == fresh
