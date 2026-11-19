# Tasks - Nikhil (Data & AI · dataset, mock ERP, impact engine, agents, workflow rules)

**Background:** Data engineer (Python, AI/ML). Claude explains frontend and Boomi concepts in data-pipeline terms (ETL steps, schemas, jobs, DataFrames).
**Stream:** Synthetic data + mock ERP, impact/risk scoring, Boomi Agentstudio agents, AI output validation, Boomi correlation process, agent invocation, case state machine, what-if engine, Data Hub (optional), AI + end-to-end tests.
**Owns:** `ai/`, `data/`, `mock-erp/`, `backend/apps/intelligence/`, `backend/apps/workflow/`, `boomi/agents/`, `boomi/processes/correlation/`, `tests/e2e/`

> Status values: `todo` · `in-progress` · `blocked` · `review` · `done`. Only change the `Status`, `Done in`, checkboxes and Notes lines - keep the format, `scripts/progress.py` parses it.
> Model hint = what the advisor (Opus 5.5) should delegate the bulk of the work to. See `docs/AI_WORKFLOW.md`.

## Handoff
<!-- Overwrite this block at the end of EVERY session (run /handoff). Newest state only; history goes to progress/SESSION_LOG.md -->
- **Last updated:** 2026-11-19 (autopilot: N-01 reviewer follow-ups fixed; autopilot stopped on request)
- **Current task:** N-01 follow-ups (N-01, N-02, N-03 in review)
- **Branch:** `nikhil/N-01-agent-schemas`
- **Where I left off:** N-01 reviewer follow-ups 1-5 done in `ai/` (snapshot PO header `expected_date`, full S-01 proposal list + tenant rule in `ai/README.md`, `recovery_plan__all_option_types` + 4 new invalid fixtures, fixture inventory = `data/seed/`, `bundled_schema` docstring); second reviewer APPROVE, `ai/tests` 93 passed. N-03 compose snippet / open items still to send to Uday.
- **Next step:** Raise the `ai/README.md` and `data/README.md` "Proposed for S-01" lists in the S-01 contracts call; send Uday the mock-erp compose snippet. N-04 starts when U-03 lands, N-05 when U-01 lands (restart `/loop /autopilot nikhil` to pick them up automatically).
- **Blockers:** N-04 waits on U-03 (uday, todo); N-05 waits on U-01 (uday, todo); S-01 ratification for N-01/N-02/N-03 contract additions; N-03 compose box needs Uday's docker-compose.yml.
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
- **Status:** todo
- **Sprint:** S1 (25-30 Nov) | **Est:** 3d | **Depends:** U-03, N-02
- **Branch:** `nikhil/N-04-impact-engine`
- **Model hint:** opus / high for the scoring design, sonnet / medium to implement + tests
- **Deliverables:** `backend/apps/intelligence/impact.py`: event + ERP snapshot -> affected POs (affected_qty, estimated_delay, exposure_amount), inventory coverage per item, shortage flags, risk_score 0-100 with documented formula and tenant thresholds; service called by the `POST /internal/risk-cases` endpoint (endpoint shell in Uday's `integrations` app).
- **Acceptance:**
  - [ ] TC-03: multiple open POs all evaluated
  - [ ] TC-04: sufficient inventory -> no shortage claimed
  - [ ] Demo scenario numbers match `data/README.md`
  - [ ] Pure functions, 90%+ line coverage on `impact.py`
- **Done in:** -
- **Notes:** -

### N-05: Supplier Risk Analyst agent (Agentstudio)
- **Status:** todo
- **Sprint:** S2 (1-4 Dec) | **Est:** 2d | **Depends:** N-01, U-01
- **Branch:** `nikhil/N-05-risk-analyst`
- **Model hint:** opus / high (prompt + guardrails); use the boomi-agentstudio skill if installed
- **Deliverables:** agent in Agentstudio with instructions, tools (ERP lookups via Uday's U-07 processes), guardrails: no invented capacity/price/dates, separate facts vs estimates, must fill `missing_data`. Prompt + config versioned in `boomi/agents/risk_analyst/`.
- **Acceptance:**
  - [ ] Output for the demo scenario validates against `risk_analysis.schema.json`
  - [ ] Every claim has an `evidence` entry pointing to snapshot data
  - [ ] 5 adversarial prompts (asking it to guess capacity etc.) -> it reports missing data instead
- **Done in:** -
- **Notes:** -

### N-06: Recovery Planning agent (Agentstudio)
- **Status:** todo
- **Sprint:** S2 (3-6 Dec) | **Est:** 2d | **Depends:** N-01, N-05
- **Branch:** `nikhil/N-06-recovery-agent`
- **Model hint:** opus / high
- **Deliverables:** agent comparing ALTERNATE_SUPPLIER, INVENTORY_TRANSFER, REVISED_SCHEDULE, SPLIT_ORDER; ranks by urgency, inventory, qualification, cost, priority; config in `boomi/agents/recovery_planner/`.
- **Acceptance:**
  - [ ] Demo scenario -> at least 2 ranked viable options (AC-04)
  - [ ] TC-05 scenario -> `options: []` + `no_viable_option_reason`
  - [ ] Output validates against `recovery_plan.schema.json`
- **Done in:** -
- **Notes:** -

### N-07: Agent output validator + persistence
- **Status:** todo
- **Sprint:** S2 (4-7 Dec) | **Est:** 1.5d | **Depends:** N-01, N-04, U-03
- **Branch:** `nikhil/N-07-validator`
- **Model hint:** sonnet / medium; opus / high review
- **Deliverables:** `backend/apps/intelligence/validation.py` + `POST /internal/risk-cases/{id}/analysis`: JSON-schema check, cross-check every referenced supplier/PO/item/qty/price against the stored snapshot, flag unsupported options (`status=UNSUPPORTED`, not actionable), persist Recommendation rows, transition case to RECOMMENDATION_READY.
- **Acceptance:**
  - [ ] Option referencing a non-existent supplier is flagged, not shown as actionable
  - [ ] Raw agent output stored for audit (AC-07)
  - [ ] Tests for valid, schema-invalid and fact-mismatch payloads
- **Done in:** -
- **Notes:** -

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
- **Status:** todo
- **Sprint:** S3 (8-11 Dec) | **Est:** 2d | **Depends:** U-03, U-04, U-05
- **Branch:** `nikhil/N-10-state-machine`
- **Model hint:** opus / high design, sonnet / medium implement
- **Deliverables:** `backend/apps/workflow/services.py` `transition()` enforcing CONTRACTS section 7; `POST /recommendations/{id}/decision` with role check (`PROCUREMENT_MANAGER` only), creates Approval, triggers execution (U-10) only on APPROVE; every transition audited.
- **Acceptance:**
  - [ ] Table-driven test covering every allowed and forbidden transition
  - [ ] TC-06: reject -> no execution call, comments audited
  - [ ] OPS_MANAGER / VIEWER cannot decide (403)
- **Done in:** -
- **Notes:** Roop's R-05 and R-06 need this by 11 Dec.

### N-11: What-if simulator backend
- **Status:** todo
- **Sprint:** S3 (11-14 Dec) | **Est:** 1.5d | **Depends:** N-04
- **Branch:** `nikhil/N-11-what-if`
- **Model hint:** sonnet / medium
- **Deliverables:** `POST /what-if` (supplier_id, duration_days, event_type) and `POST /risk-cases/{id}/what-if`; reuses impact engine on a fresh snapshot; returns affected POs, inventory gaps, exposure, best option estimate, assumptions; no DB writes except an audit row.
- **Acceptance:**
  - [ ] "Supplier A out for 7 days" returns in < 3 s on seed data
  - [ ] Response labels everything as estimate + lists assumptions (AC-03)
- **Done in:** -
- **Notes:** -

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
- **Status:** todo
- **Sprint:** S5 (21-24 Dec) | **Est:** 1.5d | **Depends:** N-07, N-10
- **Branch:** `nikhil/N-13-ai-tests`
- **Model hint:** sonnet / medium write, haiku / low run
- **Deliverables:** tests for TC-04, TC-05, TC-06, hallucination/guardrail suite, unauthorized approval attempts; results in `docs/TEST_REPORT.md`.
- **Acceptance:**
  - [ ] All listed TCs pass and are in the report
  - [ ] AC-03 evidence: JSON or screenshot showing facts vs estimates vs missing data
- **Done in:** -
- **Notes:** -

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
