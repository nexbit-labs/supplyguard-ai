import copy

import pytest
from rest_framework.exceptions import ValidationError

from apps.tenants.settings_schema import validate_tenant_settings

FULL = {
    "risk_thresholds": {"medium": 40, "high": 65, "critical": 85},
    "max_cost_increase_pct": 15,
    "require_qualified_alternates": True,
}


def with_rt(**rt):
    d = copy.deepcopy(FULL)
    d["risk_thresholds"].update(rt)
    return d


@pytest.mark.parametrize(
    "data",
    [
        FULL,
        with_rt(exposure_full_scale=250000),
        with_rt(exposure_full_scale=0.5),
        {**FULL, "max_cost_increase_pct": 0},
        {**FULL, "max_cost_increase_pct": 12.5},
        {**FULL, "require_qualified_alternates": False},
        with_rt(medium=0.1, high=0.2, critical=0.3),
    ],
)
def test_valid_full(data):
    assert validate_tenant_settings(data) == data


@pytest.mark.parametrize(
    ("data", "path"),
    [
        ({**FULL, "surprise": 1}, ["surprise"]),
        ({"max_cost_increase_pct": 1, "require_qualified_alternates": True}, ["risk_thresholds"]),
        (with_rt(exposure_full_scale=0), ["risk_thresholds", "exposure_full_scale"]),
        (with_rt(exposure_full_scale=-5), ["risk_thresholds", "exposure_full_scale"]),
        (with_rt(exposure_full_scale=True), ["risk_thresholds", "exposure_full_scale"]),
        (with_rt(exposure_full_scale="500000"), ["risk_thresholds", "exposure_full_scale"]),
        (with_rt(exposure_full_scale=float("inf")), ["risk_thresholds", "exposure_full_scale"]),
        (with_rt(medium=float("nan")), ["risk_thresholds", "medium"]),
        (with_rt(high=True), ["risk_thresholds", "high"]),
        (with_rt(high="65"), ["risk_thresholds", "high"]),
        (with_rt(extra=1), ["risk_thresholds", "extra"]),
        (with_rt(medium=70), ["risk_thresholds", "high"]),  # medium > high
        (with_rt(high=85), ["risk_thresholds", "critical"]),  # high == critical
        ({**FULL, "risk_thresholds": [1, 2, 3]}, ["risk_thresholds"]),
        ({**FULL, "max_cost_increase_pct": -1}, ["max_cost_increase_pct"]),
        ({**FULL, "max_cost_increase_pct": True}, ["max_cost_increase_pct"]),
        ({**FULL, "max_cost_increase_pct": "10"}, ["max_cost_increase_pct"]),
        ({**FULL, "max_cost_increase_pct": float("inf")}, ["max_cost_increase_pct"]),
        ({**FULL, "require_qualified_alternates": "yes"}, ["require_qualified_alternates"]),
        ({**FULL, "require_qualified_alternates": 1}, ["require_qualified_alternates"]),
        ({**FULL, "tenant_id": "TEN-X"}, ["tenant_id"]),
    ],
)
def test_invalid_full_reports_field_path(data, path):
    with pytest.raises(ValidationError) as exc:
        validate_tenant_settings(data)
    node = exc.value.detail
    for step in path:
        assert step in node, (path, node)
        node = node[step]


def test_not_an_object():
    for bad in (None, [], "x", 3):
        with pytest.raises(ValidationError):
            validate_tenant_settings(bad)


def test_partial_merges_and_keeps_untouched_keys():
    out = validate_tenant_settings(
        {"risk_thresholds": {"high": 70}}, partial=True, current=copy.deepcopy(FULL)
    )
    assert out["risk_thresholds"] == {"medium": 40, "high": 70, "critical": 85}
    assert out["max_cost_increase_pct"] == 15
    assert out["require_qualified_alternates"] is True


def test_partial_ordering_checked_against_stored_values():
    with pytest.raises(ValidationError) as exc:
        validate_tenant_settings(
            {"risk_thresholds": {"high": 90}}, partial=True, current=copy.deepcopy(FULL)
        )  # 90 >= stored critical 85
    assert "critical" in exc.value.detail["risk_thresholds"]


def test_partial_exposure_is_independent_of_ordering():
    out = validate_tenant_settings(
        {"risk_thresholds": {"exposure_full_scale": 1}}, partial=True, current=copy.deepcopy(FULL)
    )
    assert out["risk_thresholds"]["exposure_full_scale"] == 1


def test_partial_still_validates_types_and_unknown_keys():
    for bad in ({"max_cost_increase_pct": True}, {"nope": 1}, {"risk_thresholds": {"x": 1}}):
        with pytest.raises(ValidationError):
            validate_tenant_settings(bad, partial=True, current=copy.deepcopy(FULL))


def test_partial_does_not_mutate_current():
    current = copy.deepcopy(FULL)
    validate_tenant_settings({"risk_thresholds": {"high": 70}}, partial=True, current=current)
    assert current == FULL


def test_partial_on_empty_stored_settings():
    out = validate_tenant_settings({"max_cost_increase_pct": 3}, partial=True, current={})
    assert out == {"max_cost_increase_pct": 3}
