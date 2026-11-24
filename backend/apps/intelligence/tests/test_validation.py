"""N-07 tests: valid real agent runs, schema-invalid payloads, fact-mismatch payloads (pure "
"functions, no DB)."""

from __future__ import annotations

import copy
import glob
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))  # `ai` package lives at the repo root

from ai.eval.grade import resolve as grade_resolve  # noqa: E402
from ai.eval.payload import payload as eval_payload  # noqa: E402

from apps.intelligence import validation as val  # noqa: E402

RP_RUNS = ROOT / "boomi" / "agents" / "recovery_planner" / "eval" / "runs"
RA_RUNS = ROOT / "boomi" / "agents" / "risk_analyst" / "eval" / "runs"
PLAN_KIND, RISK_KIND = val.RECOVERY_PLAN, val.RISK_ANALYSIS


def _load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def _demo():
    return eval_payload("RP-DEMO")


@pytest.fixture()
def demo(_demo):
    return copy.deepcopy(_demo)


@pytest.fixture()
def plan():
    return _load(RP_RUNS / "2026-11-20_RP-DEMO_v1.3.0.json")


@pytest.fixture()
def analysis():
    return _load(RA_RUNS / "2026-11-20_DEMO_v1.3.0.json")


def run_plan(plan, demo, **kw):
    return val.validate_agent_output(
        PLAN_KIND, plan, demo["snapshot"], demo["impact"], event=demo["event"], **kw
    )


def run_risk(analysis, demo, **kw):
    return val.validate_agent_output(
        RISK_KIND, analysis, demo["snapshot"], demo["impact"], event=demo["event"], **kw
    )


def codes(result, rank=1):
    return {
        r["code"] for o in result["options"] if o["rank"] == rank for r in o["unsupported_reasons"]
    }


def fact_codes(result):
    return {e["code"] for e in result["fact_errors"]}


def opt(plan, rank):
    return next(o for o in plan["options"] if o["rank"] == rank)


def ev(option, entity, field):
    return next(e for e in option["evidence"] if e["entity"] == entity and e["field"] == field)


def alt_row(snap, supplier_id):
    return next(a for a in snap["alternate_suppliers"] if a["supplier_id"] == supplier_id)


# --------------------------------------------------------------------------- real runs are valid


@pytest.mark.parametrize("path", sorted(glob.glob(str(RP_RUNS / "*_RP-*.json"))))
def test_real_recovery_runs_valid(path):
    inp = eval_payload(re.search(r"_(RP-[A-Z0-9-]+)_v", path).group(1))
    out = _load(path)
    r = val.validate_agent_output(
        PLAN_KIND,
        out,
        inp["snapshot"],
        inp["impact"],
        event=inp["event"],
        risk_case_id=inp["risk_case_id"],
    )
    assert r["valid"], r
    assert r["actionable_count"] == len(out["options"])
    assert all(not o["unsupported"] for o in r["options"])


@pytest.mark.parametrize("path", sorted(glob.glob(str(RA_RUNS / "*.json"))))
def test_real_risk_runs_valid(path):
    inp = eval_payload(re.search(r"_(DEMO|ADV-0\d)", path).group(1))
    r = val.validate_agent_output(
        RISK_KIND,
        _load(path),
        inp["snapshot"],
        inp["impact"],
        event=inp["event"],
        risk_case_id=inp["risk_case_id"],
    )
    assert r["valid"], r


def test_demo_plan_result_shape(plan, demo):
    r = run_plan(plan, demo)
    assert r["valid"] and r["schema_errors"] == [] and r["fact_errors"] == []
    assert [o["rank"] for o in r["options"]] == [1, 2, 3, 4]
    assert r["actionable_count"] == 4


def test_tc05_empty_options_valid():
    inp = eval_payload("RP-TC05")
    out = {
        "risk_case_id": inp["risk_case_id"],
        "options": [],
        "no_viable_option_reason": "No alternates qualify.",
    }
    r = val.validate_agent_output(PLAN_KIND, out, inp["snapshot"], inp["impact"])
    assert r["valid"] and r["options"] == [] and r["actionable_count"] == 0


def test_works_with_full_impact_output(plan, demo):
    # the stored RiskCase.impact is the full N-04 output: a superset of the agent view (extra keys,
    # due_date)
    full = copy.deepcopy(demo["impact"])
    full["supplier_found"] = True
    for row in full["affected_pos"]:
        row["due_date"] = "2026-12-03" if row["po_id"] == "PO-1001" else "2026-12-04"
        row["line_nos"] = [1]
    r = val.fact_check_recovery_plan(plan, demo["snapshot"], full)
    assert not any(o["unsupported"] for o in r["options"])


# --------------------------------------------------------------------------- schema invalid


def test_schema_missing_key(plan, demo):
    del plan["options"][0]["evidence"]
    r = run_plan(plan, demo)
    assert not r["valid"] and r["options"] == [] and r["actionable_count"] == 0
    assert any("evidence" in e["message"] for e in r["schema_errors"])


def test_schema_options_empty_without_reason(demo):
    out = {"risk_case_id": "RC-000045", "options": [], "no_viable_option_reason": None}
    assert not val.validate_agent_output(PLAN_KIND, out, demo["snapshot"], demo["impact"])["valid"]


def test_schema_bad_money_format(plan, demo):
    opt(plan, 1)["estimated_cost_delta"]["amount"] = "33600"
    assert not run_plan(plan, demo)["valid"]


def test_schema_risk_missing_field(analysis, demo):
    del analysis["confidence"]
    r = run_risk(analysis, demo)
    assert not r["valid"] and r["schema_errors"][0]["path"] == "$"


def test_schema_not_an_object(demo):
    assert val.validate_schema(PLAN_KIND, [])


def test_unknown_kind():
    with pytest.raises(ValueError):
        val.validate_schema("NOPE", {})


# --------------------------------------------------------------------------- recovery fact mismatc


def test_nonexistent_supplier_flagged_not_actionable(plan, demo):
    o = opt(plan, 1)
    o["proposed_action"]["change"]["supplier_id"] = "SUP-9999"
    for e in o["evidence"]:
        if e["entity"] == "SUP-2001":
            e["entity"] = "SUP-9999"
    r = run_plan(plan, demo)
    assert r["valid"]  # schema fine; the option is flagged, the run is kept for audit
    assert r["options"][0]["unsupported"] is True
    assert {"SUPPLIER_NOT_FOUND", "EVIDENCE_UNRESOLVED"} <= codes(r)
    assert r["actionable_count"] == 3
    assert all(x["message"] for x in r["options"][0]["unsupported_reasons"])


def test_supplier_not_alternate_for_item(plan, demo):
    alt_row(demo["snapshot"], "SUP-2001")["item_id"] = "ITM-502"
    assert "SUPPLIER_NOT_ALTERNATE_FOR_ITEM" in codes(run_plan(plan, demo))


def test_supplier_not_qualified(plan, demo):
    alt_row(demo["snapshot"], "SUP-2001")["qualified"] = False
    assert "SUPPLIER_NOT_QUALIFIED" in codes(run_plan(plan, demo))


def test_supplier_is_the_disrupted_one(plan, demo):
    opt(plan, 1)["proposed_action"]["change"]["supplier_id"] = "SUP-1008"
    assert "SUPPLIER_DISRUPTED" in codes(run_plan(plan, demo))


def test_wrong_price_in_evidence(plan, demo):
    ev(opt(plan, 1), "SUP-2001", "unit_price")["value"] = "80.00"
    r = run_plan(plan, demo)
    assert r["options"][0]["unsupported"] and "PRICE_MISMATCH" in codes(r)


def test_wrong_lead_time_evidence_and_field(plan, demo):
    o = opt(plan, 1)
    ev(o, "SUP-2001", "lead_time_days")["value"] = 1
    o["estimated_lead_time_days"] = 1
    assert "LEAD_TIME_MISMATCH" in codes(run_plan(plan, demo))


def test_invented_capacity(plan, demo):
    # SUP-2002 capacity is null in the snapshot: citing a number is invented
    opt(plan, 3)["evidence"].append(
        {
            "source": "ERP",
            "entity": "SUP-2002",
            "field": "capacity",
            "value": 9000,
            "item_id": "ITM-501",
        }
    )
    r = run_plan(plan, demo)
    assert "CAPACITY_MISMATCH" in codes(r, 3) and r["options"][2]["unsupported"]


def test_qty_above_affected(plan, demo):
    opt(plan, 3)["proposed_action"]["change"]["qty"] = 4001
    assert "QTY_EXCEEDS_AFFECTED" in codes(run_plan(plan, demo), 3)


def test_qty_above_capacity(plan, demo):
    alt_row(demo["snapshot"], "SUP-2001")["capacity"] = 3000
    assert "QTY_EXCEEDS_CAPACITY" in codes(run_plan(plan, demo))


def test_transfer_above_available(plan, demo):
    # DC-CHENNAI available = 800 - 100 - 200 = 500
    opt(plan, 2)["proposed_action"]["change"]["qty"] = 501
    r = run_plan(plan, demo)
    assert "TRANSFER_EXCEEDS_AVAILABLE" in codes(r, 2) and r["options"][1]["unsupported"]
    opt(plan, 2)["proposed_action"]["change"]["qty"] = 500
    assert not run_plan(plan, demo)["options"][1]["unsupported"]


def test_transfer_unknown_location_and_same_location(plan, demo):
    change = opt(plan, 2)["proposed_action"]["change"]
    change["to_location_id"] = "DC-NOWHERE"
    assert "LOCATION_NOT_FOUND" in codes(run_plan(plan, demo), 2)
    change["to_location_id"] = change["from_location_id"]
    assert "TRANSFER_SAME_LOCATION" in codes(run_plan(plan, demo), 2)


def test_transfer_wrong_item(plan, demo):
    opt(plan, 2)["proposed_action"]["change"]["item_id"] = "ITM-502"
    assert "ITEM_NOT_AFFECTED" in codes(run_plan(plan, demo), 2)


def test_transfer_invented_lead_time_and_cost(plan, demo):
    o = opt(plan, 2)
    o["estimated_lead_time_days"] = 1
    o["estimated_cost_delta"] = {"amount": "10.00", "currency": "INR"}
    assert {"LEAD_TIME_UNSUPPORTED", "COST_DELTA_UNSUPPORTED"} <= codes(run_plan(plan, demo), 2)


def test_cost_delta_wrong(plan, demo):
    opt(plan, 1)["estimated_cost_delta"]["amount"] = "33000.00"
    r = run_plan(plan, demo)
    assert "COST_DELTA_MISMATCH" in codes(r) and r["options"][0]["unsupported"]


def test_cost_delta_rounds_half_up(plan, demo):
    o = opt(plan, 1)
    o["proposed_action"]["change"]["qty"] = 1
    price = alt_row(demo["snapshot"], "SUP-2001")["unit_price"]
    price["amount"] = "75.60005"  # (75.60005 - 75.60) * 1 = 0.00005 -> 0.00
    o["estimated_cost_delta"]["amount"] = "0.00"
    assert "COST_DELTA_MISMATCH" not in codes(run_plan(plan, demo))
    price["amount"] = "75.605"  # 0.005 -> 0.01 half-up
    o["estimated_cost_delta"]["amount"] = "0.01"
    assert "COST_DELTA_MISMATCH" not in codes(run_plan(plan, demo))
    o["estimated_cost_delta"]["amount"] = "0.00"
    assert "COST_DELTA_MISMATCH" in codes(run_plan(plan, demo))


def test_currency_mismatch(plan, demo):
    opt(plan, 1)["estimated_cost_delta"]["currency"] = "USD"
    assert "CURRENCY_MISMATCH" in codes(run_plan(plan, demo))


def test_alternate_priced_in_other_currency(plan, demo):
    alt_row(demo["snapshot"], "SUP-2001")["unit_price"]["currency"] = "USD"
    assert "CURRENCY_MISMATCH" in codes(run_plan(plan, demo))
    o = opt(plan, 1)
    o["estimated_cost_delta"] = None
    o["data_gaps"] = ["cost not comparable across currencies"]
    assert "CURRENCY_MISMATCH" not in codes(run_plan(plan, demo))


def test_po_not_affected_and_not_found(plan, demo):
    opt(plan, 1)["proposed_action"]["po_id"] = "PO-1003"
    assert "PO_NOT_AFFECTED" in codes(run_plan(plan, demo))
    opt(plan, 1)["proposed_action"]["po_id"] = "PO-4040"
    assert "PO_NOT_FOUND" in codes(run_plan(plan, demo))


def test_revised_schedule_checks(plan, demo):
    o = opt(plan, 4)
    o["proposed_action"]["change"]["expected_date"] = "2026-12-09"
    o["estimated_lead_time_days"] = 7
    o["estimated_cost_delta"] = {"amount": "5.00", "currency": "INR"}
    assert {"REVISED_DATE_MISMATCH", "LEAD_TIME_MISMATCH", "COST_DELTA_MISMATCH"} <= codes(
        run_plan(plan, demo), 4
    )


def test_duplicate_rank(plan, demo):
    plan["options"][1]["rank"] = 1
    r = run_plan(plan, demo)
    assert not r["valid"] and "DUPLICATE_RANK" in {e["code"] for e in r["fact_errors"]}
    assert "DUPLICATE_RANK" in codes(r, 1)
    assert r["options"][0]["unsupported"] and r["options"][1]["unsupported"]


def test_evidence_wrong_impact_value(plan, demo):
    ev(opt(plan, 1), "PO-1001", "affected_qty")["value"] = 9999
    assert "EVIDENCE_MISMATCH" in codes(run_plan(plan, demo))


def test_risk_case_mismatch_in_plan(plan, demo):
    r = run_plan(plan, demo, risk_case_id="RC-000999")
    assert (
        not r["valid"]
        and r["fact_errors"][0]["code"] == "RISK_CASE_MISMATCH"
        and r["actionable_count"] == 0
    )


def test_event_evidence_checked_only_with_event(analysis, demo):
    next(e for e in analysis["evidence"] if e["source"] == "EVENT" and e["field"] == "event_type")[
        "value"
    ] = "SHIPMENT_DELAY"
    assert not run_risk(analysis, demo)["valid"]
    # without the event those entries cannot be checked and are skipped
    assert val.validate_agent_output(RISK_KIND, analysis, demo["snapshot"], demo["impact"])["valid"]


def test_evidence_parity_with_grader(plan, demo):
    """Our evidence resolver agrees with the eval grader's resolve() on every real option, "
    "intact and broken."""
    ctx = val._Ctx(demo["snapshot"], demo["impact"], demo["event"])
    for o in plan["options"]:
        for e in o["evidence"]:
            for cited in (e, {**e, "value": "zzz"}):
                ok, _ = grade_resolve(cited, demo)
                assert ok == (val.check_evidence([cited], ctx) == [])


def test_snapshot_collections_may_be_pages(plan, demo):
    alts = demo["snapshot"]["alternate_suppliers"]
    demo["snapshot"]["alternate_suppliers"] = {"count": len(alts), "results": alts}
    assert run_plan(plan, demo)["actionable_count"] == 4


# --------------------------------------------------------------------------- risk analysis


def test_risk_score_and_level_mismatch(analysis, demo):
    analysis["risk_score"] = 50
    demo["impact"]["risk_level"] = "HIGH"
    r = run_risk(analysis, demo)
    assert {"RISK_SCORE_MISMATCH", "RISK_LEVEL_MISMATCH"} <= fact_codes(r)
    assert not r["valid"] and r["actionable_count"] == 0


def test_risk_case_mismatch(analysis, demo):
    assert "RISK_CASE_MISMATCH" in fact_codes(run_risk(analysis, demo, risk_case_id="RC-000999"))


def test_risk_affected_po_unknown_and_missing(analysis, demo):
    analysis["affected_entities"] = [
        e for e in analysis["affected_entities"] if e["id"] != "PO-1002"
    ]
    analysis["affected_entities"].append({"type": "PO", "id": "PO-1003"})
    assert {"AFFECTED_PO_MISSING", "AFFECTED_PO_UNKNOWN"} <= fact_codes(run_risk(analysis, demo))


def test_risk_entity_not_found(analysis, demo):
    analysis["affected_entities"] += [
        {"type": "SUPPLIER", "id": "SUP-9999"},
        {"type": "ITEM", "id": "ITM-999"},
        {"type": "LOCATION", "id": "DC-NOWHERE"},
        {"type": "SHIPMENT", "id": "SHP-1"},
    ]
    r = run_risk(analysis, demo)
    assert [e["code"] for e in r["fact_errors"]].count("ENTITY_NOT_FOUND") == 3


def test_risk_evidence_value_mismatch(analysis, demo):
    ev(analysis, "PO-1001", "exposure_amount")["value"] = "1.00"
    assert "EVIDENCE_MISMATCH" in fact_codes(run_risk(analysis, demo))


def test_risk_capacity_claimed_but_null(analysis, demo):
    ev(analysis, "SUP-1008", "capacity_per_week")["value"] = 8000
    assert "CAPACITY_MISMATCH" in fact_codes(run_risk(analysis, demo))


def test_risk_capacity_must_be_in_missing_data(analysis, demo):
    analysis["missing_data"] = ["restart date"]
    assert "CAPACITY_NOT_IN_MISSING_DATA" in fact_codes(run_risk(analysis, demo))


def test_risk_capacity_figure_in_text(analysis, demo):
    analysis["facts"].append("SUP-1008 weekly capacity is 8000 units.")
    assert "CAPACITY_INVENTED" in fact_codes(run_risk(analysis, demo))
    analysis["facts"][-1] = (
        "Alternate SUP-2001 has capacity 5000 units."  # another supplier: real snapshot value
    )
    assert run_risk(analysis, demo)["valid"]
    analysis["facts"][-1] = "SUP-1008 had a capacity reduction on 2026-03-21 causing 7 days delay."
    assert run_risk(analysis, demo)["valid"]


def test_risk_known_capacity_not_required_in_missing_data(analysis, demo):
    demo["snapshot"]["supplier"]["capacity_per_week"] = 1000
    analysis["missing_data"] = []
    ev(analysis, "SUP-1008", "capacity_per_week")["value"] = 1000
    assert run_risk(analysis, demo)["valid"]


# ----------------------------------------------------------------------- review fixes (round 2)


def alt_option(plan):
    return copy.deepcopy(opt(plan, 1))


def only(plan, option):
    plan = copy.deepcopy(plan)
    plan["options"] = [option]
    option["rank"] = 1
    return plan


def test_foreign_change_keys_per_option_type(plan, demo):
    o = copy.deepcopy(opt(plan, 4))
    o["proposed_action"]["change"]["supplier_id"] = "SUP-9999"
    assert "CHANGE_FIELD_NOT_ALLOWED" in codes(run_plan(only(plan, o), demo))
    o = copy.deepcopy(opt(plan, 2))
    o["proposed_action"]["change"].update(
        {"supplier_id": "SUP-9999", "expected_date": "2020-01-01"}
    )
    assert "CHANGE_FIELD_NOT_ALLOWED" in codes(run_plan(only(plan, o), demo))
    o = alt_option(plan)
    o["proposed_action"]["change"]["expected_date"] = "2020-01-01"
    assert "CHANGE_FIELD_NOT_ALLOWED" in codes(run_plan(only(plan, o), demo))
    o = alt_option(plan)
    o["option_type"] = "SPLIT_ORDER"
    o["proposed_action"]["change"]["item_id"] = "ITM-501"
    assert "CHANGE_FIELD_NOT_ALLOWED" in codes(run_plan(only(plan, o), demo))


def test_alternate_without_qty_cost_delta_still_checked(plan, demo):
    o = alt_option(plan)
    del o["proposed_action"]["change"]["qty"]
    o["estimated_cost_delta"] = {"amount": "-99999.00", "currency": "INR"}
    assert "COST_DELTA_MISMATCH" in codes(run_plan(only(plan, o), demo))
    o["estimated_cost_delta"] = {
        "amount": "33600.00",
        "currency": "INR",
    }  # on the affected qty 4000
    assert not run_plan(only(plan, o), demo)["options"][0]["unsupported"]


def test_null_unit_price_with_cost_delta_unsupported(plan, demo):
    alt_row(demo["snapshot"], "SUP-2001")["unit_price"] = None
    o = alt_option(plan)
    o["evidence"] = [
        e for e in o["evidence"] if e["field"] != "unit_price" or e["entity"] != "SUP-2001"
    ]
    assert "COST_DELTA_UNSUPPORTED" in codes(run_plan(only(plan, o), demo))
    demo2 = copy.deepcopy(demo)
    demo2["snapshot"]["alternate_suppliers"] = _reset_alt(demo2)
    for po in demo2["snapshot"]["purchase_orders"]:
        po["lines"][0]["unit_price"] = None
    o = alt_option(plan)
    o["evidence"] = [e for e in o["evidence"] if e["field"] != "unit_price"]
    assert "COST_DELTA_UNSUPPORTED" in codes(run_plan(only(plan, o), demo2))


def _reset_alt(demo):
    return [a for a in eval_payload("RP-DEMO")["snapshot"]["alternate_suppliers"]]


def test_float_qty_is_coerced_not_a_crash(plan, demo):
    o = alt_option(plan)
    o["proposed_action"]["change"]["qty"] = 4000.0
    r = run_plan(only(plan, o), demo)
    assert r["valid"] and not r["options"][0]["unsupported"]


def test_fractional_qty_flagged(plan, demo):
    o = alt_option(plan)
    o["proposed_action"]["change"]["qty"] = 3999.5
    r = val.fact_check_recovery_plan(only(plan, o), demo["snapshot"], demo["impact"])
    assert "QTY_INVALID" in {x["code"] for x in r["options"][0]["unsupported_reasons"]}


def test_null_inventory_numbers_fail_closed(plan, demo):
    for r in demo["snapshot"]["inventory"]:
        r["reserved"] = None
    r = run_plan(only(plan, copy.deepcopy(opt(plan, 2))), demo)
    assert "TRANSFER_STOCK_UNKNOWN" in codes(r) and r["options"][0]["unsupported"]


def test_unexpected_exception_marks_option_unsupported(plan, demo):
    bad = copy.deepcopy(plan)
    bad["options"][0]["evidence"] = [
        {"source": "ERP"}
    ]  # malformed evidence slips past a direct call
    r = val.fact_check_recovery_plan(bad, demo["snapshot"], demo["impact"])
    assert "VALIDATOR_ERROR" in {x["code"] for x in r["options"][0]["unsupported_reasons"]}


def test_plan_level_error_marks_all_options_unsupported(plan, demo):
    r = run_plan(plan, demo, risk_case_id="RC-000999")
    assert not r["valid"] and r["actionable_count"] == 0
    assert all(o["unsupported"] for o in r["options"])
    assert all(
        "RISK_CASE_MISMATCH" in {x["code"] for x in o["unsupported_reasons"]} for o in r["options"]
    )


def test_duplicate_option_flagged(plan, demo):
    dup = copy.deepcopy(opt(plan, 1))
    dup["rank"] = 5
    plan["options"].append(dup)
    r = run_plan(plan, demo)
    assert "DUPLICATE_OPTION" in codes(r, 5) and "DUPLICATE_OPTION" not in codes(r, 1)


def test_alternate_without_supplier_evidence(plan, demo):
    o = alt_option(plan)
    o["evidence"] = [e for e in o["evidence"] if e["source"] == "IMPACT"]
    assert "EVIDENCE_MISSING" in codes(run_plan(only(plan, o), demo))


def test_qty_compared_to_the_options_item(plan, demo):
    # second affected item on the PO with a small qty: qty above that item is flagged
    demo["impact"]["affected_pos"].append(
        {**demo["impact"]["affected_pos"][0], "item_id": "ITM-502", "affected_qty": 10}
    )
    demo["snapshot"]["purchase_orders"][0]["lines"].append(
        {
            "item_id": "ITM-502",
            "qty": 10,
            "due_date": "2026-12-03",
            "unit_price": {"amount": "1.00", "currency": "INR"},
        }
    )
    demo["snapshot"]["alternate_suppliers"].append(
        {
            "item_id": "ITM-502",
            "supplier_id": "SUP-2001",
            "qualified": True,
            "lead_time_days": 2,
            "unit_price": {"amount": "2.00", "currency": "INR"},
            "capacity": 100,
        }
    )
    o = alt_option(plan)
    o["proposed_action"]["change"]["qty"] = 50
    o["evidence"] = [
        e
        for e in o["evidence"]
        if e["field"] not in ("unit_price", "lead_time_days", "capacity")
        or e["entity"] == "PO-1001"
    ]
    for e in o["evidence"]:
        e["item_id"] = "ITM-502" if e["entity"] == "SUP-2001" else e.get("item_id")
    r = val.fact_check_recovery_plan(only(plan, o), demo["snapshot"], demo["impact"])
    assert "QTY_EXCEEDS_AFFECTED" in {x["code"] for x in r["options"][0]["unsupported_reasons"]}


def test_scientific_notation_string_is_not_a_number(plan, demo):
    ev(opt(plan, 1), "SUP-2001", "capacity")["value"] = "5e3"
    assert "CAPACITY_MISMATCH" in codes(run_plan(plan, demo))
    assert val._dec("NaN") is None and val._dec("Infinity") is None and val._dec(True) is None


def test_demo_capacity_claim_test_runs_on_null_capacity(demo):
    assert (
        demo["snapshot"]["supplier"]["capacity_per_week"] is None
    )  # the CAPACITY_INVENTED test relies on it


def test_schema_dir_configurable(monkeypatch, tmp_path, plan, demo):
    monkeypatch.setenv("SG_SCHEMA_DIR", str(tmp_path))
    with pytest.raises(RuntimeError, match="SG_SCHEMA_DIR"):
        val.validate_schema(PLAN_KIND, plan)
    monkeypatch.setenv("SG_SCHEMA_DIR", str(ROOT / "ai" / "schemas"))
    assert val.validate_schema(PLAN_KIND, plan) == []


def test_schema_dir_from_django_setting(monkeypatch, tmp_path, plan, settings):
    monkeypatch.delenv("SG_SCHEMA_DIR", raising=False)
    settings.SG_SCHEMA_DIR = str(ROOT / "ai" / "schemas")
    assert val.validate_schema(PLAN_KIND, plan) == []


# --------------------------------------------------------------------------- re-review fixes


def test_revised_date_unrecomputable_is_unsupported(plan, demo):
    o = copy.deepcopy(opt(plan, 4))
    o["proposed_action"]["change"]["expected_date"] = "2031-01-01"
    o["estimated_lead_time_days"] = None
    o["data_gaps"] = ["delay unknown"]
    demo["impact"]["affected_pos"][0]["estimated_delay_days"] = None
    r = run_plan(only(plan, o), demo)
    assert "REVISED_DATE_UNSUPPORTED" in codes(r) and r["options"][0]["unsupported"]
    # due date missing from the snapshot (and the impact row): same outcome
    demo2 = copy.deepcopy(demo)
    demo2["impact"]["affected_pos"][0]["estimated_delay_days"] = 2
    demo2["snapshot"]["purchase_orders"][0]["lines"][0].pop("due_date")
    assert "REVISED_DATE_UNSUPPORTED" in codes(run_plan(only(plan, copy.deepcopy(o)), demo2))


def test_revised_date_without_affected_line(plan, demo):
    demo["impact"]["affected_pos"] = []
    assert "REVISED_DATE_UNSUPPORTED" in codes(
        run_plan(only(plan, copy.deepcopy(opt(plan, 4))), demo)
    )


def test_duplicate_option_ignores_int_vs_float_qty(plan, demo):
    dup = copy.deepcopy(opt(plan, 1))
    dup["rank"] = 5
    dup["proposed_action"]["change"]["qty"] = 4000.0
    plan["options"].append(dup)
    assert "DUPLICATE_OPTION" in codes(run_plan(plan, demo), 5)


def test_evidence_missing_needs_relevant_supplier_field(plan, demo):
    o = alt_option(plan)
    o["evidence"] = [e for e in o["evidence"] if e["entity"] != "SUP-2001"]
    o["evidence"].append(
        {"source": "ERP", "entity": "SUP-2001", "field": "tenant_id", "value": "TEN-ACME"}
    )
    assert "EVIDENCE_MISSING" in codes(run_plan(only(plan, o), demo))


def test_split_must_be_partial(plan, demo):
    o = alt_option(plan)
    o["option_type"] = "SPLIT_ORDER"
    assert "SPLIT_NOT_PARTIAL" in codes(run_plan(only(plan, o), demo))
    o["proposed_action"]["change"]["qty"] = 3000
    o["estimated_cost_delta"]["amount"] = "25200.00"
    assert "SPLIT_NOT_PARTIAL" not in codes(run_plan(only(plan, o), demo))


def test_transfer_destination_checked_when_po_has_delivery_location(plan, demo):
    o = copy.deepcopy(opt(plan, 2))
    assert "TRANSFER_WRONG_DESTINATION" not in codes(
        run_plan(only(plan, o), demo)
    )  # no field: skipped
    demo["snapshot"]["purchase_orders"][0]["delivery_location_id"] = "DC-CHENNAI"
    assert "TRANSFER_WRONG_DESTINATION" in codes(run_plan(only(plan, copy.deepcopy(o)), demo))


def test_risk_check_fails_closed_on_malformed_impact(analysis, demo):
    demo["impact"]["affected_pos"] = [{"item_id": "ITM-501"}]
    r = run_risk(analysis, demo)
    assert not r["valid"] and "VALIDATOR_ERROR" in fact_codes(r)


@pytest.mark.parametrize(
    "text",
    [
        "The supplier can still ship 1,200 units per week.",
        "Capacity is down to forty percent.",
        "SUP-1008 has 40% capacity left.",
    ],
)
def test_risk_free_text_capacity_claims(analysis, demo, text):
    analysis["facts"].append(text)
    assert "CAPACITY_INVENTED" in fact_codes(run_risk(analysis, demo))


def test_risk_capacity_gap_statement_is_fine(analysis, demo):
    analysis["facts"].append("Supplier capacity is not provided in the snapshot.")
    assert run_risk(analysis, demo)["valid"]


def test_out_of_range_numbers_are_unsupported(plan, demo):
    opt(plan, 1)["estimated_lead_time_days"] = 10**12
    opt(plan, 2)["estimated_cost_delta"] = {"amount": "1000000000000000.00", "currency": "INR"}
    r = run_plan(plan, demo)
    assert "VALUE_OUT_OF_RANGE" in codes(r, 1) and "VALUE_OUT_OF_RANGE" in codes(r, 2)


def test_rank_out_of_range_is_a_plan_error(plan, demo):
    opt(plan, 1)["rank"] = 10**12
    r = run_plan(plan, demo)
    assert not r["valid"] and "RANK_OUT_OF_RANGE" in {e["code"] for e in r["fact_errors"]}
