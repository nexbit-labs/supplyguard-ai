"""N-07 agent output validation: JSON-schema check + fact-check against snapshot / impact.

Pure functions over dicts (no Django models, no DB). A later piece persists the result:
`raw`/`valid`/`validation_errors` -> AgentRun, per-option `unsupported`/`unsupported_reasons`
-> Recommendation.

Three layers, in order (schema = column types, fact check = join against the source tables):
  1. `validate_schema(kind, payload)`   - shape (ai/schemas/*.json, `common.schema.json` refs)
  2. `fact_check_recovery_plan(...)`    - per option: does every id / qty / price / date exist?
     `fact_check_risk_analysis(...)`    - evidence + affected POs + score vs snapshot / impact
  3. `validate_agent_output(...)`       - both, folded into one result dict

Policy: a recovery option that fails any fact check is `unsupported` (kept for audit, never
actionable); the run itself stays `valid` unless the schema failed or a plan-wide fact is wrong
(e.g. wrong risk_case_id). A risk analysis with wrong facts is not valid (nothing in it can be
trusted as one unit).

Reason / error entries are `{"code": <stable code>, "message": <human text>}`. Codes:
  plan-wide : RISK_CASE_MISMATCH
  recovery  : DUPLICATE_RANK, PO_NOT_FOUND, PO_NOT_AFFECTED, ITEM_NOT_AFFECTED,
              SUPPLIER_NOT_FOUND, SUPPLIER_NOT_ALTERNATE_FOR_ITEM, SUPPLIER_DISRUPTED,
              SUPPLIER_NOT_QUALIFIED, QTY_EXCEEDS_AFFECTED, QTY_EXCEEDS_CAPACITY,
              LOCATION_NOT_FOUND, TRANSFER_SAME_LOCATION, TRANSFER_EXCEEDS_AVAILABLE,
              REVISED_DATE_MISMATCH, LEAD_TIME_MISMATCH, LEAD_TIME_UNSUPPORTED,
              COST_DELTA_MISMATCH, COST_DELTA_UNSUPPORTED, CURRENCY_MISMATCH,
              EVIDENCE_UNRESOLVED, EVIDENCE_MISMATCH, PRICE_MISMATCH, CAPACITY_MISMATCH
  risk      : RISK_CASE_MISMATCH, RISK_SCORE_MISMATCH, RISK_LEVEL_MISMATCH,
              AFFECTED_PO_UNKNOWN, AFFECTED_PO_MISSING, ENTITY_NOT_FOUND,
              CAPACITY_NOT_IN_MISSING_DATA, CAPACITY_INVENTED, EVIDENCE_UNRESOLVED,
              EVIDENCE_MISMATCH, PRICE_MISMATCH, CAPACITY_MISMATCH, LEAD_TIME_MISMATCH
"""

from __future__ import annotations

import json
import os
import re
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from functools import lru_cache
from pathlib import Path

from apps.intelligence.impact import rows

RISK_ANALYSIS = "RISK_ANALYSIS"
RECOVERY_PLAN = "RECOVERY_PLAN"
KINDS = {RISK_ANALYSIS: "risk_analysis", RECOVERY_PLAN: "recovery_plan"}
CENT = Decimal("0.01")

_ID_TOKEN = re.compile(r"\b[A-Z]{2,5}-[0-9A-Z-]*\d[0-9A-Z-]*\b")
_DATE_TOKEN = re.compile(r"\b\d{4}-\d{2}-\d{2}(?:T[\d:.]+Z?)?\b")
_CAPACITY_EVENT = re.compile(r"capacity[ _]reduction", re.I)  # an event type, not a capacity figure
_NUMBER_WORD = (
    r"(?:\d|percent|%|half|quarter|zero|one|two|three|four|five|six|seven|eight|nine|ten|twenty|"
    r"thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|thousand)"
)
_CAPACITY_TALK = re.compile(
    r"\d[\d,.]*\s*(?:units?\s*)?(?:per|/|a)\s*(?:week|day)"
    r"|capacity\s+(?:is|was|has|had|dropped|fell|down|reduced)\b[^.;]{0,40}?\b" + _NUMBER_WORD,
    re.I,
)
_CAPACITY_FIGURE = re.compile(r"capacity[^.;]{0,30}?\d|\d[^.;]{0,30}?capacity", re.I)


def reason(code: str, message: str) -> dict:
    return {"code": code, "message": message}


# --------------------------------------------------------------------------- schema


MAX_RANK = 1000
MAX_LEAD_DAYS = Decimal("99999.99")  # Recommendation.estimated_lead_time_days is Decimal(7, 2)
MAX_COST_AMOUNT = Decimal("999999999999.99")  # estimated_cost_delta is Decimal(14, 2)
SCHEMA_DIR_ENV = "SG_SCHEMA_DIR"
SCHEMA_FILES = ("common", "risk_analysis", "recovery_plan")


def _schema_dir() -> Path:
    """Folder with the agent JSON Schemas: $SG_SCHEMA_DIR, Django setting SG_SCHEMA_DIR, else the
    repo ai/schemas folder (a backend-only Docker image must set the variable)."""
    configured = os.environ.get(SCHEMA_DIR_ENV)
    if not configured:
        try:
            from django.conf import settings

            configured = getattr(settings, SCHEMA_DIR_ENV, None)
        except Exception:  # settings not configured (pure-function use)
            configured = None
    folder = (
        Path(configured) if configured else Path(__file__).resolve().parents[3] / "ai" / "schemas"
    )
    if all((folder / f"{n}.schema.json").is_file() for n in SCHEMA_FILES):
        return folder
    names = ", ".join(n + ".schema.json" for n in SCHEMA_FILES)
    raise RuntimeError(
        f"agent JSON Schemas not found in {folder}. Set {SCHEMA_DIR_ENV} to a folder "
        f"containing {names} (the repo ai/schemas folder)."
    )


@lru_cache(maxsize=4)
def _validator_for(folder: str, name: str):
    from jsonschema import Draft202012Validator
    from referencing import Registry, Resource

    docs = {
        n: json.loads((Path(folder) / f"{n}.schema.json").read_text(encoding="utf-8"))
        for n in SCHEMA_FILES
    }
    registry = Registry().with_resources(
        [(d["$id"], Resource.from_contents(d)) for d in docs.values()]
    )
    return Draft202012Validator(docs[name], registry=registry)


def _schema_validator(kind: str):
    if kind not in KINDS:
        raise ValueError(f"unknown kind {kind!r}; expected one of {sorted(KINDS)}")
    return _validator_for(str(_schema_dir()), KINDS[kind])


def validate_schema(kind: str, payload) -> list[dict]:
    """Schema errors as `[{"path": "options.0.rank", "message": ...}]`; empty list = valid."""
    errors = sorted(
        _schema_validator(kind).iter_errors(payload), key=lambda e: list(map(str, e.absolute_path))
    )
    return [
        {"path": ".".join(map(str, e.absolute_path)) or "$", "message": e.message} for e in errors
    ]


# --------------------------------------------------------------------------- value helpers


def _dec(value) -> Decimal | None:
    """Plain decimal numbers only: no bool, no exponent notation (5e3), no NaN/Infinity."""
    if isinstance(value, bool) or value is None:
        return None
    text = str(value).replace(",", "")
    if isinstance(value, str) and re.search(r"[eE]", text):
        return None
    try:
        d = Decimal(text)
    except (InvalidOperation, ValueError):
        return None
    return d if d.is_finite() else None


def _int(value) -> int | None:
    """An integer value (4000 or 4000.0); None for anything else, so callers fail closed."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def _amount(value):
    return value["amount"] if isinstance(value, dict) and "amount" in value else value


def _same(actual, cited) -> bool:
    actual = _amount(actual)
    if actual is None or cited is None:
        return actual is None and cited is None
    if isinstance(actual, bool) or isinstance(cited, bool):
        return actual is cited
    a, c = _dec(actual), _dec(cited)
    return a == c if a is not None and c is not None else str(actual) == str(cited)


def _money(value) -> tuple[Decimal | None, str | None]:
    if isinstance(value, dict):
        return _dec(value.get("amount")), value.get("currency")
    return None, None


def _r2(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


class _Ctx:
    """Indexed view of the snapshot + impact so each check is a lookup, not a scan."""

    def __init__(self, snapshot: dict, impact: dict, event: dict | None = None):
        self.snapshot = snapshot or {}
        self.impact = impact or {}
        self.event = event
        s = self.snapshot
        self.supplier = s.get("supplier") if isinstance(s.get("supplier"), dict) else {}
        self.pos = {p["po_id"]: p for p in rows(s.get("purchase_orders")) if "po_id" in p}
        self.inventory = rows(s.get("inventory"))
        self.alternates = rows(s.get("alternate_suppliers"))
        self.affected = rows(self.impact.get("affected_pos"))
        self.disrupted_supplier = self.supplier.get("supplier_id") or (event or {}).get(
            "supplier_id"
        )

    def affected_rows(self, po_id: str) -> list[dict]:
        return [r for r in self.affected if r.get("po_id") == po_id]

    def po_line(self, po_id: str, item_id: str) -> dict | None:
        for line in (self.pos.get(po_id) or {}).get("lines") or []:
            if line.get("item_id") == item_id:
                return line
        return None

    def inv_row(self, item_id: str, location_id: str) -> dict | None:
        return next(
            (
                r
                for r in self.inventory
                if r.get("item_id") == item_id and r.get("location_id") == location_id
            ),
            None,
        )

    def due_date(self, po_id: str, row: dict) -> str | None:
        if row.get("due_date"):
            return row["due_date"]
        line = self.po_line(po_id, row.get("item_id"))
        return line.get("due_date") if line else None


# --------------------------------------------------------------------------- evidence


def _candidates(ev: dict, c: _Ctx) -> list | None:
    """Every input value the evidence pointer can address; None = not checkable (no event)."""
    src, ent, field = ev["source"], ev["entity"], ev["field"]
    item, loc = ev.get("item_id"), ev.get("location_id")
    out: list = []
    if src == "EVENT":
        event = c.event or c.snapshot.get("event")
        if not isinstance(event, dict):
            return None
        if ent == event.get("event_id") and field in event:
            out.append(event[field])
    elif src == "IMPACT":
        if ent == "IMPACT" and field in c.impact:
            out.append(c.impact[field])
        else:
            out += [
                r[field]
                for r in c.affected
                if r.get("po_id") == ent
                and (item is None or r.get("item_id") == item)
                and field in r
            ]
    elif src == "ERP":
        if ent == c.supplier.get("supplier_id") and field in c.supplier:
            out.append(c.supplier[field])
        out += [
            a[field]
            for a in c.alternates
            if a.get("supplier_id") == ent
            and (item is None or a.get("item_id") == item)
            and field in a
        ]
        po = c.pos.get(ent)
        if po:
            if field in po and item is None:
                out.append(po[field])
            out += [
                ln[field]
                for ln in po.get("lines") or []
                if (item is None or ln.get("item_id") == item) and field in ln
            ]
        out += [
            r[field]
            for r in c.inventory
            if r.get("item_id") == ent
            and (loc is None or r.get("location_id") == loc)
            and field in r
        ]
    return out


def _evidence_code(field: str) -> str:
    if field == "unit_price":
        return "PRICE_MISMATCH"
    if field.startswith("capacity"):
        return "CAPACITY_MISMATCH"
    if field == "lead_time_days":
        return "LEAD_TIME_MISMATCH"
    return "EVIDENCE_MISMATCH"


def check_evidence(evidence: list[dict], c: _Ctx) -> list[dict]:
    """One reason per evidence entry that does not point at a real value, or cites a different one.

    A pointer that addresses several values (no item_id / location_id) passes when the cited value
    equals any of them.
    """
    out = []
    for ev in evidence or []:
        label = f"{ev['source']}/{ev['entity']}/{ev['field']}"
        cands = _candidates(ev, c)
        if cands is None:
            continue
        if not cands:
            out.append(
                reason(
                    "EVIDENCE_UNRESOLVED", f"{label} not found in the {ev['source'].lower()} data"
                )
            )
        elif not any(_same(v, ev["value"]) for v in cands):
            shown = ", ".join(repr(_amount(v)) for v in cands)
            out.append(
                reason(
                    _evidence_code(ev["field"]),
                    f"{label}: cited {ev['value']!r}, input has {shown}",
                )
            )
    return out


# --------------------------------------------------------------------------- recovery plan


SUPPLIER_EVIDENCE_FIELDS = {"unit_price", "lead_time_days", "qualified", "capacity"}
ALLOWED_CHANGE_KEYS = {
    "ALTERNATE_SUPPLIER": {"supplier_id", "qty"},
    "SPLIT_ORDER": {"supplier_id", "qty"},
    "INVENTORY_TRANSFER": {"item_id", "from_location_id", "to_location_id", "qty"},
    "REVISED_SCHEDULE": {"expected_date"},
}


def _alt_row(c: _Ctx, supplier_id: str, items: list[str], preferred: str | None):
    rows_ = [
        a for a in c.alternates if a.get("supplier_id") == supplier_id and a.get("item_id") in items
    ]
    if not rows_:
        return None
    return next((a for a in rows_ if a.get("item_id") == preferred), rows_[0])


def _qty_limit_reason(qty: int | None, affected: list[dict], item: str | None, po_id: str):
    """qty must not exceed the affected qty of the option item (any item of the PO if unknown)."""
    rows_ = [r for r in affected if r.get("item_id") == item] or affected
    if qty is None or not rows_:
        return []
    limit = max(r["affected_qty"] for r in rows_)
    if qty > limit:
        msg = f"qty {qty} is above the affected qty {limit} of {po_id}"
        return [reason("QTY_EXCEEDS_AFFECTED", msg)]
    return []


def _range_reasons(option: dict) -> list[dict]:
    """Numbers must fit the Recommendation columns (Decimal 7,2 and 14,2): out of range is
    unsupported (VALUE_OUT_OF_RANGE) and the persistence step stores null for it."""
    out = []
    lead = _dec(option.get("estimated_lead_time_days"))
    if option.get("estimated_lead_time_days") is not None and (
        lead is None or abs(lead) > MAX_LEAD_DAYS
    ):
        out.append(reason("VALUE_OUT_OF_RANGE", "estimated_lead_time_days is out of range"))
    cost = option.get("estimated_cost_delta")
    if cost is not None:
        amount = _dec(cost.get("amount")) if isinstance(cost, dict) else None
        if amount is None or abs(amount) > MAX_COST_AMOUNT:
            out.append(reason("VALUE_OUT_OF_RANGE", "estimated_cost_delta.amount is out of range"))
    return out


def _check_option(option: dict, c: _Ctx) -> list[dict]:
    reasons: list[dict] = _range_reasons(option)
    action = option["proposed_action"]
    change = action["change"]
    po_id = action["po_id"]
    otype = option["option_type"]
    raw_qty = change.get("qty")
    qty = _int(raw_qty)
    if raw_qty is not None and qty is None:
        reasons.append(reason("QTY_INVALID", f"qty {raw_qty!r} is not a whole number"))

    foreign = sorted(set(change) - ALLOWED_CHANGE_KEYS.get(otype, set(change)))
    if foreign:
        reasons.append(
            reason("CHANGE_FIELD_NOT_ALLOWED", f"{otype} may not change {', '.join(foreign)}")
        )

    affected = c.affected_rows(po_id)
    if po_id not in c.pos:
        reasons.append(reason("PO_NOT_FOUND", f"{po_id} is not in the snapshot"))
    elif not affected:
        reasons.append(
            reason("PO_NOT_AFFECTED", f"{po_id} is not an affected PO in the impact result")
        )
    items = [r["item_id"] for r in affected]
    target = affected[0] if affected else None

    if otype in ("ALTERNATE_SUPPLIER", "SPLIT_ORDER"):
        sup = change.get("supplier_id")
        evidence_item = next(
            (
                e.get("item_id")
                for e in option["evidence"]
                if e.get("entity") == sup and e.get("item_id")
            ),
            None,
        )
        if not any(
            e.get("source") == "ERP"
            and e.get("entity") == sup
            and e.get("field") in SUPPLIER_EVIDENCE_FIELDS
            for e in option["evidence"]
        ):
            reasons.append(reason("EVIDENCE_MISSING", f"no ERP evidence cited for supplier {sup}"))
        alt = _alt_row(c, sup, items, evidence_item)
        if sup == c.disrupted_supplier:
            reasons.append(
                reason("SUPPLIER_DISRUPTED", f"{sup} is the disrupted supplier, not an alternate")
            )
        if alt is None:
            reasons += _qty_limit_reason(qty, affected, None, po_id)
            if not any(a.get("supplier_id") == sup for a in c.alternates):
                reasons.append(
                    reason(
                        "SUPPLIER_NOT_FOUND", f"{sup} is not an alternate supplier in the snapshot"
                    )
                )
            else:
                reasons.append(
                    reason(
                        "SUPPLIER_NOT_ALTERNATE_FOR_ITEM",
                        f"{sup} is not an alternate for the affected item(s) "
                        f"{', '.join(items) or '-'}",
                    )
                )
        else:
            target = next((r for r in affected if r["item_id"] == alt["item_id"]), target)
            reasons += _qty_limit_reason(qty, affected, alt["item_id"], po_id)
            if otype == "SPLIT_ORDER" and qty is not None and target is not None:
                if qty >= target["affected_qty"]:
                    reasons.append(
                        reason(
                            "SPLIT_NOT_PARTIAL",
                            f"a split must move less than the affected qty "
                            f"{target['affected_qty']}",
                        )
                    )
            if alt.get("qualified") is not True:
                reasons.append(
                    reason("SUPPLIER_NOT_QUALIFIED", f"{sup} is not qualified for {alt['item_id']}")
                )
            cap = _int(alt.get("capacity"))
            if cap is not None and qty is not None and qty > cap:
                reasons.append(
                    reason("QTY_EXCEEDS_CAPACITY", f"qty {qty} is above {sup} capacity {cap}")
                )
            # no qty = the whole affected line: recompute the cost delta on that
            cost_qty = qty if qty is not None else (target or {}).get("affected_qty")
            reasons += _check_alt_numbers(option, c, po_id, alt, cost_qty)
    elif otype == "INVENTORY_TRANSFER":
        reasons += _qty_limit_reason(qty, affected, change.get("item_id"), po_id)
        reasons += _check_transfer(option, c, change, items, qty, po_id)
    elif otype == "REVISED_SCHEDULE":
        reasons += _check_revised(option, c, po_id, target, change)

    reasons += check_evidence(option["evidence"], c)
    return reasons


def _check_alt_numbers(option: dict, c: _Ctx, po_id: str, alt: dict, qty) -> list[dict]:
    out = []
    lead = option.get("estimated_lead_time_days")
    if lead is not None and lead != alt.get("lead_time_days"):
        out.append(
            reason(
                "LEAD_TIME_MISMATCH",
                f"estimated_lead_time_days {lead} differs from snapshot "
                f"{alt.get('lead_time_days')}",
            )
        )
    cost = option.get("estimated_cost_delta")
    if cost is None:
        return out
    alt_price, alt_cur = _money(alt.get("unit_price"))
    line = c.po_line(po_id, alt["item_id"])
    po_price, po_cur = _money(line.get("unit_price")) if line else (None, None)
    if alt_price is None or po_price is None or qty is None:
        out.append(
            reason(
                "COST_DELTA_UNSUPPORTED",
                "no usable unit price (alternate or PO) or qty in the snapshot to compute a delta",
            )
        )
        return out
    if alt_cur != po_cur:
        out.append(
            reason(
                "CURRENCY_MISMATCH",
                f"alternate priced in {alt_cur}, PO in {po_cur}: no cost delta can be computed",
            )
        )
        return out
    if cost["currency"] != po_cur:
        out.append(
            reason(
                "CURRENCY_MISMATCH",
                f"cost delta currency {cost['currency']} differs from PO {po_cur}",
            )
        )
    expected = _r2((alt_price - po_price) * qty)
    if _dec(cost["amount"]) != expected:
        out.append(
            reason(
                "COST_DELTA_MISMATCH",
                f"estimated_cost_delta {cost['amount']} differs from recomputed {expected} "
                f"(({alt_price} - {po_price}) x {qty})",
            )
        )
    return out


def _check_transfer(
    option: dict, c: _Ctx, change: dict, items: list[str], qty, po_id: str = ""
) -> list[dict]:
    out = []
    item, src, dst = (
        change.get("item_id"),
        change.get("from_location_id"),
        change.get("to_location_id"),
    )
    if items and item not in items:
        out.append(reason("ITEM_NOT_AFFECTED", f"{item} is not an affected item of the PO"))
    po = c.pos.get(po_id) or {}
    delivery = po.get("delivery_location_id") or po.get("location_id")
    if delivery and dst != delivery:
        out.append(
            reason("TRANSFER_WRONG_DESTINATION", f"{po_id} is delivered to {delivery}, not {dst}")
        )
    if src == dst:
        out.append(reason("TRANSFER_SAME_LOCATION", f"from and to location are both {src}"))
    from_row, to_row = c.inv_row(item, src), c.inv_row(item, dst)
    for loc, r in ((src, from_row), (dst, to_row)):
        if r is None:
            out.append(reason("LOCATION_NOT_FOUND", f"no inventory record for {item} at {loc}"))
    if from_row is not None and qty is not None:
        stock = [_int(from_row.get(k)) for k in ("on_hand", "reserved", "safety_stock")]
        if None in stock:
            out.append(
                reason(
                    "TRANSFER_STOCK_UNKNOWN",
                    f"on_hand / reserved / safety_stock of {item} at {src} are not all known",
                )
            )
        else:
            available = stock[0] - stock[1] - stock[2]
            if qty > available:
                out.append(
                    reason(
                        "TRANSFER_EXCEEDS_AVAILABLE",
                        f"qty {qty} is above the {available} available at {src} "
                        f"(on_hand {stock[0]} - reserved {stock[1]} - safety_stock {stock[2]})",
                    )
                )
    if option.get("estimated_lead_time_days") is not None:
        out.append(
            reason("LEAD_TIME_UNSUPPORTED", "snapshot has no transit time; lead time must be null")
        )
    if option.get("estimated_cost_delta") is not None:
        out.append(
            reason(
                "COST_DELTA_UNSUPPORTED", "snapshot has no transfer cost; cost delta must be null"
            )
        )
    return out


def _check_revised(
    option: dict, c: _Ctx, po_id: str, target: dict | None, change: dict
) -> list[dict]:
    out = []
    if target is not None:
        delay = target.get("estimated_delay_days")
        if (
            option.get("estimated_lead_time_days") is not None
            and option["estimated_lead_time_days"] != delay
        ):
            out.append(
                reason(
                    "LEAD_TIME_MISMATCH",
                    f"estimated_lead_time_days {option['estimated_lead_time_days']} differs from "
                    f"estimated delay {delay}",
                )
            )
        due = c.due_date(po_id, target)
        delay_days = _int(delay) if _int(delay) is not None else None
        try:
            expected = (
                (date.fromisoformat(due) + timedelta(days=delay_days)).isoformat()
                if due and delay_days is not None
                else None
            )
        except (ValueError, TypeError):
            expected = None
        if expected is None:
            out.append(
                reason(
                    "REVISED_DATE_UNSUPPORTED",
                    "expected_date cannot be recomputed (estimated delay or due date unknown); "
                    "it must not be written to the ERP",
                )
            )
        elif change.get("expected_date") != expected:
            out.append(
                reason(
                    "REVISED_DATE_MISMATCH",
                    f"expected_date {change.get('expected_date')} differs from due date {due} "
                    f"+ {delay_days} day(s) = {expected}",
                )
            )
    else:
        out.append(
            reason("REVISED_DATE_UNSUPPORTED", "no affected line to recompute expected_date from")
        )
    cost = option.get("estimated_cost_delta")
    if cost is not None and _dec(cost["amount"]) != 0:
        out.append(
            reason(
                "COST_DELTA_MISMATCH",
                f"a schedule change has no price effect; got {cost['amount']}",
            )
        )
    return out


def fact_check_recovery_plan(
    plan: dict,
    snapshot: dict,
    impact: dict,
    *,
    event: dict | None = None,
    risk_case_id: str | None = None,
) -> dict:
    """Check each option against the snapshot / impact.

    Returns `{"plan_errors": [...], "options": [{"rank", "unsupported", "unsupported_reasons"}]}` in
    plan order.
    `plan_errors` are plan-wide (wrong risk_case_id); they do not mark options unsupported by
    themselves
    (validate_agent_output turns them into `valid=False`).
    """
    c = _Ctx(snapshot, impact, event)
    plan_errors = []
    if risk_case_id is not None and plan.get("risk_case_id") != risk_case_id:
        plan_errors.append(
            reason(
                "RISK_CASE_MISMATCH",
                f"plan is for {plan.get('risk_case_id')}, expected {risk_case_id}",
            )
        )
    options = plan.get("options") or []
    ranks = [o["rank"] for o in options]
    # Plan-level: ranks key the Recommendation rows (unique per case + round), so a repeated or
    # absurd rank makes the whole plan unusable, not just one option.
    for rank in sorted({r for r in ranks if ranks.count(r) > 1}, key=str):
        plan_errors.append(
            reason("DUPLICATE_RANK", f"rank {rank} is used by more than one option")
        )
    for rank in ranks:
        if not isinstance(rank, int) or isinstance(rank, bool) or not 1 <= rank <= MAX_RANK:
            plan_errors.append(reason("RANK_OUT_OF_RANGE", f"rank {rank!r} is not 1..{MAX_RANK}"))
    seen: set[str] = set()
    results = []
    for option in options:
        reasons = list(plan_errors)  # a plan-wide failure makes every option unsupported
        change = dict((option.get("proposed_action") or {}).get("change") or {})
        if _int(change.get("qty")) is not None:
            change["qty"] = _int(change["qty"])
        key = json.dumps(
            [option.get("option_type"), (option.get("proposed_action") or {}).get("po_id"), change],
            sort_keys=True,
            default=str,
        )
        if key in seen:
            msg = "same option type and proposed action as an earlier option"
            reasons.append(reason("DUPLICATE_OPTION", msg))
        seen.add(key)
        try:
            reasons += _check_option(option, c)
        except Exception as exc:  # fail closed: one odd payload must not stop the AgentRun
            msg = f"option could not be checked ({type(exc).__name__}: {exc})"
            reasons.append(reason("VALIDATOR_ERROR", msg))
        results.append(
            {"rank": option["rank"], "unsupported": bool(reasons), "unsupported_reasons": reasons}
        )
    return {"plan_errors": plan_errors, "options": results}


# --------------------------------------------------------------------------- risk analysis


def _entity_exists(c: _Ctx, etype: str, eid: str) -> bool:
    if etype == "SUPPLIER":
        return eid == c.supplier.get("supplier_id") or any(
            a.get("supplier_id") == eid for a in c.alternates
        )
    if etype == "PO":
        return eid in c.pos
    if etype == "ITEM":
        return (
            any(r.get("item_id") == eid for r in c.inventory)
            or any(a.get("item_id") == eid for a in c.alternates)
            or any(ln.get("item_id") == eid for p in c.pos.values() for ln in p.get("lines") or [])
        )
    if etype == "LOCATION":
        return any(r.get("location_id") == eid for r in c.inventory)
    return True  # SHIPMENT: the snapshot carries no shipments, nothing to check against


def _capacity_null(c: _Ctx) -> bool:
    return (
        c.supplier.get("capacity_per_week") is None
        and "capacity_per_week" in c.supplier
        or (c.supplier.get("capacity") is None and "capacity" in c.supplier)
    )


def _fact_check_risk_analysis(
    analysis: dict,
    snapshot: dict,
    impact: dict,
    *,
    event: dict | None = None,
    risk_case_id: str | None = None,
) -> list[dict]:
    """Plan-wide fact errors for a risk analysis (empty list = every checkable claim matches)."""
    c = _Ctx(snapshot, impact, event)
    out = []
    if risk_case_id is not None and analysis.get("risk_case_id") != risk_case_id:
        out.append(
            reason(
                "RISK_CASE_MISMATCH",
                f"analysis is for {analysis.get('risk_case_id')}, expected {risk_case_id}",
            )
        )
    if "risk_score" in c.impact and analysis.get("risk_score") != c.impact["risk_score"]:
        out.append(
            reason(
                "RISK_SCORE_MISMATCH",
                f"risk_score {analysis.get('risk_score')} differs from impact "
                f"{c.impact['risk_score']}",
            )
        )
    if c.impact.get("risk_level") and analysis.get("risk_level") != c.impact["risk_level"]:
        out.append(
            reason(
                "RISK_LEVEL_MISMATCH",
                f"risk_level {analysis.get('risk_level')} differs from impact "
                f"{c.impact['risk_level']}",
            )
        )

    listed_pos = set()
    for ent in analysis.get("affected_entities") or []:
        if ent["type"] == "PO":
            listed_pos.add(ent["id"])
            if ent["id"] not in {r["po_id"] for r in c.affected}:
                out.append(
                    reason(
                        "AFFECTED_PO_UNKNOWN",
                        f"{ent['id']} is not an affected PO in the impact result",
                    )
                )
        if not _entity_exists(c, ent["type"], ent["id"]):
            out.append(
                reason("ENTITY_NOT_FOUND", f"{ent['type']} {ent['id']} is not in the snapshot")
            )
    for po_id in sorted({r["po_id"] for r in c.affected} - listed_pos):
        out.append(
            reason(
                "AFFECTED_PO_MISSING", f"{po_id} is affected per the impact result but not listed"
            )
        )

    out += check_evidence(analysis.get("evidence"), c)

    if _capacity_null(c):
        if not any("capacity" in m.lower() for m in analysis.get("missing_data") or []):
            out.append(
                reason(
                    "CAPACITY_NOT_IN_MISSING_DATA",
                    "supplier capacity is null in the snapshot but missing_data does not mention "
                    "it",
                )
            )
        claims = (
            [analysis.get("summary", "")]
            + list(analysis.get("facts") or [])
            + [e.get("statement", "") for e in analysis.get("estimates") or []]
        )
        for text in claims:
            for sentence in re.split(r"(?<=[.;])\s+", text):
                other_supplier = re.findall(r"SUP-\d+", sentence)
                about_disrupted = c.disrupted_supplier in other_supplier or not other_supplier
                cleaned = _CAPACITY_EVENT.sub("", _DATE_TOKEN.sub("", _ID_TOKEN.sub("", sentence)))
                if about_disrupted and (
                    _CAPACITY_FIGURE.search(cleaned) or _CAPACITY_TALK.search(cleaned)
                ):
                    out.append(
                        reason(
                            "CAPACITY_INVENTED",
                            f"states a capacity figure the snapshot lacks: {sentence!r}",
                        )
                    )
    return out


# --------------------------------------------------------------------------- combined


def validate_agent_output(
    kind: str,
    payload,
    snapshot: dict,
    impact: dict,
    *,
    event: dict | None = None,
    risk_case_id: str | None = None,
) -> dict:
    """Schema check, then fact check. Result for the persistence step:

    `{"valid": bool, "schema_errors": [...], "fact_errors": [...],
      "options": [{"rank", "unsupported", "unsupported_reasons"}], "actionable_count": int}`

    -> AgentRun: raw = payload, valid = result["valid"], validation_errors = schema_errors +
    fact_errors.
    -> Recommendation per option: unsupported / unsupported_reasons. Recovery plans with options []
    and a
       no_viable_option_reason are valid (TC-05). On a schema failure no options are returned (shape
       untrusted).
    `event` (CONTRACTS s3 event dict) lets EVENT evidence be checked; without it those entries are
    skipped.
    """
    schema_errors = validate_schema(kind, payload)
    result = {
        "valid": False,
        "schema_errors": schema_errors,
        "fact_errors": [],
        "options": [],
        "actionable_count": 0,
    }
    if schema_errors:
        return result
    if kind == RISK_ANALYSIS:
        result["fact_errors"] = fact_check_risk_analysis(
            payload, snapshot, impact, event=event, risk_case_id=risk_case_id
        )
    else:
        checked = fact_check_recovery_plan(
            payload, snapshot, impact, event=event, risk_case_id=risk_case_id
        )
        result["fact_errors"] = checked["plan_errors"]
        result["options"] = checked["options"]
        result["actionable_count"] = sum(1 for o in checked["options"] if not o["unsupported"])
    result["valid"] = not result["fact_errors"]
    if not result["valid"]:
        result["actionable_count"] = 0
    return result


def fact_check_risk_analysis(
    analysis: dict,
    snapshot: dict,
    impact: dict,
    *,
    event: dict | None = None,
    risk_case_id: str | None = None,
) -> list[dict]:
    """Plan-wide fact errors for a risk analysis (empty list = every checkable claim matches).

    Fails closed: malformed data gives a VALIDATOR_ERROR entry.
    """
    try:
        return _fact_check_risk_analysis(
            analysis, snapshot, impact, event=event, risk_case_id=risk_case_id
        )
    except Exception as exc:
        return [
            reason(
                "VALIDATOR_ERROR", f"analysis could not be checked ({type(exc).__name__}: {exc})"
            )
        ]
