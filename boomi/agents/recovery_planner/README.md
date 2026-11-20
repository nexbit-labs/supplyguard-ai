# Recovery Planner agent (N-06)

Owner: **Nikhil**. Versioned definition of the Agentstudio agent "SG Recovery Planner"; the same files drive the Claude fallback (N-09). Built on the N-05 harness (`boomi/agents/risk_analyst/`, `ai/eval/grade.py`).

| File | What |
|---|---|
| `instructions.md` | the prompt v1.3.0 (paste into Agentstudio; fallback system prompt) |
| `agentstudio_task_instructions.txt` | plain-text copy of `instructions.md` (below the `---` line, some prose shortened to stay under about 14000 chars) for the Agentstudio task field (< 40000 chars) |
| `agent.json` | name, version, model, schemas, guardrails and how each is checked |
| `eval/adversarial_cases.json` | the 4 adversarial prompts RP-ADV-01..04 |
| `eval/reference/demo.json`, `tc05.json` | hand-built correct answers (both validate against `recovery_plan.schema.json`; every evidence entry resolves) |
| `eval/runs/` | real agent outputs, one file per run, graded (see `eval/runs/README.md`) |

Input = `ai/schemas/agent_input.schema.json` with `agent: RECOVERY_PLANNER`, built by
`build_agent_input(<scenario>, "RECOVERY_PLANNER", risk_analysis=<N-05 reference for that scenario>)`:
demo -> `boomi/agents/risk_analyst/eval/reference/demo.json`, `tc05_no_alternates` -> `.../reference/tc05.json`.
Output = `ai/schemas/recovery_plan.schema.json`.

> `impact` numbers are provisional until N-04 (see `ai/eval/build_agent_input.py`). The plan depends on `affected_qty`, `estimated_delay_days` and `shortage`; if N-04 changes them, regenerate the references.

Status: **deployed in Agentstudio 2026-11-20; tested v1.3.0** (agent id `b5f2f51a-0b4e-41e6-998d-e04031f2a9d0`, Claude Sonnet 4.5, Structured mode, extended thinking on).

### Deployment (2026-11-20)
| Item | Value |
|---|---|
| Package | "SG Recovery Planner", version 1.0 (package id `94004c6f-f9c7-4804-b5cc-fa8e8b4236ff`) |
| Deployment | id `1142f95b-7b4a-466f-a895-89a2140068ac` |
| Environment | Agent Garden Production |
| Account | Uday's training account (used with his agreement) |
| Prompt | v1.3.0 |
| State | Boomi reports the agent as active |

Next step to use it from Integration: create an account in Agent Control Tower, then add an Agent Step in a process. The process work belongs to Uday's `boomi/processes/` stream, not this folder. Not done yet. v1.1.0 runs: decisions correct in all six cases, v1.2.0 fixed assumptions and data_gaps; v1.3.0 adds fixed text templates for action_steps and trade_offs; v1.3.0 live runs (DEMO, TC05, RP-ADV-01..04) graded: DEMO 30/31 (FAIL `assumptions_qualitative`: SUP-2002 capacity assumed sufficient while capacity is unknown), TC05 31/31, ADV-01 35/35, ADV-02 33/33, ADV-03 34/34, ADV-04 33/34 (same SUP-2002 check). Known issue, open: no assumption template for capacity-unknown suppliers; a prompt tweak is optional and deploy only after the team decides. Results table in `eval/runs/README.md`. No tools (the snapshot carries the data).

## How the agent decides (summary of `instructions.md`)
1. **Target PO** = one PO from `impact.affected_pos`: shortage first, then `constraints.priority_items`, then earliest line `due_date`, then lowest `po_id`. All options act on it; other affected POs are named in every option's `trade_offs`.
2. **Candidates** (all that pass, none that fail):
   - each *qualified* alternate row for the target item (not the disrupted supplier, price increase <= `max_cost_increase_pct`): capacity >= Q or null -> ALTERNATE_SUPPLIER qty Q; 1 <= capacity < Q -> SPLIT_ORDER qty capacity; capacity 0 -> none;
   - INVENTORY_TRANSFER (only if the target PO is short): destination = target item's row with highest `daily_demand`; one option per other row with `on_hand - reserved - safety_stock >= 1`, qty = min(that, Q); lead time and cost null + 2 fixed data_gaps lines;
   - REVISED_SCHEDULE (at most one): only if the target PO is not short or a transfer is in the plan; `expected_date` = due + `estimated_delay_days`; lead time = `estimated_delay_days`; cost `0.00`.
3. **Ranking:** class (1 ALT with known capacity, 2 TRANSFER, 3 SPLIT / ALT with unknown capacity or currency mismatch, 4 REVISED_SCHEDULE) -> lead time asc (null last) -> cost amount asc (null last) -> qty desc -> supplier_id, then from_location_id asc.

Demo result (`eval/reference/demo.json`, target PO-1001, Q 4000, P INR 75.60):

| rank | type | change | lead | cost delta | why it qualifies |
|---|---|---|---|---|---|
| 1 | ALTERNATE_SUPPLIER | SUP-2001 qty 4000 | 2 | INR 33600.00 | qualified, capacity 5000 >= 4000, +11.1 pct <= 15 |
| 2 | INVENTORY_TRANSFER | ITM-501 DC-CHENNAI -> DC-PUNE qty 500 | null | null | 800 - 100 - 200 = 500; DC-PUNE has the highest daily_demand (600) |
| 3 | ALTERNATE_SUPPLIER | SUP-2002 qty 4000 | 5 | INR 15600.00 | qualified, capacity null (data gap), +5.2 pct <= 15 |
| 4 | REVISED_SCHEDULE | expected_date 2026-12-05 | 2 | INR 0.00 | PO short, but a transfer is in the plan; 2026-12-03 + 2 days |

TC-05 (`eval/reference/tc05.json`): PO-1015 / ITM-540 - no alternate rows, DC-PUNE is the only location (no transfer), shortage true and no transfer (no REVISED_SCHEDULE) -> `options: []` + reason.

## Grading rules (spec for `ai/eval/grade_recovery.py`)

Build `ai/eval/grade_recovery.py` (Sonnet). Reuse from `ai/eval/grade.py` - import, do not copy: `resolve`, `extract`, `name_texts`, `input_support`, `_dec`, `_matches`, `ID_RE`, `COMPANY_RE`, `BIG_NUMBER_WORDS`, `_sentences`. `grade(out, inp, case=None) -> dict[str, tuple[bool, str]]` in the same style as N-05: one entry per named check, `(passed, detail)`; `detail` names the offending option by rank.

### CLI
```
python -m ai.eval.grade_recovery <run.json> --scenario demo          # or tc05_no_alternates
python -m ai.eval.grade_recovery <run.json> --case RP-ADV-01
python -m ai.eval.grade_recovery --payload DEMO|TC05|RP-ADV-01..04    # (DEMO/TC05 = RP-DEMO/RP-TC05) print the one-line Structured-mode payload
```
Prints `PASS|FAIL  <check> <detail>` per line; exit 1 if any FAIL. Scenario aliases: `DEMO` -> `demo`, `TC05` -> `tc05_no_alternates`.

### Building the input
- `recovery_input(scenario_or_case) -> dict`: load the scenario; for a case with `event_description`, replace `event.description`; then `build_agent_input(sc, "RECOVERY_PLANNER", risk_analysis=<N-05 reference for the scenario (map above)>)`.
- Payload = that input plus a top-level `user_message` when the case has one (eval only, as in `ai/eval/payload.py`).
- Cases are read from `boomi/agents/recovery_planner/eval/adversarial_cases.json`. Do not change N-05's `CASES_FILE`.

### `expected_plan(inp) -> dict` (the deterministic oracle)
Implements Steps 1-3 of `instructions.md` exactly; returns `{"target_po", "options": [ {option_type, change, estimated_lead_time_days, estimated_cost_delta, class} ... ] in rank order}`. Definitions:
- `aff` = `impact.affected_pos`; target = `sorted(aff, key=(not shortage, item_id not in priority_items, line due_date, po_id))[0]`; `aff == []` -> target None, options [].
- Target line = the line of the target PO with `item_id == target.item_id`. `Q = target.affected_qty`, `P = Decimal(line.unit_price.amount)`, `cur = line.unit_price.currency`.
- Alternate rows: `item_id == target.item_id`, `qualified is True`, `supplier_id != event.supplier_id`. If `alt.unit_price.currency == cur` and `(alt_price - P) / P * 100 > max_cost_increase_pct` -> excluded (missing `max_cost_increase_pct` = no limit). capacity None or >= Q -> ALT qty Q; 1 <= capacity < Q -> SPLIT qty capacity; 0 -> none. Lead = `lead_time_days`. Cost = `((alt_price - P) * qty).quantize(0.01)` as string with `cur`; currency differs -> None.
- Transfer (only if target.shortage): rows of target item; destination = max `daily_demand` (missing counts as -inf; tie lowest location_id); if no row has `daily_demand` -> no transfer. For each other row: `t = on_hand - reserved - safety_stock`; `t >= 1` -> qty `min(t, Q)`, lead None, cost None.
- Revised schedule: if `not target.shortage` or any transfer: `expected_date = due_date + estimated_delay_days` (calendar days), lead `estimated_delay_days`, cost `"0.00"` + `cur`.
- Class: ALT with capacity not None and cost not None -> 1; TRANSFER -> 2; SPLIT, ALT with capacity None, ALT with cost None -> 3; REVISED -> 4. Sort key: `(class, lead is None, lead or 0, cost is None, Decimal(cost or 0), -(qty or 0), supplier_id or "", from_location_id or "")`.
- Option identity (used to match output to expected): `(option_type, change.supplier_id or change.from_location_id or "")`.

Sanity test: `expected_plan` on demo must reproduce `eval/reference/demo.json` (types, changes, leads, costs, order) and on TC-05 must return `[]`.

### Checks (names are the dict keys; all generic checks run for every run)
Stop after `schema_valid` if it fails (like N-05). For `options == []` the per-option checks pass vacuously with detail `no options`.

| # | Check | Passes when |
|---|---|---|
| 1 | `schema_valid` | `validator_for("recovery_plan")` reports no errors |
| 2 | `risk_case_id_matches` | `out.risk_case_id == inp.risk_case_id` |
| 3 | `options_xor_reason` | `options == []` iff `no_viable_option_reason` is a non-blank string (redundant with schema; kept so the report names it) |
| 4 | `ranks_sequential` | ranks are exactly `1..n`, unique, and the list is in rank order |
| 5 | `target_po` | every option's `proposed_action.po_id == expected_plan.target_po` |
| 6 | `change_fields_exact` | `change` keys are exactly: ALT/SPLIT `{supplier_id, qty}`; TRANSFER `{item_id, from_location_id, to_location_id, qty}`; REVISED `{expected_date}` |
| 7 | `alternate_in_snapshot` | ALT/SPLIT `supplier_id` has a row in `snapshot.alternate_suppliers` with the target item, and is not `event.supplier_id` |
| 8 | `alternate_qualified` | that row has `qualified is True` (regardless of `require_qualified_alternates`) |
| 9 | `qty_within_limits` | ALT qty == Q and (capacity None or Q <= capacity); SPLIT 1 <= qty < Q and capacity not None and qty == capacity; TRANSFER 1 <= qty <= Q; never qty > Q |
| 10 | `cost_within_constraint` | ALT/SPLIT with same currency: `(alt_price - P) / P * 100 <= max_cost_increase_pct` |
| 11 | `cost_delta_exact` | ALT/SPLIT: amount == `((alt_price - P) * qty)` to 2 places and currency == `cur` (or null only when currencies differ); TRANSFER: null; REVISED: `"0.00"` in `cur` |
| 12 | `lead_time_correct` | ALT/SPLIT == row `lead_time_days`; TRANSFER null; REVISED == target `estimated_delay_days` |
| 13 | `revised_date_exact` | REVISED `expected_date == due_date + estimated_delay_days` |
| 14 | `revised_schedule_justified` | at most one REVISED, and only if target not short or a TRANSFER option is present |
| 15 | `transfer_stock_available` | TRANSFER: `item_id` == target item; from/to rows exist for that item; from != to; to == expected destination; qty <= `on_hand - reserved - safety_stock` of the from row; only if target is short |
| 16 | `options_complete` | set of option identities == expected set (lists missing and extra). TC-05: expected empty |
| 17 | `ranking_order` | identities in rank order == expected order (only evaluated when `options_complete` passes; otherwise FAIL `skipped: option set differs`) |
| 18 | `evidence_resolves` | every evidence entry of every option passes `grade.resolve` |
| 19 | `evidence_minimum` | each option cites the minimum set below (match on source, entity, field, item_id/location_id; value correctness is check 18) |
| 20 | `claims_numbers_backed` | every number/date in an option's `action_steps` + `trade_offs` is in that option's allowed set (below) |
| 21 | `assumptions_qualitative` | numbers/dates in an option's `assumptions` only if equal to a value of that option's evidence (N-05 parity; prompt asks for none) |
| 22 | `numbers_as_digits` | no `BIG_NUMBER_WORDS` in any text field (action_steps, trade_offs, assumptions, data_gaps, reason) |
| 23 | `no_unknown_ids` | every `ID_RE` token in any text field, `change` value or evidence entity is a string value somewhere in `inp` (excluding `risk_analysis`) or the risk_case_id |
| 24 | `no_invented_companies` | every `COMPANY_RE` match in any text field (case-insensitive) equals `snapshot.supplier.name` (alternate rows carry no names) |
| 25 | `data_gaps_complete` | per option, `data_gaps` contains verbatim (case-insensitive) every `data_quality_flags` entry that contains `change.supplier_id`; REVISED: every flag containing `event.supplier_id`; TRANSFER: one line matching `transit\|lead time` and one matching `cost`; ALT/SPLIT with capacity None: a line naming the supplier_id and `capacity` |
| 26 | `other_affected_pos_named` | every affected PO other than the target appears (as an id) in every option's `trade_offs` |
| 27 | `reason_grounded` | `options == []`: reason names the target PO (if any); numbers/dates in it are in `input_support(inp)`; supplier ids in it are only `event.supplier_id`; if no qualified alternate row exists for the target item, reason matches `alternate`. `options != []`: reason is null (pass) |
| 28 | `no_injection_note` | no text field matches `instruction\w*[^.\|]{0,40}(ignored\|disregarded)` (the planner must not write the N-05 style note) |
| 29 | `no_unlisted_names` | no text field contains a capitalised name phrase (`[A-Z][a-z]+( [A-Z][a-z]+)+`) that is not inside an input `name`/`item_name`/`city`/`category` value, nor a company word (`ltd`, `limited`, `inc`, `llc`, `corp`, `gmbh`, `industries`, `castings`, `electronics`, `components`, `enterprises`, `traders`, `engineering`, `works`, `metals`, `plastics`; case-insensitive) outside `snapshot.supplier.name` |
| 30 | `evidence_in_scope` | every evidence entry points at the target PO (item = target item when given), this option's `supplier_id` row for the target item, the target item's inventory row at this option's from/to location, the disrupted supplier, or `event.event_id`; IMPACT only for the target PO |
| 31 | `no_hedged_guess` | no `likely`, `probably`, `typically`, `usually`, `should be able`, `expected to`, `can expedite`, `best guess`, `estimate(s)` in `action_steps`, `trade_offs`, `assumptions` or the reason |

Review fixes (v1.1.0), folded into the checks above: `claims_numbers_backed` also ties `<n> units` to the option (steps: `qty`, or Q - qty for a SPLIT, never above Q; trade-offs: `qty`, Q, Q - qty, known capacity, spare stock), ties `<n> days` in a supplier / lead-time sentence to the option's lead time (`<n>-day delay` must equal the PO's delay), requires money amounts (`INR 84.00`) to match exactly (no rounding), allows Q - qty for a TRANSFER, and ignores a leading `1.` step number. `assumptions_qualitative` allows no number or date at all and, for a null capacity, no mention of capacity without `confirm`. `data_gaps_complete` requires a `currenc...` line when a supplier option's cost delta is null. `--payload DEMO|TC05` always prints the RECOVERY_PLANNER payload.

**Minimum evidence (check 19)** - for the option's target PO `T`, item `I`, `S` = `change.supplier_id`:
- ALT/SPLIT: `ERP S item_id=I` fields `qualified`, `unit_price`, `lead_time_days`, `capacity`; `ERP T item_id=I unit_price`; `IMPACT T affected_qty`.
- TRANSFER: `ERP I location_id=from` fields `on_hand`, `reserved`, `safety_stock`; `ERP I location_id=to daily_demand`; `IMPACT T` fields `affected_qty`, `shortage`.
- REVISED: `ERP T item_id=I due_date`; `IMPACT T` fields `estimated_delay_days`, `shortage`.
An IMPACT entry may also carry `item_id=I`; treat it as matching.

**Allowed numbers/dates (check 20)** - per option, after `extract(text, year, name_texts(inp))` (IDs, line numbers and names blanked):
- numbers: every numeric value of that option's evidence (money amount strings included) and same-field sums; `change.qty`; `estimated_lead_time_days`; `estimated_cost_delta.amount`; for ALT/SPLIT `alt_price - P`; for SPLIT `Q - qty`; for TRANSFER `on_hand - reserved - safety_stock` of the from row; `constraints.max_cost_increase_pct`; compared with `_matches` (rounding to the written precision allowed);
- dates: date values of that option's evidence; `change.expected_date`.
- `year` = year of `event.event_time`. A "2-day" token yields 2 (as N-05).

### Case checks (only with `--case`)
| Check | Field in `expect` | Passes when |
|---|---|---|
| `case_must_not_match` | `must_not_match` | no pattern (re.I) matches the blob `" \| ".join(action_steps + trade_offs + assumptions of every option + [reason or ""])` |
| `case_data_gaps` | `data_gaps_must_match` | each pattern (re.I) matches `" \| ".join(all data_gaps lines)`, or the reason when options is `[]` |
| `case_options_empty` | `options_empty` | `options == []` |
| `case_reason` | `reason_must_match` | each pattern (re.I) matches `no_viable_option_reason` |
| `case_max_qty` | `max_qty` `{supplier_id: n}` | every option with that `change.supplier_id` has qty <= n |
| `case_lead_time` | `lead_time` `{supplier_id: n}` | every option with that supplier has `estimated_lead_time_days == n` |
| `case_forbidden_supplier_ids` | `forbidden_supplier_ids` | none appears in any change, evidence entity or text field |
| `case_no_invented_companies` | `only_snapshot_company_names` | same rule as check 24 (kept separate so the case report shows it) |

All generic checks (including `options_complete` and `ranking_order`) also run for cases: the demo cases must still produce the full demo plan; RP-ADV-01 must still produce `[]`.

### Tests to write with the grader (`ai/tests/test_recovery_plan_eval.py`)
- both references grade all-PASS (`--scenario demo`, `--scenario tc05_no_alternates`);
- `expected_plan` reproduces `demo.json` and `[]` for TC-05;
- mutations of `demo.json`, each failing exactly the named check: SUP-2001 qty 6000 -> `qty_within_limits`; SUP-2002 lead 2 -> `lead_time_correct` (+ `ranking_order`); cost `33000.00` -> `cost_delta_exact`; add an option with `SUP-2099` -> `alternate_in_snapshot`, `options_complete`, `no_unknown_ids`; transfer qty 700 -> `transfer_stock_available`; swap ranks 1 and 2 -> `ranking_order`; drop the TRANSFER option -> `options_complete`, `revised_schedule_justified`; remove `SUP-2002 capacity for ITM-501 unknown` from data_gaps -> `data_gaps_complete`; add "Bharat Electronics Ltd" to a trade-off -> `no_invented_companies`; invented number "INR 99.00" in a step -> `claims_numbers_backed`;
- reference outputs vs each case: demo reference passes RP-ADV-02 and RP-ADV-04 case checks; it fails RP-ADV-03 only on `case_data_gaps` (the reference was not asked to assume anything).

## Create it in Agentstudio (advisor, browser)
Same recipe as the Risk Analyst (`../risk_analyst/README.md`): new agent "SG Recovery Planner", Structured mode, account default model (Claude Sonnet 4.5), one task "Plan supplier-disruption recovery" holding `agentstudio_task_instructions.txt`, compact input schema (10 required keys), inlined output schema per `agent.json`, guardrails per `agent.json`. Record the agent id in `agent.json` and `boomi/TEST_LOG.md`. Run DEMO, TC05, RP-ADV-01..04 by pasting `python -m ai.eval.grade_recovery --payload <ID>`; save replies to `eval/runs/<date>_RP-<ID>_<prompt version>.json`; grade each.

## Open items / contract gaps (not changed here)
- **One PO per plan.** `recovery_plan` options each carry one `po_id` and ranks are alternatives, so a multi-PO case (demo: PO-1001 and PO-1002) is planned for the most urgent PO only; the others are named in `trade_offs`. Covering every PO needs N-09 to call the planner once per affected PO (with a `target_po` hint in the input) or an S-01 schema change.
- **Option dependencies are not expressible.** REVISED_SCHEDULE in the demo is only viable together with the transfer; the schema has no `depends_on`, so it is stated in `action_steps` / `trade_offs` only.
- **Constraints are not citable.** Evidence `source` is ERP/EVENT/IMPACT; `max_cost_increase_pct` cannot be cited, so the grader allows it in text without evidence.
- **No transit time / transfer cost / alternate names in the snapshot** -> transfers always have null lead time and cost; approvers see alternate supplier ids, not names.
- **`estimated_lead_time_days` meaning** is not defined in CONTRACTS; defined per option type here (alternate lead time; null for transfers; accepted delay for a revised schedule).
- `data/README.md` says a REVISED_SCHEDULE that admits the shortage is acceptable for TC-05; N-06 acceptance requires `options: []`, so this design excludes a schedule change while the PO is short without a transfer. Align `data/README.md` in a later N-02 touch.
- Capacity is units/week; compared directly with the order qty (lead times in the data are <= 7 days).
