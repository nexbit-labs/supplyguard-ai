"""Build an `agent_input` record (ai/schemas/agent_input.schema.json) from N-02 seed + scenario files.

    python -m ai.eval.build_agent_input demo                 # print RISK_ANALYST input for data/scenarios/demo.json
    python -m ai.eval.build_agent_input tc05_no_alternates --agent RECOVERY_PLANNER --risk-analysis out.json

Used by the N-05/N-06 eval harness and (later) the N-09 `mock` backend.

PROVISIONAL IMPACT: `impact` is computed here with a simple documented rule until the N-04 engine exists
(backend/apps/intelligence/impact.py). Affected POs and exposure follow data/README.md exactly; delay,
coverage, shortage and risk_score below are placeholders N-04 will replace.
"""

from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SEED = ROOT / "data" / "seed"
SCENARIOS = ROOT / "data" / "scenarios"

SEVERITY_BASE = {"LOW": 20, "MEDIUM": 35, "HIGH": 50, "CRITICAL": 65}
SHORTAGE_POINTS = 25
EXPOSURE_POINTS = 15
EXPOSURE_THRESHOLD = Decimal("250000")  # in tenant currency


def _load(name: str):
    return json.loads((SEED / f"{name}.json").read_text(encoding="utf-8"))


def _ts(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ")


def load_scenario(name: str) -> dict:
    return json.loads((SCENARIOS / f"{name}.json").read_text(encoding="utf-8"))


def _money(amount: Decimal, currency: str) -> dict:
    return {"amount": str(amount.quantize(Decimal("0.01"))), "currency": currency}


def build_agent_input(scenario: str | dict, agent: str = "RISK_ANALYST", risk_analysis: dict | None = None,
                      risk_case_id: str = "RC-000045") -> dict:
    sc = load_scenario(scenario) if isinstance(scenario, str) else scenario
    ev = sc["event"]
    tenant = ev["tenant_id"]
    currency = next(t["currency"] for t in _load("tenants") if t["tenant_id"] == tenant)
    suppliers = {s["supplier_id"]: s for s in _load("suppliers") if s["tenant_id"] == tenant}
    if ev["supplier_id"] not in suppliers:
        raise ValueError(f"{ev['supplier_id']} is not a {tenant} supplier - no agent input (TC-02 / isolation)")
    supplier = suppliers[ev["supplier_id"]]

    open_pos = [p for p in _load("purchase_orders")
                if p["tenant_id"] == tenant and p["supplier_id"] == ev["supplier_id"] and p["status"] == "OPEN"]
    items = sorted({l["item_id"] for p in open_pos for l in p["lines"]})
    inventory = [i for i in _load("inventory") if i["tenant_id"] == tenant and i["item_id"] in items]
    alternates = [a for a in _load("alternate_suppliers") if a["tenant_id"] == tenant and a["item_id"] in items]

    # ---- provisional impact (see module docstring) ----
    start = _ts(ev["event_time"])
    end = start + timedelta(hours=ev["expected_duration_hours"])
    affected, total = [], Decimal("0")
    short_items: set[str] = set()  # one stock pool per item: once short, later POs of that item are short too
    lines = sorted(((p, l) for p in open_pos for l in p["lines"]), key=lambda pl: (pl[1]["due_date"], pl[0]["po_id"]))
    for p, l in lines:
        due = datetime.fromisoformat(l["due_date"])
        if due.date() > end.date():
            continue
        exposure = Decimal(l["unit_price"]["amount"]) * l["qty"]
        total += exposure
        delay = max(1, math.ceil((end - due).total_seconds() / 86400))
        rows = [i for i in inventory if i["item_id"] == l["item_id"]]
        main = max(rows, key=lambda r: r["daily_demand"]) if rows else None  # location with the highest demand
        coverage = round((main["on_hand"] - main["reserved"]) / main["daily_demand"], 2) if main else None
        short = coverage is None or coverage < delay or l["item_id"] in short_items
        if short:
            short_items.add(l["item_id"])
        affected.append({
            "po_id": p["po_id"],
            "item_id": l["item_id"],
            "affected_qty": l["qty"],
            "estimated_delay_days": delay,
            "exposure_amount": _money(exposure, currency),
            "inventory_coverage_days": coverage,
            "shortage": short,
        })
    affected.sort(key=lambda a: a["po_id"])
    score = SEVERITY_BASE[ev["severity"]] if affected else 0
    if any(a["shortage"] for a in affected):
        score += SHORTAGE_POINTS
    if total > EXPOSURE_THRESHOLD:
        score += EXPOSURE_POINTS

    # ---- data-quality flags: every null / absent value the decision depends on ----
    flags = []
    if supplier.get("capacity_per_week") is None:
        flags.append(f"{supplier['supplier_id']} capacity not provided")
    affected_items = sorted({a["item_id"] for a in affected})
    for item in affected_items:
        quals = [a for a in alternates if a["item_id"] == item and a["qualified"]]
        if not quals:
            flags.append(f"no qualified alternate supplier for {item}")
        for a in quals:
            if a["capacity"] is None:
                flags.append(f"{a['supplier_id']} capacity for {item} unknown")
        if not [i for i in inventory if i["item_id"] == item]:
            flags.append(f"no inventory record for {item}")
    flags.append(f"{supplier['supplier_id']} restart date not provided")

    rec = {
        "agent": agent,
        "risk_case_id": risk_case_id,
        "tenant_id": tenant,
        "correlation_id": ev["correlation_id"],
        "event": ev,
        "snapshot": {
            "captured_at": (start + timedelta(seconds=4)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "supplier": supplier,
            "supplier_history": supplier.get("disruption_history", []),
            "purchase_orders": open_pos,
            "inventory": inventory,
            "alternate_suppliers": alternates,
        },
        "impact": {"risk_score": min(100, score), "total_exposure": _money(total, currency), "affected_pos": affected},
        "data_quality_flags": flags,
    }
    if agent == "RECOVERY_PLANNER":
        if risk_analysis is None:
            raise ValueError("RECOVERY_PLANNER input needs the validated risk_analysis")
        tenant_rec = next(t for t in _load("tenants") if t["tenant_id"] == tenant)
        rec["risk_analysis"] = risk_analysis
        rec["constraints"] = {
            "max_cost_increase_pct": tenant_rec["settings"]["max_cost_increase_pct"],
            "require_qualified_alternates": tenant_rec["settings"]["require_qualified_alternates"],
        }
    return rec


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("scenario")
    ap.add_argument("--agent", default="RISK_ANALYST", choices=["RISK_ANALYST", "RECOVERY_PLANNER"])
    ap.add_argument("--risk-analysis", type=Path)
    args = ap.parse_args()
    ra = json.loads(args.risk_analysis.read_text(encoding="utf-8")) if args.risk_analysis else None
    print(json.dumps(build_agent_input(args.scenario, args.agent, ra), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
