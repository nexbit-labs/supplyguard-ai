"""Pre-write checks of an ERP snapshot: sizes and ranges that would otherwise fail inside the
database (a 500) become a 400 before anything is written.

Messages name the field, never the value (a snapshot is never echoed). Content rules of the
impact engine (tenant ids, currencies, quantities) stay in apps.intelligence.
"""
import re
from decimal import Decimal, InvalidOperation

from rest_framework.exceptions import ValidationError

from apps.core.models import AffectedPO

AMOUNT_LIMIT = Decimal(10) ** 12  # DecimalField(14, 2) holds < 10**12
QUANTITY_LIMIT = Decimal(10) ** 9  # below the PositiveIntegerField maximum, far above real stock
CURRENCY = re.compile(r"^[A-Z]{3}$")
ID_MAX = AffectedPO._meta.get_field("po_id").max_length  # 40
ITEM_ID_MAX = AffectedPO._meta.get_field("item_id").max_length  # 40
ITEM_NAME_MAX = AffectedPO._meta.get_field("item_name").max_length  # 200


def _fail(where: str, what: str):
    raise ValidationError({"snapshot": [f"{where}: {what}"]})


def _text(row: dict, key: str, where: str, limit: int):
    value = row.get(key)
    if value is None:
        return
    if not isinstance(value, str) or len(value) > limit:
        _fail(where, f"{key} must be a string of at most {limit} characters")


def _number(value, where: str, key: str, limit: Decimal):
    if value is None:
        return
    if isinstance(value, bool):
        _fail(where, f"{key} must be a number")
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        _fail(where, f"{key} must be a number")
    if not number.is_finite() or abs(number) >= limit:
        _fail(where, f"{key} is out of range")


def _money(value, where: str, key: str):
    if value is None:
        return
    if not isinstance(value, dict):
        _fail(where, f"{key} must be an object with amount and currency")
    _number(value.get("amount"), where, f"{key}.amount", AMOUNT_LIMIT)
    currency = value.get("currency")
    if currency is not None and (not isinstance(currency, str) or not CURRENCY.match(currency)):
        _fail(where, f"{key}.currency must be a 3-letter upper-case code")


def _rows(snapshot: dict, key: str) -> list[dict]:
    return [r for r in (snapshot.get(key) or []) if isinstance(r, dict)]


def check_snapshot_ranges(snapshot: dict) -> None:
    for po in _rows(snapshot, "purchase_orders"):
        where = "purchase_orders"
        _text(po, "po_id", where, ID_MAX)
        _text(po, "supplier_id", where, ID_MAX)
        _money(po.get("amount"), where, "amount")
        for line in po.get("lines") or []:
            if not isinstance(line, dict):
                _fail(where, "lines must be objects")
            _text(line, "item_id", where, ITEM_ID_MAX)
            _text(line, "item_name", where, ITEM_NAME_MAX)
            _number(line.get("qty"), where, "lines.qty", QUANTITY_LIMIT)
            _money(line.get("unit_price"), where, "lines.unit_price")
    for row in _rows(snapshot, "inventory"):
        where = "inventory"
        _text(row, "item_id", where, ITEM_ID_MAX)
        _text(row, "item_name", where, ITEM_NAME_MAX)
        _text(row, "location_id", where, ID_MAX)
        for key in ("on_hand", "reserved", "safety_stock", "daily_demand"):
            _number(row.get(key), where, key, QUANTITY_LIMIT)
    for row in _rows(snapshot, "alternate_suppliers"):
        where = "alternate_suppliers"
        _text(row, "item_id", where, ITEM_ID_MAX)
        _text(row, "supplier_id", where, ID_MAX)
        _number(row.get("capacity"), where, "capacity", QUANTITY_LIMIT)
        _number(row.get("lead_time_days"), where, "lead_time_days", QUANTITY_LIMIT)
        _money(row.get("unit_price"), where, "unit_price")
