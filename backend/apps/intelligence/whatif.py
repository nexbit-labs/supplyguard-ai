"""N-11 what-if simulator: a synthetic disruption run through the N-04 impact engine.

No database access and no Django request objects here: `fetch_snapshot` talks to the ERP over
HTTP (read-only), `run_what_if` is a pure function of (scenario, snapshot, tenant). The views
add auth, tenant scoping and the one audit row. Every number is an estimate (AC-03).
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from decimal import Decimal

import httpx

from . import impact

logger = logging.getLogger(__name__)

EVENT_TYPES = (
    "FACTORY_OUTAGE",
    "SHIPMENT_DELAY",
    "QUALITY_HOLD",
    "CAPACITY_REDUCTION",
    "LOGISTICS_DISRUPTION",
)
MIN_DAYS, MAX_DAYS = 1, 90
DISCLAIMER = (
    "Simulation only: every figure below is an estimate computed on a fresh ERP snapshot. "
    "Nothing was saved or changed in the ERP."
)


class ErpError(Exception):
    """Base for ERP read failures."""


class ErpNotFound(ErpError):
    """The ERP has no such supplier for this tenant."""


class ErpTimeout(ErpError):
    pass


class ErpUnavailable(ErpError):
    pass


class ForeignSupplier(ErpError):
    """The snapshot's supplier row belongs to another tenant (an ERP that ignored X-Tenant-ID)."""


# --------------------------------------------------------------------------- ERP snapshot


def make_http_client(base_url: str, api_key: str, timeout: float) -> httpx.Client:
    headers = {"X-Api-Key": api_key} if api_key else {}
    return httpx.Client(base_url=base_url, headers=headers, timeout=timeout)


class _Budget:
    """Total time allowed for all sequential ERP calls (clock injectable for tests)."""

    def __init__(self, per_call: float, total: float, clock=time.monotonic):
        self.per_call = per_call
        self.clock = clock
        self.deadline = clock() + total

    def next_timeout(self) -> float:
        left = self.deadline - self.clock()
        if left <= 0:
            raise ErpTimeout("ERP time budget used up")
        return min(self.per_call, left)


def _get(client, path, tenant_id, correlation_id, budget: _Budget, params=None):
    headers = {"X-Tenant-ID": tenant_id}
    if correlation_id:
        headers["X-Correlation-ID"] = correlation_id
    try:
        resp = client.get(path, params=params, headers=headers, timeout=budget.next_timeout())
    except httpx.TimeoutException as exc:
        raise ErpTimeout("ERP did not answer in time") from exc
    except httpx.HTTPError as exc:
        raise ErpUnavailable("ERP is not reachable") from exc
    if resp.status_code == 404:
        raise ErpNotFound(path)
    if resp.status_code != 200:
        logger.warning("ERP %s answered %s", path, resp.status_code)
        raise ErpUnavailable(f"ERP answered {resp.status_code}")
    try:
        return resp.json()
    except ValueError as exc:
        raise ErpUnavailable("ERP returned invalid JSON") from exc


def fetch_snapshot(
    client,
    supplier_id: str,
    tenant_id: str,
    correlation_id: str | None,
    *,
    per_call_timeout: float = 2.0,
    total_timeout: float = 2.5,
    clock=time.monotonic,
) -> dict:
    """Fresh read-only snapshot: supplier, its OPEN POs, inventory + alternates for their items.

    Only a 404 on the supplier means "supplier not found". A 404 on the PO list is a broken ERP
    (502); a 404 on inventory / alternates becomes an empty list plus a `fetch_gaps` entry.
    """
    budget = _Budget(per_call_timeout, total_timeout, clock)
    supplier = _get(client, f"/erp/suppliers/{supplier_id}", tenant_id, correlation_id, budget)
    try:
        pos = _get(
            client,
            "/erp/purchase-orders",
            tenant_id,
            correlation_id,
            budget,
            {"supplier_id": supplier_id, "status": "OPEN"},
        )
    except ErpNotFound as exc:
        raise ErpUnavailable("ERP purchase-order list not found") from exc
    item_ids = sorted({ln["item_id"] for po in impact.rows(pos) for ln in po.get("lines") or []})
    inventory: list[dict] = []
    alternates: list[dict] = []
    fetch_gaps: list[str] = []
    for item in item_ids:
        for path, bucket, label in (
            ("/erp/inventory", inventory, "inventory"),
            ("/erp/alternate-suppliers", alternates, "alternate suppliers"),
        ):
            try:
                bucket += impact.rows(
                    _get(client, path, tenant_id, correlation_id, budget, {"item_id": item})
                )
            except ErpNotFound:
                fetch_gaps.append(f"ERP returned 404 for {label} of {item}; treated as none")
    return {
        "supplier": supplier,
        "purchase_orders": impact.rows(pos),
        "inventory": inventory,
        "alternate_suppliers": alternates,
        "fetch_gaps": fetch_gaps,
    }


def check_snapshot_tenant(snapshot: dict, tenant_id: str) -> None:
    """Every row must carry this tenant's id. Foreign supplier -> ForeignSupplier (404 upstream);
    any other missing / foreign row -> SnapshotError (502): the ERP data cannot be trusted."""
    supplier = snapshot.get("supplier") or {}
    if supplier.get("tenant_id") != tenant_id:
        raise ForeignSupplier()
    for key in ("purchase_orders", "inventory", "alternate_suppliers"):
        for row in snapshot.get(key) or []:
            if row.get("tenant_id") != tenant_id:
                raise impact.SnapshotError(f"{key} row without this tenant's tenant_id")


# --------------------------------------------------------------------------- scenario


def severity_for(duration_hours: Decimal) -> str:
    """Deterministic severity for a typed scenario (no real event exists): longer = worse."""
    days = duration_hours / 24
    if days <= 3:
        return "MEDIUM"
    if days <= 14:
        return "HIGH"
    return "CRITICAL"


def build_event(
    *,
    tenant_id: str,
    supplier_id: str,
    event_type: str,
    duration_hours: Decimal,
    severity: str | None,
    start: datetime,
) -> dict:
    """`start` is always passed in (request value, case event time or the injected clock)."""
    start = start.astimezone(UTC).replace(microsecond=0)
    return {
        "event_id": "WHATIF",
        "tenant_id": tenant_id,
        "supplier_id": supplier_id,
        "event_type": event_type,
        "severity": severity or severity_for(duration_hours),
        "event_time": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "expected_duration_hours": float(duration_hours),
        "description": "What-if simulation (not a real event)",
        "source_system": "what-if",
    }


# --------------------------------------------------------------------------- best option


def _money(amount: Decimal, currency: str) -> dict:
    return impact.money(amount, currency)


def _current_price(snapshot: dict, supplier_id: str, item_id: str):
    """(weighted avg open-PO unit price, currency) of the disrupted supplier, else (None, None)."""
    total = Decimal(0)
    qty = 0
    currency = None
    for po in snapshot["purchase_orders"]:
        if po.get("supplier_id") != supplier_id or po.get("status") != "OPEN":
            continue
        for ln in po.get("lines") or []:
            if ln.get("item_id") == item_id and ln.get("unit_price"):
                q = ln.get("qty") or 0
                total += Decimal(str(ln["unit_price"]["amount"])) * q
                qty += q
                currency = currency or ln["unit_price"].get("currency")
    return (total / qty, currency) if qty else (None, None)


def _item_option(item: dict, snapshot: dict, result: dict, t, supplier_id, currency) -> dict:
    item_id, qty = item["item_id"], item["affected_qty"]
    out = {"item_id": item_id, "affected_qty": qty, "recommended": None, "data_gap": None}
    if not item["shortage"] and item["min_coverage_days"] is not None:
        return {
            **out,
            "status": "COVERED_BY_STOCK",
            "note": "Existing stock covers the estimated outage; no action modelled.",
        }
    current, current_ccy = _current_price(snapshot, supplier_id, item_id)
    gap = None
    candidates = []
    for c in impact._candidates(
        item_id, snapshot["alternate_suppliers"], t.require_qualified_alternates, supplier_id
    ):
        cap = c.get("capacity")
        if cap is not None and cap < qty:
            continue
        price = Decimal(str(c["unit_price"]["amount"]))
        alt_ccy = c["unit_price"].get("currency") or currency
        delta = None
        if current is None:
            gap = gap or f"current unit price unknown for {item_id}"
        elif current_ccy and alt_ccy != current_ccy:
            gap = (
                f"currency mismatch for {item_id}: alternate {c['supplier_id']} is in {alt_ccy}, "
                f"open PO in {current_ccy}; no cost delta"
            )
        else:
            delta = (price - current) * qty
        candidates.append(
            {
                "option_type": "ALTERNATE_SUPPLIER",
                "supplier_id": c["supplier_id"],
                "qty": qty,
                "unit_price": _money(price, alt_ccy),
                "lead_time_days": c.get("lead_time_days"),
                "capacity_known": cap is not None,
                "estimated_cost_delta": _money(delta, currency) if delta is not None else None,
                "_sort": (price, c.get("lead_time_days") or 0, c["supplier_id"]),
            }
        )
    candidates.sort(key=lambda c: c["_sort"])

    chosen = None
    for r in result["inventory_coverage"]:  # sorted by location_id: deterministic
        if r["item_id"] == item_id and (r["transfer_surplus_qty"] or 0) >= qty:
            chosen = {
                "option_type": "INVENTORY_TRANSFER",
                "from_location_id": r["location_id"],
                "qty": qty,
                "lead_time_days": None,
                "estimated_cost_delta": _money(Decimal(0), currency),
            }
            break
    if chosen is None and candidates:
        chosen = {k: v for k, v in candidates[0].items() if not k.startswith("_")}
        out["data_gap"] = gap
    if chosen is None:
        return {
            **out,
            "status": "NO_OPTION",
            "note": "No qualified alternate with enough capacity and no transferable stock.",
        }
    return {**out, "status": "RECOMMENDED", "recommended": chosen, "note": None}


def best_option_estimate(result: dict, snapshot: dict, t, supplier_id: str) -> dict | None:
    if not result["items"]:
        return None
    currency = result["total_exposure"]["currency"]
    per_item = [
        _item_option(i, snapshot, result, t, supplier_id, currency) for i in result["items"]
    ]
    chosen = [p["recommended"] for p in per_item if p["recommended"]]
    deltas = [
        Decimal(c["estimated_cost_delta"]["amount"]) for c in chosen if c["estimated_cost_delta"]
    ]
    leads = [c["lead_time_days"] for c in chosen if c.get("lead_time_days") is not None]
    return {
        "is_estimate": True,
        "rule": (
            "Per item: stock transfer if a location has surplus above the outage need, else the "
            "cheapest qualified alternate whose capacity is enough or unknown."
        ),
        "items": per_item,
        "uncovered_item_ids": [p["item_id"] for p in per_item if p["status"] == "NO_OPTION"],
        "total_estimated_cost_delta": _money(sum(deltas, Decimal(0)), currency),
        "cost_delta_complete": len(deltas) == len(chosen),
        "max_lead_time_days": max(leads) if leads else None,
    }


# --------------------------------------------------------------------------- orchestration


def run_what_if(
    *,
    tenant: dict,
    event: dict,
    snapshot: dict,
    scenario_extra: dict | None = None,
    assumptions_extra: list[str] | None = None,
) -> dict:
    """`tenant` = {"tenant_id", "currency", "settings"}; returns the API response body."""
    gaps: list[str] = list(snapshot.get("fetch_gaps") or [])
    try:
        t = impact.thresholds_from_tenant(tenant)
    except (ValueError, TypeError, ArithmeticError):
        t = impact.TenantThresholds(currency=tenant.get("currency") or "INR")
        gaps.append("tenant risk settings invalid; default thresholds used")
    result = impact.compute_impact(event, snapshot, t)
    supplier_id = event["supplier_id"]
    supplier = snapshot.get("supplier") or {}

    window = result["window"]
    duration_hours = Decimal(str(event["expected_duration_hours"]))
    assumptions = [
        "Every figure is an ESTIMATE from a simulated disruption, not a real event.",
        f"The supplier is fully unable to deliver for {float(duration_hours / 24):g} days from "
        f"{window['start']}; event type {event['event_type']} does not change the maths.",
        "Open POs with a line due on or before the end of the outage are treated as late.",
        "Stock coverage = (on hand - reserved - safety stock) / daily demand per location.",
        f"Severity input to the risk score: {event['severity']}.",
        "Alternate supplier capacity is units per week; like the N-04 engine it is compared "
        "with the whole affected quantity (weekly pacing of the delivery is not modelled).",
        "Alternate lead times and prices are taken from the ERP as-is; "
        + (
            "only qualified alternates are used."
            if t.require_qualified_alternates
            else "unqualified alternates are allowed (tenant setting)."
        ),
        "Inventory transfers are assumed free and instant (no cost or lead time modelled).",
        "Nothing was written to the ERP or saved as a risk case.",
    ]
    assumptions += assumptions_extra or []
    if not result["affected_pos"]:
        gaps.append("no open purchase order line falls inside the simulated outage window")
    gaps += result["data_quality_flags"]
    option = best_option_estimate(result, snapshot, t, supplier_id)
    if option:
        for item in option["items"]:
            if item["data_gap"]:
                gaps.append(item["data_gap"])
            rec = item["recommended"]
            if rec and rec["option_type"] == "ALTERNATE_SUPPLIER" and not rec["capacity_known"]:
                gaps.append(
                    f"capacity unknown for recommended alternate {rec['supplier_id']} "
                    f"({item['item_id']})"
                )
    gaps = list(dict.fromkeys(gaps))

    items = result["items"]
    return {
        "estimate": True,
        "disclaimer": DISCLAIMER,
        "labels": {
            k: "ESTIMATE"
            for k in ("affected_pos", "inventory", "exposure", "risk", "best_option_estimate")
        },
        "scenario": {
            "supplier_id": supplier_id,
            "supplier_name": supplier.get("name"),
            "event_type": event["event_type"],
            "severity": event["severity"],
            "duration_days": float(duration_hours / 24),
            "duration_hours": float(duration_hours),
            "start": window["start"],
            "end": window["end"],
            **(scenario_extra or {}),
        },
        "affected_pos": result["affected_pos"],
        "inventory": {
            "gaps": [i for i in items if i["shortage"]],
            "items": items,
            "coverage": result["inventory_coverage"],
        },
        "exposure": result["total_exposure"],
        "estimated_delay_days": result["estimated_delay_days"],
        "shortage": result["shortage"],
        "risk": {
            "score": result["risk_score"],
            "level": result["risk_level"],
            "components": result["score_components"],
        },
        "best_option_estimate": option,
        "assumptions": assumptions,
        "data_gaps": gaps,
    }
