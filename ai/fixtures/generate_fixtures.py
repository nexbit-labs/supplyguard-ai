"""Regenerate ai/fixtures/ (valid, invalid, expected_errors.json). Run from repo root:
    python ai/fixtures/generate_fixtures.py
Each invalid case = a valid fixture with exactly one defect + the jsonschema keyword it must fail on.
"""
import copy
import json
import pathlib

root = pathlib.Path("ai/fixtures")
(root / "valid").mkdir(parents=True, exist_ok=True)
(root / "invalid").mkdir(parents=True, exist_ok=True)


def dump(p, d):
    (root / p).write_text(json.dumps(d, indent=2) + "\n", encoding="utf-8", newline=chr(10))


def ev(e, f, v, src="ERP", **qual):
    return {"source": src, "entity": e, "field": f, "value": v, **qual}


def money(a):
    return {"amount": a, "currency": "INR"}


CORR = "6f1c2a9e-8f0b-4c7a-9a43-2f3d1b5e7c10"

risk = {
    "risk_case_id": "RC-000045",
    "risk_level": "CRITICAL",
    "risk_score": 92,
    "summary": "SUP-1008 factory outage (48 h) delays 2 open POs for ITM-501 due inside the outage window; DC-PUNE stock covers about 1.5 days, so a shortage is likely.",
    "affected_entities": [
        {"type": "SUPPLIER", "id": "SUP-1008"},
        {"type": "PO", "id": "PO-1001"},
        {"type": "PO", "id": "PO-1002"},
        {"type": "ITEM", "id": "ITM-501"},
    ],
    "evidence": [
        ev("PO-1001", "expected_date", "2026-12-03"),
        ev("PO-1001", "due_date", "2026-12-03", item_id="ITM-501"),
        ev("PO-1001", "qty", 4000, item_id="ITM-501"),
        ev("PO-1002", "due_date", "2026-12-04", item_id="ITM-501"),
        ev("ITM-501", "on_hand", 1100, location_id="DC-PUNE"),
        ev("IMPACT", "risk_score", 92, src="IMPACT"),
        ev("EVT-000123", "expected_duration_hours", 48, src="EVENT"),
    ],
    "facts": [
        "PO-1001 (4000 units ITM-501) is due 2026-12-03, inside the outage window.",
        "On-hand ITM-501 at DC-PUNE is 1100 units (200 reserved).",
    ],
    "estimates": [
        {
            "statement": "Delivery of PO-1001 slips by about 2 days.",
            "basis": "Outage duration of 48 h from the event; supplier gave no restart date.",
        }
    ],
    "assumptions": ["Outage duration reported in the event is accurate."],
    "missing_data": ["SUP-1008 remaining production capacity not provided"],
    "confidence": "MEDIUM",
}

plan = {
    "risk_case_id": "RC-000045",
    "options": [
        {
            "rank": 1,
            "option_type": "ALTERNATE_SUPPLIER",
            "proposed_action": {"po_id": "PO-1001", "change": {"supplier_id": "SUP-2001", "qty": 4000}},
            "action_steps": ["Move 4000 units of PO-1001 to SUP-2001", "Notify SUP-1008 of the change"],
            "estimated_lead_time_days": 2,
            "estimated_cost_delta": money("33600.00"),
            "trade_offs": ["SUP-2001 unit price 84.00 vs 75.60 today (+8.40/unit)"],
            "evidence": [
                ev("SUP-2001", "unit_price", "84.00", item_id="ITM-501"),
                ev("SUP-2001", "lead_time_days", 2, item_id="ITM-501"),
                ev("PO-1001", "unit_price", "75.60", item_id="ITM-501"),
                ev("SUP-2002", "capacity", None, item_id="ITM-501"),
            ],
            "assumptions": ["SUP-2001 capacity in the ERP record is still available", "SUP-2002 not considered: capacity unknown in ERP"],
            "data_gaps": [],
        },
        {
            "rank": 2,
            "option_type": "REVISED_SCHEDULE",
            "proposed_action": {"po_id": "PO-1001", "change": {"expected_date": "2026-12-05"}},
            "action_steps": ["Move PO-1001 expected date to 2026-12-05", "Cover the gap from DC-PUNE stock"],
            "estimated_lead_time_days": None,
            "estimated_cost_delta": None,
            "trade_offs": ["No extra purchase cost, but about 1.5 days of stock-out risk at DC-PUNE"],
            "evidence": [ev("ITM-501", "on_hand", 1100, location_id="DC-PUNE")],
            "assumptions": [],
            "data_gaps": ["Supplier has not confirmed a restart date"],
        },
    ],
    "no_viable_option_reason": None,
}

no_opt = {
    "risk_case_id": "RC-000050",
    "options": [],
    "no_viable_option_reason": "No qualified alternate supplier for ITM-777 and no stock at other locations.",
}

event = {
    "event_id": "EVT-000123",
    "tenant_id": "TEN-ACME",
    "supplier_id": "SUP-1008",
    "event_type": "FACTORY_OUTAGE",
    "severity": "HIGH",
    "event_time": "2026-12-02T10:15:00Z",
    "expected_duration_hours": 48,
    "description": "Line 2 fire, production halted",
    "source_system": "supplier-portal",
    "correlation_id": CORR,
}

inp = {
    "agent": "RISK_ANALYST",
    "risk_case_id": "RC-000045",
    "tenant_id": "TEN-ACME",
    "correlation_id": CORR,
    "event": event,
    "snapshot": {
        "captured_at": "2026-12-02T10:15:04Z",
        "supplier": {"supplier_id": "SUP-1008", "name": "Shakti Components Pvt Ltd"},
        "supplier_history": [{"date": "2026-06-10", "event_type": "SHIPMENT_DELAY", "delay_days": 3}],
        "purchase_orders": [
            {"po_id": "PO-1001", "supplier_id": "SUP-1008", "status": "OPEN", "expected_date": "2026-12-03",
             "lines": [{"item_id": "ITM-501", "qty": 4000, "due_date": "2026-12-03", "unit_price": money("75.60")}]},
            {"po_id": "PO-1002", "supplier_id": "SUP-1008", "status": "OPEN", "expected_date": "2026-12-04",
             "lines": [{"item_id": "ITM-501", "qty": 2500, "due_date": "2026-12-04", "unit_price": money("75.60")}]},
        ],
        "inventory": [
            {"item_id": "ITM-501", "location_id": "DC-PUNE", "on_hand": 1100, "reserved": 200, "safety_stock": 300},
            {"item_id": "ITM-501", "location_id": "DC-CHENNAI", "on_hand": 800, "reserved": 100, "safety_stock": 200},
        ],
        "alternate_suppliers": [
            {"supplier_id": "SUP-2001", "item_id": "ITM-501", "lead_time_days": 2, "unit_price": money("84.00"), "capacity": 5000},
            {"supplier_id": "SUP-2002", "item_id": "ITM-501", "lead_time_days": 5, "unit_price": money("79.50"), "capacity": None},
        ],
    },
    "impact": {
        "risk_score": 92,
        "total_exposure": money("491400.00"),
        "affected_pos": [
            {"po_id": "PO-1001", "item_id": "ITM-501", "affected_qty": 4000, "estimated_delay_days": 2,
             "exposure_amount": money("302400.00"), "inventory_coverage_days": 1.5, "shortage": True},
            {"po_id": "PO-1002", "item_id": "ITM-501", "affected_qty": 2500, "estimated_delay_days": 1,
             "exposure_amount": money("189000.00"), "inventory_coverage_days": 1.5, "shortage": True},
        ],
    },
    "data_quality_flags": ["SUP-1008 capacity not provided", "SUP-2002 capacity unknown"],
}

# One option of every option_type, each with exactly the change fields its type requires.
all_types = {
    "risk_case_id": "RC-000045",
    "options": [
        plan["options"][0],
        {
            "rank": 2,
            "option_type": "SPLIT_ORDER",
            "proposed_action": {"po_id": "PO-1002", "change": {"supplier_id": "SUP-2001", "qty": 1500}},
            "action_steps": ["Move 1500 of 2500 units of PO-1002 to SUP-2001", "Keep 1000 units with SUP-1008"],
            "estimated_lead_time_days": 2,
            "estimated_cost_delta": money("12600.00"),
            "trade_offs": ["Two suppliers to follow up instead of one"],
            "evidence": [
                ev("SUP-2001", "capacity", 5000, item_id="ITM-501"),
                ev("PO-1002", "qty", 2500, item_id="ITM-501"),
            ],
            "assumptions": ["SUP-1008 delivers the remaining 1000 units after the outage"],
            "data_gaps": [],
        },
        {
            "rank": 3,
            "option_type": "INVENTORY_TRANSFER",
            "proposed_action": {
                "po_id": "PO-1001",
                "change": {"item_id": "ITM-501", "from_location_id": "DC-CHENNAI", "to_location_id": "DC-PUNE", "qty": 500},
            },
            "action_steps": ["Transfer 500 units of ITM-501 from DC-CHENNAI to DC-PUNE (800 on hand - 100 reserved - 200 safety stock)"],
            "estimated_lead_time_days": 1,
            "estimated_cost_delta": None,
            "trade_offs": ["DC-CHENNAI drops to its safety stock (200 units)"],
            "evidence": [ev("ITM-501", "on_hand", 800, location_id="DC-CHENNAI"), ev("ITM-501", "safety_stock", 200, location_id="DC-CHENNAI")],
            "assumptions": [],
            "data_gaps": ["Inter-DC freight cost is not in the ERP snapshot"],
        },
        {**plan["options"][1], "rank": 4},
    ],
    "no_viable_option_reason": None,
}

inp_rec = copy.deepcopy(inp)
inp_rec["agent"] = "RECOVERY_PLANNER"
inp_rec["risk_analysis"] = risk
inp_rec["constraints"] = {"max_cost_increase_pct": 15, "require_qualified_alternates": True, "priority_items": ["ITM-501"]}

dump("valid/risk_analysis__demo_sup1008.json", risk)
dump("valid/recovery_plan__demo_sup1008.json", plan)
dump("valid/recovery_plan__tc05_no_option.json", no_opt)
dump("valid/recovery_plan__all_option_types.json", all_types)
dump("valid/agent_input__risk_analyst_demo.json", inp)
dump("valid/agent_input__recovery_planner_demo.json", inp_rec)


EXPECTED = {}


def bad(name, base, mut, keyword):
    d = copy.deepcopy(base)
    mut(d)
    dump(f"invalid/{name}.json", d)
    EXPECTED[name] = keyword


bad("risk_analysis__missing_missing_data", risk, lambda d: d.pop("missing_data"), "required")
bad("risk_analysis__missing_assumptions", risk, lambda d: d.pop("assumptions"), "required")
bad("risk_analysis__missing_evidence", risk, lambda d: d.pop("evidence"), "required")
bad("risk_analysis__empty_evidence", risk, lambda d: d.update(evidence=[]), "minItems")
bad("risk_analysis__evidence_without_field", risk, lambda d: d["evidence"][0].pop("field"), "required")
bad("risk_analysis__blank_missing_data_item", risk, lambda d: d.update(missing_data=["  "]), "pattern")
bad("risk_analysis__score_out_of_range", risk, lambda d: d.update(risk_score=150), "maximum")
bad("risk_analysis__bad_risk_level", risk, lambda d: d.update(risk_level="SEVERE"), "enum")
bad("risk_analysis__estimate_without_basis", risk, lambda d: d["estimates"][0].pop("basis"), "required")
bad("risk_analysis__invented_extra_field", risk, lambda d: d.update(supplier_capacity=5000), "additionalProperties")
bad("recovery_plan__missing_evidence", plan, lambda d: d["options"][0].pop("evidence"), "required")
bad("recovery_plan__missing_assumptions", plan, lambda d: d["options"][0].pop("assumptions"), "required")
bad("recovery_plan__missing_data_gaps", plan, lambda d: d["options"][1].pop("data_gaps"), "required")
bad("recovery_plan__bad_option_type", plan, lambda d: d["options"][0].update(option_type="EXPEDITE"), "enum")
bad("recovery_plan__money_as_number", plan,
    lambda d: d["options"][0].update(estimated_cost_delta={"amount": 33600, "currency": "INR"}), "anyOf")
bad("recovery_plan__empty_options_no_reason", no_opt, lambda d: d.update(no_viable_option_reason=None), "type")
bad("recovery_plan__options_and_reason", plan, lambda d: d.update(no_viable_option_reason="none"), "type")
bad("recovery_plan__empty_change", plan, lambda d: d["options"][0]["proposed_action"].update(change={}), "required")
bad("agent_input__planner_without_risk_analysis", inp_rec, lambda d: d.pop("risk_analysis"), "required")
bad("agent_input__missing_data_quality_flags", inp, lambda d: d.pop("data_quality_flags"), "required")
bad("agent_input__bad_tenant_id", inp, lambda d: d.update(tenant_id="acme"), "pattern")
bad("agent_input__bad_event_type", inp, lambda d: d["event"].update(event_type="EARTHQUAKE"), "enum")
bad("risk_analysis__evidence_unknown_source", risk, lambda d: d["evidence"][0].update(source="WEB"), "enum")
bad("recovery_plan__null_estimate_without_data_gaps", plan, lambda d: d["options"][1].update(data_gaps=[]), "minItems")
bad("recovery_plan__alternate_without_supplier_id", plan,
    lambda d: d["options"][0]["proposed_action"].update(change={"qty": 4000}), "required")
bad("recovery_plan__split_order_without_qty", all_types,
    lambda d: d["options"][1]["proposed_action"]["change"].pop("qty"), "required")
bad("recovery_plan__transfer_without_to_location", all_types,
    lambda d: d["options"][2]["proposed_action"]["change"].pop("to_location_id"), "required")
bad("recovery_plan__revised_schedule_without_expected_date", all_types,
    lambda d: d["options"][3]["proposed_action"]["change"].pop("expected_date"), "required")
bad("agent_input__bad_po_expected_date", inp,
    lambda d: d["snapshot"]["purchase_orders"][0].update(expected_date="03-12-2026"), "pattern")
bad("agent_input__bad_correlation_id", inp, lambda d: d.update(correlation_id="not-a-uuid"), "pattern")
bad("agent_input__analyst_with_risk_analysis", inp, lambda d: d.update(risk_analysis=risk), "not")
(root / "expected_errors.json").write_text(json.dumps(EXPECTED, indent=2, sort_keys=True) + chr(10), encoding="utf-8", newline=chr(10))
print("ok", len(EXPECTED))