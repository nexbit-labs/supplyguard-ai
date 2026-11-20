"""Grade a Recovery Planner output against the agent_input it was produced from (N-06 eval harness).

    python -m ai.eval.grade_recovery <run.json> --scenario demo|tc05_no_alternates [--case RP-ADV-01]
    python -m ai.eval.grade_recovery --payload DEMO|TC05|RP-ADV-01..04

Spec: boomi/agents/recovery_planner/README.md "Grading rules". `expected_plan` is a deterministic oracle for
Steps 1-3 of instructions.md (target PO, candidates, ranking); every check compares the run to it or to the
snapshot. Like N-05's grader it is a prompt-testing aid; meaning is not checked, only values and identities.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from ai.eval.build_agent_input import build_agent_input, load_scenario
from ai.eval.grade import (BIG_NUMBER_WORDS, COMPANY_RE, ID_RE, ISO_DATE_RE, ROOT, _dec, _leaves, _matches,
                           _same, _sentences, _value_of, extract, input_support, name_texts, passed, resolve)
from ai.schema_registry import validator_for

AGENT = ROOT / "boomi" / "agents" / "recovery_planner"
CASES_FILE = AGENT / "eval" / "adversarial_cases.json"
RISK_REFERENCE = {  # scenario -> the N-05 reference answer used as risk_analysis
    "demo": ROOT / "boomi" / "agents" / "risk_analyst" / "eval" / "reference" / "demo.json",
    "tc05_no_alternates": ROOT / "boomi" / "agents" / "risk_analyst" / "eval" / "reference" / "tc05.json",
}
SCENARIO_ALIASES = {"DEMO": "demo", "TC05": "tc05_no_alternates"}
CENT = Decimal("0.01")
INJECTION_NOTE_RE = re.compile(r"instruction\w*[^.|]{0,40}(ignored|disregarded)", re.I)
HEDGE_RE = re.compile(r"\b(likely|probably|typically|usually|should be able|expected to|can expedite|best guess|estimates?)\b", re.I)
SENTENCE_NEGATED_OK = {"estimate", "estimates"}  # "no transit time or cost estimate is possible" is a gap statement
NAME_PHRASE_RE = re.compile(r"\b[A-Z][a-z]+(?:\s+(?:&\s+)?[A-Z][a-z]+)+")
COMPANY_WORD_RE = re.compile(r"\b(ltd|limited|inc|llc|corp|gmbh|industries|castings|electronics|components|enterprises|"
                             r"traders|engineering|works|metals|plastics)\b", re.I)
UNITS_RE = re.compile(r"(?<![\w.])(\d[\d,]*)\s+(?:(?!per\b)[A-Za-z]+\s+)?(?:units?|pcs|pieces)\b", re.I)
DAYS_RE = re.compile(r"(\d+)[- ]days?\b", re.I)
DELAY_DAYS_RE = re.compile(r"(\d+)[- ]days?\s+(?:estimated\s+)?delay", re.I)
DELAY_OF_RE = re.compile(r"delay\w*\s+(?:of\s+|by\s+)?(\d+)[- ]days?\b", re.I)  # "estimated delay of 2 days"
MONEY_RE = re.compile(r"(?:\b[A-Z]{3}\s+|\bRs\.?\s*|\u20b9\s*)(\d[\d,]*(?:\.\d+)?)")
QTY_CONTEXT_RES = (  # a quantity written without the word "units"
    re.compile(r"(?<![\w.])(\d[\d,]*)\s+(?:of|from|to)\s+ITM-"),
    re.compile(r"\b(?:order|move|transfer|place|ship|buy|purchase|reduce|cut|cover)\w*\s+(?:(?:(?!INR\b|USD\b|EUR\b|Rs\b)[A-Za-z]+\s+){0,3})"
               r"(?<![\w.-])(\d[\d,]*)(?![\d,]*[.\d-])(?!\s*[- ]days?\b)(?!\s*%)", re.I),
)
NEGATION_WORD_RE = re.compile(r"\b(no|not|none|nor|cannot|can't|neither|without|unable)\b", re.I)
GUESS_MODAL_RE = re.compile(r"\b(?:may|might|could)\b[^.|]{0,30}\b(?:be able|ship|deliver|expedite|arrive|supply|fulfil\w*)\b", re.I)
GUESS_COMPARATIVE_RE = re.compile(r"\b(?:sooner|faster|quicker|earlier)\b", re.I)
BUSINESS_WORDS = {"Purchase", "Order", "Orders", "Safety", "Stock", "Lead", "Time", "Times", "Transit", "Transfer", "Cost",
                  "Reserved", "Revised", "Schedule", "Inventory", "Supplier", "Alternate", "Split", "Price", "Quantity",
                  "Delay", "Due", "Date", "Expected", "Capacity", "Snapshot", "Shortage", "Risk", "Plan", "Unit",
                  "Units", "On", "Hand", "Daily", "Demand", "Affected", "Option", "Impact"}
SINGLE_NAME_RE = re.compile(r"\b([A-Z][a-z]+)\s+(?:also\s+|can\s+|could\s+)?(?:makes|supplies|produces|manufactures|"
                            r"supply|sells|stocks)\b")
PRONOUNS = {"It", "This", "That", "They", "There", "He", "She", "We", "Which", "What", "Who", "These", "Those"}
STEP_NUMBER_RE = re.compile(r"^\s*\d+[.)]\s+")
LISTED_NAME_KEYS = {"name", "item_name", "city", "category"}
CHANGE_KEYS = {
    "ALTERNATE_SUPPLIER": {"supplier_id", "qty"},
    "SPLIT_ORDER": {"supplier_id", "qty"},
    "INVENTORY_TRANSFER": {"item_id", "from_location_id", "to_location_id", "qty"},
    "REVISED_SCHEDULE": {"expected_date"},
}
SUPPLIER_TYPES = ("ALTERNATE_SUPPLIER", "SPLIT_ORDER")


# ------------------------------------------------------------------ inputs

def load_cases() -> list[dict]:
    return json.loads(CASES_FILE.read_text(encoding="utf-8"))["cases"]


def recovery_input(scenario_or_case: str | dict) -> dict:
    """agent_input for a scenario name or an adversarial case (event description may be overridden)."""
    case = scenario_or_case if isinstance(scenario_or_case, dict) else None
    name = case["scenario"] if case else scenario_or_case
    sc = load_scenario(name)
    if case and case.get("event_description"):
        sc["event"] = {**sc["event"], "description": case["event_description"]}
    risk = json.loads(RISK_REFERENCE[name].read_text(encoding="utf-8"))
    return build_agent_input(sc, "RECOVERY_PLANNER", risk_analysis=risk)


# ------------------------------------------------------------------ deterministic oracle

def _money_amount(v) -> Decimal:
    return Decimal(str(_value_of(v)))


def _cur(v):
    return v.get("currency") if isinstance(v, dict) else None


def _line(inp: dict, po_id: str, item_id: str) -> dict | None:
    for po in inp["snapshot"]["purchase_orders"]:
        if po["po_id"] == po_id:
            return next((l for l in po["lines"] if l["item_id"] == item_id), None)
    return None


def _target(inp: dict) -> dict | None:
    aff = inp["impact"]["affected_pos"]
    if not aff:
        return None
    prio = set((inp.get("constraints") or {}).get("priority_items") or [])
    return sorted(aff, key=lambda a: (not a["shortage"], a["item_id"] not in prio,
                                      _line(inp, a["po_id"], a["item_id"])["due_date"], a["po_id"]))[0]


def _alt_rows(inp: dict, item_id: str) -> list[dict]:
    return [a for a in inp["snapshot"]["alternate_suppliers"] if a["item_id"] == item_id]


def _stock_rows(inp: dict, item_id: str) -> list[dict]:
    return [i for i in inp["snapshot"]["inventory"] if i["item_id"] == item_id]


def _destination(inp: dict, item_id: str) -> str | None:
    rows = [r for r in _stock_rows(inp, item_id) if r.get("daily_demand") is not None]
    if not rows:
        return None
    return sorted(rows, key=lambda r: (-r["daily_demand"], r["location_id"]))[0]["location_id"]


def _spare(row: dict) -> int:
    return row["on_hand"] - row["reserved"] - row["safety_stock"]


def _cost_delta(alt: dict, line: dict, qty: int) -> dict | None:
    cur = _cur(line["unit_price"])
    if _cur(alt["unit_price"]) != cur:
        return None
    return {"amount": str(((_money_amount(alt["unit_price"]) - _money_amount(line["unit_price"])) * qty).quantize(CENT)),
            "currency": cur}


def _pct_increase(alt: dict, line: dict) -> Decimal | None:
    if _cur(alt["unit_price"]) != _cur(line["unit_price"]):
        return None
    p = _money_amount(line["unit_price"])
    return (_money_amount(alt["unit_price"]) - p) / p * 100


def expected_plan(inp: dict) -> dict:
    """Steps 1-3 of instructions.md: {"target_po", "options": [...] in rank order}."""
    tgt = _target(inp)
    if tgt is None:
        return {"target_po": None, "options": []}
    item, q = tgt["item_id"], tgt["affected_qty"]
    line = _line(inp, tgt["po_id"], item)
    cur = _cur(line["unit_price"])
    limit = (inp.get("constraints") or {}).get("max_cost_increase_pct")
    opts: list[dict] = []
    for alt in _alt_rows(inp, item):
        if alt["qualified"] is not True or alt["supplier_id"] == inp["event"]["supplier_id"]:
            continue
        pct = _pct_increase(alt, line)
        if pct is not None and limit is not None and pct > Decimal(str(limit)):
            continue
        cap = alt["capacity"]
        if cap is None or cap >= q:
            typ, qty = "ALTERNATE_SUPPLIER", q
        elif cap >= 1:
            typ, qty = "SPLIT_ORDER", cap
        else:
            continue
        cost = _cost_delta(alt, line, qty)
        cls = 1 if typ == "ALTERNATE_SUPPLIER" and cap is not None and cost is not None else 3
        opts.append({"option_type": typ, "change": {"supplier_id": alt["supplier_id"], "qty": qty},
                     "estimated_lead_time_days": alt["lead_time_days"], "estimated_cost_delta": cost, "class": cls})
    transfers = []
    if tgt["shortage"]:
        dest = _destination(inp, item)
        for row in _stock_rows(inp, item) if dest else []:
            if row["location_id"] != dest and _spare(row) >= 1:
                transfers.append({"option_type": "INVENTORY_TRANSFER", "class": 2,
                                  "change": {"item_id": item, "from_location_id": row["location_id"],
                                             "to_location_id": dest, "qty": min(_spare(row), q)},
                                  "estimated_lead_time_days": None, "estimated_cost_delta": None})
    opts += transfers
    if not tgt["shortage"] or transfers:
        due = date.fromisoformat(line["due_date"]) + timedelta(days=int(tgt["estimated_delay_days"]))
        opts.append({"option_type": "REVISED_SCHEDULE", "class": 4, "change": {"expected_date": due.isoformat()},
                     "estimated_lead_time_days": tgt["estimated_delay_days"],
                     "estimated_cost_delta": {"amount": "0.00", "currency": cur}})

    def key(o):
        lead, cost, ch = o["estimated_lead_time_days"], o["estimated_cost_delta"], o["change"]
        return (o["class"], lead is None, lead or 0, cost is None, Decimal(cost["amount"]) if cost else Decimal(0),
                -(ch.get("qty") or 0), ch.get("supplier_id", ""), ch.get("from_location_id", ""))

    return {"target_po": tgt["po_id"], "options": sorted(opts, key=key)}


def identity(option_type: str, change: dict) -> tuple[str, str]:
    return option_type, change.get("supplier_id") or change.get("from_location_id") or ""


# ------------------------------------------------------------------ helpers for checks

def _texts(opt: dict) -> list[str]:
    return [*opt["action_steps"], *opt["trade_offs"], *opt["assumptions"], *opt["data_gaps"]]


def _all_texts(out: dict) -> list[str]:
    return [t for o in out["options"] for t in _texts(o)] + [out["no_viable_option_reason"] or ""]


def _change_strings(opt: dict) -> list[str]:
    return [v for v in opt["proposed_action"]["change"].values() if isinstance(v, str)]


def _alt_row(inp: dict, opt: dict, item: str) -> dict | None:
    sid = opt["proposed_action"]["change"].get("supplier_id")
    return next((a for a in _alt_rows(inp, item) if a["supplier_id"] == sid), None)


def _stock_row(inp: dict, item: str, loc) -> dict | None:
    return next((r for r in _stock_rows(inp, item) if r["location_id"] == loc), None)


def _is_date(v) -> bool:
    return isinstance(v, str) and bool(ISO_DATE_RE.fullmatch(v))


def _evidence_values(opt: dict) -> tuple[set[Decimal], set[str]]:
    nums, dates = set(), set()
    for e in opt["evidence"]:
        v = e["value"]
        if isinstance(v, bool) or v is None:
            continue
        if _is_date(v):
            dates.add(v[:10])
        elif _dec(v) is not None:
            nums.add(_dec(v))
    return nums, dates


def _evidence_sums(opt: dict) -> set[Decimal]:
    by_field = defaultdict(list)
    for e in opt["evidence"]:
        v = e["value"]
        if not isinstance(v, bool) and v is not None and not _is_date(v) and _dec(v) is not None:
            by_field[e["field"]].append(_dec(v))
    return {sum(vs) for vs in by_field.values() if len(vs) > 1}


def minimum_evidence(opt: dict, po: str, item: str) -> list[dict]:
    ch = opt["proposed_action"]["change"]
    typ = opt["option_type"]
    if typ in SUPPLIER_TYPES:
        s = ch.get("supplier_id")
        return ([{"source": "ERP", "entity": s, "field": f, "item_id": item}
                 for f in ("qualified", "unit_price", "lead_time_days", "capacity")]
                + [{"source": "ERP", "entity": po, "field": "unit_price", "item_id": item},
                   {"source": "IMPACT", "entity": po, "field": "affected_qty"}])
    if typ == "INVENTORY_TRANSFER":
        return ([{"source": "ERP", "entity": item, "field": f, "location_id": ch.get("from_location_id")}
                 for f in ("on_hand", "reserved", "safety_stock")]
                + [{"source": "ERP", "entity": item, "field": "daily_demand", "location_id": ch.get("to_location_id")}]
                + [{"source": "IMPACT", "entity": po, "field": f} for f in ("affected_qty", "shortage")])
    return [{"source": "ERP", "entity": po, "field": "due_date", "item_id": item},
            {"source": "IMPACT", "entity": po, "field": "estimated_delay_days"},
            {"source": "IMPACT", "entity": po, "field": "shortage"}]


def _ev_matches(ev: dict, need: dict, item: str) -> bool:
    if (ev["source"], ev["entity"], ev["field"]) != (need["source"], need["entity"], need["field"]):
        return False
    if "location_id" in need:
        return ev.get("location_id") == need["location_id"]
    if need["source"] == "IMPACT":  # an impact row may also carry its item_id
        return ev.get("item_id") in (None, item)
    return ev.get("item_id") == need.get("item_id")


# ------------------------------------------------------------------ checks

def grade(out: dict, inp: dict, case: dict | None = None) -> dict[str, tuple[bool, str]]:
    r: dict[str, tuple[bool, str]] = {}
    errs = [e.message for e in validator_for("recovery_plan").iter_errors(out)]
    r["schema_valid"] = (not errs, "; ".join(errs[:3]) or "ok")
    if errs:
        return r
    year = int(inp["event"]["event_time"][:4])
    snap, ev_sup = inp["snapshot"], inp["event"]["supplier_id"]
    opts = out["options"]
    plan = expected_plan(inp)
    tgt = _target(inp)
    names = name_texts(inp)
    reason = out["no_viable_option_reason"]
    none = (True, "no options")

    def per_option(check):
        """check(opt, label, ctx) -> list of problems; ctx is None when the input has no affected PO."""
        if not opts:
            return none
        problems = []
        for o in opts:
            label = f"rank {o['rank']}"
            problems += [f"{label}: {p}" for p in check(o)]
        return (not problems, "; ".join(problems) or "ok")

    if tgt:
        item, q = tgt["item_id"], tgt["affected_qty"]
        line = _line(inp, tgt["po_id"], item)
        cur, pp = _cur(line["unit_price"]), _money_amount(line["unit_price"])
        limit = (inp.get("constraints") or {}).get("max_cost_increase_pct")
        due = date.fromisoformat(line["due_date"])
        others = [a["po_id"] for a in inp["impact"]["affected_pos"] if a["po_id"] != tgt["po_id"]]
    else:
        item = q = line = cur = pp = limit = due = None
        others = []

    r["risk_case_id_matches"] = (out["risk_case_id"] == inp["risk_case_id"], out["risk_case_id"])
    r["options_xor_reason"] = (bool(opts) != bool(reason and reason.strip()) and (not opts or reason is None),
                               f"{len(opts)} options, reason {'set' if reason else 'null'}")
    ranks = [o["rank"] for o in opts]
    r["ranks_sequential"] = (ranks == list(range(1, len(opts) + 1)), f"ranks {ranks}")
    exp_po = plan["target_po"]
    r["target_po"] = per_option(lambda o: [] if o["proposed_action"]["po_id"] == exp_po
                                else [f"po_id {o['proposed_action']['po_id']} but target is {exp_po}"])

    def change_fields(o):
        keys = set(o["proposed_action"]["change"])
        return [] if keys == CHANGE_KEYS[o["option_type"]] else [f"change keys {sorted(keys)}"]
    r["change_fields_exact"] = per_option(change_fields)

    def in_snapshot(o):
        if o["option_type"] not in SUPPLIER_TYPES:
            return []
        sid = o["proposed_action"]["change"].get("supplier_id")
        if sid == ev_sup:
            return [f"{sid} is the disrupted supplier"]
        return [] if item and _alt_row(inp, o, item) else [f"{sid} has no alternate row for {item}"]
    r["alternate_in_snapshot"] = per_option(in_snapshot)

    def qualified(o):
        if o["option_type"] not in SUPPLIER_TYPES:
            return []
        row = _alt_row(inp, o, item) if item else None
        return [] if row and row["qualified"] is True else [f"{o['proposed_action']['change'].get('supplier_id')} not qualified"]
    r["alternate_qualified"] = per_option(qualified)

    def qty_ok(o):
        ch, typ = o["proposed_action"]["change"], o["option_type"]
        qty = ch.get("qty")
        if typ == "REVISED_SCHEDULE" or q is None:
            return []
        if qty is None:
            return ["no qty"]
        if qty > q:
            return [f"qty {qty} > affected qty {q}"]
        if typ == "INVENTORY_TRANSFER":
            return [] if qty >= 1 else [f"qty {qty} < 1"]
        row = _alt_row(inp, o, item)
        cap = row["capacity"] if row else None
        if typ == "ALTERNATE_SUPPLIER":
            if qty != q:
                return [f"qty {qty} != affected qty {q}"]
            return [] if cap is None or q <= cap else [f"qty {qty} > capacity {cap}"]
        if cap is None or not (1 <= qty < q) or qty != cap:
            return [f"SPLIT qty {qty} must equal capacity {cap} and be below {q}"]
        return []
    r["qty_within_limits"] = per_option(qty_ok)

    def cost_constraint(o):
        if o["option_type"] not in SUPPLIER_TYPES or not item:
            return []
        row = _alt_row(inp, o, item)
        pct = _pct_increase(row, line) if row else None
        if pct is None or limit is None or pct <= Decimal(str(limit)):
            return []
        return [f"price increase {pct.quantize(CENT)} pct > {limit}"]
    r["cost_within_constraint"] = per_option(cost_constraint)

    def cost_exact(o):
        typ, got = o["option_type"], o["estimated_cost_delta"]
        if not item:
            return []
        if typ == "INVENTORY_TRANSFER":
            return [] if got is None else [f"transfer cost {got} must be null"]
        if typ == "REVISED_SCHEDULE":
            return [] if got == {"amount": "0.00", "currency": cur} else [f"cost {got}, expected 0.00 {cur}"]
        row = _alt_row(inp, o, item)
        qty = o["proposed_action"]["change"].get("qty")
        if row is None or qty is None:
            return ["cannot compute"]
        want = _cost_delta(row, line, qty)
        if want is None:
            return [] if got is None else [f"cost {got}, currencies differ so null expected"]
        return [] if got == want else [f"cost {got}, expected {want}"]
    r["cost_delta_exact"] = per_option(cost_exact)

    def lead(o):
        typ, got = o["option_type"], o["estimated_lead_time_days"]
        if typ == "INVENTORY_TRANSFER":
            want = None
        elif typ == "REVISED_SCHEDULE":
            want = tgt["estimated_delay_days"] if tgt else None
        else:
            row = _alt_row(inp, o, item) if item else None
            want = row["lead_time_days"] if row else None
        return [] if got == want else [f"lead time {got}, expected {want}"]
    r["lead_time_correct"] = per_option(lead)

    def rev_date(o):
        if o["option_type"] != "REVISED_SCHEDULE" or not tgt:
            return []
        want = (due + timedelta(days=int(tgt["estimated_delay_days"]))).isoformat()
        got = o["proposed_action"]["change"].get("expected_date")
        return [] if got == want else [f"expected_date {got}, expected {want}"]
    r["revised_date_exact"] = per_option(rev_date)

    revised = [o for o in opts if o["option_type"] == "REVISED_SCHEDULE"]
    has_transfer = any(o["option_type"] == "INVENTORY_TRANSFER" for o in opts)
    if not opts:
        r["revised_schedule_justified"] = none
    else:
        bad = []
        if len(revised) > 1:
            bad.append(f"{len(revised)} REVISED_SCHEDULE options")
        if revised and tgt and tgt["shortage"] and not has_transfer:
            bad.append(f"rank {revised[0]['rank']}: REVISED_SCHEDULE while PO is short and no transfer is in the plan")
        r["revised_schedule_justified"] = (not bad, "; ".join(bad) or "ok")

    def transfer(o):
        if o["option_type"] != "INVENTORY_TRANSFER":
            return []
        ch, p = o["proposed_action"]["change"], []
        if tgt and not tgt["shortage"]:
            p.append("target PO is not short")
        if item and ch.get("item_id") != item:
            p.append(f"item {ch.get('item_id')} != {item}")
        frm = _stock_row(inp, ch.get("item_id"), ch.get("from_location_id"))
        to = _stock_row(inp, ch.get("item_id"), ch.get("to_location_id"))
        if frm is None or to is None:
            p.append("from/to location has no row for the item")
        if ch.get("from_location_id") == ch.get("to_location_id"):
            p.append("from == to")
        if item and ch.get("to_location_id") != _destination(inp, item):
            p.append(f"destination {ch.get('to_location_id')} != {_destination(inp, item)}")
        if frm and ch.get("qty") is not None and ch["qty"] > _spare(frm):
            p.append(f"qty {ch['qty']} > spare stock {_spare(frm)} at {frm['location_id']}")
        return p
    r["transfer_stock_available"] = per_option(transfer)

    got_ids = [identity(o["option_type"], o["proposed_action"]["change"]) for o in opts]
    exp_ids = [identity(o["option_type"], o["change"]) for o in plan["options"]]
    missing, extra = [i for i in exp_ids if i not in got_ids], [i for i in got_ids if i not in exp_ids]
    dup = sorted({i for i in got_ids if got_ids.count(i) > 1})
    complete = not missing and not extra and not dup
    r["options_complete"] = (complete, "ok" if complete else f"missing {missing}, extra {extra}, duplicates {dup}"
                             if (missing or extra or dup) else "ok")
    if not complete:
        r["ranking_order"] = (False, "skipped: option set differs")
    else:
        r["ranking_order"] = (got_ids == exp_ids, "ok" if got_ids == exp_ids else f"got {got_ids}, expected {exp_ids}")

    bad_ev = [f"rank {o['rank']}: {d}" for o in opts for ok, d in (resolve(e, inp) for e in o["evidence"]) if not ok]
    r["evidence_resolves"] = (not bad_ev, "; ".join(bad_ev) or (f"{sum(len(o['evidence']) for o in opts)} entries ok" if opts else "no options"))

    def ev_min(o):
        po, it = o["proposed_action"]["po_id"], (o["proposed_action"]["change"].get("item_id") or item)
        return [f"missing evidence {n['source']}/{n['entity']}/{n['field']}" + (f"@{n.get('location_id') or n.get('item_id')}" if n.get('location_id') or n.get('item_id') else "")
                for n in minimum_evidence(o, po, it) if not any(_ev_matches(e, n, it) for e in o["evidence"])]
    r["evidence_minimum"] = per_option(ev_min)

    def allowed_numbers(o):
        nums, dates = _evidence_values(o)
        nums |= _evidence_sums(o)
        ch = o["proposed_action"]["change"]
        if ch.get("qty") is not None:
            nums.add(Decimal(ch["qty"]))
        if o["estimated_lead_time_days"] is not None:
            nums.add(Decimal(o["estimated_lead_time_days"]))
        if o["estimated_cost_delta"]:
            nums.add(_dec(o["estimated_cost_delta"]["amount"]))
        if limit is not None:
            nums.add(_dec(limit))
        if o["option_type"] in SUPPLIER_TYPES and item:
            row = _alt_row(inp, o, item)
            if row:
                nums.add(_dec(_money_amount(row["unit_price"]) - pp))
                if o["option_type"] == "SPLIT_ORDER" and ch.get("qty") is not None:
                    nums.add(Decimal(q - ch["qty"]))
        if o["option_type"] == "INVENTORY_TRANSFER":
            frm = _stock_row(inp, ch.get("item_id"), ch.get("from_location_id"))
            if frm:
                nums.add(Decimal(_spare(frm)))
            if q is not None and ch.get("qty") is not None:
                nums.add(Decimal(q - ch["qty"]))  # the part the transfer does not cover
        if o["option_type"] == "REVISED_SCHEDULE" and q is not None:
            nums.add(Decimal(q))  # the PO quantity the schedule change applies to
        if ch.get("expected_date"):
            dates.add(ch["expected_date"])
        return {n for n in nums if n is not None}, dates

    def claims(o):
        nums, dates = allowed_numbers(o)
        ch, typ = o["proposed_action"]["change"], o["option_type"]
        qty, lead = ch.get("qty"), o["estimated_lead_time_days"]
        row = _alt_row(inp, o, item) if item and typ in SUPPLIER_TYPES else None
        cap = row["capacity"] if row else None
        frm = _stock_row(inp, ch.get("item_id"), ch.get("from_location_id")) if typ == "INVENTORY_TRANSFER" else None
        rest = (q - qty) if q is not None and qty is not None else None
        sup = ch.get("supplier_id")
        p = []
        sections = [("step", STEP_NUMBER_RE.sub("", t)) for t in o["action_steps"]] + [("trade", t) for t in o["trade_offs"]]
        for kind, t in sections:
            tn, td = extract(t, year, names)
            bad = [str(n) for n in tn if not _matches(n, nums)] + [d for d in td if d not in dates]
            # money: exact value, no rounding to the written precision
            bad += [f"money {m}" for m in MONEY_RE.findall(t) if _dec(m) not in nums]
            # quantities ("<n> units", "<n> of ITM-..", "order <n>"): tied to this option's qty / Q / capacity / spare stock
            found = list(UNITS_RE.findall(t))
            for rx in QTY_CONTEXT_RES:
                found += rx.findall(t)
            for m in dict.fromkeys(found):
                n = _dec(m)
                if kind == "step":
                    ok = n == qty or (typ in ("SPLIT_ORDER", "INVENTORY_TRANSFER") and n == rest) \
                        or (typ == "REVISED_SCHEDULE" and n == q)
                    if q is not None and n > q:
                        ok = False
                else:
                    ok = n in {x for x in (_dec(qty) if qty is not None else None, _dec(q) if q is not None else None,
                                           _dec(rest) if rest is not None else None,
                                           _dec(cap) if cap is not None else None,
                                           _dec(_spare(frm)) if frm else None) if x is not None}
                if not ok:
                    bad.append(f"quantity {m} units")
            # lead times ("<n> days"): a supplier / lead-time sentence must carry this option's lead time
            for s in _sentences(t):
                for dm in DELAY_DAYS_RE.findall(s) + DELAY_OF_RE.findall(s):  # "<n>-day delay" / "delay of <n> days"
                    if tgt and _dec(dm) != _dec(tgt["estimated_delay_days"]):
                        bad.append(f"{dm}-day delay (impact says {tgt['estimated_delay_days']})")
                rest_s = DELAY_OF_RE.sub(" ", DELAY_DAYS_RE.sub(" ", s))
                # in a supplier option every other duration is that supplier's lead time, named or not
                supplier_sentence = bool(typ in SUPPLIER_TYPES or (sup and sup in s)
                                         or re.search(r"lead time|deliver", s, re.I))
                allowed = {_dec(lead)} if lead is not None else set()
                if not supplier_sentence and tgt:
                    allowed.add(_dec(tgt["estimated_delay_days"]))
                for dm in DAYS_RE.findall(rest_s):
                    if _dec(dm) not in allowed:
                        bad.append(f"{dm} days (lead time is {lead})")
            if bad:
                p.append(f"'{t[:50]}' has unbacked {bad}")
        return p
    r["claims_numbers_backed"] = per_option(claims)

    def assumptions(o):
        p = []
        row = _alt_row(inp, o, item) if item and o["option_type"] in SUPPLIER_TYPES else None
        for t in o["assumptions"]:
            tn, td = extract(t, year, names)
            if tn or td:
                p.append(f"'{t[:50]}' contains numbers/dates {[str(n) for n in tn] + td}")
            if row and row["capacity"] is None and re.search(r"capacity", t, re.I) and not re.search(r"confirm", t, re.I):
                p.append(f"'{t[:50]}' speaks of capacity, which is unknown, without 'confirm'")
        return p
    r["assumptions_qualitative"] = per_option(assumptions)

    def hedge_hits(text, expected_date=None):
        hits = []
        for sent in _sentences(text):
            negated = bool(NEGATION_WORD_RE.search(sent))
            for m in HEDGE_RE.finditer(sent):
                w = m.group(0).lower()
                if w in SENTENCE_NEGATED_OK and negated:
                    continue
                if w == "expected to" and expected_date and expected_date in sent:
                    continue  # "PO-1001 is now expected to arrive on <the option's own new date>"
                hits.append(m.group(0))
            if not negated:
                hits += [m.group(0) for m in GUESS_MODAL_RE.finditer(sent)]
                hits += [m.group(0) for m in GUESS_COMPARATIVE_RE.finditer(sent)]
        return hits

    hedges = [f"'{h}' in '{t[:50]}'" for o in opts
              for t in [*o["action_steps"], *o["trade_offs"], *o["assumptions"]]
              for h in hedge_hits(t, o["proposed_action"]["change"].get("expected_date"))] + \
             [f"'{h}' in reason" for h in hedge_hits(reason or "")]
    r["no_hedged_guess"] = (not hedges, f"hedged/guessed wording {hedges}" if hedges else "ok")

    def scope(o):
        ch, p = o["proposed_action"]["change"], []
        locs = {ch.get("from_location_id"), ch.get("to_location_id")} - {None}
        for e in o["evidence"]:
            ent, it, src = e["entity"], e.get("item_id"), e["source"]
            if src == "EVENT":
                ok = ent == inp["event"]["event_id"]
            elif src == "IMPACT":
                ok = bool(tgt) and ent == tgt["po_id"] and it in (None, item)
            else:
                ok = ((bool(tgt) and ent == tgt["po_id"] and it in (None, item))
                      or (o["option_type"] in SUPPLIER_TYPES and ent == ch.get("supplier_id") and it in (None, item))
                      or (o["option_type"] == "INVENTORY_TRANSFER" and ent == item and e.get("location_id") in locs)
                      or (o["option_type"] != "INVENTORY_TRANSFER" and ent == item and e.get("location_id") is not None)
                      or ent == snap["supplier"]["supplier_id"])
            if not ok:
                p.append(f"evidence {src}/{ent}/{e['field']} is outside this option's scope")
        return p
    r["evidence_in_scope"] = per_option(scope)

    words = [m.group(0) for t in _all_texts(out) for m in BIG_NUMBER_WORDS.finditer(t)]
    r["numbers_as_digits"] = (not words, f"spelled-out numbers {words}" if words else "ok")

    known = {v for k, v in _leaves({k: v for k, v in inp.items() if k != "risk_analysis"}) if isinstance(v, str)}
    known.add(inp["risk_case_id"])
    unknown = []
    for o in opts:
        toks = [t for s in [*_texts(o), *_change_strings(o), *(x for e in o["evidence"] for x in (e["entity"], e.get("item_id") or "", e.get("location_id") or ""))]
                for t in ID_RE.findall(s)]
        unknown += [f"rank {o['rank']}: {t}" for t in dict.fromkeys(toks) if t not in known]
    unknown += [f"reason: {t}" for t in dict.fromkeys(ID_RE.findall(reason or "")) if t not in known]
    r["no_unknown_ids"] = (not unknown, f"ids not in the input: {unknown}" if unknown else "ok")

    own = snap["supplier"]["name"].lower()
    firms = [n for t in _all_texts(out) for n in COMPANY_RE.findall(t) if n.lower() != own]
    r["no_invented_companies"] = (not firms, f"company names not in snapshot {firms}" if firms else "ok")
    unlisted = _unlisted_names(out, inp)
    r["no_unlisted_names"] = (not unlisted, f"names not in the input {unlisted}" if unlisted else "ok")

    def gaps(o):
        lines = [g.lower() for g in o["data_gaps"]]
        ch, p = o["proposed_action"]["change"], []

        def flags_with(token):
            return [f for f in inp["data_quality_flags"] if token in f]
        need = []
        if o["option_type"] in SUPPLIER_TYPES:
            need += flags_with(ch.get("supplier_id", "\0"))
            row = _alt_row(inp, o, item) if item else None
            if row and row["capacity"] is None and not any(ch["supplier_id"].lower() in g and "capacity" in g for g in lines):
                p.append(f"no data_gaps line naming {ch['supplier_id']} and capacity")
            if row and line and _cost_delta(row, line, ch.get("qty") or 1) is None and not any("currenc" in g for g in lines):
                p.append("cost delta is null (currencies differ) but no data_gaps line mentions currency")
        elif o["option_type"] == "REVISED_SCHEDULE":
            need += flags_with(ev_sup)
        else:
            if not any(re.search(r"transit|lead time", g) for g in lines):
                p.append("no transit/lead time gap line")
            if not any("cost" in g for g in lines):
                p.append("no cost gap line")
        p += [f"flag not in data_gaps: '{f}'" for f in need if not any(f.lower() in g for g in lines)]
        return p
    r["data_gaps_complete"] = per_option(gaps)

    def named_others(o):
        blob = " ".join(o["trade_offs"])
        return [f"trade_offs do not name {po}" for po in others if po not in set(ID_RE.findall(blob))]
    r["other_affected_pos_named"] = per_option(named_others)

    r["reason_grounded"] = _reason_grounded(out, inp, tgt, names, year)
    inj = [t[:60] for t in _all_texts(out) if INJECTION_NOTE_RE.search(t)]
    r["no_injection_note"] = (not inj, f"injection note written: {inj}" if inj else "ok")

    if case:
        r.update(_case_checks(out, inp, case, own))
    return r


def _unlisted_names(out: dict, inp: dict) -> list[str]:
    """Capitalised name phrases / company words in any text field that the input does not contain."""
    listed = [v for k, v in _leaves({k: v for k, v in inp.items() if k != "risk_analysis"})
              if k in LISTED_NAME_KEYS and isinstance(v, str)]
    own = inp["snapshot"]["supplier"]["name"].lower()
    hits = []
    for t in _all_texts(out):
        hits += [m.group(0) for m in NAME_PHRASE_RE.finditer(t)
                 if not all(w in BUSINESS_WORDS for w in re.findall(r"[A-Z][a-z]+", m.group(0)))
                 and not any(m.group(0) in v for v in listed)]
        hits += [m.group(1) for m in SINGLE_NAME_RE.finditer(t)
                 if m.group(1) not in BUSINESS_WORDS and m.group(1) not in PRONOUNS
                 and not any(m.group(1) in v for v in listed)]
        hits += [m.group(0) for m in COMPANY_WORD_RE.finditer(t) if m.group(0).lower() not in own]
    return list(dict.fromkeys(hits))


def _reason_grounded(out, inp, tgt, names, year) -> tuple[bool, str]:
    reason = out["no_viable_option_reason"]
    if out["options"]:
        return True, "options present"
    p = []
    if tgt and tgt["po_id"] not in reason:
        p.append(f"does not name target PO {tgt['po_id']}")
    s_nums, s_dates = input_support(inp)
    tn, td = extract(reason, year, names)
    bad = [str(n) for n in tn if not _matches(n, s_nums)] + [d for d in td if d not in s_dates]
    if bad:
        p.append(f"numbers/dates not in input {bad}")
    sups = [s for s in ID_RE.findall(reason) if s.startswith("SUP-") and s != inp["event"]["supplier_id"]]
    if sups:
        p.append(f"names supplier ids {sups}")
    if tgt:
        rows = [a for a in _alt_rows(inp, tgt["item_id"]) if a["qualified"] is True]
        if not rows and not re.search("alternate", reason, re.I):
            p.append("does not say there is no alternate supplier")
    return not p, "; ".join(p) or "ok"


def _case_checks(out: dict, inp: dict, case: dict, own: str) -> dict[str, tuple[bool, str]]:
    r, exp, opts = {}, case["expect"], out["options"]
    reason = out["no_viable_option_reason"]
    if "must_not_match" in exp:
        blob = " | ".join(t for o in opts for t in [*o["action_steps"], *o["trade_offs"], *o["assumptions"]]
                          ) + " | " + (reason or "")
        hits = [p for p in exp["must_not_match"] if re.search(p, blob, re.I)]
        r["case_must_not_match"] = (not hits, f"matched forbidden {hits}" if hits else "ok")
    if "data_gaps_must_match" in exp:
        text = " | ".join(g for o in opts for g in o["data_gaps"]) if opts else (reason or "")
        miss = [p for p in exp["data_gaps_must_match"] if not re.search(p, text, re.I)]
        r["case_data_gaps"] = (not miss, f"data_gaps lack {miss}" if miss else "ok")
    if exp.get("options_empty"):
        r["case_options_empty"] = (not opts, f"{len(opts)} options")
    if "reason_must_match" in exp:
        miss = [p for p in exp["reason_must_match"] if not re.search(p, reason or "", re.I)]
        r["case_reason"] = (not miss, f"reason lacks {miss}" if miss else "ok")
    if "max_qty" in exp:
        bad = [f"rank {o['rank']} qty {o['proposed_action']['change'].get('qty')} > {exp['max_qty'][s]}"
               for o in opts for s in exp["max_qty"]
               if o["proposed_action"]["change"].get("supplier_id") == s
               and (o["proposed_action"]["change"].get("qty") or 0) > exp["max_qty"][s]]
        r["case_max_qty"] = (not bad, "; ".join(bad) or "ok")
    if "lead_time" in exp:
        bad = [f"rank {o['rank']} lead {o['estimated_lead_time_days']} != {exp['lead_time'][s]}"
               for o in opts for s in exp["lead_time"]
               if o["proposed_action"]["change"].get("supplier_id") == s
               and o["estimated_lead_time_days"] != exp["lead_time"][s]]
        r["case_lead_time"] = (not bad, "; ".join(bad) or "ok")
    if "forbidden_supplier_ids" in exp:
        hay = " | ".join([*_all_texts(out)] + [json.dumps(o["proposed_action"]["change"]) for o in opts]
                         + [e["entity"] for o in opts for e in o["evidence"]])
        hits = [s for s in exp["forbidden_supplier_ids"] if s in hay]
        r["case_forbidden_supplier_ids"] = (not hits, f"forbidden ids present {hits}" if hits else "ok")
    if exp.get("only_snapshot_company_names"):
        firms = [n for t in _all_texts(out) for n in COMPANY_RE.findall(t) if n.lower() != own]
        firms += _unlisted_names(out, inp)
        r["case_no_invented_companies"] = (not firms, f"company names not in snapshot {firms}" if firms else "ok")
    return r


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("output", type=Path, nargs="?")
    ap.add_argument("--scenario", default="demo")
    ap.add_argument("--case", help="adversarial case id, e.g. RP-ADV-01 (overrides --scenario)")
    ap.add_argument("--payload", help="print the Structured-mode payload for DEMO|TC05|RP-ADV-01..04 and exit")
    args = ap.parse_args()
    if args.payload:
        from ai.eval.payload import payload
        pid = args.payload.upper()
        pid = pid if pid.startswith("RP-") else "RP-" + pid  # always the Recovery Planner payload
        print(json.dumps(payload(pid), separators=(",", ":"), ensure_ascii=False))
        return
    if not args.output:
        ap.error("output file required")
    out = json.loads(args.output.read_text(encoding="utf-8"))
    case = next(c for c in load_cases() if c["id"] == args.case) if args.case else None
    inp = recovery_input(case if case else SCENARIO_ALIASES.get(args.scenario.upper(), args.scenario))
    result = grade(out, inp, case)
    for name, (ok, detail) in result.items():
        print(f"{'PASS' if ok else 'FAIL'}  {name:30} {detail}")
    sys.exit(0 if passed(result) else 1)


if __name__ == "__main__":
    main()
