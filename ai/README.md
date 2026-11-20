# ai/ - Agent contracts

Owner: **Nikhil**. Think of this folder as the data contract for the agent "feed": every agent response is validated here before anything downstream (N-07) persists it.

| Path | What |
|---|---|
| `schemas/risk_analysis.schema.json` | Supplier Risk Analyst output (CONTRACTS section 6) |
| `schemas/recovery_plan.schema.json` | Recovery Planning Agent output (CONTRACTS section 6) |
| `schemas/agent_input.schema.json` | Context sent to either agent: event + ERP snapshot + impact-engine result + data-quality flags (from LLD 16.5; proposed for CONTRACTS section 6) |
| `schemas/common.schema.json` | Shared pieces: IDs, Money, evidence, enums |
| `schema_registry.py` | `validator_for("risk_analysis")` - Draft 2020-12 validator with cross-file `$ref`s resolved (the authoritative check). `bundled_schema(name)` - single-file version with only internal refs, for Claude structured outputs in N-09 |
| `fixtures/valid/` | Demo scenario (SUP-1008 outage), one option of every `option_type`, TC-05 no-option sample. PO, inventory and alternate-supplier values match `data/seed/` (N-02); agent text and the RC/EVT ids are illustrative |
| `fixtures/invalid/` | Each file = a valid fixture with exactly one defect |
| `fixtures/expected_errors.json` | Invalid case -> the one jsonschema keyword it must fail on |
| `fixtures/generate_fixtures.py` | Regenerates all fixtures (`python ai/fixtures/generate_fixtures.py` from repo root). Edit this, not the JSON |
| `eval/build_agent_input.py` | (N-05) builds a schema-valid `agent_input` from `data/seed` + `data/scenarios` (impact provisional until N-04) |
| `eval/grade.py` | (N-05) grades an agent output against its input: evidence resolves, no unsupported numbers, flags reported, adversarial cases |
| agent definitions | live in `boomi/agents/<agent>/` (instructions, config, tools, eval cases) |

Fixture naming: `<schema>__<case>.json`.

## Rules the schemas enforce
- Every key is required, including `facts`, `estimates`, `assumptions`, `missing_data` (risk) and `assumptions`, `data_gaps` (each option). Empty arrays are fine; omitting the key is not - the agent must state "nothing missing" explicitly.
- `evidence` needs at least one `{source, entity, field, value}` entry; estimates need a `basis`.
- No extra keys (`additionalProperties: false`) - an agent cannot slip in invented fields like `supplier_capacity`.
- Money is `{"amount": "33600.00", "currency": "INR"}` (string decimal).
- Recovery plan: options non-empty and `no_viable_option_reason: null`, or `options: []` and a reason (TC-05).
- Lead time / cost delta may be `null` when the data does not support an estimate; `data_gaps` must then be non-empty.
- `change` must carry the fields its `option_type` needs: ALTERNATE_SUPPLIER -> `supplier_id`; SPLIT_ORDER -> `supplier_id` + `qty`; REVISED_SCHEDULE -> `expected_date`; INVENTORY_TRANSFER -> `item_id`, `from_location_id`, `to_location_id`, `qty`.
- Agent input: `RECOVERY_PLANNER` must carry `risk_analysis`; `RISK_ANALYST` must not.

## Evidence addressing (what N-07 resolves)
Each evidence entry points at ONE value in the agent input - like a (table, primary key, column) lookup:
- `source`: `ERP` (snapshot), `EVENT` (the event), `IMPACT` (impact-engine result).
- `entity`: the record id - `SUP-…`, `PO-…`, `EVT-…`; for inventory, the `item_id`.
- `field`: the snapshot field name exactly (`due_date`, `qty`, `unit_price`, `on_hand`, `capacity`, `lead_time_days`…).
- `item_id` narrows a PO / alternate-supplier record to one line; `location_id` narrows inventory to one row.
- Money is cited by its amount string (`"84.00"`); `value: null` = the record has no value (cite it as missing data).

- PO dates: the PO header `expected_date` (= latest line `due_date`) is cited without `item_id`, as in the CONTRACTS section 6 example; a line date is cited as `due_date` + `item_id`. Both exist in the N-02 seed and the N-03 mock ERP.

Not checked here (N-07 does it against the stored snapshot): evidence resolves and values match, IDs/qty/prices exist, ranks unique and contiguous, agent `risk_score` vs `impact.risk_score`.

**Tenant:** the agent payload's `tenant_id` is context only. N-07 (validator) and N-08 (agent invocation) take the tenant from the stored RiskCase, never from the agent input or output, and reject a payload whose tenant differs.

## Proposed for S-01 (not in CONTRACTS v0.1 yet)
New structures:
- `agent_input.schema.json` as a whole (event + snapshot + impact + `data_quality_flags`, `risk_analysis` only for `RECOVERY_PLANNER`), with `TEN-…` / `EVT-…` id patterns, `correlation_id` UUID and UTC `…Z` timestamps.
- CONTRACTS section 5 `GET /erp/purchase-orders`: list the PO header `expected_date` (= latest line `due_date`) so the section 6 example `{"entity": "PO-1001", "field": "expected_date"}` resolves. Optional in the snapshot schema until ratified.
- Evidence: `source` enum (`ERP | EVENT | IMPACT`), `item_id` / `location_id` qualifiers, nullable `value`.
- Nullable `estimated_lead_time_days` / `estimated_cost_delta` (then `data_gaps` must be non-empty).
- `change.item_id`, `change.from_location_id`, `change.to_location_id` (INVENTORY_TRANSFER).

Tightenings of the section 6 examples (the examples still validate; the test parses CONTRACTS.md):
- `risk_level` enum `LOW | MEDIUM | HIGH | CRITICAL`; `confidence` enum `LOW | MEDIUM | HIGH`; `affected_entities.type` enum.
- Integers: `risk_score` 0-100; option `rank` >= 1; `estimated_lead_time_days` >= 0; `change.qty` >= 1.
- `minItems: 1` on `evidence` and `action_steps`; `data_gaps` non-empty when an estimate is null.
- ID patterns `RC-…`, `SUP-…`, `PO-…`; dates `YYYY-MM-DD`; `currency` matches `^[A-Z]{3}$`.
- Evidence `value` is a string, number, boolean or null (no objects or arrays).
- Every string field in agent output must be non-blank (list items, `summary`, `statement` / `basis`, ids, evidence `entity` / `field`, `no_viable_option_reason`, `change` fields).
- `additionalProperties: false` everywhere in agent output; every top-level and option key required (empty arrays allowed); evidence `item_id` / `location_id` optional; `change` keys required per `option_type`.
- Per-`option_type` required `change` fields (see Rules above); `options: []` iff `no_viable_option_reason` is set.
- Agent money = exactly 2 dp string (snapshot money 0-4 dp).

## Run
```
pip install -r ai/requirements.txt
python -m pytest ai/tests -q
```
