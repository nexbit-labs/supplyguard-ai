"""One validator for `Tenant.settings` (docs/CONTRACTS.md s10, Tenant row).

Used by `PATCH /api/v1/settings`. (The U-03 seed loader still has its own copy.)

Shape: `risk_thresholds` {medium < high < critical, each 0..100; optional exposure_full_scale in
(0, 1e12]}, `max_cost_increase_pct` (number 0..1000), `require_qualified_alternates` (bool).
Unknown keys, booleans where numbers are required, strings, NaN and Infinity are rejected.
Errors are raised as a DRF `ValidationError` keyed by field path,
e.g. `{"risk_thresholds": {"high": [...]}}`.
"""
import copy
import math

from rest_framework.exceptions import ValidationError

TOP_KEYS = ("risk_thresholds", "max_cost_increase_pct", "require_qualified_alternates")
LEVELS = ("medium", "high", "critical")
RT_KEYS = (*LEVELS, "exposure_full_scale")
DEFAULT_EXPOSURE_FULL_SCALE = 500000  # applies when the key is absent (read by N-04)


LEVEL_MAX = 100  # risk thresholds are scores 0..100
PCT_MAX = 1000
EXPOSURE_MAX = 1e12


def _is_number(v) -> bool:
    if not isinstance(v, (int, float)) or isinstance(v, bool):
        return False
    try:
        return math.isfinite(v)
    except OverflowError:  # an int too large for a float, e.g. 10**400
        return False


def effective_settings(stored) -> dict:
    """What the API returns: the stored settings plus the documented default of
    `risk_thresholds.exposure_full_scale` when thresholds exist but it is absent. Never stored."""
    out = copy.deepcopy(stored) if isinstance(stored, dict) else {}
    rt = out.get("risk_thresholds")
    if isinstance(rt, dict):
        rt.setdefault("exposure_full_scale", DEFAULT_EXPOSURE_FULL_SCALE)
    return out


def _validate_thresholds(rt, partial, errors):
    if not isinstance(rt, dict):
        errors["risk_thresholds"] = ["Must be an object."]
        return None
    sub: dict = {}
    for key in rt:
        if key not in RT_KEYS:
            sub[key] = ["Unknown key."]
    for key in RT_KEYS:
        if key not in rt:
            if not partial and key in LEVELS:
                sub[key] = ["This field is required."]
            continue
        value = rt[key]
        if not _is_number(value):
            sub[key] = ["Must be a finite number."]
        elif key == "exposure_full_scale":
            if not 0 < value <= EXPOSURE_MAX:
                sub[key] = [f"Must be greater than 0 and at most {EXPOSURE_MAX:g}."]
        elif not 0 <= value <= LEVEL_MAX:
            sub[key] = [f"Must be between 0 and {LEVEL_MAX}."]
    if sub:
        errors["risk_thresholds"] = sub
        return None
    return rt


def _check_ordering(rt, errors):
    present = [(k, rt[k]) for k in LEVELS if k in rt]
    for (_, low), (name, high) in zip(present, present[1:], strict=False):
        if not low < high:
            errors.setdefault("risk_thresholds", {})[name] = [
                "Thresholds must satisfy medium < high < critical."
            ]
            return


def drop_default_exposure(new: dict, old: dict) -> dict:
    """GET shows the default `exposure_full_scale`, so a client may send it straight back.
    When it equals the default and was not stored before, it is not a change: leave it out."""
    rt = new.get("risk_thresholds")
    old_rt = (old or {}).get("risk_thresholds") or {}
    if (
        isinstance(rt, dict)
        and "exposure_full_scale" not in old_rt
        and rt.get("exposure_full_scale") == DEFAULT_EXPOSURE_FULL_SCALE
    ):
        rt = {k: v for k, v in rt.items() if k != "exposure_full_scale"}
        new = {**new, "risk_thresholds": rt}
    return new


def validate_tenant_settings(data, partial: bool = False, current: dict | None = None) -> dict:
    """Return the cleaned settings dict or raise `ValidationError`.

    partial=False: `data` is the complete new settings (all three top-level keys, all three
    levels). partial=True: `data` holds only the keys to change; the result is `current` with
    those keys applied, `risk_thresholds` merged key by key, and the ordering is re-validated on
    the merged thresholds. `current` is only used in partial mode.
    """
    if not isinstance(data, dict):
        raise ValidationError({"non_field_errors": ["Settings must be a JSON object."]})
    errors: dict = {}
    for key in data:
        if key not in TOP_KEYS:
            errors[key] = ["Unknown setting."]
    if not partial:
        for key in TOP_KEYS:
            if key not in data:
                errors[key] = ["This field is required."]

    if "risk_thresholds" in data:
        _validate_thresholds(data["risk_thresholds"], partial, errors)
    if "max_cost_increase_pct" in data:
        pct = data["max_cost_increase_pct"]
        if not _is_number(pct) or not 0 <= pct <= PCT_MAX:
            errors["max_cost_increase_pct"] = [f"Must be a number between 0 and {PCT_MAX}."]
    if "require_qualified_alternates" in data and not isinstance(
        data["require_qualified_alternates"], bool
    ):
        errors["require_qualified_alternates"] = ["Must be true or false."]

    result = copy.deepcopy(current) if (partial and isinstance(current, dict)) else {}
    if not errors:
        for key in TOP_KEYS:
            if key not in data:
                continue
            if key == "risk_thresholds":
                merged = dict(result.get("risk_thresholds") or {}) if partial else {}
                merged.update(data["risk_thresholds"])
                result["risk_thresholds"] = merged
            else:
                result[key] = data[key]
        if "risk_thresholds" in data:
            _check_ordering(result["risk_thresholds"], errors)
    if errors:
        raise ValidationError(errors)
    return result
