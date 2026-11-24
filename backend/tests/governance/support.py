# ruff: noqa: E501  (long evidence literals / table rows read better unwrapped)
"""Shared helpers for the N-13 governance tests: they drive the real HTTP API the way Boomi (a
SYSTEM API key) and the portal (human JWTs) would, on top of the `load_seed` data.

Nothing here touches product code; fixtures only build callers, payloads and snapshots.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
DATA = ROOT / "data"
RP_RUNS = ROOT / "boomi" / "agents" / "recovery_planner" / "eval" / "runs"
RA_RUNS = ROOT / "boomi" / "agents" / "risk_analyst" / "eval" / "runs"

EVENTS_URL = "/api/v1/events/disruptions"
CASES_URL = "/api/v1/internal/risk-cases"
CAPTURED_AT = "2026-12-02T10:16:00Z"  # fixed: the snapshot clock never depends on the wall clock


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def scenario_file(name: str) -> dict:
    return load_json(DATA / "scenarios" / f"{name}.json")


def _seed(name: str) -> list[dict]:
    return load_json(DATA / "seed" / f"{name}.json")


def snapshot_for(event: dict) -> dict:
    """What Boomi assembles from the ERP for the event's supplier (CONTRACTS s5 field names)."""
    tenant, supplier_id = event["tenant_id"], event["supplier_id"]
    supplier = next(
        s for s in _seed("suppliers") if s["supplier_id"] == supplier_id and s["tenant_id"] == tenant
    )
    pos = [
        p
        for p in _seed("purchase_orders")
        if p["supplier_id"] == supplier_id and p["tenant_id"] == tenant and p["status"] == "OPEN"
    ]
    items = {line["item_id"] for p in pos for line in p["lines"]}
    return {
        "captured_at": CAPTURED_AT,
        "supplier": supplier,
        "purchase_orders": pos,
        "inventory": [
            i for i in _seed("inventory") if i["tenant_id"] == tenant and i["item_id"] in items
        ],
        "alternate_suppliers": [
            a
            for a in _seed("alternate_suppliers")
            if a["tenant_id"] == tenant and a["item_id"] in items
        ],
    }


def open_case(system_client, scenario: str | dict) -> dict:
    """event intake -> snapshot -> POST /internal/risk-cases. Returns the 201 body.
    `scenario` is a data/scenarios name or a ready event dict."""
    event = scenario_file(scenario)["event"] if isinstance(scenario, str) else scenario
    r = system_client.post(EVENTS_URL, event, format="json")
    assert r.status_code == 202, r.content
    r = system_client.post(
        CASES_URL, {"event_id": event["event_id"], "snapshot": snapshot_for(event)}, format="json"
    )
    assert r.status_code == 201, r.content
    return r.json()


def analysis_url(case_id: str) -> str:
    return f"/api/v1/internal/risk-cases/{case_id}/analysis"


def post_analysis(system_client, case_id: str, kind: str, payload: dict, round_: int = 1):
    return system_client.post(
        analysis_url(case_id), {"kind": kind, "payload": payload, "round": round_}, format="json"
    )


def decision_url(rec_id: str) -> str:
    return f"/api/v1/recommendations/{rec_id}/decision"


def demo_plan(case_id: str) -> dict:
    """The saved real Recovery Planner v1.3.0 DEMO run, re-pointed at `case_id`."""
    plan = load_json(RP_RUNS / "2026-11-20_RP-DEMO_v1.3.0.json")
    plan["risk_case_id"] = case_id
    return copy.deepcopy(plan)


def demo_analysis(case, impact: dict) -> dict:
    """The saved real Risk Analyst v1.3.0 DEMO run, with the cited impact numbers aligned to the
    live engine (the saved run was graded on a hand-built input: score 90 / coverage 1.5)."""
    a = load_json(RA_RUNS / "2026-11-20_DEMO_v1.3.0.json")
    a["risk_case_id"] = case
    a["risk_score"], a["risk_level"] = impact["risk_score"], impact["risk_level"]
    cov = {r["po_id"]: r["inventory_coverage_days"] for r in impact["affected_pos"]}
    for e in a["evidence"]:
        if e.get("source") == "IMPACT" and e["field"] == "risk_score":
            e["value"] = impact["risk_score"]
        elif e.get("source") == "IMPACT" and e["field"] == "inventory_coverage_days":
            e["value"] = cov[e["entity"]]
    return a


def unqualified_event() -> dict:
    """SUP-1005 supplies ITM-518 (PO-1103); the seed lists SUP-2003 as an alternate for it with
    qualified=false. A 20-day outage reaches PO-1103 (due 2026-12-11)."""
    event = dict(scenario_file("demo")["event"])
    event.update(
        event_id="EVT-000913",
        supplier_id="SUP-1005",
        expected_duration_hours=480,
        correlation_id="5b0f5e5a-0000-4000-8000-00000000d013",
    )
    return event
