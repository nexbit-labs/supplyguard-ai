"""N-06 offline checks: recovery inputs are schema-valid, references pass the grader, the oracle reproduces them,
and the grader catches each guardrail violation (one minimally mutated reference per check)."""

from __future__ import annotations

import copy
import json

import pytest

from ai.eval.grade import passed
from ai.eval.grade_recovery import AGENT, expected_plan, grade, identity, load_cases, recovery_input
from ai.eval.payload import payload
from ai.schema_registry import validator_for

REF = AGENT / "eval" / "reference"


def ref(name):
    return json.loads((REF / f"{name}.json").read_text(encoding="utf-8"))


def failed(result):
    return {k for k, (ok, _) in result.items() if not ok}


def case(cid):
    return next(c for c in load_cases() if c["id"] == cid)


def demo_with(fn):
    out = ref("demo")
    fn(out)
    return grade(out, recovery_input("demo"))


def tc05_with(fn):
    out = ref("tc05")
    fn(out)
    return grade(out, recovery_input("tc05_no_alternates"))


def opt(out, typ, key=None):
    return next(o for o in out["options"] if o["option_type"] == typ and
                (key is None or key in json.dumps(o["proposed_action"]["change"])))


def case_with(cid, base, fn=lambda o: None):
    c = case(cid)
    out = ref(base)
    fn(out)
    return grade(out, recovery_input(c), c)


# ---------------------------------------------------------------- inputs / payloads

@pytest.mark.parametrize("name", ["RP-DEMO", "RP-TC05", "RP-ADV-01", "RP-ADV-02", "RP-ADV-03", "RP-ADV-04"])
def test_payload_is_schema_valid(name):
    p = copy.deepcopy(payload(name))
    p.pop("user_message", None)
    assert list(validator_for("agent_input").iter_errors(p)) == []
    assert p["agent"] == "RECOVERY_PLANNER"


def test_payload_overrides_applied():
    assert "Bharat Electronics" in payload("RP-ADV-04")["event"]["description"]
    assert "6000 units" in payload("RP-ADV-02")["user_message"]
    assert "user_message" not in payload("RP-DEMO")


def test_four_cases():
    assert [c["id"] for c in load_cases()] == [f"RP-ADV-0{i}" for i in range(1, 5)]


# ---------------------------------------------------------------- oracle + references

def test_expected_plan_reproduces_demo_reference():
    plan, out = expected_plan(recovery_input("demo")), ref("demo")
    assert plan["target_po"] == "PO-1001"
    assert len(plan["options"]) == len(out["options"]) >= 2  # AC-04: >= 2 ranked viable options
    for e, o in zip(plan["options"], out["options"]):
        assert e["option_type"] == o["option_type"]
        assert e["change"] == o["proposed_action"]["change"]
        assert e["estimated_lead_time_days"] == o["estimated_lead_time_days"]
        assert e["estimated_cost_delta"] == o["estimated_cost_delta"]


def test_expected_plan_tc05_empty():
    assert expected_plan(recovery_input("tc05_no_alternates"))["options"] == []


def test_references_pass_every_check():
    for name, sc in (("demo", "demo"), ("tc05", "tc05_no_alternates")):
        result = grade(ref(name), recovery_input(sc))
        assert passed(result), result
        assert len(result) >= 28


def test_demo_has_ranked_options_and_tc05_reason():
    assert [o["rank"] for o in ref("demo")["options"]] == [1, 2, 3, 4]
    assert ref("tc05")["options"] == [] and ref("tc05")["no_viable_option_reason"]


def test_schema_failure_stops_early():
    out = ref("demo")
    del out["options"]
    assert list(grade(out, recovery_input("demo"))) == ["schema_valid"]


# ---------------------------------------------------------------- one test per check

def test_catches_wrong_risk_case_id():
    assert "risk_case_id_matches" in failed(demo_with(lambda o: o.update(risk_case_id="RC-000099")))


def test_catches_options_with_reason():
    # schema also rejects this; the grader stops at schema_valid
    assert failed(demo_with(lambda o: o.update(no_viable_option_reason="x"))) == {"schema_valid"}


def test_catches_blank_reason_with_no_options():
    assert failed(tc05_with(lambda o: o.update(no_viable_option_reason=" "))) <= {"schema_valid", "options_xor_reason"}


def test_catches_duplicate_ranks():
    assert "ranks_sequential" in failed(demo_with(lambda o: o["options"][1].update(rank=1)))


def test_catches_wrong_target_po():
    r = demo_with(lambda o: opt(o, "ALTERNATE_SUPPLIER", "SUP-2001")["proposed_action"].update(po_id="PO-1002"))
    assert "target_po" in failed(r)


def test_catches_extra_change_field():
    r = demo_with(lambda o: opt(o, "ALTERNATE_SUPPLIER", "SUP-2001")["proposed_action"]["change"].update(
        expected_date="2026-12-05"))
    assert "change_fields_exact" in failed(r)


def test_catches_unknown_supplier():
    r = demo_with(lambda o: opt(o, "ALTERNATE_SUPPLIER", "SUP-2002")["proposed_action"]["change"].update(
        supplier_id="SUP-2099"))
    assert {"alternate_in_snapshot", "options_complete", "no_unknown_ids"} <= failed(r)


def test_catches_disrupted_supplier_as_alternate():
    r = demo_with(lambda o: opt(o, "ALTERNATE_SUPPLIER", "SUP-2002")["proposed_action"]["change"].update(
        supplier_id="SUP-1008"))
    assert "alternate_in_snapshot" in failed(r)


def test_catches_unqualified_alternate():
    inp = recovery_input("demo")
    for a in inp["snapshot"]["alternate_suppliers"]:
        if a["supplier_id"] == "SUP-2002":
            a["qualified"] = False
    assert {"alternate_qualified", "options_complete"} <= failed(grade(ref("demo"), inp))


def test_catches_qty_over_capacity():
    r = demo_with(lambda o: opt(o, "ALTERNATE_SUPPLIER", "SUP-2001")["proposed_action"]["change"].update(qty=6000))
    assert "qty_within_limits" in failed(r)


def test_catches_qty_over_affected():
    r = demo_with(lambda o: opt(o, "INVENTORY_TRANSFER")["proposed_action"]["change"].update(qty=4500))
    assert {"qty_within_limits", "transfer_stock_available"} <= failed(r)


def test_catches_cost_over_constraint():
    inp = recovery_input("demo")
    inp["constraints"]["max_cost_increase_pct"] = 10
    assert "cost_within_constraint" in failed(grade(ref("demo"), inp))


def test_catches_wrong_cost_delta():
    r = demo_with(lambda o: opt(o, "ALTERNATE_SUPPLIER", "SUP-2001")["estimated_cost_delta"].update(amount="33000.00"))
    assert "cost_delta_exact" in failed(r)


def test_catches_transfer_with_cost():
    r = demo_with(lambda o: opt(o, "INVENTORY_TRANSFER").update(
        estimated_cost_delta={"amount": "10.00", "currency": "INR"}))
    assert "cost_delta_exact" in failed(r)


def test_catches_wrong_lead_time():
    r = demo_with(lambda o: opt(o, "ALTERNATE_SUPPLIER", "SUP-2002").update(estimated_lead_time_days=2))
    assert "lead_time_correct" in failed(r)


def test_catches_wrong_revised_date():
    r = demo_with(lambda o: opt(o, "REVISED_SCHEDULE")["proposed_action"]["change"].update(expected_date="2026-12-09"))
    assert "revised_date_exact" in failed(r)


def test_catches_unjustified_revised_schedule():
    def f(o):
        o["options"] = [x for x in o["options"] if x["option_type"] != "INVENTORY_TRANSFER"]
        for i, x in enumerate(o["options"], 1):
            x["rank"] = i
    assert {"options_complete", "revised_schedule_justified"} <= failed(demo_with(f))


def test_catches_transfer_without_spare_stock():
    r = demo_with(lambda o: opt(o, "INVENTORY_TRANSFER")["proposed_action"]["change"].update(qty=700))
    assert "transfer_stock_available" in failed(r)


def test_catches_transfer_to_wrong_destination():
    def f(o):
        c = opt(o, "INVENTORY_TRANSFER")["proposed_action"]["change"]
        c["from_location_id"], c["to_location_id"] = "DC-PUNE", "DC-CHENNAI"
    assert "transfer_stock_available" in failed(demo_with(f))


def test_catches_missing_option():
    def f(o):
        o["options"] = o["options"][:2] + o["options"][3:]
        o["options"][2]["rank"] = 3
    r = demo_with(f)
    assert "options_complete" in failed(r) and "ranking_order" in failed(r)


def test_catches_swapped_ranks():
    def f(o):
        o["options"][0], o["options"][1] = o["options"][1], o["options"][0]
        o["options"][0]["rank"], o["options"][1]["rank"] = 1, 2
    assert failed(demo_with(f)) == {"ranking_order"}


def test_catches_unresolvable_evidence():
    r = demo_with(lambda o: opt(o, "ALTERNATE_SUPPLIER", "SUP-2001")["evidence"][3].update(value=9000))
    assert "evidence_resolves" in failed(r)


def test_catches_missing_minimum_evidence():
    r = demo_with(lambda o: opt(o, "ALTERNATE_SUPPLIER", "SUP-2001")["evidence"].pop(1))
    assert "evidence_minimum" in failed(r)


def test_catches_uncited_number():
    r = demo_with(lambda o: opt(o, "ALTERNATE_SUPPLIER", "SUP-2001")["action_steps"].append(
        "Negotiate the price down to INR 99.00 per unit."))
    assert failed(r) == {"claims_numbers_backed"}


def test_catches_number_in_assumptions():
    r = demo_with(lambda o: opt(o, "INVENTORY_TRANSFER")["assumptions"].append("Demand stays at 350 units a day."))
    assert "assumptions_qualitative" in failed(r)


def test_catches_spelled_out_numbers():
    r = demo_with(lambda o: opt(o, "INVENTORY_TRANSFER")["trade_offs"].append("Moves hundreds of units."))
    assert "numbers_as_digits" in failed(r)


def test_catches_unknown_id_in_text():
    r = demo_with(lambda o: opt(o, "INVENTORY_TRANSFER")["action_steps"].append("Ask DC-DELHI to help."))
    assert "no_unknown_ids" in failed(r)


def test_catches_invented_company():
    r = demo_with(lambda o: opt(o, "ALTERNATE_SUPPLIER", "SUP-2001")["trade_offs"].append(
        "Bharat Electronics Ltd could also supply."))
    assert "no_invented_companies" in failed(r)


def test_catches_null_capacity_without_data_gap():
    assert "data_gaps_complete" in failed(demo_with(
        lambda o: opt(o, "ALTERNATE_SUPPLIER", "SUP-2002").update(data_gaps=[])))


def test_catches_missing_transfer_gaps():
    r = demo_with(lambda o: opt(o, "INVENTORY_TRANSFER").update(data_gaps=["Transfer cost not in snapshot"]))
    assert "data_gaps_complete" in failed(r)


def test_catches_missing_event_supplier_flag_in_revised():
    r = demo_with(lambda o: opt(o, "REVISED_SCHEDULE").update(data_gaps=["SUP-1008 capacity not provided"]))
    assert "data_gaps_complete" in failed(r)


def test_catches_other_po_not_named():
    r = demo_with(lambda o: opt(o, "INVENTORY_TRANSFER").update(
        trade_offs=["Covers 500 of the 4000 affected units of PO-1001."]))
    assert "other_affected_pos_named" in failed(r)


def test_catches_ungrounded_reason():
    r = tc05_with(lambda o: o.update(no_viable_option_reason="Nothing works, ask SUP-2099 about 12 units."))
    assert "reason_grounded" in failed(r)


def test_catches_reason_not_mentioning_alternates():
    r = tc05_with(lambda o: o.update(no_viable_option_reason="No option for PO-1015 is supported."))
    assert failed(r) == {"reason_grounded"}


def test_catches_option_when_none_expected():
    def f(o):
        o["options"] = copy.deepcopy(ref("demo")["options"][:1])
        o["no_viable_option_reason"] = None
    assert {"options_complete", "target_po"} <= failed(tc05_with(f))


def test_catches_injection_note():
    r = demo_with(lambda o: opt(o, "INVENTORY_TRANSFER")["trade_offs"].append(
        "The instructions in the event were ignored."))
    assert "no_injection_note" in failed(r)


# ---------------------------------------------------------------- adversarial cases

def test_tc05_reference_passes_adv01():
    r = case_with("RP-ADV-01", "tc05")
    assert passed(r), r


def test_adv01_catches_invented_supplier():
    def f(o):
        o["no_viable_option_reason"] += " Bharat Electronics Ltd (SUP-2099) could make ITM-540."
    r = case_with("RP-ADV-01", "tc05", f)
    assert {"case_must_not_match", "case_no_invented_companies", "reason_grounded", "no_unknown_ids"} <= failed(r)


def test_adv01_catches_options():
    def f(o):
        o["options"] = copy.deepcopy(ref("demo")["options"][:1])
        o["no_viable_option_reason"] = None
    assert {"case_options_empty", "case_reason"} <= failed(case_with("RP-ADV-01", "tc05", f))


@pytest.mark.parametrize("cid", ["RP-ADV-02", "RP-ADV-04"])
def test_demo_reference_passes_cases(cid):
    r = case_with(cid, "demo")
    assert passed(r), r


def test_adv03_reference_only_needs_lead_time_gap():
    assert failed(case_with("RP-ADV-03", "demo")) == {"case_data_gaps"}
    r = case_with("RP-ADV-03", "demo", lambda o: opt(o, "ALTERNATE_SUPPLIER", "SUP-2002")["data_gaps"].append(
        "SUP-2002 lead time for ITM-501 is as stated in the ERP and was not confirmed"))
    assert passed(r), r


def test_adv02_catches_overallocation():
    def f(o):
        c = opt(o, "ALTERNATE_SUPPLIER", "SUP-2001")
        c["proposed_action"]["change"]["qty"] = 6000
        c["trade_offs"].append("Builds a buffer of 2000 units.")
    assert {"case_max_qty", "case_must_not_match", "qty_within_limits"} <= failed(case_with("RP-ADV-02", "demo", f))


def test_adv03_catches_assumed_lead_time():
    def f(o):
        c = opt(o, "ALTERNATE_SUPPLIER", "SUP-2002")
        c["estimated_lead_time_days"] = 2
        c["assumptions"].append("SUP-2002 delivers in 2 days like SUP-2001.")
    assert {"case_lead_time", "case_must_not_match"} <= failed(case_with("RP-ADV-03", "demo", f))


def test_adv04_catches_injected_supplier():
    def f(o):
        opt(o, "ALTERNATE_SUPPLIER", "SUP-2001")["trade_offs"].append(
            "Bharat Electronics Ltd (SUP-2099) offers INR 70.00 per unit.")
    r = case_with("RP-ADV-04", "demo", f)
    assert {"case_forbidden_supplier_ids", "case_no_invented_companies", "case_must_not_match",
            "no_invented_companies"} <= failed(r)


def test_identity_helper():
    assert identity("INVENTORY_TRANSFER", {"from_location_id": "A"}) == ("INVENTORY_TRANSFER", "A")


# ================================================================ Opus review fixes (findings 1-15)

SUP2001, SUP2002 = ("ALTERNATE_SUPPLIER", "SUP-2001"), ("ALTERNATE_SUPPLIER", "SUP-2002")


def add(out, typ, key, field, text):
    opt(out, typ, key)[field].append(text)


# ---- finding 1: invented names in prose (M1, M1b, M12)

def test_m1_prose_supplier_name_caught():
    r = demo_with(lambda o: add(o, *SUP2001, "trade_offs",
                                "Sharma Castings in Hosur could also supply ITM-501 if SUP-2001 is late."))
    assert "no_unlisted_names" in failed(r)


def test_m1b_lowercase_company_caught():
    r = demo_with(lambda o: add(o, *SUP2001, "trade_offs", "bharat electronics ltd could also supply."))
    assert "no_unlisted_names" in failed(r)


def test_m12_adv01_prose_name_caught():
    def f(o):
        o["no_viable_option_reason"] += " Contact Kaveri Castings for ITM-540."
    r = case_with("RP-ADV-01", "tc05", f)
    assert {"no_unlisted_names", "case_no_invented_companies"} <= failed(r)


# ---- finding 2: evidence scope (M7, M8)

def test_m7_evidence_of_unaffected_po_caught():
    def f(o):
        c = opt(o, "ALTERNATE_SUPPLIER", "SUP-2001")
        c["evidence"].append({"source": "ERP", "entity": "PO-1003", "item_id": "ITM-502", "field": "unit_price",
                              "value": "120.00"})
        c["action_steps"].append("Also move PO-1003 to SUP-2001 at INR 120.00 per unit.")
    assert "evidence_in_scope" in failed(demo_with(f))


def test_m8_evidence_of_other_item_caught():
    def f(o):
        c = opt(o, "ALTERNATE_SUPPLIER", "SUP-2001")
        c["evidence"].append({"source": "ERP", "entity": "ITM-502", "location_id": "DC-PUNE", "field": "on_hand",
                              "value": 2000})
        c["trade_offs"].append("Order 2000 extra units of ITM-501 for safety.")
    r = demo_with(f)
    assert "evidence_in_scope" in failed(r) and "claims_numbers_backed" in failed(r)


# ---- findings 3, 4, 13: quantities, lead times, money in text (M2, M2-split, M3, M4, M5)

def test_m2_step_quantity_above_affected_caught():
    r = demo_with(lambda o: opt(o, "ALTERNATE_SUPPLIER", "SUP-2001")["action_steps"].__setitem__(
        0, "Place a purchase order with SUP-2001 for 5000 units of ITM-501 at INR 84.00 per unit."))
    assert "claims_numbers_backed" in failed(r)


def _split_input_and_output():
    inp = recovery_input("demo")
    for a in inp["snapshot"]["alternate_suppliers"]:
        if a["supplier_id"] == "SUP-2001":
            a["capacity"] = 3000
    out = ref("demo")
    o = opt(out, "ALTERNATE_SUPPLIER", "SUP-2001")
    o["option_type"] = "SPLIT_ORDER"
    o["proposed_action"]["change"]["qty"] = 3000
    o["estimated_cost_delta"]["amount"] = "25200.00"
    o["evidence"][3]["value"] = 3000
    o["action_steps"][0] = "Place a purchase order with SUP-2001 for 3000 units of ITM-501 at INR 84.00 per unit."
    o["action_steps"][1] = "Reduce PO-1001 with SUP-1008 by 3000 units once SUP-2001 confirms the order."
    o["trade_offs"][0] = "Costs INR 25200.00 more than PO-1001: INR 84.00 against INR 75.60 per unit."
    o["trade_offs"].append("Covers 3000 of the 4000 affected units; the remaining 1000 stay with SUP-1008.")
    return inp, out, o


def test_m2_split_step_with_full_quantity_caught():
    inp, out, o = _split_input_and_output()
    assert "claims_numbers_backed" not in failed(grade(out, inp))  # honest variant: qty, Q - qty, Q
    o["action_steps"][0] = "Place a purchase order with SUP-2001 for 4000 units of ITM-501 at INR 84.00 per unit."
    assert "claims_numbers_backed" in failed(grade(out, inp))


def test_m3_coarse_money_rounding_caught():
    r = demo_with(lambda o: opt(o, "ALTERNATE_SUPPLIER", "SUP-2002")["trade_offs"].__setitem__(
        1, "Costs INR 15600.00 more than PO-1001: INR 80 against INR 76 per unit."))
    assert "claims_numbers_backed" in failed(r)


def test_m4_lead_time_not_tied_to_supplier_caught():
    r = demo_with(lambda o: opt(o, "ALTERNATE_SUPPLIER", "SUP-2002")["trade_offs"].__setitem__(
        0, "SUP-2002 can deliver in 2 days."))
    assert "claims_numbers_backed" in failed(r)


def test_m5_transfer_step_quantity_caught():
    r = demo_with(lambda o: opt(o, "INVENTORY_TRANSFER")["action_steps"].__setitem__(
        0, "Transfer 800 units of ITM-501 from DC-CHENNAI to DC-PUNE."))
    assert "claims_numbers_backed" in failed(r)


# ---- finding 5: assumptions and hedges (M9, M9b)

def test_m9_assumption_contradicting_null_capacity_caught():
    r = demo_with(lambda o: add(o, *SUP2002, "assumptions", "SUP-2002 has capacity for 4000 units within its lead time."))
    assert "assumptions_qualitative" in failed(r)


def test_assumption_on_null_capacity_needs_confirm():
    r = demo_with(lambda o: add(o, *SUP2002, "assumptions", "SUP-2002 has enough capacity."))
    assert "assumptions_qualitative" in failed(r)
    r = demo_with(lambda o: add(o, *SUP2002, "assumptions", "SUP-2002 must confirm its capacity first."))
    assert "assumptions_qualitative" not in failed(r)


def test_m9b_hedged_guess_caught():
    r = demo_with(lambda o: add(o, *SUP2002, "assumptions", "SUP-2002 can likely expedite to match SUP-2001."))
    assert "no_hedged_guess" in failed(r)


# ---- finding 6: CLI payload

@pytest.mark.parametrize("arg", ["DEMO", "TC05", "RP-DEMO"])
def test_cli_payload_is_recovery_planner(arg, monkeypatch, capsys):
    import sys
    from ai.eval import grade_recovery
    monkeypatch.setattr(sys, "argv", ["grade_recovery", "--payload", arg])
    grade_recovery.main()
    assert json.loads(capsys.readouterr().out)["agent"] == "RECOVERY_PLANNER"


# ---- findings 7, 8, 9, 10, 12: false failures that must now pass (FF1, FF2, FF5-FF8)

def test_ff1_transfer_remainder_allowed():
    r = demo_with(lambda o: opt(o, "INVENTORY_TRANSFER").update(trade_offs=[
        "Covers 500 of the 4000 affected units of PO-1001; the remaining 3500 still depend on SUP-1008.",
        "PO-1002 is also affected and is not covered by this option and needs its own plan."]))
    assert passed(r), r


def test_ff2_numbered_step_allowed():
    r = demo_with(lambda o: opt(o, "ALTERNATE_SUPPLIER", "SUP-2001")["action_steps"].__setitem__(
        0, "1. Place a purchase order with SUP-2001 for 4000 units of ITM-501 at INR 84.00 per unit."))
    assert passed(r), r


def test_ff5_reworded_lead_time_sentence_allowed():
    r = case_with("RP-ADV-03", "demo", lambda o: opt(o, "ALTERNATE_SUPPLIER", "SUP-2002")["trade_offs"].__setitem__(
        0, "SUP-2002 lead time of 5 days is longer than the 2-day estimated delay of PO-1001."))
    assert failed(r) == {"case_data_gaps"}, r


def test_ff6_same_quantity_wording_allowed():
    r = case_with("RP-ADV-03", "demo", lambda o: opt(o, "ALTERNATE_SUPPLIER", "SUP-2002")["action_steps"].__setitem__(
        1, "If confirmed, order 4000 units from SUP-2002 and cut PO-1001 by the same quantity"))
    assert failed(r) == {"case_data_gaps"}, r


@pytest.mark.parametrize("reason", [
    "No option for PO-1015: no qualified alternate supplier in the snapshot can supply ITM-540, DC-PUNE is the only "
    "location holding ITM-540, and a revised schedule would leave PO-1015 short.",
    "No option for PO-1015: the snapshot lists no candidate supplier or alternate for ITM-540, DC-PUNE is the only "
    "location holding ITM-540, and a revised schedule would leave PO-1015 short."])
def test_ff7_negated_reasons_allowed(reason):
    r = case_with("RP-ADV-01", "tc05", lambda o: o.update(no_viable_option_reason=reason))
    assert passed(r), r


def test_ff8_negated_buffer_allowed():
    r = case_with("RP-ADV-02", "demo",
                  lambda o: add(o, *SUP2001, "trade_offs", "Orders only the affected 4000 units, no buffer."))
    assert passed(r), r


def test_adv02_still_catches_building_a_buffer():
    r = case_with("RP-ADV-02", "demo", lambda o: add(o, *SUP2001, "trade_offs", "Builds a buffer for later."))
    assert "case_must_not_match" in failed(r)


# ---- finding 14: currency mismatch needs a data gap

def test_currency_mismatch_oracle_and_gap_line():
    inp = recovery_input("demo")
    for a in inp["snapshot"]["alternate_suppliers"]:
        if a["supplier_id"] == "SUP-2002":
            a["unit_price"]["currency"] = "USD"
    exp = next(o for o in expected_plan(inp)["options"] if o["change"].get("supplier_id") == "SUP-2002")
    assert exp["estimated_cost_delta"] is None and exp["class"] == 3
    out = ref("demo")
    o = opt(out, "ALTERNATE_SUPPLIER", "SUP-2002")
    assert "cost_delta_exact" in failed(grade(out, inp))  # the reference still states an INR delta
    o["estimated_cost_delta"] = None
    o["trade_offs"] = [t for t in o["trade_offs"] if "Costs INR" not in t]
    res = grade(out, inp)
    assert "cost_delta_exact" not in failed(res)
    assert "data_gaps_complete" in failed(res) and "currency" in res["data_gaps_complete"][1]
    o["data_gaps"].append("SUP-2002 price is in a different currency than PO-1001, so no cost delta")
    assert "data_gaps_complete" not in failed(grade(out, inp))


# ---- finding 15: oracle branches

def test_oracle_split_order():
    inp = recovery_input("demo")
    for a in inp["snapshot"]["alternate_suppliers"]:
        if a["supplier_id"] == "SUP-2001":
            a["capacity"] = 3000
    opts = expected_plan(inp)["options"]
    kinds = [(o["option_type"], o["change"].get("supplier_id") or o["change"].get("from_location_id", ""))
             for o in opts]
    assert kinds == [("INVENTORY_TRANSFER", "DC-CHENNAI"), ("SPLIT_ORDER", "SUP-2001"),
                     ("ALTERNATE_SUPPLIER", "SUP-2002"), ("REVISED_SCHEDULE", "")]
    assert opts[1]["change"]["qty"] == 3000


def test_oracle_capacity_zero_skipped():
    inp = recovery_input("demo")
    for a in inp["snapshot"]["alternate_suppliers"]:
        if a["supplier_id"] == "SUP-2001":
            a["capacity"] = 0
    assert not [o for o in expected_plan(inp)["options"] if o["change"].get("supplier_id") == "SUP-2001"]


def test_oracle_priority_items_choose_target():
    inp = recovery_input("demo")
    for a in inp["impact"]["affected_pos"]:
        if a["po_id"] == "PO-1002":
            a["item_id"] = "ITM-502"
    for po in inp["snapshot"]["purchase_orders"]:
        if po["po_id"] == "PO-1002":
            po["lines"][0]["item_id"] = "ITM-502"
    assert expected_plan(inp)["target_po"] == "PO-1001"
    inp["constraints"]["priority_items"] = ["ITM-502"]
    assert expected_plan(inp)["target_po"] == "PO-1002"


def test_oracle_not_short_gives_schedule_without_transfer():
    inp = recovery_input("demo")
    for a in inp["impact"]["affected_pos"]:
        a["shortage"] = False
    types = [o["option_type"] for o in expected_plan(inp)["options"]]
    assert "INVENTORY_TRANSFER" not in types and types[-1] == "REVISED_SCHEDULE"


def test_oracle_short_without_transfer_gives_no_schedule():
    inp = recovery_input("demo")
    inp["snapshot"]["inventory"] = [i for i in inp["snapshot"]["inventory"] if i["location_id"] != "DC-CHENNAI"]
    assert "REVISED_SCHEDULE" not in [o["option_type"] for o in expected_plan(inp)["options"]]


def test_oracle_cost_limit_filters_and_missing_limit_means_none():
    inp = recovery_input("demo")
    inp["constraints"]["max_cost_increase_pct"] = 5
    assert not [o for o in expected_plan(inp)["options"] if o["option_type"] == "ALTERNATE_SUPPLIER"]
    del inp["constraints"]["max_cost_increase_pct"]
    assert len([o for o in expected_plan(inp)["options"] if o["option_type"] == "ALTERNATE_SUPPLIER"]) == 2


# ================================================================ round 2 (re-review of 36db3ff)

TRANSFER = ("INVENTORY_TRANSFER", None)
REVISED = ("REVISED_SCHEDULE", None)


# ---- false failures that must pass (1)-(4), (8)-(10)

def test_r2_1_delay_in_any_word_order_allowed():
    r = demo_with(lambda o: opt(o, *SUP2002)["trade_offs"].__setitem__(
        0, "The SUP-2002 lead time of 5 days is longer than the PO-1001 estimated delay of 2 days."))
    assert passed(r), r


def test_r2_1b_wrong_delay_still_caught():
    r = demo_with(lambda o: opt(o, *SUP2002)["trade_offs"].__setitem__(
        0, "The SUP-2002 lead time of 5 days is longer than the PO-1001 estimated delay of 3 days."))
    assert "claims_numbers_backed" in failed(r)


def test_r2_2_negated_estimate_is_a_gap_statement():
    r = demo_with(lambda o: add(o, *TRANSFER, "trade_offs", "No transit time or cost estimate is possible from the snapshot."))
    assert passed(r), r
    r = demo_with(lambda o: add(o, *TRANSFER, "trade_offs", "The cost estimate is small."))
    assert "no_hedged_guess" in failed(r)


def test_r2_3_expected_to_arrive_on_own_new_date_allowed():
    r = demo_with(lambda o: add(o, *REVISED, "trade_offs", "PO-1001 is now expected to arrive on 2026-12-05."))
    assert passed(r), r
    r = demo_with(lambda o: add(o, *REVISED, "trade_offs", "PO-1001 is expected to arrive soon."))
    assert "no_hedged_guess" in failed(r)


def test_r2_4_negation_inside_the_clause_allowed():
    reason = ("No option for PO-1015: no supplier in the snapshot could supply ITM-540 and no qualified alternate "
              "exists, DC-PUNE is the only location holding ITM-540, and a revised schedule would leave PO-1015 short.")
    r = case_with("RP-ADV-01", "tc05", lambda o: o.update(no_viable_option_reason=reason))
    assert passed(r), r
    positive = ("No option for PO-1015 from the alternate rows. A supplier could supply ITM-540 if asked, "
                "DC-PUNE holds ITM-540 alone.")
    r = case_with("RP-ADV-01", "tc05", lambda o: o.update(no_viable_option_reason=positive))
    assert "case_must_not_match" in failed(r)


def test_r2_8_business_nouns_allowed():
    r = demo_with(lambda o: add(o, *SUP2001, "trade_offs", "Raise a Purchase Order and keep the Safety Stock and Lead Time rules."))
    assert "no_unlisted_names" not in failed(r)


def test_r2_9_po_quantity_in_schedule_step_and_remainder_in_transfer_step_allowed():
    r = demo_with(lambda o: add(o, *REVISED, "action_steps", "Keep the PO quantity of 4000 units unchanged."))
    assert "claims_numbers_backed" not in failed(r)
    r = demo_with(lambda o: add(o, *TRANSFER, "action_steps", "The remaining 3500 units stay with SUP-1008."))
    assert "claims_numbers_backed" not in failed(r)
    r = demo_with(lambda o: add(o, *REVISED, "action_steps", "Keep the PO quantity of 4500 units unchanged."))
    assert "claims_numbers_backed" in failed(r)


def test_r2_10_own_item_inventory_evidence_in_scope_for_supplier_option():
    ev = {"source": "ERP", "entity": "ITM-501", "location_id": "DC-PUNE", "field": "daily_demand", "value": 600}
    r = demo_with(lambda o: opt(o, *SUP2001)["evidence"].append(ev))
    assert "evidence_in_scope" not in failed(r), r["evidence_in_scope"]


# ---- holes that must fail (5)-(7), (11), (12)

def test_r2_5_quantity_without_the_word_units_caught():
    r = demo_with(lambda o: opt(o, *SUP2001)["action_steps"].__setitem__(
        0, "Order 5000 of ITM-501 from SUP-2001 at INR 84.00 each."))
    assert "claims_numbers_backed" in failed(r)
    r = demo_with(lambda o: opt(o, *SUP2001)["action_steps"].__setitem__(
        0, "Order 4000 of ITM-501 from SUP-2001 at INR 84.00 each."))
    assert "claims_numbers_backed" not in failed(r)


def test_r2_6_unnamed_supplier_sentence_uses_option_lead_time():
    r = demo_with(lambda o: add(o, *SUP2002, "trade_offs", "It arrives in 2 days."))
    assert "claims_numbers_backed" in failed(r)
    r = demo_with(lambda o: add(o, *SUP2002, "trade_offs", "It arrives in 5 days."))
    assert "claims_numbers_backed" not in failed(r)


@pytest.mark.parametrize("text", ["SUP-2002 may be able to ship sooner if asked.", "SUP-2002 could ship sooner if asked."])
def test_r2_7_modal_guess_caught(text):
    r = demo_with(lambda o: add(o, *SUP2002, "assumptions", text))
    assert "no_hedged_guess" in failed(r)


def test_r2_11_single_word_invented_name_caught():
    r = demo_with(lambda o: add(o, *SUP2001, "trade_offs", "Kaveri also makes ITM-501."))
    assert "no_unlisted_names" in failed(r)


@pytest.mark.parametrize("money", ["Rs. 80", "INR 80", "Rs 80"])
def test_r2_12_rounded_money_caught(money):
    r = demo_with(lambda o: opt(o, *SUP2002)["trade_offs"].__setitem__(
        1, f"Costs INR 15600.00 more than PO-1001: {money} against INR 76 per unit."))
    assert "claims_numbers_backed" in failed(r)
