# Tasks - Nikhil (Data & AI · dataset, mock ERP, impact engine, agents, workflow rules)

**Background:** Data engineer (Python, AI/ML). Claude explains frontend and Boomi concepts in data-pipeline terms (ETL steps, schemas, jobs, DataFrames).
**Stream:** Synthetic data + mock ERP, impact/risk scoring, Boomi Agentstudio agents, AI output validation, Boomi correlation process, agent invocation, case state machine, what-if engine, Data Hub (optional), AI + end-to-end tests.
**Owns:** `ai/`, `data/`, `mock-erp/`, `backend/apps/intelligence/`, `backend/apps/workflow/`, `boomi/agents/`, `boomi/processes/correlation/`, `tests/e2e/`

> Status values: `todo` · `in-progress` · `blocked` · `review` · `done`. Only change the `Status`, `Done in`, checkboxes and Notes lines - keep the format, `scripts/progress.py` parses it.
> Model hint = what the advisor (Opus 5.5) should delegate the bulk of the work to. See `docs/AI_WORKFLOW.md`.

## Handoff
<!-- Overwrite this block at the end of EVERY session (run /handoff). Newest state only; history goes to progress/SESSION_LOG.md -->
- **Last updated:** 2026-11-23 (N-07 validator built + reviewed, in review)
- **Current task:** N-04 and N-07 engines (both review; N-06 in review, N-05 in review)
- **Branch:** `nikhil/N-07-validator` (docs on `nikhil/N-07-validator--docs`)
- **Where I left off:** N-07 validator `backend/apps/intelligence/validation.py` built and Opus-approved (task branch e9ff488): pure functions, 163 tests, 98% coverage, all 26 live agent runs pass. Persistence and the analysis endpoint wait on U-03 and N-10.
- **Next step:** N-04 and N-07 engines in review; waiting on U-03 merge (send Uday the point-7 OK + `jsonschema>=4.18` / schema-folder asks), then the N-04 and N-07 persistence steps and N-10. N-11 what-if is ready to start now.
- **Blockers:** U-03 not in collaboration (awaiting re-review + s10 approvals). U-07 tools not built (agents have no tools).
---

### N-01: Agent I/O JSON schemas + fixtures
- **Status:** review
- **Sprint:** S0 (19-20 Nov) | **Est:** 1d | **Depends:** -
- **Branch:** `nikhil/N-01-agent-schemas`
- **Model hint:** opus / high (schema design is a contract)
- **Deliverables:** `ai/schemas/risk_analysis.schema.json`, `ai/schemas/recovery_plan.schema.json`, `ai/schemas/agent_input.schema.json`; valid + invalid fixtures in `ai/fixtures/`; schema test.
- **Acceptance:**
  - [ ] Schemas match CONTRACTS section 6 exactly
  - [x] Required `missing_data` / `assumptions` / `evidence` fields enforced
  - [x] Invalid fixtures fail validation in tests
- **Done in:** -
- **Notes:** CONTRACTS section 6 examples validate and keys match (test parses CONTRACTS.md). Additions beyond the contract are listed in `ai/README.md` 'Proposed for S-01' - first box ticks once S-01 ratifies them. Fact-checking evidence against the snapshot is N-07. Merged to collaboration 2026-11-19 (reviewer APPROVE, 82 tests). Reviewer follow-ups before S-01 done 2026-11-19 (reviewer APPROVE, 93 tests): PO header `expected_date` added to the snapshot (optional, proposed for CONTRACTS s5); full tightening list + tenant rule in `ai/README.md`; all-option-types valid fixture + 4 new invalid fixtures; fixture inventory aligned to `data/seed/`; `bundled_schema` docstring softened.

### N-02: Synthetic demo dataset
- **Status:** review
- **Sprint:** S0 (20-22 Nov) | **Est:** 1d | **Depends:** S-01
- **Branch:** `nikhil/N-02-seed-data`
- **Model hint:** sonnet / medium (generator script), haiku / low to sanity-check
- **Deliverables:** `data/generate_seed.py` (deterministic, seeded RNG) -> `data/seed/{tenants,suppliers,purchase_orders,inventory,alternate_suppliers,shipments}.json`; 2 tenants (TEN-ACME, TEN-GLOBEX), 100 suppliers, 500 POs, 3 locations, 20 active shipments, 3 deliberate duplicate suppliers (for N-12); `data/scenarios/*.json` canonical events for TC-01..TC-09 and the demo (SUP-1008 factory outage, 48 h).
- **Acceptance:**
  - [x] Demo scenario numbers documented in `data/README.md` (affected POs, exposure, 2 alternates)
  - [x] TC-04 scenario has enough inventory to cover the delay; TC-05 scenario has no alternates
  - [ ] Uday's seed loader (U-03) and the mock ERP (N-03) load the same files
- **Done in:** -
- **Notes:** Third box ticks when U-03 `load_seed` and N-03 both load `data/seed/` (field table in `data/README.md`). Started before S-01 was frozen; CONTRACTS asks listed under 'Proposed for S-01' in `data/README.md`. Seed has 187 inventory rows, 101 alternates.

### N-03: Mock ERP service
- **Status:** review
- **Sprint:** S0-S1 (22-25 Nov) | **Est:** 2d | **Depends:** S-01, N-02
- **Branch:** `nikhil/N-03-mock-erp`
- **Model hint:** sonnet / medium
- **Deliverables:** `mock-erp/` FastAPI app (Python 3.12) implementing CONTRACTS section 5, loads `data/seed/*.json`, in-memory store with reset endpoint, request log (for TC-06 assertions), `Idempotency-Key` enforcement on PATCH, chaos endpoint, Dockerfile, pytest.
- **Acceptance:**
  - [x] All endpoints in CONTRACTS section 5 respond with seed data
  - [x] Same `Idempotency-Key` twice -> second call returns first result, no double update
  - [x] `_chaos timeout` makes PATCH hang > 30 s; `off` restores
  - [ ] Runs via `docker compose up mock-erp`
- **Done in:** -
- **Notes:** Uday's U-07 (Boomi ERP reads) needs this by 25 Nov. Compose box ticks once Uday adds the service (snippet in `mock-erp/README.md`); image already builds/runs via `docker build -f mock-erp/Dockerfile .`. Extensions beyond CONTRACTS s5 (GET PO by id, shipments, X-Tenant-ID, PATCH body, INTERNAL_ERROR) listed for S-01.

### N-04: Deterministic impact + risk scoring engine
- **Status:** review
- **Sprint:** S1 (25-30 Nov) | **Est:** 3d | **Depends:** U-03, N-02
- **Branch:** `nikhil/N-04-impact-engine`
- **Model hint:** opus / high for the scoring design, sonnet / medium to implement + tests
- **Deliverables:** `backend/apps/intelligence/impact.py`: event + ERP snapshot -> affected POs (affected_qty, estimated_delay, exposure_amount), inventory coverage per item, shortage flags, risk_score 0-100 with documented formula and tenant thresholds; service called by the `POST /internal/risk-cases` endpoint (endpoint shell in Uday's `integrations` app).
- **Acceptance:**
  - [x] TC-03: multiple open POs all evaluated
  - [x] TC-04: sufficient inventory -> no shortage claimed
  - [x] Demo scenario numbers match `data/README.md`
  - [x] Pure functions, 90%+ line coverage on `impact.py`
- **Done in:** -
- **Notes:** Design spec: `backend/apps/intelligence/IMPACT_DESIGN.md`. Plan:
  | piece | runs as | model / effort | branch |
  |---|---|---|---|
  | design spec | nested session | opus / medium | `nikhil/N-04-impact-engine--design` |
  | impact.py + tests | nested session | sonnet / medium | `nikhil/N-04-impact-engine--impl` |
  | service wiring to POST /internal/risk-cases | after U-03 | sonnet / medium | later |
  | review | subagent reviewer-lite | sonnet / medium | - |
  Engine done: `backend/apps/intelligence/impact.py` pure functions, 68 tests, 100% line coverage, reviewer APPROVE (commit e0ab4a4). Coverage is net of safety stock (data/README.md updated); exposure scale 500000 is a per-tenant threshold `risk_thresholds.exposure_full_scale` (Uday: add it to the Tenant settings shape in CONTRACTS s10). LEFT after U-03 merges: service `create_risk_case(tenant, event, snapshot)` that stores the full `compute_impact` output on RiskCase (risk_score, risk_level, total_exposure -> exposure_amount+currency, impact JSON, data_quality_flags, snapshot, snapshot_captured_at) and one AffectedPO per (po_id, item_id) (item_name from snapshot, due_date parsed, delay/coverage to Decimal); called by Uday's U-06 `POST /internal/risk-cases`. Minors from review: float in parse_window, `qualified_alternate_supplier_ids` name when unqualified allowed. Service done: `services.create_risk_case()` persists RiskCase + AffectedPO (commit f5f08f6); the endpoint is Uday's U-06. Review fixes: `create_risk_case()` returns `(case, created)` (`(None, False)` for zero affected POs or an unknown supplier, `(existing, False)` on repeat). U-06 must map `services.TenantMismatch` and `impact.SnapshotError` (both `ValueError`; also plain `ValueError` for a bad event/threshold) to 4xx; nothing is written on any of them. Coverage over 9,999,999.99 is stored as null + flag `COVERAGE_OUT_OF_RANGE`; snapshot currency != tenant currency adds flag `CURRENCY_MISMATCH`.

### N-05: Supplier Risk Analyst agent (Agentstudio)
- **Status:** review
- **Sprint:** S2 (1-4 Dec) | **Est:** 2d | **Depends:** N-01, U-01
- **Branch:** `nikhil/N-05-risk-analyst`
- **Model hint:** opus / high (prompt + guardrails); use the boomi-agentstudio skill if installed
- **Deliverables:** agent in Agentstudio with instructions, tools (ERP lookups via Uday's U-07 processes), guardrails: no invented capacity/price/dates, separate facts vs estimates, must fill `missing_data`. Prompt + config versioned in `boomi/agents/risk_analyst/`.
- **Acceptance:**
  - [x] Output for the demo scenario validates against `risk_analysis.schema.json`
  - [x] Every claim has an `evidence` entry pointing to snapshot data
  - [x] 5 adversarial prompts (asking it to guess capacity etc.) -> it reports missing data instead
- **Done in:** -
- **Notes:** Agent in Agentstudio (prompt v1.3.0, not deployed). Clean single-version acceptance set run 2026-11-20: DEMO passes every check; ADV-01..05 pass every `case_*` and generic check (see `boomi/agents/risk_analyst/README.md`, `eval/runs/README.md`). Tools pending U-07 (agent runs on the input snapshot only). Impact in eval inputs is provisional until N-04. Open for human review / next prompt version: ADV-01/03/04/05 add a false 'event description contained instructions' line (rule 5); ADV-03/05 assumption 'supplier will resume production immediately after the expected duration' is restart-leaning. Known limit: `disruption_history` entries cannot be cited as evidence yet.

### N-06: Recovery Planning agent (Agentstudio)
- **Status:** review
- **Sprint:** S2 (3-6 Dec) | **Est:** 2d | **Depends:** N-01, N-05
- **Branch:** `nikhil/N-06-recovery-agent`
- **Model hint:** opus / high
- **Deliverables:** agent comparing ALTERNATE_SUPPLIER, INVENTORY_TRANSFER, REVISED_SCHEDULE, SPLIT_ORDER; ranks by urgency, inventory, qualification, cost, priority; config in `boomi/agents/recovery_planner/`.
- **Acceptance:**
  - [x] Demo scenario -> at least 2 ranked viable options (AC-04)
  - [x] TC-05 scenario -> `options: []` + `no_viable_option_reason`
  - [x] Output validates against `recovery_plan.schema.json`
- **Done in:** -
- **Notes:** Agent "SG Recovery Planner" (id b5f2f51a-0b4e-41e6-998d-e04031f2a9d0) built in Agentstudio as a saved draft, not deployed, prompt v1.3.0. Six live v1.3.0 runs graded 2026-11-20: DEMO 30/31, TC05 31/31, ADV-01 35/35, ADV-02 33/33, ADV-03 34/34, ADV-04 33/34. Known issue (open): `assumptions_qualitative` fails in DEMO and RP-ADV-04 - the SUP-2002 option (capacity unknown) assumes "sufficient capacity available". Deployed in Agentstudio 2026-11-20: package "SG Recovery Planner" 1.0 (94004c6f-f9c7-4804-b5cc-fa8e8b4236ff), deployment 1142f95b-7b4a-466f-a895-89a2140068ac, environment Agent Garden Production, Boomi reports active. Not yet callable from Integration (needs an Agent Control Tower account and an Agent Step, Uday's stream). Optional next: a prompt assumption template for capacity-unknown suppliers. See `boomi/agents/recovery_planner/eval/runs/README.md`.

### N-07: Agent output validator + persistence
- **Status:** review
- **Sprint:** S2 (4-7 Dec) | **Est:** 1.5d | **Depends:** N-01, N-04, U-03
- **Branch:** `nikhil/N-07-validator`
- **Model hint:** sonnet / medium; opus / high review
- **Deliverables:** `backend/apps/intelligence/validation.py` + `POST /internal/risk-cases/{id}/analysis`: JSON-schema check, cross-check every referenced supplier/PO/item/qty/price against the stored snapshot, flag unsupported options with `Recommendation.unsupported = true` + `unsupported_reasons` (not actionable; Recommendation `status` stays PENDING | APPROVED | REJECTED | CHANGES_REQUESTED and an unsupported one can never be APPROVED), persist Recommendation rows with `round = RiskCase.analysis_round`; write one append-only `AgentRun` row per agent call (`raw`, `valid`, `validation_errors`) for AC-07, transition case to RECOMMENDATION_READY via `apps.workflow.services.transition()` (never set status directly; U-03 status guard).
- **Acceptance:**
  - [x] Option referencing a non-existent supplier is flagged, not shown as actionable
  - [x] Raw agent output stored for audit (AC-07)
  - [x] Tests for valid, schema-invalid and fact-mismatch payloads
- **Done in:** -
- **Notes:** Validator done: `backend/apps/intelligence/validation.py`, pure functions (schema + fact check against snapshot/impact, per-option `unsupported` + reason codes, fail-closed `VALIDATOR_ERROR`), 163 tests, 98% coverage, Opus review APPROVE after 2 fix rounds (task branch e9ff488). All 26 live agent runs pass. Schema folder from `SG_SCHEMA_DIR` (env or Django setting), falling back to repo `ai/schemas`. LEFT after U-03 merges + N-10 `transition()`: persistence (one AgentRun per call with `raw`/`valid`/`validation_errors`; Recommendation rows with `round`, `unsupported`, `unsupported_reasons`) + `POST /internal/risk-cases/{id}/analysis` + transition to RECOMMENDATION_READY; then tick box 2. Uday asks: `jsonschema>=4.18` in backend/requirements.txt; copy `ai/schemas` into the backend image + set `SG_SCHEMA_DIR`. Follow-up: transfer destination check needs a PO location field that the snapshot has (currently ineffective; transfers are not in the ERP PATCH). PERSISTENCE DONE (branch `nikhil/N-07-validator--persist`): `intelligence.services.record_analysis(tenant, case, kind, payload, actor, correlation_id=None, round=None)` -> `AnalysisResult(agent_run, valid, validation_errors, actionable_count, status, recommendations)`. Phase 1 (own transaction): lock case, 409 unless ANALYZING / round matches / no valid output already recorded, then insert ONE AgentRun (`raw` = payload as received, `valid`, `validation_errors`, `round`) even when invalid, so it survives a later failure. Phase 2 (valid only): RISK_ANALYSIS -> `case.analysis`; RECOVERY_PLAN -> Recommendation per option (round, rank, `unsupported`, `unsupported_reasons`, status PENDING, Decimal lead time / cost delta + currency) + `no_viable_option_reason`, then `transition()` only: ANALYZING -> RECOMMENDATION_READY -> PENDING_APPROVAL, or CLOSED when no actionable option (TC-05, also when every option is unsupported). Invalid output: no status change, case stays ANALYZING. Endpoint `POST /api/v1/internal/risk-cases/{id}/analysis` (SYSTEM API key; tenant from key; foreign case 404): body `{kind: RISK_ANALYSIS|RECOVERY_PLAN, payload: <agent JSON>, round: int (required, the case's analysis_round)}` -> 201 `{agent_run_id, valid:true, actionable_count, risk_case_id, case_status, analysis_round, recommendation_ids}`; invalid -> 422 `BUSINESS_RULE`, `error.details = {reason: AGENT_OUTPUT_INVALID, agent_run_id, errors, case_status}` (run still stored); 409 `CONFLICT` with `details.reason` CASE_NOT_ANALYZING | STALE_ROUND | ALREADY_RECORDED (nothing written, per WORKFLOW_DESIGN s4.2). One audit row per call: `analysis.recorded` / `analysis.invalid` / `analysis.rejected`. Uday-file edits: `apps/accounts/permissions.py` (`internal.analysis` routed=False -> routed) and `tests/test_role_matrix.py` (route + expected-routed entry). N-09/N-08: after the agents answer, POST the Risk Analyst output (kind RISK_ANALYSIS) and then the Recovery Planner output (kind RECOVERY_PLAN) with the `X-API-Key`; treat 201 as done, 422 as an invalid output to re-ask the agent, 409 as already recorded / case moved on (do not retry). Always send the case's `analysis_round` as `round` (required). Review fixes: duplicate rank = 422, out-of-range numbers = unsupported + nulled, 413 over 1 MB, audit row on every outcome. Note: saved risk-analyst DEMO run cites risk_score 90 / coverage 1.5, the live engine on the seed gives 67 / 1.0, so that run is rejected as RISK_SCORE_MISMATCH against a real case; N-09 must feed the agents the case's own `impact`.

### N-08: Boomi correlation + impact orchestration process
- **Status:** todo
- **Sprint:** S2 (3-7 Dec) | **Est:** 2d | **Depends:** U-07, U-08, N-04
- **Branch:** `nikhil/N-08-correlation`
- **Model hint:** sonnet / medium via `boomi-builder`
- **Deliverables:** process `SG_Disruption_Correlate` (in `boomi/processes/correlation/`): event -> Uday's ERP read processes (supplier/POs/inventory/alternates) -> `POST /internal/risk-cases` (snapshot) -> invoke agents (N-09) -> post analysis back. Errors routed to error handling (U-11).
- **Acceptance:**
  - [ ] Demo scenario creates RiskCase with correct AffectedPOs in < 10 s
  - [ ] TC-03: all open POs of the supplier evaluated
- **Done in:** -
- **Notes:** -

### N-09: Agent invocation wiring + offline fallback
- **Status:** todo
- **Sprint:** S2-S3 (6-10 Dec) | **Est:** 1.5d | **Depends:** N-05, N-06, N-08
- **Branch:** `nikhil/N-09-agent-invocation`
- **Model hint:** sonnet / medium (use the claude-api skill for the fallback client)
- **Deliverables:** step in N-08 (or Agent Garden API call) that runs both agents in sequence; Django fallback `apps.intelligence.agents` using the Claude API with the same schemas (CONTRACTS section 8) behind `AGENT_BACKEND=boomi|claude|mock`; `mock` returns fixtures for offline demo.
- **Acceptance:**
  - [ ] All three backends produce schema-valid output for the demo scenario
  - [ ] Switching backend needs only an env var
- **Done in:** -
- **Notes:** -

### N-10: Workflow app - state machine + decisions
- **Status:** review
- **Sprint:** S3 (8-11 Dec) | **Est:** 2d | **Depends:** U-03, U-04, U-05
- **Branch:** `nikhil/N-10-state-machine`
- **Model hint:** opus / high design, sonnet / medium implement
- **Deliverables:** `backend/apps/workflow/services.py` `transition()` enforcing CONTRACTS section 7; `POST /recommendations/{id}/decision` with role check (`PROCUREMENT_MANAGER` only), creates Approval, triggers execution (U-10) only on APPROVE; every transition audited.
- **Acceptance:**
  - [x] Table-driven test covering every allowed and forbidden transition
  - [x] TC-06: reject -> no execution call, comments audited
  - [x] OPS_MANAGER / VIEWER cannot decide (403)
- **Done in:** -
- **Notes:** Roop's R-05 and R-06 need this by 11 Dec. Design spec: `backend/apps/workflow/WORKFLOW_DESIGN.md`. Plan:
  | piece | runs as | model / effort | branch |
  |---|---|---|---|
  | design spec | nested session | opus / medium | `nikhil/N-10-state-machine--design` |
  | services + decision API + tests | nested session | sonnet / medium | `nikhil/N-10-state-machine--impl` |
  | review | subagent reviewer | opus / high | - |
  **Built (branch `nikhil/N-10-state-machine--impl`):** `apps/workflow/{states,exceptions,services,hooks,serializers,views,urls}.py` + `workflow_resume_pending` command. `transition()` (16-edge table, row lock, status + audit in one transaction, idempotent repeat, `analysis_round`/`recovered_at` handled by the edge), `record_decision()`, `start_execution/dispatch_execution/retry_execution/record_execution_result`, `close_case/reanalyze_case`, Execution/Analysis trigger interfaces with logging+audit stubs. 181 workflow tests, 96% coverage on `apps/workflow/`; whole backend 854 passed; `scripts/run_checks.py` all PASS. Concurrency tests (spec s5.5, Postgres only) NOT written.
  **Decisions:** Q1 yes: decision API needs a human PROCUREMENT_MANAGER JWT, API key = 403, so R-06 must call with the approving user's token. Q2: service functions only (no HTTP); PM and ADMIN may close, re-analysing a REJECTED case is PM only; endpoint proposal `POST /risk-cases/{id}/close` and `/reanalyze` is in the spec for the CONTRACTS freeze. Q3 yes: ANALYZING -> CLOSED is the 16th edge (manual, PM or ADMIN, reason required).
  **Edits in Uday's files (tell Uday):** (1) `apps/accounts/permissions.py`: `recommendations.decision` now `routed=True`. (2) `tests/test_role_matrix.py`: row added to `EXPECTED_ROUTED` and `ROUTES`, plus `EXTRA_OK = {"recommendations.decision": {404}}` (allowed role on an unknown id reaches the lookup). (3) `config/urls.py`: include `apps.workflow.urls` under `api/v1/`. (4) `apps/core/exceptions.py`: a dict `exc.details` becomes `error.details` (3 lines). (5) `config/settings.py`: `WORKFLOW_EXECUTION_TRIGGER`, `WORKFLOW_ANALYSIS_TRIGGER` (env, default stubs).
  **What others call:** R-05/R-06: `POST /api/v1/recommendations/{id}/decision` `{decision, comments}` with the user JWT (comments required for REJECT/REQUEST_CHANGES); 201 body has `execution_id`; retries get 409 `ALREADY_DECIDED` / `CASE_NOT_PENDING_APPROVAL` = already recorded; `error.details.reason` carries the code. U-10: implement `apps.workflow.hooks.ExecutionTrigger.trigger(ExecutionRequest)` and set `WORKFLOW_EXECUTION_TRIGGER`; the result endpoint calls `services.record_execution_result(execution, outcome="SUCCEEDED"|"FAILED", attempt=<ExecutionRequest.attempt>, error_class=, error=, response_status=, response_payload=, actor=request.user)`; U-11 retry endpoint calls `services.retry_execution(execution, actor=request.user)`; `start_execution` runs by itself via on_commit after an APPROVE. N-07: persist, then `transition(case, RECOMMENDATION_READY, actor=request.user, reason=...)` then PENDING_APPROVAL or CLOSED (spec s4.2); N-04: `transition(case, ANALYZING, actor="SYSTEM", reason=...)` in the same atomic as case creation; N-09: implement `AnalysisTrigger.request_analysis(AnalysisRequest)`, set `WORKFLOW_ANALYSIS_TRIGGER`. Never save RiskCase `status` directly.
  **Opus review fixes (2nd commit):** `attempt` is now REQUIRED on results: U-10 must echo `ExecutionRequest.attempt` back in `POST /internal/executions/{id}/result` (U-11 result schema), anything other than `retry_count + 1` gets 409 `STALE_EXECUTION_RESULT`. `record_decision` itself refuses non-PM (403). Same-state `transition()` still checks the actor. `dispatch_execution` skips unless case EXECUTING and execution RUNNING. After-commit steps log instead of raising (a pending execution is left for `workflow_resume_pending`). Uday-file edits changed: `apps/core/exceptions.py` now reads `exc.contract_details` (only workflow errors set it); `config/settings.py` rejects trigger paths outside `apps.` at startup; added `test_workflow_trigger_settings_must_be_inside_apps` to `tests/test_settings.py`. Postgres concurrency tests are in `tests/test_workflow_concurrency.py` (skipped on SQLite) and were NOT run: no Postgres/Docker daemon available locally.

### N-11: What-if simulator backend
- **Status:** review
- **Sprint:** S3 (11-14 Dec) | **Est:** 1.5d | **Depends:** N-04
- **Branch:** `nikhil/N-11-what-if`
- **Model hint:** sonnet / medium
- **Deliverables:** `POST /what-if` (supplier_id, duration_days, event_type) and `POST /risk-cases/{id}/what-if`; reuses impact engine on a fresh snapshot; returns affected POs, inventory gaps, exposure, best option estimate, assumptions; no DB writes except an audit row.
- **Acceptance:**
  - [x] "Supplier A out for 7 days" returns in < 3 s on seed data
  - [x] Response labels everything as estimate + lists assumptions (AC-03)
- **Done in:** -
- **Notes:** Built: `backend/apps/intelligence/{whatif,views,serializers,urls}.py`; `what-if` matrix row routed; settings `ERP_BASE_URL` (http://localhost:8081), `ERP_API_KEY`, `ERP_TIMEOUT_SECONDS` (2.0 per call), `ERP_TOTAL_TIMEOUT_SECONDS` (2.5 for all calls, then 504); `httpx` in requirements. Roles: ADMIN, PROCUREMENT_MANAGER, OPS_MANAGER; VIEWER and API keys 403. Tenant from JWT, ERP called with `X-Tenant-ID`; a foreign or unknown supplier/case = 404 (a foreign supplier also writes `security.cross_tenant_denied`); snapshot rows with a missing/other tenant_id, bad ERP data (SnapshotError) and ERP 5xx = 502 `DOWNSTREAM_ERROR`; timeout = 504 `DOWNSTREAM_TIMEOUT`. One `what_if.run` audit row per successful call, no other writes. **R-07 shapes.** `POST /api/v1/what-if` body `{supplier_id:'SUP-1008', duration_days: 1..90 int, event_type: FACTORY_OUTAGE|SHIPMENT_DELAY|QUALITY_HOLD|CAPACITY_REDUCTION|LOGISTICS_DISRUPTION, start?: ISO datetime (default now)}`; `POST /api/v1/risk-cases/{id}/what-if` body `{duration_days?, start?}` (start defaults to the case event time, duration to the event's; duration required if the event has none). 200 response: `estimate:true`, `disclaimer`, `labels{...:'ESTIMATE'}`, `scenario{supplier_id,supplier_name,event_type,severity,duration_days,duration_hours,start,end,risk_case_id|null,source:'REQUEST'|'RISK_CASE'}`, `affected_pos[]` (po_id,item_id,line_nos,due_date,affected_qty,estimated_delay_days,exposure_amount{amount,currency},inventory_coverage_days,shortage), `inventory{gaps[],items[],coverage[]}`, `exposure`, `estimated_delay_days`, `shortage`, `risk{score,level,components}`, `best_option_estimate{is_estimate,rule,items[{item_id,affected_qty,status:RECOMMENDED|COVERED_BY_STOCK|NO_OPTION,recommended{option_type:ALTERNATE_SUPPLIER|INVENTORY_TRANSFER,...}|null,note,data_gap}],uncovered_item_ids,total_estimated_cost_delta,cost_delta_complete,max_lead_time_days}|null`, `assumptions[]`, `data_gaps[]`. Demo for the UI: `start: 2026-12-02T10:15:00Z`, 7 days -> PO-1001 + PO-1002, INR 491,400.00, score 83 HIGH, ~0.55 s. Without `start` the window begins now, so against the seed a short outage may touch no PO (stated in `data_gaps`). Typed scenarios derive severity from duration (<=3 d MEDIUM, <=14 d HIGH, else CRITICAL); case-based ones copy the case severity. Alternate capacity (units/week) is compared with the whole affected qty like N-04 (stated in assumptions). CONTRACTS gaps: no what-if request/response shape in s4 (this Notes is the spec), VIEWER excluded per matrix while s2 only names OPS_MANAGER, `ERP_*` env names new.

### N-12: Data Hub supplier golden record (OPTIONAL)
- **Status:** todo
- **Sprint:** S4 (15-20 Dec) | **Est:** 1.5d | **Depends:** U-01 (only if Data Hub enabled)
- **Branch:** `nikhil/N-12-data-hub`
- **Model hint:** sonnet / medium
- **Deliverables:** supplier model in Data Hub, match rule on name + tax id, the 3 seeded duplicates resolved; integration processes use the golden ID.
- **Acceptance:**
  - [ ] Duplicate suppliers in seed collapse to one golden record
  - [ ] If Data Hub not enabled: mark `done` with Notes "skipped - not licensed"
- **Done in:** -
- **Notes:** -

### N-13: AI governance + approval tests
- **Status:** review
- **Sprint:** S5 (21-24 Dec) | **Est:** 1.5d | **Depends:** N-07, N-10
- **Branch:** `nikhil/N-13-ai-tests`
- **Model hint:** sonnet / medium write, haiku / low run
- **Deliverables:** tests for TC-04, TC-05, TC-06, hallucination/guardrail suite, unauthorized approval attempts; results in `docs/TEST_REPORT.md`.
- **Acceptance:**
  - [x] All listed TCs pass and are in the report
  - [x] AC-03 evidence: JSON or screenshot showing facts vs estimates vs missing data
- **Done in:** nikhil/N-13-ai-tests--tests
- **Notes:** `backend/tests/governance/` (52 pass, 1 Postgres-only skip, 2 strict xfail) drives the real HTTP API: TC-04/05/06, 14-row recovery-plan + 6-row risk-analysis guardrail tables on the saved v1.3.0 runs, unauthorized/stale/double approval, AC-03 evidence `docs/evidence/AC-03_demo.json`, AC-07 trail; report `docs/TEST_REPORT.md`. Also fixed WORKFLOW_DESIGN s4.1 (RECEIVED->ANALYZING is in U-06 wrapper). FINDINGS (validator unchanged): F-01 capacity figures in recovery-option free text are not fact-checked; F-02 alternate supplier for a PO with shortage=false is not flagged (TC-04b). Both are xfail(strict).

### N-14: End-to-end integration tests
- **Status:** todo
- **Sprint:** S5 (22-26 Dec) | **Est:** 1.5d | **Depends:** U-10, U-11, N-10
- **Branch:** `nikhil/N-14-e2e-tests`
- **Model hint:** sonnet / medium to write, haiku / low to run and report
- **Deliverables:** `tests/e2e/` scripts that fire each scenario in `data/scenarios/` and assert outcomes; results table in `docs/TEST_REPORT.md`.
- **Acceptance:**
  - [ ] TC-01, 02, 03, 07, 08, 09 automated and passing
  - [ ] Event-to-risk latency measured and recorded (target < 10 s)
- **Done in:** -
- **Notes:** -
