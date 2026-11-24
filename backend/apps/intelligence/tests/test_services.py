"""N-04 service tests: `create_risk_case()` against the real seed and demo scenarios."""

from __future__ import annotations

import copy
import io
import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from unittest import mock

import pytest
from django.core.management import call_command
from django.db import IntegrityError

from apps.core.models import AffectedPO, DisruptionEvent, RiskCase, Supplier
from apps.intelligence.impact import SnapshotError
from apps.intelligence.services import TenantMismatch, create_risk_case
from apps.tenants.models import Tenant

ROOT = Path(__file__).resolve().parents[4]
DATA = ROOT / "data"
D = Decimal


def _load(p):
    return json.loads(p.read_text(encoding="utf-8"))


SEED = {
    n: _load(DATA / "seed" / f"{n}.json")
    for n in ("suppliers", "purchase_orders", "inventory", "alternate_suppliers")
}


def _page(rs):
    return {"count": len(rs), "results": rs}


def snapshot_for(event, captured_at="2026-12-02T10:15:03Z"):
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
        "captured_at": captured_at,
        "supplier": sup,
        "purchase_orders": _page(pos),
        "inventory": _page(inv),
        "alternate_suppliers": _page(alt),
    }


@pytest.fixture
def seeded(db):
    call_command("load_seed", stdout=io.StringIO(), stderr=io.StringIO())
    return Tenant.objects.get(pk="TEN-ACME")


def scenario(name, tenant):
    ev = _load(DATA / "scenarios" / f"{name}.json")["event"]
    supplier = Supplier.objects.filter(tenant=tenant, erp_id=ev["supplier_id"]).first()
    event = DisruptionEvent.objects.create(
        tenant=Tenant.objects.get(pk=ev["tenant_id"]),
        event_id=ev["event_id"],
        supplier_erp_id=ev["supplier_id"],
        supplier=supplier,
        event_type=ev["event_type"],
        severity=ev["severity"],
        event_time=datetime.fromisoformat(ev["event_time"].replace("Z", "+00:00")),
        expected_duration_hours=D(str(ev["expected_duration_hours"])),
        description=ev["description"],
        source_system=ev["source_system"],
        correlation_id=ev["correlation_id"],
    )
    return event, snapshot_for(ev)


def test_demo_creates_case_and_affected_pos(seeded):
    event, snap = scenario("demo", seeded)
    case, created = create_risk_case(tenant=seeded, event=event, snapshot=snap)

    assert created is True
    assert case.risk_score == 67 and case.risk_level == "HIGH"
    assert case.exposure_amount == D("491400.00") and case.currency == "INR"
    assert case.supplier == event.supplier and case.correlation_id == event.correlation_id
    assert case.snapshot == snap
    assert case.snapshot_captured_at == datetime(2026, 12, 2, 10, 15, 3, tzinfo=UTC)
    assert case.impact["risk_score"] == 67 and case.impact["shortage"] is True
    assert case.data_quality_flags == case.impact["data_quality_flags"]
    assert "491400.00" in case.impact_summary and case.title

    pos = list(case.affected_pos.order_by("po_id"))
    assert [(p.po_id, p.item_id, p.item_name) for p in pos] == [
        ("PO-1001", "ITM-501", "Bracket 501"),
        ("PO-1002", "ITM-501", "Bracket 501"),
    ]
    assert sum(p.exposure_amount for p in pos) == D("491400.00")
    p1 = pos[0]
    assert p1.affected_qty == 4000 and p1.exposure_amount == D("302400.00")
    assert p1.estimated_delay_days == D("2") and p1.due_date == date(2026, 12, 3)
    assert p1.currency == "INR" and p1.shortage is True
    assert p1.inventory_coverage_days == D("1.0")
    assert [p.estimated_delay_days for p in pos] == [D("2"), D("1")]
    assert {p.tenant_id for p in pos} == {"TEN-ACME"}


def test_tc03_all_affected_pos_persisted(seeded):
    event, snap = scenario("tc03_multiple_open_pos", seeded)
    case, created = create_risk_case(tenant=seeded, event=event, snapshot=snap)
    assert case.exposure_amount == D("537649.00")
    assert set(case.affected_pos.values_list("po_id", flat=True)) == {
        "PO-1007", "PO-1008", "PO-1009", "PO-1010", "PO-1011",
    }  # fmt: skip
    assert case.affected_pos.count() == len(case.impact["affected_pos"])


def test_tc04_no_shortage(seeded):
    event, snap = scenario("tc04_inventory_sufficient", seeded)
    case, created = create_risk_case(tenant=seeded, event=event, snapshot=snap)
    assert case.affected_pos.count() == 2
    assert not case.affected_pos.filter(shortage=True).exists()
    assert case.impact["shortage"] is False


def test_idempotent_second_call_returns_same_case(seeded):
    event, snap = scenario("demo", seeded)
    first, first_created = create_risk_case(tenant=seeded, event=event, snapshot=snap)
    other = copy.deepcopy(snap)
    other["captured_at"] = "2027-01-01T00:00:00Z"
    second, second_created = create_risk_case(tenant=seeded, event=event, snapshot=other)
    assert (first_created, second_created) == (True, False)
    assert second.pk == first.pk
    assert RiskCase.objects.count() == 1 and AffectedPO.objects.count() == 2
    second.refresh_from_db()
    assert second.snapshot["captured_at"] == "2026-12-02T10:15:03Z"


def test_tenant_mismatch_raises_and_writes_nothing(seeded):
    globex = Tenant.objects.get(pk="TEN-GLOBEX")
    event, snap = scenario("demo", seeded)
    with pytest.raises(TenantMismatch):
        create_risk_case(tenant=globex, event=event, snapshot=snap)

    bad = copy.deepcopy(snap)
    bad["inventory"]["results"][0]["tenant_id"] = "TEN-GLOBEX"
    with pytest.raises(TenantMismatch):
        create_risk_case(tenant=seeded, event=event, snapshot=bad)
    assert RiskCase.objects.count() == 0 and AffectedPO.objects.count() == 0


def test_status_stays_received(seeded):
    event, snap = scenario("demo", seeded)
    case, created = create_risk_case(tenant=seeded, event=event, snapshot=snap)
    case.refresh_from_db()
    assert case.status == RiskCase.Status.RECEIVED


def test_money_and_decimal_round_trip(seeded):
    event, snap = scenario("demo", seeded)
    case, created = create_risk_case(tenant=seeded, event=event, snapshot=snap)
    case = RiskCase.objects.get(pk=case.pk)
    assert isinstance(case.exposure_amount, Decimal) and case.exposure_amount == D("491400.00")
    assert case.exposure_amount == D(case.impact["total_exposure"]["amount"])
    for p in AffectedPO.objects.filter(risk_case=case):
        assert isinstance(p.estimated_delay_days, Decimal)
        assert isinstance(p.exposure_amount, Decimal)
        assert isinstance(p.inventory_coverage_days, Decimal)
    assert RiskCase.objects.filter(exposure_amount=D("491400.00")).count() == 1


def test_zero_affected_returns_none_and_writes_nothing(seeded):
    event, snap = scenario("demo", seeded)
    empty = copy.deepcopy(snap)
    empty["purchase_orders"] = _page([])
    assert create_risk_case(tenant=seeded, event=event, snapshot=empty) == (None, False)
    assert RiskCase.objects.count() == 0 and AffectedPO.objects.count() == 0


def test_unknown_supplier_returns_none(seeded):
    event, snap = scenario("tc02_unknown_supplier", seeded)
    assert snap["supplier"] is None
    assert create_risk_case(tenant=seeded, event=event, snapshot=snap) == (None, False)
    assert RiskCase.objects.count() == 0


def test_missing_item_name_and_captured_at_are_blank(seeded):
    event, snap = scenario("demo", seeded)
    snap = copy.deepcopy(snap)
    snap.pop("captured_at")
    for po in snap["purchase_orders"]["results"]:
        for ln in po["lines"]:
            ln.pop("item_name", None)
    for r in snap["inventory"]["results"]:
        r.pop("item_name", None)
    case, created = create_risk_case(tenant=seeded, event=event, snapshot=snap)
    assert case.snapshot_captured_at is None
    assert set(case.affected_pos.values_list("item_name", flat=True)) == {""}


# ---- review fixes -------------------------------------------------------------------------


def test_event_without_known_supplier_returns_none_even_with_full_snapshot(seeded):
    event, snap = scenario("demo", seeded)
    event.supplier = None
    event.save()
    assert create_risk_case(tenant=seeded, event=event, snapshot=snap) == (None, False)
    assert RiskCase.objects.count() == 0


def test_snapshot_supplier_must_match_event_supplier(seeded):
    event, snap = scenario("demo", seeded)
    bad = copy.deepcopy(snap)
    bad["supplier"]["supplier_id"] = "SUP-9999"
    with pytest.raises(SnapshotError):
        create_risk_case(tenant=seeded, event=event, snapshot=bad)
    assert RiskCase.objects.count() == 0 and AffectedPO.objects.count() == 0


def test_coverage_overflow_is_nulled_and_flagged(seeded):
    event, snap = scenario("demo", seeded)
    huge = copy.deepcopy(snap)
    for r in huge["inventory"]["results"]:
        r["daily_demand"] = 0.00000001
    case, created = create_risk_case(tenant=seeded, event=event, snapshot=huge)
    assert created is True
    assert set(case.affected_pos.values_list("inventory_coverage_days", flat=True)) == {None}
    assert any(f.startswith("COVERAGE_OUT_OF_RANGE") for f in case.data_quality_flags)
    assert case.impact["data_quality_flags"] == case.data_quality_flags


def test_malformed_snapshot_becomes_snapshot_error(seeded):
    event, snap = scenario("demo", seeded)
    bad = copy.deepcopy(snap)
    del bad["purchase_orders"]["results"][0]["lines"][0]["due_date"]
    with pytest.raises(SnapshotError):
        create_risk_case(tenant=seeded, event=event, snapshot=bad)
    with pytest.raises(SnapshotError):
        create_risk_case(tenant=seeded, event=event, snapshot=["not", "a", "dict"])
    assert RiskCase.objects.count() == 0


def test_missing_currency_is_snapshot_error_before_any_write(seeded):
    event, snap = scenario("demo", seeded)
    bad = copy.deepcopy(snap)
    for po in bad["purchase_orders"]["results"]:
        for ln in po["lines"]:
            del ln["unit_price"]["currency"]
    with pytest.raises(SnapshotError):
        create_risk_case(tenant=seeded, event=event, snapshot=bad)
    assert RiskCase.objects.count() == 0 and AffectedPO.objects.count() == 0


def test_snapshot_row_without_tenant_id_is_rejected(seeded):
    event, snap = scenario("demo", seeded)
    bad = copy.deepcopy(snap)
    del bad["inventory"]["results"][0]["tenant_id"]
    with pytest.raises(TenantMismatch):
        create_risk_case(tenant=seeded, event=event, snapshot=bad)
    assert RiskCase.objects.count() == 0


def test_currency_differing_from_tenant_is_flagged_not_raised(seeded):
    event, snap = scenario("demo", seeded)
    seeded.currency = "USD"  # in memory only
    case, created = create_risk_case(tenant=seeded, event=event, snapshot=snap)
    assert created is True and case.currency == "INR"
    assert any(f.startswith("CURRENCY_MISMATCH") for f in case.data_quality_flags)


def test_race_returns_winner_case(seeded):
    event, snap = scenario("demo", seeded)
    winner, _ = create_risk_case(tenant=seeded, event=event, snapshot=snap)
    real_filter = RiskCase.objects.filter
    calls = {"n": 0}

    def filter_hides_winner_once(*a, **kw):
        calls["n"] += 1
        return RiskCase.objects.none() if calls["n"] == 1 else real_filter(*a, **kw)

    with mock.patch.object(RiskCase.objects, "filter", side_effect=filter_hides_winner_once):
        case, created = create_risk_case(tenant=seeded, event=event, snapshot=snap)
    assert (case.pk, created) == (winner.pk, False)
    assert RiskCase.objects.count() == 1 and AffectedPO.objects.count() == 2


def test_integrity_error_without_winner_is_reraised_and_rolled_back(seeded):
    event, snap = scenario("demo", seeded)
    with mock.patch.object(AffectedPO.objects, "create", side_effect=IntegrityError("boom")):
        with pytest.raises(IntegrityError):
            create_risk_case(tenant=seeded, event=event, snapshot=snap)
    assert RiskCase.objects.count() == 0 and AffectedPO.objects.count() == 0


def test_invalid_captured_at_is_snapshot_error(seeded):
    event, snap = scenario("demo", seeded)
    bad = copy.deepcopy(snap)
    bad["captured_at"] = "yesterday"
    with pytest.raises(SnapshotError):
        create_risk_case(tenant=seeded, event=event, snapshot=bad)
    assert RiskCase.objects.count() == 0


def test_non_object_snapshot_row_is_snapshot_error(seeded):
    event, snap = scenario("demo", seeded)
    bad = copy.deepcopy(snap)
    bad["inventory"] = ["not-a-row"]
    with pytest.raises(SnapshotError):
        create_risk_case(tenant=seeded, event=event, snapshot=bad)
