"""Tests for `manage.py load_seed` (U-03, docs/CONTRACTS.md section 10 "Seed loader")."""

import json
import re
import shutil
from decimal import Decimal
from io import StringIO
from pathlib import Path

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import CommandError, call_command

from apps.core.models import Inventory, PurchaseOrder, PurchaseOrderLine, Shipment, Supplier
from apps.tenants.models import Tenant

REAL_SEED = Path(settings.SEED_DATA_DIR)
PASSWORD = "Zx9-demo-Qw4rt-pass"
User = get_user_model()


def run(*args, data_dir=None):
    out, err = StringIO(), StringIO()
    argv = list(args)
    if data_dir is not None:
        argv += ["--data-dir", str(data_dir)]
    call_command("load_seed", *argv, stdout=out, stderr=err)
    return out.getvalue(), err.getvalue()


def table(out):
    """Parse the summary table into {model: {created, updated, unchanged, deleted}}."""
    rows = {}
    for line in out.splitlines():
        m = re.match(r"^(\w+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)$", line)
        if m:
            rows[m[1]] = dict(
                zip(
                    ("created", "updated", "unchanged", "deleted"),
                    map(int, m.groups()[1:]),
                    strict=True,
                )
            )
    return rows


def counts():
    return {
        m.__name__: m.objects.count()
        for m in (Tenant, Supplier, PurchaseOrder, PurchaseOrderLine, Inventory, Shipment)
    }


def pks():
    return {
        m.__name__: sorted(m.objects.values_list("pk", flat=True))
        for m in (Supplier, PurchaseOrder, PurchaseOrderLine, Inventory, Shipment)
    }


@pytest.fixture
def fast_hasher(settings):
    # Test-only: PBKDF2 is slow; hashing is still real (set_password / check_password).
    settings.PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]


@pytest.fixture
def seed_copy(tmp_path):
    dst = tmp_path / "seed"
    shutil.copytree(REAL_SEED, dst)
    return dst


def edit(seed, name, fn):
    path = seed / name
    data = json.loads(path.read_text(encoding="utf-8"))
    fn(data)
    path.write_text(json.dumps(data), encoding="utf-8")


def tenant_rec(tid, currency="INR", locations=("L1",)):
    return {
        "tenant_id": tid,
        "tenant_name": tid.title(),
        "currency": currency,
        "status": "ACTIVE",
        "created_at": "2026-11-01T00:00:00Z",
        "locations": list(locations),
        "settings": {
            "risk_thresholds": {"medium": 40, "high": 65, "critical": 85},
            "max_cost_increase_pct": 15,
            "require_qualified_alternates": True,
        },
    }


def supplier_rec(tid, sid, **kw):
    r = {
        "supplier_id": sid,
        "tenant_id": tid,
        "name": f"Sup {sid}",
        "tax_id": "TAX1",
        "category": "Raw",
        "city": "Pune",
        "country": "IN",
        "status": "ACTIVE",
        "risk_level": "LOW",
        "on_time_delivery_pct": 90.5,
        "quality_score": 4.0,
        "capacity_per_week": None,
        "contact_email": "a@example.com",
        "disruption_history": [{"date": "2026-01-01"}],
    }
    r.update(kw)
    return r


def po_rec(tid, pid, sid, currency="INR", qty=10, price="2.50", **kw):
    r = {
        "po_id": pid,
        "tenant_id": tid,
        "supplier_id": sid,
        "order_date": "2026-11-01",
        "expected_date": "2026-12-01",
        "status": "OPEN",
        "amount": {"amount": f"{qty * float(price):.2f}", "currency": currency},
        "lines": [
            {
                "line_no": 1,
                "item_id": "ITM-1",
                "item_name": "Item",
                "qty": qty,
                "due_date": "2026-12-01",
                "unit_price": {"amount": price, "currency": currency},
            }
        ],
    }
    r.update(kw)
    return r


def write_mini(tmp_path, tenants, suppliers, pos, inventory=(), shipments=(), manifest=None):
    d = tmp_path / "mini"
    d.mkdir(exist_ok=True)
    files = {
        "tenants.json": tenants,
        "suppliers.json": suppliers,
        "purchase_orders.json": pos,
        "inventory.json": list(inventory),
        "shipments.json": list(shipments),
    }
    for name, data in files.items():
        (d / name).write_text(json.dumps(data), encoding="utf-8")
    manifest = manifest or {
        "counts": {
            "tenants": len(tenants),
            "suppliers": len(suppliers),
            "purchase_orders": len(pos),
            "inventory": len(inventory),
            "shipments": len(shipments),
        }
    }
    (d / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return d


@pytest.fixture
def mini(tmp_path):
    """Two tenants, one supplier each (ids differ), one PO each."""
    return write_mini(
        tmp_path,
        [tenant_rec("TEN-ONE"), tenant_rec("TEN-TWO", "USD")],
        [supplier_rec("TEN-ONE", "SUP-1"), supplier_rec("TEN-TWO", "SUP-9")],
        [po_rec("TEN-ONE", "PO-1", "SUP-1"), po_rec("TEN-TWO", "PO-9", "SUP-9", "USD")],
    )


# ---- real seed ---------------------------------------------------------------------------------


@pytest.mark.django_db
def test_load_real_seed_counts_and_currencies():
    out, _ = run()
    assert counts() == {
        "Tenant": 2,
        "Supplier": 100,
        "PurchaseOrder": 500,
        "PurchaseOrderLine": 806,
        "Inventory": 187,
        "Shipment": 20,
    }
    assert dict(Tenant.objects.values_list("id", "currency")) == {
        "TEN-ACME": "INR",
        "TEN-GLOBEX": "USD",
    }
    t = table(out)
    assert t["purchase_orders"]["created"] == 500 and t["po_lines"]["created"] == 806
    assert t["users"] == {"created": 0, "updated": 0, "unchanged": 0, "deleted": 0}
    assert not User.objects.exists()


@pytest.mark.django_db
def test_real_seed_field_mapping():
    run()
    po = PurchaseOrder.objects.get(tenant_id="TEN-ACME", erp_id="PO-1001")
    assert str(po.amount) == "302400.00" and po.currency == "INR"
    assert po.supplier.erp_id == "SUP-1008" and po.supplier.tenant_id == "TEN-ACME"
    line = po.lines.get()
    assert str(line.unit_price) == "75.6000" and line.qty == 4000
    assert po.expected_date == line.due_date
    t = Tenant.objects.get(pk="TEN-ACME")
    assert t.name == "Acme Manufacturing Pvt Ltd" and t.created_at.isoformat().startswith(
        "2026-11-01T00:00:00"
    )
    assert Supplier.objects.get(tenant_id="TEN-ACME", erp_id="SUP-1008").capacity_per_week is None
    shp = Shipment.objects.get(tenant_id="TEN-ACME", erp_id="SHP-00001")
    assert shp.departed_at.tzinfo is not None and shp.eta.isoformat() == "2027-01-14"
    assert shp.po.erp_id == "PO-1020"


@pytest.mark.django_db
def test_second_run_changes_nothing():
    run()
    before_counts, before_pks = counts(), pks()
    out, _ = run()
    for model, r in table(out).items():
        assert r["created"] == 0 and r["updated"] == 0 and r["deleted"] == 0, model
    assert table(out)["purchase_orders"]["unchanged"] == 500
    assert counts() == before_counts and pks() == before_pks


@pytest.mark.django_db
def test_duplicate_tax_id_and_null_capacity_load():
    run()
    dup = Supplier.objects.filter(
        tenant_id="TEN-ACME",
        tax_id=Supplier.objects.get(tenant_id="TEN-ACME", erp_id="SUP-1011").tax_id,
    )
    assert {s.erp_id for s in dup} >= {"SUP-1011", "SUP-1046"}
    assert Supplier.objects.filter(capacity_per_week__isnull=True).exists()


@pytest.mark.django_db
def test_reanchored_seed_updates_in_place(seed_copy):
    run(data_dir=seed_copy)
    before = pks()
    po_before = PurchaseOrder.objects.get(tenant_id="TEN-ACME", erp_id="PO-1001")
    removed = {}

    def pos_edit(data):
        po = next(p for p in data if p["po_id"] == "PO-1001")
        po["expected_date"] = "2026-12-10"
        po["lines"][0]["due_date"] = "2026-12-10"
        # a multi-line PO: drop its last line and fix amount/expected_date
        multi = next(p for p in data if len(p["lines"]) > 1)
        gone = multi["lines"].pop()
        removed["po"], removed["line_no"] = multi["po_id"], gone["line_no"]
        removed["tenant"] = multi["tenant_id"]
        multi["amount"]["amount"] = str(
            sum(
                Decimal(str(ln["qty"])) * Decimal(ln["unit_price"]["amount"])
                for ln in multi["lines"]
            )
        )
        multi["expected_date"] = max(ln["due_date"] for ln in multi["lines"])

    edit(seed_copy, "purchase_orders.json", pos_edit)
    edit(seed_copy, "suppliers.json", lambda d: d[0].update(name="Renamed Supplier"))
    out, _ = run(data_dir=seed_copy)
    t = table(out)
    assert t["suppliers"]["updated"] == 1
    assert t["purchase_orders"]["updated"] >= 2 and t["purchase_orders"]["created"] == 0
    assert t["po_lines"]["deleted"] == 1 and t["po_lines"]["updated"] >= 1
    po_after = PurchaseOrder.objects.get(tenant_id="TEN-ACME", erp_id="PO-1001")
    assert po_after.pk == po_before.pk and po_after.expected_date.isoformat() == "2026-12-10"
    assert po_after.lines.get().due_date.isoformat() == "2026-12-10"
    assert not PurchaseOrderLine.objects.filter(
        po__tenant_id=removed["tenant"], po__erp_id=removed["po"], line_no=removed["line_no"]
    ).exists()
    assert Supplier.objects.get(pk=pks()["Supplier"][0]).name == "Renamed Supplier"
    assert counts()["PurchaseOrder"] == 500 and counts()["PurchaseOrderLine"] == 805
    after = pks()
    assert (
        after["PurchaseOrder"] == before["PurchaseOrder"]
        and after["Supplier"] == before["Supplier"]
    )


@pytest.mark.django_db
def test_pos_missing_from_seed_are_not_deleted(seed_copy):
    run(data_dir=seed_copy)

    def drop(data):
        data[:] = [p for p in data if p["po_id"] != "PO-1300"]

    edit(seed_copy, "purchase_orders.json", drop)
    # PO-1300 may be referenced by no shipment; manifest count must follow the file
    edit(seed_copy, "manifest.json", lambda m: m["counts"].update(purchase_orders=499))
    run(data_dir=seed_copy)
    assert PurchaseOrder.objects.filter(erp_id="PO-1300").exists()


# ---- failures ----------------------------------------------------------------------------------


@pytest.mark.django_db
def test_cross_tenant_supplier_reference_aborts(mini, tmp_path):
    # PO of TEN-ONE points at SUP-9, which exists only in TEN-TWO.
    edit(mini, "purchase_orders.json", lambda d: d[0].update(supplier_id="SUP-9"))
    with pytest.raises(CommandError, match=r"purchase_orders.json.*PO-1.*SUP-9.*TEN-ONE"):
        run(data_dir=mini)
    assert counts() == dict.fromkeys(counts(), 0)  # atomic: nothing written, not even tenants


@pytest.mark.django_db
def test_shipment_cross_tenant_and_supplier_mismatch(tmp_path):
    ship = {
        "shipment_id": "SHP-1",
        "tenant_id": "TEN-ONE",
        "po_id": "PO-1",
        "supplier_id": "SUP-1",
        "carrier": "X",
        "status": "IN_TRANSIT",
        "origin": "Y",
        "destination_location_id": "L1",
        "departed_at": "2026-11-26T16:00:00Z",
        "eta": "2027-01-14",
    }
    base = dict(
        tenants=[tenant_rec("TEN-ONE"), tenant_rec("TEN-TWO", "USD")],
        suppliers=[
            supplier_rec("TEN-ONE", "SUP-1"),
            supplier_rec("TEN-ONE", "SUP-2"),
            supplier_rec("TEN-TWO", "SUP-9"),
        ],
        pos=[po_rec("TEN-ONE", "PO-1", "SUP-1"), po_rec("TEN-TWO", "PO-9", "SUP-9", "USD")],
    )
    d = write_mini(tmp_path, shipments=[ship], **base)
    run(data_dir=d)
    assert Shipment.objects.count() == 1
    for bad, rule in (
        ({"po_id": "PO-9"}, "does not exist in tenant TEN-ONE"),
        ({"supplier_id": "SUP-2"}, "supplier_id"),
        ({"destination_location_id": "NOPE"}, "location"),
    ):
        d = write_mini(tmp_path, shipments=[{**ship, **bad}], **base)
        with pytest.raises(CommandError, match=rule):
            run(data_dir=d)


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("fname", "mutate", "pattern"),
    [
        (
            "purchase_orders.json",
            lambda d: d[0]["amount"].update(amount="1.00"),
            r"purchase_orders.json.*PO-1001.*amount",
        ),
        (
            "purchase_orders.json",
            lambda d: d[0].update(expected_date="2030-01-01"),
            r"purchase_orders.json.*PO-1001.*expected_date",
        ),
        (
            "purchase_orders.json",
            lambda d: d[0]["lines"][0]["unit_price"].update(currency="USD"),
            r"PO-1001.*currency",
        ),
        (
            "purchase_orders.json",
            lambda d: d[0]["amount"].update(currency="USD"),
            r"PO-1001.*currency",
        ),
        (
            "inventory.json",
            lambda d: d[0].update(location_id="DC-MARS"),
            r"inventory.json.*DC-MARS",
        ),
        (
            "tenants.json",
            lambda d: d[0]["settings"]["risk_thresholds"].update(medium=90),
            r"tenants.json.*TEN-ACME.*medium < high < critical",
        ),
        (
            "tenants.json",
            lambda d: d[0]["settings"].update(max_cost_increase_pct=-1),
            r"max_cost_increase_pct",
        ),
        (
            "tenants.json",
            lambda d: d[0]["settings"].update(require_qualified_alternates="yes"),
            r"require_qualified_alternates",
        ),
        ("manifest.json", lambda d: d["counts"].update(suppliers=99), r"manifest.json.*suppliers"),
        (
            "suppliers.json",
            lambda d: d[0].update(status="BOGUS"),
            r"suppliers.json.*SUP-1001.*status",
        ),
    ],
)
def test_validation_failures_abort_everything(seed_copy, fname, mutate, pattern):
    edit(seed_copy, fname, mutate)
    with pytest.raises(CommandError, match=pattern):
        run(data_dir=seed_copy)
    assert sum(counts().values()) == 0


@pytest.mark.django_db
def test_missing_field_names_file_and_record(seed_copy):
    edit(seed_copy, "suppliers.json", lambda d: d[3].pop("name"))
    with pytest.raises(CommandError, match=r"suppliers.json: SUP-1004"):
        run(data_dir=seed_copy)


@pytest.mark.django_db
def test_failure_on_rerun_leaves_previous_data_untouched(seed_copy):
    run(data_dir=seed_copy)
    before = counts()
    edit(seed_copy, "suppliers.json", lambda d: d[0].update(name="Changed"))
    edit(seed_copy, "purchase_orders.json", lambda d: d[-1]["amount"].update(amount="1.00"))
    with pytest.raises(CommandError):
        run(data_dir=seed_copy)
    assert counts() == before
    assert Supplier.objects.get(tenant_id="TEN-ACME", erp_id="SUP-1001").name != "Changed"


# ---- tenancy / synthetic seeds -----------------------------------------------------------------


@pytest.mark.django_db
def test_erp_id_reuse_across_tenants_is_legal(tmp_path):
    d = write_mini(
        tmp_path,
        [tenant_rec("TEN-ONE"), tenant_rec("TEN-TWO", "USD")],
        [supplier_rec("TEN-ONE", "SUP-1"), supplier_rec("TEN-TWO", "SUP-1", name="Other")],
        [po_rec("TEN-ONE", "PO-1", "SUP-1"), po_rec("TEN-TWO", "PO-1", "SUP-1", "USD")],
    )
    run(data_dir=d)
    assert Supplier.objects.filter(erp_id="SUP-1").count() == 2
    one = PurchaseOrder.objects.get(tenant_id="TEN-ONE", erp_id="PO-1")
    two = PurchaseOrder.objects.get(tenant_id="TEN-TWO", erp_id="PO-1")
    assert one.supplier.tenant_id == "TEN-ONE" and two.supplier.tenant_id == "TEN-TWO"
    assert two.supplier.name == "Other" and two.currency == "USD"
    out, _ = run(data_dir=d)
    assert table(out)["suppliers"]["unchanged"] == 2


@pytest.mark.django_db
def test_disruption_history_ignored_and_data_dir_option(mini):
    out, _ = run(data_dir=mini)
    assert table(out)["suppliers"]["created"] == 2
    assert not hasattr(Supplier.objects.first(), "disruption_history")
    assert str(mini) in out


@pytest.mark.django_db
def test_default_data_dir_comes_from_settings(mini, settings):
    settings.SEED_DATA_DIR = mini
    out, _ = run()
    assert counts()["Supplier"] == 2 and table(out)["tenants"]["created"] == 2


# ---- demo users --------------------------------------------------------------------------------


@pytest.mark.django_db
def test_demo_users_need_password_env(monkeypatch, mini):
    monkeypatch.delenv("DEMO_USER_PASSWORD", raising=False)
    with pytest.raises(CommandError, match="DEMO_USER_PASSWORD"):
        run("--demo-users", data_dir=mini)
    monkeypatch.setenv("DEMO_USER_PASSWORD", "")
    with pytest.raises(CommandError, match="DEMO_USER_PASSWORD"):
        run("--demo-users", data_dir=mini)
    monkeypatch.setenv("DEMO_USER_PASSWORD", "12345678")  # numeric + common: fails validation
    with pytest.raises(CommandError) as ei:
        run("--demo-users", data_dir=mini)
    assert "12345678" not in str(ei.value)
    assert not User.objects.exists() and not Tenant.objects.exists()


@pytest.mark.django_db
def test_demo_users_created_with_roles_and_hashed_password(fast_hasher, monkeypatch, mini, capsys):
    monkeypatch.setenv("DEMO_USER_PASSWORD", PASSWORD)
    out, err = run("--demo-users", data_dir=mini)
    assert User.objects.count() == 6
    expect = {
        "one.admin": ("ADMIN", "TEN-ONE", "One Admin"),
        "one.procurement": ("PROCUREMENT_MANAGER", "TEN-ONE", "One Procurement"),
        "one.viewer": ("VIEWER", "TEN-ONE", "One Viewer"),
        "two.admin": ("ADMIN", "TEN-TWO", "Two Admin"),
        "two.procurement": ("PROCUREMENT_MANAGER", "TEN-TWO", "Two Procurement"),
        "two.viewer": ("VIEWER", "TEN-TWO", "Two Viewer"),
    }
    for username, (role, tenant, name) in expect.items():
        u = User.objects.get(username=username)
        assert (u.role, u.tenant_id, u.name) == (role, tenant, name)
        assert u.email == f"{username}@example.com"
        assert not u.is_superuser and u.password != PASSWORD and u.check_password(PASSWORD)
    assert table(out)["users"]["created"] == 6
    captured = capsys.readouterr()
    assert PASSWORD not in out + err + captured.out + captured.err


@pytest.mark.django_db
def test_demo_users_rerun_keeps_existing_password(fast_hasher, monkeypatch, mini):
    monkeypatch.setenv("DEMO_USER_PASSWORD", PASSWORD)
    run("--demo-users", data_dir=mini)
    hashes = dict(User.objects.values_list("username", "password"))
    monkeypatch.setenv("DEMO_USER_PASSWORD", "Another-Pass-77-xyz")
    out, _ = run("--demo-users", data_dir=mini)
    assert table(out)["users"] == {"created": 0, "updated": 0, "unchanged": 6, "deleted": 0}
    assert dict(User.objects.values_list("username", "password")) == hashes
    assert User.objects.get(username="one.admin").check_password(PASSWORD)


@pytest.mark.django_db
def test_demo_user_in_other_tenant_aborts(fast_hasher, monkeypatch, mini):
    monkeypatch.setenv("DEMO_USER_PASSWORD", PASSWORD)
    run(data_dir=mini)
    User.objects.create_user(
        username="one.admin", password="x", tenant=Tenant.objects.get(pk="TEN-TWO")
    )
    with pytest.raises(CommandError, match="another tenant"):
        run("--demo-users", data_dir=mini)
    assert User.objects.count() == 1


@pytest.mark.django_db
def test_no_users_without_flag(monkeypatch, mini):
    monkeypatch.setenv("DEMO_USER_PASSWORD", PASSWORD)
    run(data_dir=mini)
    assert not User.objects.exists()
