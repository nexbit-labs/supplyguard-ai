"""N-04 tests: spec table S1-S7 (real seed + scenarios) and U1-U37 (inline dicts)."""

from __future__ import annotations

import copy
import json
import sys
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from apps.intelligence import impact as im
from apps.intelligence.impact import (
    DisruptionWindow,
    SnapshotError,
    TenantThresholds,
    compute_impact,
    to_agent_impact,
)

ROOT = Path(__file__).resolve().parents[4]
DATA = ROOT / "data"
sys.path.insert(0, str(ROOT))  # `ai` package lives at the repo root
D = Decimal


def _load(p):
    return json.loads(p.read_text(encoding="utf-8"))


SEED = {
    n: _load(DATA / "seed" / f"{n}.json")
    for n in ("suppliers", "purchase_orders", "inventory", "alternate_suppliers", "tenants")
}


def _page(rs):
    return {"count": len(rs), "results": rs}


def snapshot_for(event):
    tid, sid = event["tenant_id"], event["supplier_id"]
    sup = next(
        (s for s in SEED["suppliers"] if s["supplier_id"] == sid and s["tenant_id"] == tid), None
    )
    pos = [p for p in SEED["purchase_orders"] if p["supplier_id"] == sid and p["tenant_id"] == tid]
    items = {ln["item_id"] for p in pos for ln in p["lines"]}
    inv = [r for r in SEED["inventory"] if r["item_id"] in items and r["tenant_id"] == tid]
    alt = [
        r for r in SEED["alternate_suppliers"] if r["item_id"] in items and r["tenant_id"] == tid
    ]
    return {
        "supplier": sup,
        "purchase_orders": _page(pos),
        "inventory": _page(inv),
        "alternate_suppliers": _page(alt),
    }


def run_scenario(name):
    sc = _load(DATA / "scenarios" / f"{name}.json")
    tenant = next(t for t in SEED["tenants"] if t["tenant_id"] == sc["event"]["tenant_id"])
    return sc, compute_impact(
        sc["event"], snapshot_for(sc["event"]), im.thresholds_from_tenant(tenant)
    )


# ------------------------------------------------------------------ S1-S7
def test_s1_demo():
    sc, r = run_scenario("demo")
    e = sc["expected"]
    assert r["evaluated_po_ids"] == e["evaluated_po_ids"] == ["PO-1001", "PO-1002", "PO-1003"]
    assert r["affected_po_ids"] == e["affected_po_ids"]
    assert r["total_exposure"] == e["exposure"] == {"amount": "491400.00", "currency": "INR"}
    assert r["affected_item_ids"] == ["ITM-501"] and r["shortage"] is True
    assert r["qualified_alternate_supplier_ids"] == e["qualified_alternate_supplier_ids"]
    assert [p["estimated_delay_days"] for p in r["affected_pos"]] == [2, 1]
    assert (r["risk_score"], r["risk_level"]) == (67, "HIGH")
    cov = {c["location_id"]: c for c in r["inventory_coverage"]}
    assert cov["DC-PUNE"]["coverage_days"] == 1.0 and cov["DC-PUNE"]["coverage_days_gross"] == 1.5
    assert (
        cov["DC-CHENNAI"]["coverage_days"] == 3.33
        and cov["DC-CHENNAI"]["transfer_surplus_qty"] == 200
    )
    assert r["items"][0]["shortage_gap_ratio"] == 0.5 and r["items"][0]["mitigation_level"] == 0.25
    assert r["score_components"]["exposure"] == {"weight": 20, "value": "0.9828", "points": "19.66"}
    assert r["data_quality_flags"] == [
        "supplier capacity not provided",
        "capacity unknown for alternate SUP-2002 (ITM-501)",
    ]


def test_s2_tc03():
    sc, r = run_scenario("tc03_multiple_open_pos")
    assert len(r["evaluated_po_ids"]) == 6 and len(r["affected_po_ids"]) == 5
    assert "PO-1012" not in r["affected_po_ids"]
    assert r["total_exposure"]["amount"] == "537649.00" == sc["expected"]["exposure"]["amount"]
    assert len(r["affected_item_ids"]) == 4
    assert (r["risk_score"], r["risk_level"]) == (70, "HIGH")


def test_s3_tc04_no_shortage():
    _, r = run_scenario("tc04_inventory_sufficient")
    assert r["shortage"] is False and not any(p["shortage"] for p in r["affected_pos"])
    assert all(p["inventory_coverage_days"] == 42.5 for p in r["affected_pos"])
    assert r["total_exposure"]["amount"] == "1468775.00"
    assert (r["risk_score"], r["risk_level"]) == (37, "LOW")  # raw 36.5 -> half-up


def test_s4_tc05_no_alternates():
    _, r = run_scenario("tc05_no_alternates")
    assert r["qualified_alternate_supplier_ids"] == []
    assert r["score_components"]["mitigation"]["value"] == "1.0000"
    assert r["shortage"] is True and r["total_exposure"]["amount"] == "474384.00"
    assert (r["risk_score"], r["risk_level"]) == (91, "CRITICAL")
    assert "supplier capacity not provided" in r["data_quality_flags"]


def test_s5_tc01():
    _, r = run_scenario("tc01_valid_event")
    assert r["affected_po_ids"] == ["PO-1005"] and r["total_exposure"]["amount"] == "310312.00"
    assert r["shortage"] is True and (r["risk_score"], r["risk_level"]) == (48, "MEDIUM")


def test_s6_unknown_supplier():
    sc = _load(DATA / "scenarios" / "tc02_unknown_supplier.json")
    r = compute_impact(sc["event"], snapshot_for(sc["event"]))
    assert r["supplier_found"] is False and r["affected_pos"] == [] and r["items"] == []
    assert (r["risk_score"], r["risk_level"]) == (0, "LOW")
    assert r["data_quality_flags"] == ["supplier not found in ERP"]
    assert r["total_exposure"] == {"amount": "0.00", "currency": "INR"}


@pytest.mark.parametrize(
    "name",
    [
        "demo",
        "tc03_multiple_open_pos",
        "tc04_inventory_sufficient",
        "tc05_no_alternates",
        "tc01_valid_event",
    ],
)
def test_s7_agent_block_valid(name):
    from ai.schema_registry import _registry, load_schema
    from jsonschema import Draft202012Validator

    _, r = run_scenario(name)
    block = to_agent_impact(r)
    ref = {"$ref": load_schema("agent_input")["$id"] + "#/$defs/impact"}
    v = Draft202012Validator(
        ref, registry=_registry(), format_checker=Draft202012Validator.FORMAT_CHECKER
    )
    assert not list(v.iter_errors(block))
    assert all(len(row) == 7 for row in block["affected_pos"])


def test_s7_negative_control():
    from ai.schema_registry import _registry, load_schema
    from jsonschema import Draft202012Validator

    ref = {"$ref": load_schema("agent_input")["$id"] + "#/$defs/impact"}
    v = Draft202012Validator(ref, registry=_registry())
    assert list(v.iter_errors({"risk_score": 500, "affected_pos": [], "total_exposure": {}}))


def test_other_scenarios_do_not_crash():
    for name in ("tc06_reject", "tc07_approve", "tc08_erp_timeout", "tc09_duplicate_event"):
        sc = _load(DATA / "scenarios" / f"{name}.json")
        if "event" in sc:
            compute_impact(sc["event"], snapshot_for(sc["event"]))


# ------------------------------------------------------------------ helpers for unit tests
EV = {
    "tenant_id": "T",
    "supplier_id": "S1",
    "event_type": "FACTORY_OUTAGE",
    "severity": "HIGH",
    "event_time": "2026-12-02T10:00:00Z",
    "expected_duration_hours": 48,
}


def line(no=1, item="ITM-A", qty=10, due="2026-12-03", price="1.00", cur="INR"):
    return {
        "line_no": no,
        "item_id": item,
        "qty": qty,
        "due_date": due,
        "unit_price": {"amount": price, "currency": cur},
    }


def po(pid="PO-1", lines=None, status="OPEN", sup="S1", tenant="T"):
    return {
        "po_id": pid,
        "tenant_id": tenant,
        "supplier_id": sup,
        "status": status,
        "lines": lines if lines is not None else [line()],
    }


def inv(item="ITM-A", loc="DC1", on_hand=1000, reserved=0, safety=0, demand=100):
    r = {
        "tenant_id": "T",
        "item_id": item,
        "location_id": loc,
        "on_hand": on_hand,
        "reserved": reserved,
        "safety_stock": safety,
    }
    if demand != "absent":
        r["daily_demand"] = demand
    return r


def alt(sup="S9", item="ITM-A", qualified=True, cap=1000):
    return {
        "tenant_id": "T",
        "item_id": item,
        "supplier_id": sup,
        "qualified": qualified,
        "capacity": cap,
    }


def snap(pos=None, invs=None, alts=None, supplier="ok"):
    s = {"supplier_id": "S1", "capacity_per_week": 1000} if supplier == "ok" else supplier
    return {
        "supplier": s,
        "purchase_orders": pos if pos is not None else [po()],
        "inventory": invs if invs is not None else [inv()],
        "alternate_suppliers": alts or [],
    }


def win(h=48, start="2026-12-02T10:00:00Z"):
    return im.parse_window({**EV, "event_time": start, "expected_duration_hours": h})


# ------------------------------------------------------------------ U1-U5
def test_u1_rows():
    assert im.rows({"count": 1, "results": [{"a": 1}]}) == [{"a": 1}]
    assert im.rows([{"a": 1}]) == [{"a": 1}] and im.rows(None) == [] and im.rows({"count": 0}) == []


def test_u2_status_filter():
    pos = [po("PO-1"), po("PO-2", status="RECEIVED"), po("PO-3", status="CANCELLED")]
    assert [p["po_id"] for p in im.select_evaluated_pos(EV, pos)] == ["PO-1"]


def test_u3_other_supplier():
    assert im.select_evaluated_pos(EV, [po(sup="SUP-9")]) == []


def test_u4_po_id_narrowing_and_sort():
    pos = [po("PO-10"), po("PO-9")]
    assert [p["po_id"] for p in im.select_evaluated_pos(EV, pos)] == ["PO-9", "PO-10"]
    assert [p["po_id"] for p in im.select_evaluated_pos({**EV, "po_id": "PO-10"}, pos)] == ["PO-10"]


def test_u5_tenant_mismatch():
    with pytest.raises(SnapshotError):
        im.select_evaluated_pos(EV, [po(tenant="TEN-GLOBEX")])
    for key, bad in (
        ("inventory", [{**inv(), "tenant_id": "X"}]),
        ("alternate_suppliers", [{**alt(), "tenant_id": "X"}]),
    ):
        with pytest.raises(SnapshotError):
            compute_impact(EV, {**snap(), key: bad})
    with pytest.raises(SnapshotError):
        compute_impact(EV, snap(supplier={"supplier_id": "S1", "tenant_id": "X"}))
    # missing tenant on rows / event is tolerated
    p = po()
    del p["tenant_id"]
    assert compute_impact({k: v for k, v in EV.items() if k != "tenant_id"}, snap(pos=[p]))


# ------------------------------------------------------------------ U6-U18 lines
def affected(lines, h=48, buf=0, start="2026-12-02T10:00:00Z"):
    return im.select_affected_lines([po(lines=lines)], win(h, start), buf)


def test_u6_window_boundary():
    assert len(affected([line(due="2026-12-04")])) == 1
    assert affected([line(due="2026-12-05")]) == []


def test_u7_overdue():
    a = affected([line(due="2026-11-27")], h=24)
    assert len(a) == 1 and a[0].delay_days == 7


def test_u8_delay_min_1():
    a = affected([line(due="2026-12-03")], h=24, start="2026-12-02T00:00:00Z")
    assert a[0].delay_days == 1


def test_u9_buffer():
    assert [x.delay_days for x in affected([line(), line(2, due="2026-12-04")], buf=2)] == [4, 3]


def test_u10_no_open_pos():
    r = compute_impact(EV, snap(pos=[]), TenantThresholds(currency="USD"))
    assert r["affected_pos"] == [] and r["total_exposure"] == {"amount": "0.00", "currency": "USD"}
    assert r["risk_score"] == 8 and r["risk_level"] == "LOW"  # 7.5 -> 8, severity only


def test_u11_u12_u13_multiline():
    two = [line(1, "ITM-A", 10, "2026-12-03", "1.00"), line(2, "ITM-A", 5, "2026-12-04", "2.00")]
    r = compute_impact(EV, snap(pos=[po(lines=two)]))
    (row,) = r["affected_pos"]
    assert (row["affected_qty"], row["exposure_amount"]["amount"], row["line_nos"]) == (
        15,
        "20.00",
        [1, 2],
    )
    assert row["estimated_delay_days"] == 2 and row["due_date"] == "2026-12-03"
    r = compute_impact(
        EV,
        snap(
            pos=[po(lines=[line(1, "ITM-A"), line(2, "ITM-B")])], invs=[inv("ITM-A"), inv("ITM-B")]
        ),
    )
    assert [(x["po_id"], x["item_id"]) for x in r["affected_pos"]] == [
        ("PO-1", "ITM-A"),
        ("PO-1", "ITM-B"),
    ]
    r = compute_impact(EV, snap(pos=[po(lines=[line(1), line(2, due="2026-12-30")])]))
    assert r["affected_pos"][0]["line_nos"] == [1] and r["affected_pos"][0]["affected_qty"] == 10


def test_u14_zero_price():
    r = compute_impact(EV, snap(pos=[po(lines=[line(price="0.00")])]))
    assert r["total_exposure"]["amount"] == "0.00" and r["affected_po_ids"] == ["PO-1"]
    assert "unit_price is zero for PO-1 line 1" in r["data_quality_flags"]


def test_u15_qty_zero():
    assert affected([line(qty=0)]) == []


@pytest.mark.parametrize("q", [-1, True, "5", 2.5, None])
def test_u16_bad_qty(q):
    with pytest.raises(SnapshotError):
        affected([line(qty=q)])


def test_u16b_missing_price():
    bad = line()
    del bad["unit_price"]
    with pytest.raises(SnapshotError):
        affected([bad])


def test_u17_mixed_currency():
    with pytest.raises(SnapshotError):
        affected([line(1), line(2, item="ITM-B", cur="USD")])


def test_u18_money_rounding():
    r = compute_impact(EV, snap(pos=[po(lines=[line(qty=3, price="0.335")])]))
    assert r["total_exposure"]["amount"] == "1.01"
    assert im.money(D("25555.5"), "INR")["amount"] == "25555.50"


# ------------------------------------------------------------------ U19-U25 inventory
def test_u19_multiple_locations():
    rws = im.inventory_coverage(
        "ITM-A", [inv(loc="DC2", on_hand=100), inv(loc="DC1", on_hand=500)], D(2)
    )
    assert [r["location_id"] for r in rws] == ["DC1", "DC2"]
    assert im.item_min_coverage(rws) == D(1)
    assert im.shortage_gap_ratio(D(1), True, D(2)) == D("0.5")
    assert rws[0]["transfer_surplus_qty"] == 300 and rws[1]["transfer_surplus_qty"] == 0


def test_u20_missing_demand():
    for demand in ("absent", None):
        r = compute_impact(EV, snap(invs=[inv(demand=demand)]))
        assert r["inventory_coverage"][0]["coverage_days"] is None
        assert "daily_demand missing for ITM-A at DC1" in r["data_quality_flags"]
        assert r["shortage"] is False and r["score_components"]["shortage"]["value"] == "0.2500"
    assert im.item_min_coverage([]) is None
    assert im.shortage_gap_ratio(None, True, D(2)) is None


def test_u21_zero_demand():
    r = compute_impact(EV, snap(invs=[inv(demand=0)]))
    assert r["inventory_coverage"][0]["coverage_days"] is None
    assert not any("daily_demand" in f for f in r["data_quality_flags"])


def test_u22_negative_available():
    r = compute_impact(EV, snap(invs=[inv(on_hand=100, safety=300)]))
    c = r["inventory_coverage"][0]
    assert c["available_qty"] == -200 and c["coverage_days"] == 0.0 and r["shortage"] is True


def test_u23_no_inventory_rows():
    r = compute_impact(EV, snap(invs=[]))
    assert r["shortage"] is True and r["items"][0]["shortage_gap_ratio"] == 1.0
    assert "no inventory record for ITM-A" in r["data_quality_flags"]


def test_u24_outage_zero():
    two = [line(due="2026-12-02"), line(2, due="2026-12-03")]
    r = compute_impact({**EV, "expected_duration_hours": 0}, snap(pos=[po(lines=two)]))
    assert r["window"]["outage_days"] == 0.0 and r["shortage"] is False
    assert r["affected_pos"][0]["line_nos"] == [1]


def test_u25_supplier_capacity_flag():
    flag = "supplier capacity not provided"
    none_cap = compute_impact(EV, snap(supplier={"supplier_id": "S1", "capacity_per_week": None}))
    assert flag in none_cap["data_quality_flags"]
    assert flag in compute_impact(EV, snap(supplier={"supplier_id": "S1"}))["data_quality_flags"]
    assert flag not in compute_impact(EV, snap())["data_quality_flags"]


# ------------------------------------------------------------------ U26-U28 mitigation
@pytest.mark.parametrize(
    "alts,req,exp",
    [
        ([alt(cap=500)], True, "0"),
        ([alt(cap=100)], True, "0.25"),
        ([alt(cap=None)], True, "0.5"),
        ([], True, "1"),
        ([alt(qualified=False)], True, "1"),
        ([alt(qualified=False, cap=500)], False, "0"),
        ([alt(qualified=False, cap=100)], False, "0.25"),
        ([alt(cap=0)], True, "0.5"),
    ],
)
def test_u26_mitigation(alts, req, exp):
    assert im.mitigation_level("ITM-A", 400, alts, req) == D(exp)


def test_u27_u28_disrupted_excluded_and_flag():
    alts = [alt(sup="S1"), alt(sup="S2", cap=None), alt(sup="S3", item="ITM-Z")]
    r = compute_impact(EV, snap(alts=alts))
    assert r["qualified_alternate_supplier_ids"] == ["S2"]
    assert r["data_quality_flags"] == ["capacity unknown for alternate S2 (ITM-A)"]


# ------------------------------------------------------------------ U29-U32 score
def comps(**kw):
    base = {
        "item_gaps": {},
        "max_delay_days": 0,
        "total_exposure": D(0),
        "mitigation": {},
        "severity": "LOW",
        "t": TenantThresholds(),
    }
    return im.score_components(**{**base, **kw})


def test_u29_severity():
    vals = [comps(severity=s)["severity"] for s in ("LOW", "MEDIUM", "HIGH", "CRITICAL")]
    assert vals == [D("0.25"), D("0.5"), D("0.75"), D(1)]
    with pytest.raises(ValueError):
        comps(severity="FOO")


def test_u30_clamping():
    c = comps(total_exposure=D(1000000), max_delay_days=20)
    assert c["exposure"] == 1 and c["delay"] == 1


def _only_shortage(raw):
    return {
        "shortage": raw / 40,
        "exposure": D(0),
        "mitigation": D(0),
        "delay": D(0),
        "severity": D(0),
    }


def test_u31_rounding():
    assert im.risk_score(_only_shortage(D("36.5"))) == 37
    assert im.risk_score(_only_shortage(D("36.49"))) == 36
    assert im.risk_score(_only_shortage(D("400"))) == 100
    assert im.risk_score(_only_shortage(D("-5"))) == 0


def test_u32_bands():
    t = TenantThresholds()
    assert [im.severity_band(s, t) for s in (39, 40, 64, 65, 84, 85)] == [
        "LOW",
        "MEDIUM",
        "MEDIUM",
        "HIGH",
        "HIGH",
        "CRITICAL",
    ]
    assert im.severity_band(10, TenantThresholds(medium=5, high=8, critical=9)) == "CRITICAL"


def test_shortage_component_uses_max_gap_and_unknown():
    assert comps(item_gaps={"a": D("0.2"), "b": D("0.6"), "c": None})["shortage"] == D("0.8")
    assert comps(item_gaps={"a": D(0), "b": None})["shortage"] == D("0.25")
    assert comps(item_gaps={"a": D(0)})["shortage"] == 0


# ------------------------------------------------------------------ U33-U37
def test_u33_thresholds():
    acme = next(t for t in SEED["tenants"] if t["tenant_id"] == "TEN-ACME")
    t = im.thresholds_from_tenant(acme)
    assert (t.medium, t.high, t.critical, t.currency, t.exposure_full_scale) == (
        40,
        65,
        85,
        "INR",
        D(500000),
    )
    assert im.thresholds_from_tenant(None) == TenantThresholds()
    t = im.thresholds_from_tenant(
        {
            "currency": "USD",
            "settings": {
                "risk_thresholds": {"high": 70, "exposure_full_scale": 20000},
                "delay_full_scale_days": 3,
                "recovery_buffer_days": 1,
                "require_qualified_alternates": False,
            },
        }
    )
    assert (
        t.medium,
        t.high,
        t.exposure_full_scale,
        t.delay_full_scale_days,
        t.recovery_buffer_days,
        t.require_qualified_alternates,
        t.currency,
    ) == (40, 70, D(20000), 3, 1, False, "USD")
    assert (
        im.thresholds_from_tenant({"settings": {"exposure_full_scale": 9}}).exposure_full_scale == 9
    )
    for bad in (
        {"risk_thresholds": {"medium": 70, "high": 60}},
        {"risk_thresholds": {"exposure_full_scale": 0}},
        {"delay_full_scale_days": 0},
        {"recovery_buffer_days": -1},
    ):
        with pytest.raises(ValueError):
            im.thresholds_from_tenant({"settings": bad})


def test_tenant_exposure_scale_changes_score():
    pos = [po(lines=[line(price="1000")])]
    base = compute_impact(EV, snap(pos=pos))
    small = compute_impact(EV, snap(pos=pos), TenantThresholds(exposure_full_scale=D(1000)))
    assert small["risk_score"] > base["risk_score"]


@pytest.mark.parametrize(
    "patch",
    [
        {"expected_duration_hours": -1},
        {"event_time": "garbage"},
        {"event_time": None},
        {"expected_duration_hours": True},
        {"expected_duration_hours": "x"},
    ],
)
def test_u34_bad_event(patch):
    with pytest.raises(ValueError):
        im.parse_window({**EV, **patch})


def test_u34b_missing_event_time():
    with pytest.raises(ValueError):
        im.parse_window({k: v for k, v in EV.items() if k != "event_time"})


def test_parse_window_naive_and_offset():
    w = im.parse_window({**EV, "event_time": "2026-12-02T10:00:00"})
    assert w.start == datetime(2026, 12, 2, 10, tzinfo=UTC)
    w = im.parse_window(
        {**EV, "event_time": "2026-12-02T12:00:00.5+02:00", "expected_duration_hours": 1.5}
    )
    assert w.start.hour == 10 and w.end.minute == 30 and isinstance(w, DisruptionWindow)
    assert im._iso(w.start) == "2026-12-02T10:00:00.500000Z"
    assert im.line_delay_days(date(2026, 12, 2), w) == 1


def test_u35_weights():
    assert sum(im.WEIGHTS.values()) == 100


def test_u36_determinism_no_mutation():
    sc = _load(DATA / "scenarios" / "demo.json")
    s = snapshot_for(sc["event"])
    before, ev_before = copy.deepcopy(s), copy.deepcopy(sc["event"])
    a, b = compute_impact(sc["event"], s), compute_impact(sc["event"], s)
    assert a == b and s == before and sc["event"] == ev_before


def test_u37_flag_order_and_dedupe():
    pos = [po("PO-1", lines=[line(1, "ITM-A", price="0.00"), line(2, "ITM-B")])]
    r = compute_impact(
        EV,
        snap(
            pos=pos,
            supplier={"supplier_id": "S1"},
            invs=[inv("ITM-A", demand=None), inv("ITM-A", "DC2", demand=None)],
            alts=[alt(sup="S2", cap=None)],
        ),
    )
    flags = r["data_quality_flags"]
    assert flags == [
        "supplier capacity not provided",
        "daily_demand missing for ITM-A at DC1",
        "daily_demand missing for ITM-A at DC2",
        "no inventory record for ITM-B",
        "capacity unknown for alternate S2 (ITM-A)",
        "unit_price is zero for PO-1 line 1",
    ]
    assert len(flags) == len(set(flags))
    assert im._dedupe(["a", "b", "a"]) == ["a", "b"]


def test_unknown_severity_in_compute_impact():
    with pytest.raises(ValueError):
        compute_impact({**EV, "severity": "FOO"}, snap())
