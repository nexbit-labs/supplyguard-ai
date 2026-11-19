"""N-01: agent schemas accept the valid fixtures and reject every invalid one.

Fixture naming: ai/fixtures/{valid,invalid}/<schema>__<case>.json
ai/fixtures/expected_errors.json maps each invalid case to the ONE jsonschema keyword it must fail on.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from ai.schema_registry import SCHEMA_NAMES, bundled_schema, load_schema, validator_for

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "ai" / "fixtures"
EXPECTED = json.loads((FIXTURES / "expected_errors.json").read_text(encoding="utf-8"))
AGENT_SCHEMAS = ("agent_input", "risk_analysis", "recovery_plan")


def _cases(kind: str):
    files = sorted((FIXTURES / kind).glob("*.json"))
    assert files, f"no {kind} fixtures found"
    return [pytest.param(f, id=f.stem) for f in files]


def _schema_of(path: Path) -> str:
    return path.stem.split("__", 1)[0]


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _contract_examples() -> list[dict]:
    """JSON blocks from CONTRACTS section 6, in order: risk analysis, recovery plan."""
    text = (ROOT / "docs" / "CONTRACTS.md").read_text(encoding="utf-8")
    section = text.split("## 6.", 1)[1].split("## 7.", 1)[0]
    return [json.loads(block) for block in re.findall(r"```json\n(.*?)```", section, re.S)]


@pytest.mark.parametrize("name", SCHEMA_NAMES)
def test_schema_is_valid_draft_2020_12(name):
    Draft202012Validator.check_schema(load_schema(name))


@pytest.mark.parametrize("path", _cases("valid"))
def test_valid_fixture_passes(path):
    errors = [e.message for e in validator_for(_schema_of(path)).iter_errors(_load(path))]
    assert errors == []


@pytest.mark.parametrize("path", _cases("invalid"))
def test_invalid_fixture_fails_for_intended_reason(path):
    errors = list(validator_for(_schema_of(path)).iter_errors(_load(path)))
    # Each invalid fixture is a valid one with ONE defect, so exactly one error, of the expected kind.
    assert len(errors) == 1, f"{path.name}: expected 1 error, got {[e.message for e in errors]}"
    assert errors[0].validator == EXPECTED[path.stem], errors[0].message


def test_expected_errors_map_matches_fixture_files():
    assert set(EXPECTED) == {p.stem for p in (FIXTURES / "invalid").glob("*.json")}


def test_all_option_types_fixture_covers_every_option_type():
    fixture = _load(FIXTURES / "valid" / "recovery_plan__all_option_types.json")
    enum = set(load_schema("common")["$defs"]["optionType"]["enum"])
    assert {o["option_type"] for o in fixture["options"]} == enum


def test_every_schema_has_valid_and_invalid_fixtures():
    for name in AGENT_SCHEMAS:
        assert list((FIXTURES / "valid").glob(f"{name}__*.json")), name
        assert list((FIXTURES / "invalid").glob(f"{name}__*.json")), name


@pytest.mark.parametrize("field", ["evidence", "assumptions", "missing_data", "facts", "estimates"])
def test_risk_analysis_governance_fields_required(field):
    assert field in load_schema("risk_analysis")["required"]


@pytest.mark.parametrize("field", ["evidence", "assumptions", "data_gaps"])
def test_recovery_option_governance_fields_required(field):
    assert field in load_schema("recovery_plan")["$defs"]["option"]["required"]


def test_contract_examples_validate_and_keys_match():
    """CONTRACTS section 6 examples are accepted, and their keys are exactly the schema's (no drift)."""
    risk, plan = _contract_examples()
    assert list(validator_for("risk_analysis").iter_errors(risk)) == []
    assert list(validator_for("recovery_plan").iter_errors(plan)) == []
    assert set(load_schema("risk_analysis")["properties"]) == set(risk)
    assert set(load_schema("recovery_plan")["properties"]) == set(plan)
    assert set(load_schema("recovery_plan")["$defs"]["option"]["properties"]) == set(plan["options"][0])


# --- bundled (single-file) form used by the N-09 Claude fallback -------------------------------

def _all_refs(node):
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "$ref":
                yield v
            else:
                yield from _all_refs(v)
    elif isinstance(node, list):
        for v in node:
            yield from _all_refs(v)


@pytest.mark.parametrize("name", AGENT_SCHEMAS)
def test_bundled_schema_has_only_internal_refs(name):
    schema = bundled_schema(name)
    Draft202012Validator.check_schema(schema)
    refs = list(_all_refs(schema))
    assert refs and all(r.startswith("#/$defs/") for r in refs)
    for r in refs:
        assert r.split("/", 2)[2] in schema["$defs"], r


@pytest.mark.parametrize("path", _cases("valid") + _cases("invalid"))
def test_bundled_schema_agrees_with_registry(path):
    payload = _load(path)
    bundled = Draft202012Validator(bundled_schema(_schema_of(path)))
    assert bundled.is_valid(payload) == validator_for(_schema_of(path)).is_valid(payload)
