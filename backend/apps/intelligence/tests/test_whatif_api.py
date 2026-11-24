"""N-11 what-if endpoints, with the real mock-ERP app standing in for the ERP (no network)."""
import sys
import time
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from rest_framework.test import APIClient

from apps.audit.models import AuditEvent
from apps.core import models as core_models
from apps.intelligence import views, whatif
from tests.factories import make_case, make_event, make_supplier, make_tenant, make_user
from tests.helpers import api_key_client, jwt_client

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "mock-erp"))
from app.main import app as erp_app  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

URL = "/api/v1/what-if"
DEMO_START = "2026-12-02T10:15:00Z"  # seed demo date (data/README.md)
FIXED_NOW = datetime(2026, 11, 1, 9, 0, tzinfo=UTC)  # well before the first PO falls due
BODY = {"supplier_id": "SUP-1008", "duration_days": 7, "event_type": "FACTORY_OUTAGE"}
DEMO = {**BODY, "start": DEMO_START}


@pytest.fixture(autouse=True)
def erp(monkeypatch):
    monkeypatch.setattr(views, "_client", lambda: TestClient(erp_app))


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch):
    monkeypatch.setattr(views, "_now", lambda: FIXED_NOW)


@pytest.fixture
def acme(db):
    return make_tenant("TEN-ACME", currency="INR")


@pytest.fixture
def user(acme):
    return make_user(acme, role="OPS_MANAGER")


def _write_counts():
    return {
        m.__name__: m.objects.count()
        for m in core_models.__dict__.values()
        if isinstance(m, type) and hasattr(m, "objects") and hasattr(m, "_meta")
        and not m._meta.abstract
    }


def test_sup_1008_seven_days_demo_fast_and_labelled(user):
    t0 = time.perf_counter()
    r = jwt_client(user).post(URL, DEMO, format="json")
    elapsed = time.perf_counter() - t0
    assert r.status_code == 200, r.content
    assert elapsed < 3, elapsed
    d = r.json()
    assert d["estimate"] is True
    assert set(d["labels"].values()) == {"ESTIMATE"}
    assert d["assumptions"] and any("ESTIMATE" in a for a in d["assumptions"])
    assert any("units per week" in a for a in d["assumptions"])
    assert isinstance(d["data_gaps"], list)
    # demo numbers (data/README.md): PO-1001 + PO-1002 on ITM-501, 6500 x 75.60
    assert {p["po_id"] for p in d["affected_pos"]} >= {"PO-1001", "PO-1002"}
    assert Decimal(d["exposure"]["amount"]) >= Decimal("491400.00")
    assert d["scenario"]["start"] == DEMO_START
    assert d["scenario"]["duration_days"] == 7 and d["scenario"]["source"] == "REQUEST"
    assert d["risk"]["level"] in {"LOW", "MEDIUM", "HIGH", "CRITICAL"}
    assert d["inventory"]["coverage"] and "gaps" in d["inventory"]
    best = d["best_option_estimate"]
    assert best["is_estimate"] is True and best["items"]
    assert "cost_delta_complete" in best and "uncovered_item_ids" in best
    assert any("capacity" in g for g in d["data_gaps"])  # SUP-1008 capacity is null in the seed
    print(
        "DEMO",
        d["scenario"]["end"],
        [p["po_id"] for p in d["affected_pos"]],
        d["exposure"],
        d["risk"]["score"],
        d["risk"]["level"],
        round(elapsed, 2),
    )


def test_nothing_affected_is_a_data_gap_with_fixed_clock(user):
    # clock fixed at 2026-11-01: 7 days ends 11-08, long before PO-1001 is due (12-03)
    r = jwt_client(user).post(URL, BODY, format="json")
    assert r.status_code == 200
    d = r.json()
    assert d["scenario"]["start"] == "2026-11-01T09:00:00Z"
    assert d["affected_pos"] == [] and d["best_option_estimate"] is None
    assert any("no open purchase order line" in g for g in d["data_gaps"])


def test_start_override_and_validation(user):
    r = jwt_client(user).post(URL, {**BODY, "start": "2026-12-02T00:00:00+05:30"}, format="json")
    assert r.status_code == 200
    assert r.json()["scenario"]["start"] == "2026-12-01T18:30:00Z"
    bad = jwt_client(user).post(URL, {**BODY, "start": "yesterday"}, format="json")
    assert bad.status_code == 400


def test_long_outage_full_estimate(user):
    r = jwt_client(user).post(URL, {**DEMO, "duration_days": 45}, format="json")
    assert r.status_code == 200, r.content
    d = r.json()
    assert d["affected_pos"] and d["scenario"]["severity"] == "CRITICAL"


def test_no_writes_except_one_audit_row(user):
    before = _write_counts()
    audit_before = AuditEvent.objects.count()
    assert jwt_client(user).post(URL, DEMO, format="json").status_code == 200
    assert _write_counts() == before
    assert AuditEvent.objects.count() == audit_before + 1
    row = AuditEvent.objects.latest("id")
    assert row.action == "what_if.run" and row.tenant_id == "TEN-ACME"
    assert row.data["supplier_id"] == "SUP-1008"


def test_unauthenticated_401():
    r = APIClient().post(URL, BODY, format="json")
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "UNAUTHENTICATED"


def test_viewer_forbidden(acme):
    viewer = make_user(acme, role="VIEWER")
    assert jwt_client(viewer).post(URL, BODY, format="json").status_code == 403


def test_other_tenants_supplier_is_404_and_body_tenant_ignored(db):
    globex = make_tenant("TEN-GLOBEX", currency="INR")
    u = make_user(globex, role="ADMIN")
    # SUP-1008 belongs to TEN-ACME in the seed; a tenant_id in the body must not help
    r = jwt_client(u).post(URL, {**BODY, "tenant_id": "TEN-ACME"}, format="json")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "NOT_FOUND"
    assert AuditEvent.objects.filter(action="what_if.run").count() == 0


@pytest.mark.parametrize(
    "patch",
    [
        {"duration_days": 0},
        {"duration_days": 91},
        {"duration_days": "x"},
        {"event_type": "METEOR"},
        {"supplier_id": "ACME-1"},
        {"supplier_id": "SUP-1; DROP"},
    ],
)
def test_validation_errors(user, patch):
    r = jwt_client(user).post(URL, {**BODY, **patch}, format="json")
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"
    assert AuditEvent.objects.filter(action="what_if.run").count() == 0


def test_missing_fields_400(user):
    assert jwt_client(user).post(URL, {}, format="json").status_code == 400


def test_erp_timeout_gives_504(user, monkeypatch):
    class Boom:
        def get(self, *a, **k):
            raise httpx.ReadTimeout("slow")

    monkeypatch.setattr(views, "_client", lambda: Boom())
    r = jwt_client(user).post(URL, BODY, format="json")
    assert r.status_code == 504
    assert r.json()["error"]["code"] == "DOWNSTREAM_TIMEOUT"


def test_erp_down_gives_502(user, monkeypatch):
    class Down:
        def get(self, *a, **k):
            raise httpx.ConnectError("refused")

    monkeypatch.setattr(views, "_client", lambda: Down())
    r = jwt_client(user).post(URL, BODY, format="json")
    assert r.status_code == 502
    assert r.json()["error"]["code"] == "DOWNSTREAM_ERROR"


def test_erp_unknown_supplier_404(user):
    r = jwt_client(user).post(URL, {**BODY, "supplier_id": "SUP-9999"}, format="json")
    assert r.status_code == 404


def test_longer_outage_not_less_risky(user):
    c = jwt_client(user)
    short = c.post(URL, {**DEMO, "duration_days": 1}, format="json").json()
    long = c.post(URL, {**DEMO, "duration_days": 30}, format="json").json()
    assert long["risk"]["score"] >= short["risk"]["score"]
    assert long["scenario"]["severity"] == "CRITICAL"


def test_system_api_key_not_allowed(acme):
    assert api_key_client(acme).post(URL, BODY, format="json").status_code == 403


# ------------------------------------------------------------------ case variant


def _case(tenant, supplier_erp="SUP-1008", hours=48):
    sup = make_supplier(tenant, erp_id=supplier_erp)
    ev = make_event(
        tenant,
        supplier=sup,
        event_type="CAPACITY_REDUCTION",
        severity="MEDIUM",
        expected_duration_hours=Decimal(hours) if hours else None,
        event_time=datetime(2026, 12, 2, 10, 15, tzinfo=UTC),
    )
    return make_case(tenant, event=ev)


def test_case_what_if_seeded_from_case(user, acme):
    case = _case(acme)
    r = jwt_client(user).post(f"/api/v1/risk-cases/{case.id}/what-if", {}, format="json")
    assert r.status_code == 200, r.content
    d = r.json()
    sc = d["scenario"]
    assert sc["risk_case_id"] == case.id and sc["source"] == "RISK_CASE"
    assert sc["supplier_id"] == "SUP-1008"
    assert sc["event_type"] == "CAPACITY_REDUCTION" and sc["severity"] == "MEDIUM"
    assert sc["duration_days"] == 2
    assert sc["start"] == "2026-12-02T10:15:00Z"  # defaults to the case event's time
    assert d["estimate"] is True and d["assumptions"]
    row = AuditEvent.objects.latest("id")
    assert row.action == "what_if.run" and row.entity_type == "RiskCase"


def test_case_what_if_duration_override(user, acme):
    case = _case(acme)
    r = jwt_client(user).post(
        f"/api/v1/risk-cases/{case.id}/what-if", {"duration_days": 10}, format="json"
    )
    assert r.json()["scenario"]["duration_days"] == 10


def test_case_without_duration_needs_override(user, acme):
    case = _case(acme, hours=None)
    c = jwt_client(user)
    assert c.post(f"/api/v1/risk-cases/{case.id}/what-if", {}, format="json").status_code == 400
    assert (
        c.post(f"/api/v1/risk-cases/{case.id}/what-if", {"duration_days": 3}, format="json")
        .status_code
        == 200
    )


def test_case_override_validated(user, acme):
    case = _case(acme)
    r = jwt_client(user).post(
        f"/api/v1/risk-cases/{case.id}/what-if", {"duration_days": 500}, format="json"
    )
    assert r.status_code == 400


def test_case_of_other_tenant_404(user, db):
    globex = make_tenant("TEN-GLOBEX", currency="INR")
    foreign = _case(globex, supplier_erp="SUP-3001")
    r = jwt_client(user).post(f"/api/v1/risk-cases/{foreign.id}/what-if", {}, format="json")
    assert r.status_code == 404
    assert AuditEvent.objects.filter(action="what_if.run").count() == 0
    assert jwt_client(user).post(
        "/api/v1/risk-cases/RC-999999/what-if", {}, format="json"
    ).status_code == 404


def test_case_no_writes_besides_audit(user, acme):
    case = _case(acme)
    before = _write_counts()
    a = AuditEvent.objects.count()
    r = jwt_client(user).post(f"/api/v1/risk-cases/{case.id}/what-if", {}, format="json")
    assert r.status_code == 200
    assert _write_counts() == before
    assert AuditEvent.objects.count() == a + 1


# ------------------------------------------------------------------ pure helpers


def test_severity_bands():
    assert whatif.severity_for(Decimal(24)) == "MEDIUM"
    assert whatif.severity_for(Decimal(24 * 7)) == "HIGH"
    assert whatif.severity_for(Decimal(24 * 30)) == "CRITICAL"


def test_case_start_override(user, acme):
    case = _case(acme)
    r = jwt_client(user).post(
        f"/api/v1/risk-cases/{case.id}/what-if", {"start": "2026-12-05T00:00:00Z"}, format="json"
    )
    assert r.json()["scenario"]["start"] == "2026-12-05T00:00:00Z"


# ------------------------------------------------------------------ review fixes: ERP data


def _patched_snapshot(monkeypatch, mutate):
    real = whatif.fetch_snapshot

    def fake(*a, **k):
        snap = real(*a, **k)
        mutate(snap)
        return snap

    monkeypatch.setattr(whatif, "fetch_snapshot", fake)


def test_snapshot_error_missing_price_is_502(user, monkeypatch):
    def mutate(snap):
        snap["purchase_orders"][0]["lines"][0]["unit_price"] = {}

    _patched_snapshot(monkeypatch, mutate)
    r = jwt_client(user).post(URL, DEMO, format="json")
    assert r.status_code == 502
    assert r.json()["error"]["code"] == "DOWNSTREAM_ERROR"


def test_snapshot_error_mixed_currency_is_502(user, monkeypatch):
    def mutate(snap):
        snap["purchase_orders"][0]["lines"][0]["unit_price"]["currency"] = "USD"

    _patched_snapshot(monkeypatch, mutate)
    assert jwt_client(user).post(URL, DEMO, format="json").status_code == 502


@pytest.mark.parametrize("bad", [None, "TEN-GLOBEX"])
@pytest.mark.parametrize("key", ["purchase_orders", "inventory", "alternate_suppliers"])
def test_foreign_or_missing_tenant_row_is_502(user, monkeypatch, key, bad):
    def mutate(snap):
        snap[key][0]["tenant_id"] = bad

    _patched_snapshot(monkeypatch, mutate)
    r = jwt_client(user).post(URL, DEMO, format="json")
    assert r.status_code == 502
    assert AuditEvent.objects.filter(action="what_if.run").count() == 0


@pytest.mark.parametrize("bad", [None, "TEN-GLOBEX"])
def test_supplier_row_of_other_tenant_is_404_and_audited(user, monkeypatch, bad):
    def mutate(snap):
        snap["supplier"]["tenant_id"] = bad

    make_supplier(make_tenant("TEN-GLOBEX", currency="INR"), erp_id="SUP-1008")
    _patched_snapshot(monkeypatch, mutate)
    r = jwt_client(user).post(URL, DEMO, format="json")
    assert r.status_code == 404
    row = AuditEvent.objects.get(action="security.cross_tenant_denied")
    assert row.tenant_id == "TEN-ACME" and row.entity_id == "SUP-1008"
    assert row.data == {"path": URL, "method": "POST"}


def test_foreign_supplier_known_only_in_django_is_audited(db):
    globex = make_tenant("TEN-GLOBEX", currency="INR")
    u = make_user(globex, role="ADMIN")
    make_supplier(make_tenant("TEN-ACME", currency="INR"), erp_id="SUP-1008")
    r = jwt_client(u).post(URL, DEMO, format="json")
    assert r.status_code == 404
    assert AuditEvent.objects.filter(action="security.cross_tenant_denied", tenant_id="TEN-GLOBEX")
    assert not AuditEvent.objects.filter(action="what_if.run").exists()


def test_unknown_supplier_404_is_not_audited_as_cross_tenant(user):
    r = jwt_client(user).post(URL, {**DEMO, "supplier_id": "SUP-9999"}, format="json")
    assert r.status_code == 404
    assert not AuditEvent.objects.filter(action="security.cross_tenant_denied").exists()


def test_total_erp_budget_gives_504(user, monkeypatch):
    ticks = iter(range(0, 1000))  # each clock() call advances 1 s; the budget is 2.5 s
    real = whatif.fetch_snapshot
    monkeypatch.setattr(
        whatif,
        "fetch_snapshot",
        lambda *a, **k: real(*a, **{**k, "clock": lambda: float(next(ticks))}),
    )
    r = jwt_client(user).post(URL, DEMO, format="json")
    assert r.status_code == 504
    assert r.json()["error"]["code"] == "DOWNSTREAM_TIMEOUT"


class _FakeResp:
    def __init__(self, status, body):
        self.status_code, self._b = status, body

    def json(self):
        return self._b


def _client_with(routes):
    class C:
        def get(self, path, params=None, headers=None, timeout=None):
            return routes(path, params)

    return C()


def _event(days=7):
    return whatif.build_event(
        tenant_id="T",
        supplier_id="SUP-1",
        event_type="FACTORY_OUTAGE",
        duration_hours=Decimal(24 * days),
        severity="HIGH",
        start=datetime(2026, 12, 2, tzinfo=UTC),
    )


def _run(snap):
    return whatif.run_what_if(
        tenant={"tenant_id": "T", "currency": "INR", "settings": {}}, event=_event(), snapshot=snap
    )


def _po(qty=100, price="10.00", ccy="INR"):
    return {
        "po_id": "PO-1",
        "tenant_id": "T",
        "supplier_id": "SUP-1",
        "status": "OPEN",
        "lines": [
            {
                "line_no": 1,
                "item_id": "ITM-1",
                "qty": qty,
                "due_date": "2026-12-03",
                "unit_price": {"amount": price, "currency": ccy},
            }
        ],
    }


def test_inventory_404_is_empty_plus_gap_not_supplier_not_found():
    def routes(path, params):
        if path.startswith("/erp/suppliers"):
            return _FakeResp(200, {"supplier_id": "SUP-1", "tenant_id": "T"})
        if path == "/erp/purchase-orders":
            return _FakeResp(200, {"count": 1, "results": [_po()]})
        return _FakeResp(404, {})

    snap = whatif.fetch_snapshot(_client_with(routes), "SUP-1", "T", None)
    assert snap["inventory"] == [] and snap["alternate_suppliers"] == []
    assert len(snap["fetch_gaps"]) == 2
    assert any("404 for inventory" in g for g in _run(snap)["data_gaps"])


def test_po_list_404_is_erp_failure():
    def routes(path, params):
        if path.startswith("/erp/suppliers"):
            return _FakeResp(200, {"supplier_id": "SUP-1", "tenant_id": "T"})
        return _FakeResp(404, {})

    with pytest.raises(whatif.ErpUnavailable):
        whatif.fetch_snapshot(_client_with(routes), "SUP-1", "T", None)


# ------------------------------------------------------------------ best-option branches


def _scenario(inventory, alternates):
    snap = {
        "supplier": {"supplier_id": "SUP-1", "tenant_id": "T", "capacity_per_week": 1},
        "purchase_orders": [_po()],
        "inventory": inventory,
        "alternate_suppliers": alternates,
    }
    return _run(snap)


def _inv(loc, on_hand, demand=10):
    return {
        "tenant_id": "T",
        "item_id": "ITM-1",
        "location_id": loc,
        "on_hand": on_hand,
        "reserved": 0,
        "safety_stock": 0,
        "daily_demand": demand,
    }


def _alt(sid, price="12.00", cap=1000, ccy="INR", lead=3):
    return {
        "tenant_id": "T",
        "item_id": "ITM-1",
        "supplier_id": sid,
        "qualified": True,
        "lead_time_days": lead,
        "unit_price": {"amount": price, "currency": ccy},
        "capacity": cap,
    }


def test_transfer_branch_wins_when_a_location_has_surplus():
    body = _scenario([_inv("DC-A", 5000), _inv("DC-B", 10)], [_alt("SUP-2")])
    item = body["best_option_estimate"]["items"][0]
    assert item["status"] == "RECOMMENDED"
    assert item["recommended"]["option_type"] == "INVENTORY_TRANSFER"
    assert item["recommended"]["from_location_id"] == "DC-A"
    assert body["best_option_estimate"]["cost_delta_complete"] is True
    assert body["best_option_estimate"]["uncovered_item_ids"] == []


def test_cheapest_alternate_and_cost_delta():
    body = _scenario([_inv("DC-B", 10)], [_alt("SUP-3", "13.00"), _alt("SUP-2", "12.00")])
    rec = body["best_option_estimate"]["items"][0]["recommended"]
    assert rec["supplier_id"] == "SUP-2"
    assert rec["estimated_cost_delta"]["amount"] == "200.00"  # (12-10) x 100


def test_no_option_is_structured():
    body = _scenario([_inv("DC-B", 10)], [_alt("SUP-2", cap=5)])
    best = body["best_option_estimate"]
    assert best["items"][0]["status"] == "NO_OPTION"
    assert best["uncovered_item_ids"] == ["ITM-1"]


def test_currency_mismatch_gives_gap_and_no_delta():
    body = _scenario([_inv("DC-B", 10)], [_alt("SUP-2", ccy="USD")])
    best = body["best_option_estimate"]
    assert best["items"][0]["recommended"]["estimated_cost_delta"] is None
    assert best["cost_delta_complete"] is False
    assert any("currency mismatch" in g for g in body["data_gaps"])


def test_stock_covered_item_needs_no_action():
    body = _scenario([_inv("DC-A", 100000)], [_alt("SUP-2")])
    assert body["best_option_estimate"]["items"][0]["status"] == "COVERED_BY_STOCK"
