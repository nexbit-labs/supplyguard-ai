"""Load the agent JSON Schemas with their cross-file $refs resolved.

Usage (N-07 validator, N-09 fallback client, tests):

    from ai.schema_registry import validator_for
    errors = list(validator_for("risk_analysis").iter_errors(payload))
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

SCHEMA_DIR = Path(__file__).resolve().parent / "schemas"
SCHEMA_NAMES = ("common", "agent_input", "risk_analysis", "recovery_plan")


@lru_cache(maxsize=1)
def _registry() -> Registry:
    resources = []
    for name in SCHEMA_NAMES:
        contents = json.loads((SCHEMA_DIR / f"{name}.schema.json").read_text(encoding="utf-8"))
        resources.append((contents["$id"], Resource.from_contents(contents)))
    return Registry().with_resources(resources)


def load_schema(name: str) -> dict:
    return json.loads((SCHEMA_DIR / f"{name}.schema.json").read_text(encoding="utf-8"))


def _rewrite_refs(node, mapping):
    """Return a copy of node with every "$ref" rewritten via mapping(ref)."""
    if isinstance(node, dict):
        return {k: (mapping(v) if k == "$ref" else _rewrite_refs(v, mapping)) for k, v in node.items()}
    if isinstance(node, list):
        return [_rewrite_refs(v, mapping) for v in node]
    return node


def bundled_schema(name: str) -> dict:
    """Single self-contained schema (only internal #/$defs refs) for tools that cannot
    resolve other files, e.g. Claude structured outputs in the N-09 fallback.

    Best effort: structured-output engines support only a subset of JSON Schema and may
    ignore or reject keywords used here (if/then/else, allOf conditionals, pattern,
    minimum/maximum, format). N-09 may need to strip those before sending the schema.
    jsonschema via validator_for() stays the authoritative check after any API call.
    """
    common_defs = load_schema("common")["$defs"]
    defs: dict = {}

    def ref_map(prefix: str):
        def m(ref: str) -> str:
            if ref.startswith("common.schema.json#/$defs/"):
                return "#/$defs/common__" + ref.rsplit("/", 1)[1]
            if ref == "risk_analysis.schema.json":
                return "#/$defs/risk_analysis"
            if ref.startswith("#/$defs/"):
                return f"#/$defs/{prefix}" + ref.rsplit("/", 1)[1]
            raise ValueError(f"cannot bundle $ref {ref!r}")
        return m

    for key, sub in common_defs.items():
        defs[f"common__{key}"] = _rewrite_refs(sub, ref_map("common__"))

    def inline(schema_name: str, prefix: str) -> dict:
        schema = load_schema(schema_name)
        for key, sub in schema.pop("$defs", {}).items():
            defs[f"{prefix}{key}"] = _rewrite_refs(sub, ref_map(prefix))
        for meta in ("$schema", "$id"):
            schema.pop(meta, None)
        return _rewrite_refs(schema, ref_map(prefix))

    if name == "agent_input":
        defs["risk_analysis"] = inline("risk_analysis", "risk_analysis__")
    root = inline(name, "")
    root["$defs"] = {**defs, **root.get("$defs", {})}
    root["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    return root


@lru_cache(maxsize=None)
def validator_for(name: str) -> Draft202012Validator:
    if name not in SCHEMA_NAMES or name == "common":
        raise ValueError(f"unknown agent schema: {name}")
    schema = load_schema(name)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(
        schema,
        registry=_registry(),
        format_checker=Draft202012Validator.FORMAT_CHECKER,
    )
