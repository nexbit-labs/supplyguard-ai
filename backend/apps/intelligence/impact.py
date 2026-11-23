"""N-04 impact + risk scoring engine. Pure functions over dicts; see IMPACT_DESIGN.md.

No Django, no DB, no HTTP, no clock, no randomness: same input -> same output.
Money and ratios are Decimal internally; money leaves as 2-dp strings (ROUND_HALF_UP).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

WEIGHTS = {"shortage": 40, "exposure": 20, "mitigation": 15, "delay": 15, "severity": 10}
assert sum(WEIGHTS.values()) == 100

SEVERITY_VALUES = {
    "LOW": Decimal("0.25"),
    "MEDIUM": Decimal("0.5"),
    "HIGH": Decimal("0.75"),
    "CRITICAL": Decimal("1"),
}
ZERO = Decimal("0")
ONE = Decimal("1")
_US_PER_DAY = 86_400_000_000


class SnapshotError(ValueError):
    """The ERP extract is inconsistent (upstream bug): fail loudly."""


@dataclass(frozen=True)
class TenantThresholds:
    medium: int = 40
    high: int = 65
    critical: int = 85
    exposure_full_scale: Decimal = Decimal("500000")
    delay_full_scale_days: int = 5
    recovery_buffer_days: int = 0
    require_qualified_alternates: bool = True
    currency: str = "INR"


@dataclass(frozen=True)
class DisruptionWindow:
    start: datetime
    end: datetime
    outage_days: Decimal


@dataclass(frozen=True)
class AffectedLine:
    po_id: str
    line_no: int
    item_id: str
    qty: int
    due_date: date
    unit_price: Decimal
    currency: str
    delay_days: int
    exposure: Decimal


# --------------------------------------------------------------------------- helpers


def rows(collection: dict | list | None) -> list[dict]:
    """Normalise a `{count, results}` page, a plain list or None to a list."""
    if collection is None:
        return []
    if isinstance(collection, dict):
        return list(collection.get("results") or [])
    return list(collection)


def _dec(value) -> Decimal:
    return Decimal(str(value))


def _po_sort_key(po_id: str) -> tuple[int, str]:
    m = re.search(r"(\d+)$", po_id or "")
    return (int(m.group(1)) if m else 10**12, po_id or "")


def _check_tenant(event: dict, collection: list[dict], what: str) -> None:
    tenant = event.get("tenant_id")
    if tenant is None:
        return
    for r in collection:
        tid = r.get("tenant_id")
        if tid is not None and tid != tenant:
            raise SnapshotError(f"{what} row belongs to tenant {tid}, event is for {tenant}")


def money(amount: Decimal, currency: str) -> dict:
    return {"amount": str(amount.quantize(Decimal("0.01"), ROUND_HALF_UP)), "currency": currency}


def _r2(value: Decimal | None) -> float | None:
    return None if value is None else float(value.quantize(Decimal("0.01"), ROUND_HALF_UP))


def _r4(value: Decimal | None) -> float | None:
    return None if value is None else float(value.quantize(Decimal("0.0001"), ROUND_HALF_UP))


def _iso(dt: datetime) -> str:
    return (
        dt.strftime("%Y-%m-%dT%H:%M:%S")
        + (f".{dt.microsecond:06d}" if dt.microsecond else "")
        + "Z"
    )


# --------------------------------------------------------------------------- thresholds


def thresholds_from_tenant(tenant: dict | None) -> TenantThresholds:
    if not tenant:
        return TenantThresholds()
    d = TenantThresholds()
    settings = tenant.get("settings") or {}
    rt = settings.get("risk_thresholds") or {}
    medium = rt.get("medium", d.medium)
    high = rt.get("high", d.high)
    critical = rt.get("critical", d.critical)
    if not (0 <= medium <= high <= critical <= 100):
        raise ValueError("risk thresholds must satisfy 0 <= medium <= high <= critical <= 100")
    # exposure scale lives next to the 40/65/85 thresholds (per-tenant override, Q2)
    scale = rt.get(
        "exposure_full_scale", settings.get("exposure_full_scale", d.exposure_full_scale)
    )
    scale = _dec(scale)
    delay_scale = settings.get("delay_full_scale_days", d.delay_full_scale_days)
    if scale <= 0 or delay_scale <= 0:
        raise ValueError("full-scale values must be > 0")
    buffer_days = settings.get("recovery_buffer_days", d.recovery_buffer_days)
    if buffer_days < 0:
        raise ValueError("recovery_buffer_days must be >= 0")
    return TenantThresholds(
        medium=medium,
        high=high,
        critical=critical,
        exposure_full_scale=scale,
        delay_full_scale_days=delay_scale,
        recovery_buffer_days=buffer_days,
        require_qualified_alternates=bool(
            settings.get("require_qualified_alternates", d.require_qualified_alternates)
        ),
        currency=tenant.get("currency") or d.currency,
    )


# --------------------------------------------------------------------------- window / POs / lines


def parse_window(event: dict) -> DisruptionWindow:
    try:
        raw = event["event_time"]
        start = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        hours = event["expected_duration_hours"]
        if isinstance(hours, bool):
            raise TypeError("bool duration")
        hours_d = _dec(hours)
    except (KeyError, AttributeError, TypeError, ValueError, ArithmeticError) as exc:
        raise ValueError(f"invalid event time/duration: {exc}") from exc
    if hours_d < 0 or not hours_d.is_finite():
        raise ValueError("expected_duration_hours must be >= 0")
    start = start.replace(tzinfo=UTC) if start.tzinfo is None else start.astimezone(UTC)
    end = start + timedelta(seconds=float(hours_d * 3600))
    return DisruptionWindow(start=start, end=end, outage_days=hours_d / 24)


def select_evaluated_pos(event: dict, purchase_orders: list[dict]) -> list[dict]:
    _check_tenant(event, purchase_orders, "purchase order")
    supplier = event.get("supplier_id")
    out = [
        p for p in purchase_orders if p.get("status") == "OPEN" and p.get("supplier_id") == supplier
    ]
    if event.get("po_id"):
        out = [p for p in out if p.get("po_id") == event["po_id"]]
    return sorted(out, key=lambda p: _po_sort_key(p.get("po_id")))


def line_delay_days(due: date, window: DisruptionWindow, recovery_buffer_days: int = 0) -> int:
    due_dt = datetime(due.year, due.month, due.day, tzinfo=UTC)
    micros = (window.end - due_dt) // timedelta(microseconds=1)
    days = -(-micros // _US_PER_DAY)  # integer ceil
    return max(1, days) + recovery_buffer_days


def _valid_qty(po_id: str, qty) -> int:
    if isinstance(qty, bool) or not isinstance(qty, int) or qty < 0:
        raise SnapshotError(f"invalid qty {qty!r} on {po_id}")
    return qty


def select_affected_lines(
    evaluated_pos: list[dict], window: DisruptionWindow, recovery_buffer_days: int = 0
) -> list[AffectedLine]:
    out: list[AffectedLine] = []
    for po in evaluated_pos:
        for line in po.get("lines") or []:
            qty = _valid_qty(po["po_id"], line.get("qty"))
            if qty == 0:
                continue
            due = date.fromisoformat(line["due_date"])
            if due > window.end.date():
                continue
            price = line.get("unit_price") or {}
            if "amount" not in price:
                raise SnapshotError(
                    f"missing unit_price on {po['po_id']} line {line.get('line_no')}"
                )
            unit = _dec(price["amount"])
            out.append(
                AffectedLine(
                    po_id=po["po_id"],
                    line_no=line.get("line_no"),
                    item_id=line["item_id"],
                    qty=qty,
                    due_date=due,
                    unit_price=unit,
                    currency=price.get("currency"),
                    delay_days=line_delay_days(due, window, recovery_buffer_days),
                    exposure=unit * qty,
                )
            )
    if len({a.currency for a in out}) > 1:
        raise SnapshotError("mixed currencies in affected lines")
    return out


# --------------------------------------------------------------------------- inventory


def inventory_coverage(item_id: str, inventory: list[dict], outage_days: Decimal) -> list[dict]:
    result = []
    for r in sorted(
        (r for r in inventory if r.get("item_id") == item_id), key=lambda r: r["location_id"]
    ):
        on_hand = r.get("on_hand") or 0
        reserved = r.get("reserved") or 0
        safety = r.get("safety_stock") or 0
        demand_raw = r.get("daily_demand")
        demand = _dec(demand_raw) if demand_raw else None  # None / 0 -> no usable demand
        available = on_hand - reserved - safety
        if demand is None:
            cov = gross = surplus = None
        else:
            cov = Decimal(max(0, available)) / demand
            gross = Decimal(on_hand - reserved) / demand
            need = int((demand * outage_days).to_integral_value(rounding="ROUND_CEILING"))
            surplus = max(0, available - need)
        result.append(
            {
                "item_id": item_id,
                "location_id": r["location_id"],
                "on_hand": on_hand,
                "reserved": reserved,
                "safety_stock": safety,
                "daily_demand": demand_raw,
                "available_qty": available,
                "coverage_days": cov,
                "coverage_days_gross": gross,
                "transfer_surplus_qty": surplus,
            }
        )
    return result


def item_min_coverage(coverage_rows: list[dict]) -> Decimal | None:
    vals = [r["coverage_days"] for r in coverage_rows if r["coverage_days"] is not None]
    return min(vals) if vals else None


def shortage_gap_ratio(
    min_coverage: Decimal | None, has_inventory_rows: bool, outage_days: Decimal
) -> Decimal | None:
    if not has_inventory_rows:
        return ONE
    if min_coverage is None:
        return None
    if min_coverage < outage_days:
        return min(ONE, (outage_days - min_coverage) / outage_days)
    return ZERO


# --------------------------------------------------------------------------- mitigation


def _candidates(item_id, alternates, require_qualified, exclude_supplier_id=None) -> list[dict]:
    return [
        a
        for a in alternates
        if a.get("item_id") == item_id
        and a.get("supplier_id") != exclude_supplier_id
        and (a.get("qualified") is True or not require_qualified)
    ]


def mitigation_level(
    item_id: str,
    affected_qty: int,
    alternates: list[dict],
    require_qualified: bool = True,
    exclude_supplier_id: str | None = None,
) -> Decimal:
    cands = _candidates(item_id, alternates, require_qualified, exclude_supplier_id)
    if not cands:
        return ONE
    known = sum((_dec(c["capacity"]) for c in cands if c.get("capacity") is not None), ZERO)
    if known > 0 and known >= affected_qty:
        return ZERO
    if known > 0:
        return Decimal("0.25")
    return Decimal("0.5")


# --------------------------------------------------------------------------- score


def score_components(
    *,
    item_gaps: dict[str, Decimal | None],
    max_delay_days: int,
    total_exposure: Decimal,
    mitigation: dict[str, Decimal],
    severity: str,
    t: TenantThresholds,
) -> dict[str, Decimal]:
    if severity not in SEVERITY_VALUES:
        raise ValueError(f"unknown severity {severity!r}")
    gaps = [g for g in item_gaps.values() if g is not None and g > 0]
    if gaps:
        shortage = Decimal("0.5") + Decimal("0.5") * max(gaps)
    elif any(g is None for g in item_gaps.values()):
        shortage = Decimal("0.25")
    else:
        shortage = ZERO
    return {
        "shortage": shortage,
        "exposure": min(ONE, total_exposure / t.exposure_full_scale),
        "mitigation": max(mitigation.values(), default=ZERO),
        "delay": min(ONE, Decimal(max_delay_days) / t.delay_full_scale_days),
        "severity": SEVERITY_VALUES[severity],
    }


def _raw_score(components: dict[str, Decimal]) -> Decimal:
    return sum((WEIGHTS[k] * components[k] for k in WEIGHTS), ZERO)


def risk_score(components: dict[str, Decimal]) -> int:
    raw = max(ZERO, min(Decimal(100), _raw_score(components)))
    return int(raw.quantize(Decimal("1"), ROUND_HALF_UP))


def severity_band(score: int, t: TenantThresholds) -> str:
    if score >= t.critical:
        return "CRITICAL"
    if score >= t.high:
        return "HIGH"
    if score >= t.medium:
        return "MEDIUM"
    return "LOW"


# --------------------------------------------------------------------------- orchestration


def _dedupe(flags: list[str]) -> list[str]:
    return list(dict.fromkeys(flags))


def compute_impact(event: dict, snapshot: dict, thresholds: TenantThresholds | None = None) -> dict:
    t = thresholds or TenantThresholds()
    window = parse_window(event)
    window_out = {
        "start": _iso(window.start),
        "end": _iso(window.end),
        "outage_days": float(window.outage_days),
    }
    supplier = snapshot.get("supplier")
    if supplier is None:
        return {
            "supplier_found": False,
            "window": window_out,
            "evaluated_po_ids": [],
            "affected_po_ids": [],
            "affected_item_ids": [],
            "affected_pos": [],
            "items": [],
            "inventory_coverage": [],
            "qualified_alternate_supplier_ids": [],
            "total_exposure": money(ZERO, t.currency),
            "estimated_delay_days": 0,
            "shortage": False,
            "score_components": {},
            "risk_score": 0,
            "risk_level": "LOW",
            "data_quality_flags": ["supplier not found in ERP"],
        }

    pos = rows(snapshot.get("purchase_orders"))
    inventory = rows(snapshot.get("inventory"))
    alternates = rows(snapshot.get("alternate_suppliers"))
    _check_tenant(event, [supplier], "supplier")
    _check_tenant(event, inventory, "inventory")
    _check_tenant(event, alternates, "alternate supplier")

    evaluated = select_evaluated_pos(event, pos)
    lines = select_affected_lines(evaluated, window, t.recovery_buffer_days)
    currency = lines[0].currency if lines else t.currency
    total_exposure = sum((ln.exposure for ln in lines), ZERO)

    item_ids = sorted({ln.item_id for ln in lines})
    cov_rows: list[dict] = []
    items: list[dict] = []
    item_gaps: dict[str, Decimal | None] = {}
    mitigation: dict[str, Decimal] = {}
    item_cov: dict[str, Decimal | None] = {}
    item_short: dict[str, bool] = {}
    flags_demand: list[str] = []
    flags_noinv: list[str] = []
    flags_alt: list[str] = []
    for item in item_ids:
        qty = sum(ln.qty for ln in lines if ln.item_id == item)
        rws = inventory_coverage(item, inventory, window.outage_days)
        cov_rows.extend(rws)
        min_cov = item_min_coverage(rws)
        gap = shortage_gap_ratio(min_cov, bool(rws), window.outage_days)
        item_gaps[item] = gap
        item_cov[item] = min_cov
        item_short[item] = gap is not None and gap > 0
        mitigation[item] = mitigation_level(
            item, qty, alternates, t.require_qualified_alternates, event.get("supplier_id")
        )
        cands = _candidates(
            item, alternates, t.require_qualified_alternates, event.get("supplier_id")
        )
        if not rws:
            flags_noinv.append(f"no inventory record for {item}")
        for r in rws:
            if not r["daily_demand"]:
                if r["daily_demand"] is None:
                    flags_demand.append(f"daily_demand missing for {item} at {r['location_id']}")
        for c in sorted(cands, key=lambda c: c["supplier_id"]):
            if c.get("capacity") is None:
                flags_alt.append(f"capacity unknown for alternate {c['supplier_id']} ({item})")
        items.append(
            {
                "item_id": item,
                "affected_qty": qty,
                "min_coverage_days": _r2(min_cov),
                "shortage": item_short[item],
                "shortage_gap_ratio": _r4(gap),
                "mitigation_level": float(mitigation[item]),
                "candidate_alternate_supplier_ids": sorted({c["supplier_id"] for c in cands}),
            }
        )

    # affected_pos: one row per (po_id, item_id)
    groups: dict[tuple[str, str], list[AffectedLine]] = {}
    for ln in lines:
        groups.setdefault((ln.po_id, ln.item_id), []).append(ln)
    affected_pos = []
    for (po_id, item), lns in sorted(
        groups.items(), key=lambda kv: (_po_sort_key(kv[0][0]), kv[0][1])
    ):
        affected_pos.append(
            {
                "po_id": po_id,
                "item_id": item,
                "line_nos": [ln.line_no for ln in lns],
                "due_date": min(ln.due_date for ln in lns).isoformat(),
                "affected_qty": sum(ln.qty for ln in lns),
                "estimated_delay_days": max(ln.delay_days for ln in lns),
                "exposure_amount": money(sum((ln.exposure for ln in lns), ZERO), currency),
                "inventory_coverage_days": _r2(item_cov[item]),
                "shortage": item_short[item],
            }
        )

    max_delay = max((ln.delay_days for ln in lines), default=0)
    comps = score_components(
        item_gaps=item_gaps,
        max_delay_days=max_delay,
        total_exposure=total_exposure,
        mitigation=mitigation,
        severity=event.get("severity"),
        t=t,
    )
    score = risk_score(comps)

    flags = []
    if supplier.get("capacity_per_week") is None:
        flags.append("supplier capacity not provided")
    flags += flags_demand + flags_noinv + flags_alt
    flags += [
        f"unit_price is zero for {ln.po_id} line {ln.line_no}" for ln in lines if ln.unit_price == 0
    ]

    return {
        "supplier_found": True,
        "window": window_out,
        "evaluated_po_ids": [p["po_id"] for p in evaluated],
        "affected_po_ids": sorted({ln.po_id for ln in lines}, key=_po_sort_key),
        "affected_item_ids": item_ids,
        "affected_pos": affected_pos,
        "items": items,
        "inventory_coverage": [
            {
                **r,
                "coverage_days": _r2(r["coverage_days"]),
                "coverage_days_gross": _r2(r["coverage_days_gross"]),
            }
            for r in cov_rows
        ],
        "qualified_alternate_supplier_ids": sorted(
            {s for i in items for s in i["candidate_alternate_supplier_ids"]}
        ),
        "total_exposure": money(total_exposure, currency),
        "estimated_delay_days": max_delay,
        "shortage": any(item_short.values()),
        "score_components": {
            k: {
                "weight": WEIGHTS[k],
                "value": str(comps[k].quantize(Decimal("0.0001"), ROUND_HALF_UP)),
                "points": str((WEIGHTS[k] * comps[k]).quantize(Decimal("0.01"), ROUND_HALF_UP)),
            }
            for k in WEIGHTS
        },
        "risk_score": score,
        "risk_level": severity_band(score, t),
        "data_quality_flags": _dedupe(flags),
    }


_AGENT_ROW_KEYS = (
    "po_id",
    "item_id",
    "affected_qty",
    "estimated_delay_days",
    "exposure_amount",
    "inventory_coverage_days",
    "shortage",
)


def to_agent_impact(result: dict) -> dict:
    return {
        "risk_score": result["risk_score"],
        "total_exposure": result["total_exposure"],
        "affected_pos": [{k: row[k] for k in _AGENT_ROW_KEYS} for row in result["affected_pos"]],
    }
