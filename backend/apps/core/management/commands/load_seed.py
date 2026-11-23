"""`manage.py load_seed [--data-dir PATH] [--demo-users]` (docs/CONTRACTS.md section 10).

Loads the synthetic ERP seed (data/seed) in one transaction: tenants, suppliers, purchase orders
with lines, inventory, shipments. Idempotent (rows are found by tenant + erp_id or natural key and
updated in place), strict (any rule violation aborts everything with a CommandError naming the
file, the record and the rule). Skips `disruption_history` and `alternate_suppliers.json`.
"""

import json
import os
import time
from collections import defaultdict
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils.dateparse import parse_date, parse_datetime

from apps.core.models import Inventory, PurchaseOrder, PurchaseOrderLine, Shipment, Supplier
from apps.tenants.models import Tenant

DEMO_ROLES = (
    ("admin", "ADMIN", "Admin"),
    ("procurement", "PROCUREMENT_MANAGER", "Procurement"),
    ("viewer", "VIEWER", "Viewer"),
)
FILES = {
    "tenants": "tenants.json",
    "suppliers": "suppliers.json",
    "purchase_orders": "purchase_orders.json",
    "inventory": "inventory.json",
    "shipments": "shipments.json",
}
MANIFEST = "manifest.json"
MODELS = ("tenants", "suppliers", "purchase_orders", "po_lines", "inventory", "shipments", "users")


class Stats:
    def __init__(self):
        self.rows = {m: {"created": 0, "updated": 0, "unchanged": 0, "deleted": 0} for m in MODELS}

    def add(self, model, kind, n=1):
        self.rows[model][kind] += n

    def table(self):
        head = f"{'model':<18}{'created':>9}{'updated':>9}{'unchanged':>11}{'deleted':>9}"
        lines = [head, "-" * len(head)]
        for m, r in self.rows.items():
            lines.append(
                f"{m:<18}{r['created']:>9}{r['updated']:>9}{r['unchanged']:>11}{r['deleted']:>9}"
            )
        return "\n".join(lines)


def _fail(file, rec, rule):
    raise CommandError(f"{file}: {rec}: {rule}")


@contextmanager
def _record(file, rec):
    """Turn parse problems (missing key, bad number, ...) into a CommandError naming the record."""
    try:
        yield
    except CommandError:
        raise
    except (
        KeyError,
        TypeError,
        ValueError,
        InvalidOperation,
        ValidationError,
        AttributeError,
    ) as e:
        detail = e.__class__.__name__ if isinstance(e, KeyError) else str(e)
        _fail(file, rec, f"invalid record ({e.__class__.__name__}: {detail})")


def _money(value, what):
    if not isinstance(value, dict) or "amount" not in value or "currency" not in value:
        raise ValueError(f"{what} must be an object with amount and currency")
    amount = Decimal(str(value["amount"]))
    if not amount.is_finite():
        raise ValueError(f"{what} amount is not finite")
    return amount, value["currency"]


def _fit(value, places, what):
    """Require that `value` has at most `places` decimals (no silent rounding of money)."""
    q = Decimal(1).scaleb(-places)
    if value != value.quantize(q):
        raise ValueError(f"{what} {value} has more than {places} decimals")
    return value.quantize(q)


def _date(value, what):
    d = parse_date(value) if isinstance(value, str) else None
    if d is None:
        raise ValueError(f"{what} {value!r} is not a YYYY-MM-DD date")
    return d


def _aware(value, what):
    dt = parse_datetime(value) if isinstance(value, str) else None
    if dt is None or dt.tzinfo is None:
        raise ValueError(f"{what} {value!r} is not a timezone-aware ISO datetime")
    return dt


def _int(value, what, minimum=0):
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{what} {value!r} must be an integer >= {minimum}")
    return value


def _choice(value, choices, what):
    if value not in choices.values:
        raise ValueError(f"{what} {value!r} not one of {', '.join(choices.values)}")
    return value


def _is_number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _check_settings(s):
    if not isinstance(s, dict):
        raise ValueError("settings must be an object")
    rt = s.get("risk_thresholds")
    if not isinstance(rt, dict) or not all(
        _is_number(rt.get(k)) for k in ("medium", "high", "critical")
    ):
        raise ValueError("settings.risk_thresholds needs numeric medium, high, critical")
    if not rt["medium"] < rt["high"] < rt["critical"]:
        raise ValueError("settings.risk_thresholds must satisfy medium < high < critical")
    pct = s.get("max_cost_increase_pct")
    if not _is_number(pct) or pct < 0:
        raise ValueError("settings.max_cost_increase_pct must be a number >= 0")
    if not isinstance(s.get("require_qualified_alternates"), bool):
        raise ValueError("settings.require_qualified_alternates must be a boolean")


def _apply(obj, values, stats, model, is_new=None):
    """Create or update in place. `obj` is the existing row or a new unsaved instance."""
    if is_new is None:
        is_new = obj.pk is None
    if is_new:
        for k, v in values.items():
            setattr(obj, k, v)
        obj.save()
        stats.add(model, "created")
        return
    changed = [k for k, v in values.items() if getattr(obj, k) != v]
    if not changed:
        stats.add(model, "unchanged")
        return
    for k in changed:
        setattr(obj, k, values[k])
    obj.save()
    stats.add(model, "updated")


class Command(BaseCommand):
    help = "Load the synthetic seed data (data/seed) idempotently; optionally create demo users."

    def add_arguments(self, parser):
        parser.add_argument(
            "--data-dir", default=None, help="Seed folder (default: settings.SEED_DATA_DIR)"
        )
        parser.add_argument(
            "--demo-users", action="store_true", help="Create admin/procurement/viewer per tenant"
        )

    # ---- reading -------------------------------------------------------------------------------
    def _read(self, name):
        path = self.data_dir / name
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            raise CommandError(f"{name}: file not found in {self.data_dir}") from None
        except (OSError, ValueError) as e:
            raise CommandError(f"{name}: cannot read ({e})") from None
        if name != MANIFEST and not isinstance(data, list):
            raise CommandError(f"{name}: expected a JSON array")
        for i, rec in enumerate(data if isinstance(data, list) else []):
            if not isinstance(rec, dict):
                raise CommandError(f"{name}: record #{i + 1}: expected a JSON object")
        return data

    # ---- entry ---------------------------------------------------------------------------------
    def handle(self, *args, **options):
        self.data_dir = Path(options["data_dir"] or settings.SEED_DATA_DIR)
        password = None
        if options["demo_users"]:
            password = os.environ.get("DEMO_USER_PASSWORD", "")
            if not password:
                raise CommandError(
                    "--demo-users needs the DEMO_USER_PASSWORD environment variable (non-empty)."
                )
            try:
                validate_password(password)
            except ValidationError as e:
                raise CommandError(
                    "DEMO_USER_PASSWORD is rejected by password validation: " + " ".join(e.messages)
                ) from None

        started = time.perf_counter()
        self.stats = Stats()
        data = {key: self._read(fname) for key, fname in FILES.items()}
        manifest = self._read(MANIFEST)
        with transaction.atomic():
            self._tenants(data["tenants"])
            self._suppliers(data["suppliers"])
            self._purchase_orders(data["purchase_orders"])
            self._inventory(data["inventory"])
            self._shipments(data["shipments"])
            self._manifest(manifest, data)
            if password:
                self._users(password)
        self.stdout.write(self.stats.table())
        self.stdout.write(
            f"PO lines in seed: {self.line_total}; seed dir: {self.data_dir}; "
            f"done in {time.perf_counter() - started:.2f}s"
        )

    # ---- tenants -------------------------------------------------------------------------------
    def _tenants(self, records):
        f = FILES["tenants"]
        existing = {t.pk: t for t in Tenant.objects.all()}
        self.tenants = {}
        for r in records:
            tid = r.get("tenant_id", "?")
            with _record(f, tid):
                if tid in self.tenants:
                    _fail(f, tid, "duplicate tenant_id")
                locations = r["locations"]
                if not isinstance(locations, list) or not all(
                    isinstance(x, str) for x in locations
                ):
                    raise ValueError("locations must be a list of strings")
                _check_settings(r["settings"])
                if not isinstance(r["currency"], str) or len(r["currency"]) != 3:
                    raise ValueError("currency must be a 3-letter code")
                values = {
                    "name": r["tenant_name"],
                    "status": _choice(r["status"], Tenant.Status, "status"),
                    "currency": r["currency"],
                    "locations": locations,
                    "settings": r["settings"],
                    "created_at": _aware(r["created_at"], "created_at"),
                }
                _apply(
                    existing.get(tid) or Tenant(id=tid),
                    values,
                    self.stats,
                    "tenants",
                    is_new=tid not in existing,
                )
            self.tenants[tid] = Tenant.objects.get(pk=tid)

    def _tenant(self, f, rec, r):
        t = self.tenants.get(r.get("tenant_id"))
        if t is None:
            _fail(f, rec, f"tenant_id {r.get('tenant_id')!r} is not in tenants.json")
        return t

    # ---- suppliers -----------------------------------------------------------------------------
    def _suppliers(self, records):
        f = FILES["suppliers"]
        existing = {
            (s.tenant_id, s.erp_id): s
            for s in Supplier.objects.filter(tenant__in=self.tenants.values())
        }
        seen = set()
        for r in records:
            rec = r.get("supplier_id", "?")
            with _record(f, rec):
                tenant = self._tenant(f, rec, r)
                key = (tenant.pk, rec)
                if key in seen:
                    _fail(f, rec, f"duplicate supplier_id in tenant {tenant.pk}")
                seen.add(key)
                cap = r["capacity_per_week"]
                values = {
                    "name": r["name"],
                    "tax_id": r["tax_id"],
                    "category": r["category"],
                    "city": r["city"],
                    "country": r["country"],
                    "status": _choice(r["status"], Supplier.Status, "status"),
                    "risk_level": _choice(r["risk_level"], Supplier.RiskLevel, "risk_level"),
                    "on_time_delivery_pct": _fit(
                        Decimal(str(r["on_time_delivery_pct"])), 2, "on_time_delivery_pct"
                    ),
                    "quality_score": _fit(Decimal(str(r["quality_score"])), 1, "quality_score"),
                    "capacity_per_week": None if cap is None else _int(cap, "capacity_per_week"),
                    "contact_email": r["contact_email"],
                }
                obj = existing.get(key) or Supplier(tenant=tenant, erp_id=rec)
                _apply(obj, values, self.stats, "suppliers")
        self.suppliers = {
            (s.tenant_id, s.erp_id): s
            for s in Supplier.objects.filter(tenant__in=self.tenants.values())
        }

    # ---- purchase orders + lines ---------------------------------------------------------------
    def _purchase_orders(self, records):
        f = FILES["purchase_orders"]
        existing = {
            (p.tenant_id, p.erp_id): p
            for p in PurchaseOrder.objects.filter(tenant__in=self.tenants.values())
        }
        existing_lines = defaultdict(dict)
        for ln in PurchaseOrderLine.objects.filter(tenant__in=self.tenants.values()):
            existing_lines[ln.po_id][ln.line_no] = ln
        seen = set()
        self.line_total = 0
        for r in records:
            rec = r.get("po_id", "?")
            with _record(f, rec):
                tenant = self._tenant(f, rec, r)
                key = (tenant.pk, rec)
                if key in seen:
                    _fail(f, rec, f"duplicate po_id in tenant {tenant.pk}")
                seen.add(key)
                supplier = self.suppliers.get((tenant.pk, r["supplier_id"]))
                if supplier is None:
                    _fail(
                        f,
                        rec,
                        f"supplier {r['supplier_id']!r} does not exist in tenant {tenant.pk}",
                    )
                amount, currency = _money(r["amount"], "amount")
                if currency != tenant.currency:
                    _fail(f, rec, f"PO currency {currency} != tenant currency {tenant.currency}")
                lines = r["lines"]
                if not isinstance(lines, list) or not lines:
                    _fail(f, rec, "PO needs at least one line")
                parsed, total, nos = [], Decimal(0), set()
                for ln in lines:
                    no = _int(ln["line_no"], "line_no", 1)
                    if no in nos:
                        _fail(f, rec, f"duplicate line_no {no}")
                    nos.add(no)
                    price, lcur = _money(ln["unit_price"], f"line {no} unit_price")
                    if lcur != currency:
                        _fail(f, rec, f"line {no} currency {lcur} != PO currency {currency}")
                    qty = _int(ln["qty"], f"line {no} qty")
                    total += qty * price
                    parsed.append(
                        (
                            no,
                            {
                                "item_id": ln["item_id"],
                                "item_name": ln["item_name"],
                                "qty": qty,
                                "due_date": _date(ln["due_date"], f"line {no} due_date"),
                                "unit_price": _fit(price, 4, f"line {no} unit_price"),
                            },
                        )
                    )
                if total != amount:
                    _fail(f, rec, f"amount {amount} != sum of qty x unit_price {total}")
                expected = _date(r["expected_date"], "expected_date")
                latest = max(v["due_date"] for _, v in parsed)
                if expected != latest:
                    _fail(f, rec, f"expected_date {expected} != latest line due_date {latest}")
                values = {
                    "supplier": supplier,
                    "order_date": _date(r["order_date"], "order_date"),
                    "expected_date": expected,
                    "status": _choice(r["status"], PurchaseOrder.Status, "status"),
                    "amount": _fit(amount, 2, "amount"),
                    "currency": currency,
                }
                po = existing.get(key) or PurchaseOrder(tenant=tenant, erp_id=rec)
                _apply(po, values, self.stats, "purchase_orders")
                have = existing_lines.get(po.pk, {})
                for no, lv in parsed:
                    _apply(
                        have.get(no) or PurchaseOrderLine(po=po, tenant=tenant, line_no=no),
                        lv,
                        self.stats,
                        "po_lines",
                    )
                stale = [ln.pk for no, ln in have.items() if no not in nos]
                if stale:
                    PurchaseOrderLine.objects.filter(pk__in=stale, po=po).delete()
                    self.stats.add("po_lines", "deleted", len(stale))
                self.line_total += len(parsed)
        self.pos = {
            (p.tenant_id, p.erp_id): p
            for p in PurchaseOrder.objects.filter(tenant__in=self.tenants.values())
        }

    # ---- inventory -----------------------------------------------------------------------------
    def _inventory(self, records):
        f = FILES["inventory"]
        existing = {
            (i.tenant_id, i.item_id, i.location_id): i
            for i in Inventory.objects.filter(tenant__in=self.tenants.values())
        }
        seen = set()
        for r in records:
            rec = f"{r.get('tenant_id')}/{r.get('item_id')}/{r.get('location_id')}"
            with _record(f, rec):
                tenant = self._tenant(f, rec, r)
                loc = r["location_id"]
                if loc not in tenant.locations:
                    _fail(f, rec, f"location {loc!r} is not in tenant {tenant.pk} locations")
                key = (tenant.pk, r["item_id"], loc)
                if key in seen:
                    _fail(f, rec, "duplicate tenant + item_id + location_id")
                seen.add(key)
                values = {
                    "item_name": r["item_name"],
                    "on_hand": _int(r["on_hand"], "on_hand", -(2**31)),
                    "reserved": _int(r["reserved"], "reserved", -(2**31)),
                    "safety_stock": _int(r["safety_stock"], "safety_stock", -(2**31)),
                    "daily_demand": _fit(Decimal(str(r["daily_demand"])), 2, "daily_demand"),
                }
                obj = existing.get(key) or Inventory(
                    tenant=tenant, item_id=r["item_id"], location_id=loc
                )
                _apply(obj, values, self.stats, "inventory")

    # ---- shipments -----------------------------------------------------------------------------
    def _shipments(self, records):
        f = FILES["shipments"]
        existing = {
            (s.tenant_id, s.erp_id): s
            for s in Shipment.objects.filter(tenant__in=self.tenants.values())
        }
        seen = set()
        for r in records:
            rec = r.get("shipment_id", "?")
            with _record(f, rec):
                tenant = self._tenant(f, rec, r)
                key = (tenant.pk, rec)
                if key in seen:
                    _fail(f, rec, f"duplicate shipment_id in tenant {tenant.pk}")
                seen.add(key)
                po = self.pos.get((tenant.pk, r["po_id"]))
                if po is None:
                    _fail(f, rec, f"po {r['po_id']!r} does not exist in tenant {tenant.pk}")
                if po.supplier.erp_id != r["supplier_id"]:
                    _fail(
                        f,
                        rec,
                        f"supplier_id {r['supplier_id']!r} != supplier "
                        f"{po.supplier.erp_id!r} of {po.erp_id}",
                    )
                dest = r["destination_location_id"]
                if dest not in tenant.locations:
                    _fail(f, rec, f"location {dest!r} is not in tenant {tenant.pk} locations")
                departed = r["departed_at"]
                values = {
                    "po": po,
                    "carrier": r["carrier"],
                    "status": _choice(r["status"], Shipment.Status, "status"),
                    "origin": r["origin"],
                    "destination_location_id": dest,
                    "departed_at": None if departed is None else _aware(departed, "departed_at"),
                    "eta": _date(r["eta"], "eta"),
                }
                obj = existing.get(key) or Shipment(tenant=tenant, erp_id=rec)
                _apply(obj, values, self.stats, "shipments")

    # ---- manifest ------------------------------------------------------------------------------
    def _manifest(self, manifest, data):
        counts = manifest.get("counts") if isinstance(manifest, dict) else None
        if not isinstance(counts, dict):
            _fail(MANIFEST, "counts", "missing")
        for key in FILES:
            if counts.get(key) != len(data[key]):
                _fail(
                    MANIFEST,
                    key,
                    f"manifest count {counts.get(key)!r} != "
                    f"{len(data[key])} records in {FILES[key]}",
                )

    # ---- demo users ----------------------------------------------------------------------------
    def _users(self, password):
        User = get_user_model()
        for tid, tenant in self.tenants.items():
            if not tid.startswith("TEN-") or len(tid) <= 4:
                _fail("tenants.json", tid, "demo users need a tenant_id like TEN-<NAME>")
            slug = tid[4:].lower()
            for suffix, role, label in DEMO_ROLES:
                username = f"{slug}.{suffix}"
                user = User.objects.filter(username=username).first()
                if user is not None:
                    if user.tenant_id != tenant.pk:
                        raise CommandError(
                            f"user {username!r} already exists under another tenant; aborting"
                        )
                    self.stats.add("users", "unchanged")
                    continue
                user = User(
                    username=username,
                    email=f"{username}@example.com",
                    name=f"{tid[4:].capitalize()} {label}",
                    role=role,
                    tenant=tenant,
                )
                user.set_password(password)
                user.save()
                self.stats.add("users", "created")
