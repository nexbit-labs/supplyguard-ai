# CONTRACTS v1 freeze - decision sheet (task S-01)

Driver: Uday. Base: `docs/CONTRACTS.md` v0.2. Sources: `ai/README.md` and `data/README.md` ("Proposed for S-01"), `mock-erp/README.md` ("Open items"), `tasks/NIKHIL.md` N-01/N-03 notes, `tasks/ROOP.md` Handoff, `tasks/SHARED.md` S-01, LLD 16.x in `docs/BRD_HLD_LLD.md`.
Each item has a RECOMMENDED decision. Default on the call: accept the recommendation unless someone objects. Where a source was unclear, the row says so. Items marked "(new)" were not raised in any source file; they are gaps found while preparing this sheet.

## How to run the call (35 min)
1. (2 min) Uday shares this sheet. Rule: silence = accept the recommendation.
2. (8 min) D-0.1 (`expected_date` vs `due_date`) first, since it touches sections 5, 6 and the seed. Then section 5 and 6 (Nikhil): D-5.x, D-6.x.
3. (5 min) Sections 4 and 2 (Roop): D-4.x, D-2.x (auth asks).
4. (10 min) Sections 1, 3, 7, 8: D-1.x, D-3.x, D-7.x, D-8.x, then section 9 (items added after U-01 / U-02 / U-07 work).
5. (5 min) Anything rejected or deferred: assign an owner and a date; mark it "not in v1".
6. (5 min) Sign-off checklist (below), then "After the call".

Approvers: Roop = sections 2, 4. Nikhil = 3, 5, 6, 7. Uday = 1, 2, 4, 8. Item 0.x needs all three.

**If the call slips:** U-03 (the Django models) does not need all of this sheet. The items that gate it are listed in section 10 ("Minimum for U-03"); approving only those, in a PR comment, unblocks U-03.

Sign-off checklist (matches the S-01 acceptance boxes in `tasks/SHARED.md`):
- [ ] Roop approved sections 4 (UI-facing endpoints) and 2 (roles shown in the UI)
- [ ] Nikhil approved sections 3, 5, 6, 7
- [ ] Uday approved sections 1, 2, 4, 8
- [ ] Every item below is marked ACCEPT / MODIFY / REJECT / DEFER (write it in the PR description or next to the item)

## 0. Cross-cutting (all three)
| # | Item | Raised by | Current v0.2 text | Proposal | RECOMMENDED | Approve |
|---|---|---|---|---|---|---|
| D-0.1 | PO date field: header `expected_date` vs line `due_date` | S-01 notes; N-01 reviewer (MAJOR); `data/README.md` | s5: PATCH changes `expected_date`. s6 evidence example cites `expected_date`. Seed/snapshot POs have a header `expected_date` (= latest line `due_date`) and line `due_date`. LLD 16.1 has header `expected_date` only | Keep both and document the relationship | Keep both. Header `expected_date` = PO-level field (LLD, PATCH, UI). Line `due_date` = per-line field (impact rule, evidence). PATCH `expected_date` moves the header and every line `due_date` (mock ERP already does this). Fix the s6 analysis example to cite `entity: PO-1001, field: due_date, item_id: ITM-501`. No seed change | Roop, Nikhil, Uday |
| D-0.2 | CONTRACTS names win over LLD 16.1 names | `data/README.md` | not stated | Add one sentence and point to the field table in `data/README.md` (inventory `on_hand`/`reserved`, event `event_time`/`expected_duration_hours`/`source_system`, money object) | Accept | Nikhil, Uday |

## 1. Conventions (Uday)
| # | Item | Raised by | Current v0.2 text | Proposal | RECOMMENDED | Approve |
|---|---|---|---|---|---|---|
| D-1.1 | Error code `INTERNAL_ERROR` | `mock-erp/README.md` | Codes stop at `DOWNSTREAM_TIMEOUT`; chaos 500 returns `INTERNAL_ERROR` | Add `INTERNAL_ERROR` (500, "unexpected server error") | Accept. Django's generic 500 handler uses it too; Boomi maps ERP `INTERNAL_ERROR` to `DOWNSTREAM_ERROR` (502) toward Django | Uday |
| D-1.2 | Money precision | `ai/README.md`, `data/README.md` | "decimal as string", no dp stated | Seed/ERP/agent-output money = exactly 2 dp; snapshot money may carry 0-4 dp on input | Accept: outputs are 2 dp strings, inputs tolerate up to 4 dp | Uday, Nikhil |
| D-1.3 | ID format patterns | `ai/README.md` (ID regexes in schemas) | prefixed strings, examples only | Treat the regexes in `ai/schemas/common.schema.json` as normative | Accept by reference (one source of truth, do not copy regexes). Unclear from sources whether Django models will enforce them; Uday confirms in U-03 | Uday, Nikhil |
| D-1.4 | (new) `TEN-` prefix missing from the ID list | gap | s1 ID list omits tenant ids, s3 uses `TEN-ACME` | Add `TEN-ACME` / `TEN-GLOBEX` style | Accept | Uday |

## 2. Roles and auth (Roop, Uday)
| # | Item | Raised by | Current v0.2 text | Proposal | RECOMMENDED | Approve |
|---|---|---|---|---|---|---|
| D-2.1 | JWT access lifetime | Roop (Handoff) | s4: "simplejwt". Code default 30 min (`JWT_ACCESS_MINUTES`) | Fix a value | 15 minutes, env-overridable. Frontend already refreshes on 401 | Roop, Uday |
| D-2.2 | JWT refresh lifetime | Roop: "<= 1 day" | Code default 1 day (`JWT_REFRESH_DAYS`) | Fix a value | 1 day. Note: the example in the call brief ("7 days") conflicts with Roop's explicit ask of <= 1 day (refresh token sits in localStorage), so honour Roop. Env override for demo day | Roop, Uday |
| D-2.3 | Refresh rotation | Roop: keep `ROTATE_REFRESH_TOKENS` off | Not set (simplejwt default off) | Decide yes/no | OFF in v1, no blacklist app. Frontend refreshes from several tabs and cross-tab rotation handling is deferred in R-01; rotation would log users out on a race. Revisit in S-04 | Roop, Uday |
| D-2.4 | `/me` response shape | Roop (provisional in `frontend/src/api/types.ts`: `{user:{id,username,full_name,email}, tenant:{id,name}, role}`) | s4: "user + tenant + role" | Fix the JSON | Flat user: `{ "id": "...", "email": "...", "name": "...", "role": "ADMIN", "tenant": { "id": "TEN-ACME", "name": "Acme Industries" } }`. Roop updates `types.ts` and the MSW mock (no `username`). Unclear whether the UI needs `username`; if yes add it, equal to `email` | Roop, Uday |
| D-2.5 | Token endpoints and claims | s4 | `POST /auth/token`, `/auth/refresh`; claims `user_id`, `tenant_id`, `role` | State request/response | Keep claims. `/auth/token` body `{email,password}` (unclear if Roop's login form uses email or username; confirm), response `{access, refresh}`; `/auth/refresh` body `{refresh}` response `{access}` | Roop, Uday |
| D-2.6 | (new) Role-per-endpoint column in s4 | gap | s2 is prose; s4 names a role on some rows only | Add a "Roles" column to every s4 row | Reads: all four user roles. What-if: OPS_MANAGER, PROCUREMENT_MANAGER, ADMIN. Decision and retry: PROCUREMENT_MANAGER only. Settings, users, integrations: ADMIN. Audit: ADMIN + PROCUREMENT_MANAGER (read). Suggestion only, not from a source; Roop's R-08 gating depends on it | Roop, Uday |
| D-2.7 | (new) 401 vs 403 | gap | codes defined in s1 | Expired/invalid token = 401 `UNAUTHENTICATED` (frontend refreshes); wrong role or tenant = 403 `FORBIDDEN` | Accept | Roop, Uday |

## 3. Canonical disruption event (Nikhil)
| # | Item | Raised by | Current v0.2 text | Proposal | RECOMMENDED | Approve |
|---|---|---|---|---|---|---|
| D-3.1 | Event field names vs LLD | `data/README.md` | s3 names | s3 wins (see D-0.2) | Accept, no shape change | Nikhil |
| D-3.2 | Scenario timestamps in `/demo/simulate-disruption` | `data/README.md` | "builds a canonical event from a scenario name" | Scenarios carry their own `event_time`; either replay it, or re-anchor the seed with `--as-of <today>` | Accept: endpoint takes an optional `event_time`, default = scenario value; note in s4. Source phrases this as a caveat, not a hard requirement | Nikhil, Uday |
| D-3.3 | (new) Unknown supplier event (TC-02) | `data/README.md` scenario `tc02` ("flagged, no impact") | s3 silent | Unknown `supplier_id` is accepted (202); a RiskCase is created and flagged with no affected POs; not a 400 | Accept. Exact HTTP behaviour is not stated in any source, hence the explicit decision | Nikhil, Uday |
| D-3.4 | Other topics (`shipment.delay`, `recovery.status`) | s3 | inline only | No schema files for v1 | Accept unchanged | Nikhil |

## 4. Django REST API (Roop, Uday)
| # | Item | Raised by | Current v0.2 text | Proposal | RECOMMENDED | Approve |
|---|---|---|---|---|---|---|
| D-4.1 | `/me` shape | Roop | see D-2.4 | see D-2.4 | see D-2.4 | Roop, Uday |
| D-4.2 | (new) Pagination on every list | gap | `{count,next,previous,results}` only on suppliers | Apply to all list endpoints (risk-cases, users, audit); page size 25 (DRF default already in code), `?page=` | Accept | Roop, Uday |
| D-4.3 | Body of `POST /internal/risk-cases` | gap (not specified anywhere) | "posts ERP snapshot" | Snapshot = the ERP-snapshot part of `agent_input.schema.json` | Accept: same field names as the s5 ERP responses (`supplier`, `purchase_orders[]`, `inventory[]`, `alternate_suppliers[]`, `shipments[]`); Uday + Nikhil confirm against the schema | Uday, Nikhil |
| D-4.4 | `POST /events/disruptions` response | s3 vs s4 | s3: duplicate -> 200 with `correlation_id` and `risk_case_id`; s4: new -> 202 `{event_id, correlation_id, status}` | Same keys both ways | Accept: both return `{event_id, correlation_id, status, risk_case_id}`; `risk_case_id` may be null on 202 if creation is async | Uday |
| D-4.5 | `GET /risk-cases/{id}` nested shapes | s4 | "approvals, executions, timeline", no shape | Defer to OpenAPI (`/api/schema/`), which s4 already calls the machine-readable version | Accept, no change in v1 | Roop |
| D-4.6 | `agent_input` as snapshot contract | `ai/README.md` | not in contract | see D-6.1 | see D-6.1 | Uday, Nikhil |

## 5. Mock ERP API (Nikhil)
| # | Item | Raised by | Current v0.2 text | Proposal | RECOMMENDED | Approve |
|---|---|---|---|---|---|---|
| D-5.1 | `GET /erp/purchase-orders/{id}` | N-03 | missing | Add (U-10 re-reads after a write) | Accept | Nikhil |
| D-5.2 | `GET /erp/shipments?po_id=&status=` | N-03 | missing | Add | Accept | Nikhil |
| D-5.3 | `X-Tenant-ID` header | N-03 | missing | Required on PATCH, optional on reads (absent = all tenants); another tenant's id -> 404 | Accept, with one change: make it required on every `/erp/*` call (Boomi always sends it; "absent = all tenants" is a cross-tenant leak path). If Nikhil prefers, keep as implemented | Nikhil, Uday |
| D-5.4 | PATCH body | N-03 | "change `expected_date` / `supplier_id` / split qty" | Document the three forms: `{expected_date}`, `{supplier_id}`, `{supplier_id, qty[, item_id][, expected_date]}`; optional `approval_id`, `reason`; unknown fields 400; response `{idempotency_key, change_type, po, created_po}` | Accept; copy the table from `mock-erp/README.md` into s5 | Nikhil |
| D-5.5 | INVENTORY_TRANSFER is not a PO change | N-03 | s6 allows the option type; s5 has no endpoint | U-10 must not PATCH it (400) | Accept for v1: approval recorded, no ERP write. What final state the case reaches is unclear in all sources; propose RECOVERED with a note "manual transfer", Nikhil (transition owner) confirms | Nikhil, Uday |
| D-5.6 | `Idempotency-Key` = `execution_id` | N-03 | "header required" | Boomi sends `execution_id` and reuses it on every retry (a new key would apply a SPLIT twice) | Accept | Nikhil, Uday |
| D-5.7 | 409 semantics and replay header | N-03 | missing | `CONFLICT` with `details.reason` in `IN_FLIGHT`, `KEY_REUSED`, `RESET_DURING_REQUEST`; replays carry `Idempotent-Replayed: true` | Accept (copy to s5) | Nikhil |
| D-5.8 | `INTERNAL_ERROR` | N-03 | missing | see D-1.1 | see D-1.1 | Nikhil |
| D-5.9 | Inventory fields | `data/README.md`, N-03 | `on_hand, reserved, safety_stock` | Add `daily_demand` (units/day) and `location_id` filter | Accept | Nikhil |
| D-5.10 | Alternate supplier fields | N-03 | `lead_time_days, unit_price, capacity` | Add `qualified` and `?qualified=`; `capacity` units/week, null = unknown (agents report as missing data) | Accept | Nikhil |
| D-5.11 | PO response shape | N-02/N-03 | "lines: item_id, qty, due_date, unit_price" | Document header `po_id, tenant_id, supplier_id, order_date, expected_date, status, amount{amount,currency}`, lines also `line_no`, `item_name`; list response `{count, results}` | Accept | Nikhil |
| D-5.12 | ERP auth | N-03 | none | `X-Api-Key` when `MOCK_ERP_API_KEY` is set; Boomi connection sends it | Accept (also goes in `docs/BOOMI_ENV.md`, Uday) | Nikhil, Uday |
| D-5.13 | Test-only endpoints | N-03 | `_chaos` only | Add `_reset`, `_requests`; hidden when `MOCK_ERP_ENABLE_TEST_ENDPOINTS=false`; chaos accepts `delay_seconds` | Accept, labelled "test only" | Nikhil |
| D-5.14 | Chaos `timeout` semantics | N-03 | `mode: timeout` | PATCH hangs, then applies the write; retry with the same key gets 409 while hung, then the stored result | Accept (drives U-10 and TC-08) | Nikhil, Uday |

## 6. AI agent contracts (Nikhil)
| # | Item | Raised by | Current v0.2 text | Proposal | RECOMMENDED | Approve |
|---|---|---|---|---|---|---|
| D-6.1 | `agent_input.schema.json` as the agent/snapshot input contract | `ai/README.md` | not in s6 | Add as third schema; `RECOVERY_PLANNER` input must carry `risk_analysis`, `RISK_ANALYST` must not | Accept; one line in s6 pointing at the file (do not paste it) | Nikhil, Uday |
| D-6.2 | Evidence qualifiers | `ai/README.md` | `{source, entity, field, value}` | `source` enum `ERP\|EVENT\|IMPACT`; optional `item_id`, `location_id`; `value` may be null (= missing) | Accept | Nikhil |
| D-6.3 | Nullable estimates | `ai/README.md` | example has values only | `estimated_lead_time_days` and `estimated_cost_delta` nullable; then `data_gaps` must be non-empty | Accept | Nikhil |
| D-6.4 | `affected_entities.type` enum | `ai/README.md` | free string `"PO"` | Enum as in `ai/schemas/` | Accept; full list lives in the schema (not reproduced here) | Nikhil |
| D-6.5 | `confidence` enum | `ai/README.md` | `"MEDIUM"` | `LOW\|MEDIUM\|HIGH` (exact values per schema) | Accept | Nikhil |
| D-6.6 | `change` fields per `option_type` | `ai/README.md` | ALTERNATE_SUPPLIER example only | ALTERNATE_SUPPLIER -> `supplier_id`; SPLIT_ORDER -> `supplier_id`+`qty`; REVISED_SCHEDULE -> `expected_date`; INVENTORY_TRANSFER -> `item_id`,`from_location_id`,`to_location_id`,`qty` | Accept. Ask Nikhil to also allow optional `item_id` on ALTERNATE_SUPPLIER/SPLIT_ORDER (mock ERP PATCH uses it for multi-line POs) | Nikhil |
| D-6.7 | Other tightenings (N-01 reviewer follow-up 2) | N-01 review | examples only | risk_level enum, `risk_score` 0-100, `rank` >= 1, `minItems`, ID regexes, non-blank strings, `additionalProperties:false`, all keys required (empty arrays OK) | Accept as one sentence: "`ai/schemas/` is normative; s6 examples are illustrative" | Nikhil |
| D-6.8 | Recovery example arithmetic | `data/README.md` | `qty: 400` with delta 33,600.00 | `qty` -> 4000 ((84.00 - 75.60) x 4000) | Accept | Nikhil |
| D-6.9 | Analysis example evidence | D-0.1 | `field: expected_date` | `due_date` + `item_id` | see D-0.1 | Nikhil |
| D-6.10 | Agent money = exactly 2 dp | `ai/README.md` | not stated | see D-1.2 | Accept | Nikhil |
| D-6.11 | Schema vs N-07 responsibilities | `ai/README.md` | "validator rejects options whose IDs/qty/prices do not exist" | Schema = shape only; N-07 = evidence resolves and matches, IDs/qty/prices exist, ranks unique and contiguous, agent `risk_score` vs `impact.risk_score`; N-07/N-08 take tenant from the stored case, not the payload | Accept. Tolerance for the score comparison is not stated in any source; propose exact match | Nikhil |
| D-6.12 | `bundled_schema` docstring, 3 missing option-type fixtures | N-01 follow-ups 3, 4 | n/a | Not contract items (code/test tidy-up in `ai/`) | No decision needed; Nikhil handles | - |

## 7. RiskCase state machine (Nikhil)
| # | Item | Raised by | Current v0.2 text | Proposal | RECOMMENDED | Approve |
|---|---|---|---|---|---|---|
| D-7.1 | (new) Explicit transition list | gap (diagram is hard to read) | ASCII diagram | Add the list: RECEIVED->ANALYZING; ANALYZING->RECOMMENDATION_READY; RECOMMENDATION_READY->PENDING_APPROVAL; PENDING_APPROVAL->APPROVED or REJECTED; APPROVED->EXECUTING; EXECUTING->RECOVERED or EXECUTION_FAILED; EXECUTION_FAILED->EXECUTING or CLOSED; REJECTED->ANALYZING or CLOSED; RECOVERED->CLOSED | Accept (reads straight off the diagram, no behaviour change) | Nikhil |
| D-7.2 | (new) No viable option (TC-05) | gap | s6 allows `options: []`; s7 silent | Case reaches RECOMMENDATION_READY with zero options; nothing to approve | Recommend: allow `RECOMMENDATION_READY -> CLOSED` (not in the diagram) and re-analysis via `-> ANALYZING`; never PENDING_APPROVAL with zero options. Nikhil decides | Nikhil |
| D-7.3 | 409 detail | s7 | "Invalid transition -> 409 CONFLICT" | `details = {from, to}` | Accept | Nikhil, Uday |
| D-7.4 | (new) Actor for system-driven transitions | gap | `transition(case, to_state, actor)` | actor = user id or the literal `SYSTEM`; AuditEvent records which | Accept | Nikhil, Uday |

## 8. Fallbacks (Uday)
| # | Item | Raised by | Current v0.2 text | Proposal | RECOMMENDED | Approve |
|---|---|---|---|---|---|---|
| D-8.1 | Fallbacks "decided in U-01" | s8 header | UPDATED 2026-11-23: U-01 is done and merged (`docs/BOOMI_ENV.md`). Flow is not available in Uday's account; Event Streams, Agentstudio, API Management, Data Hub open (access-verified only). Uday decided: no native Flow app; the React UI is built as one HTML file (`vite-plugin-singlefile`) and hosted in Flow | Freeze s8 and reword the Flow row | Freeze. s8 Flow row becomes: "Flow is not used natively. Approval happens in the React UI (SaaS Approval Center), hosted in Flow as a single-file HTML where Roop's account has Flow; Django triggers the Boomi execution process after the decision." Other rows stay as defaults. No shape changes | Uday, Roop |
| D-8.2 | `AGENT_BACKEND` switch | N-09 task | s8 describes fallback only | Name `AGENT_BACKEND=boomi\|claude\|mock` | Accept | Uday, Nikhil |

## 9. Added after U-01 / U-02 / U-07 (2026-11-23, found while building; not in any earlier source)
Same rule: silence = accept the recommendation. "New" = raised by the Boomi/backend work, not by the sources listed at the top.

| # | Item | Raised by | Current v0.2 text | Proposal | RECOMMENDED | Approve |
|---|---|---|---|---|---|---|
| D-1.5 | Rate-limit error code | U-09 plan (API Management rate limit), U-02 handler | s1 codes have no 429 | Django maps 429, 405, 415 to `VALIDATION_ERROR` today (U-02) | Add `RATE_LIMITED` (429) so clients can back off instead of treating it as bad input; keep 405/415 folded into `VALIDATION_ERROR` and say so in s1 | Uday, Roop |
| D-1.6 | Error body for non-DRF errors | U-02 review | s1: "Error body (all services)" | Django's own 404 (unknown URL) and unhandled 500 are HTML pages today; the contract handler only covers DRF exceptions | U-05 adds JSON handlers so 404/500 also use the s1 body with a generic message (no stack trace, no exception text); s1 gains the 500 code `INTERNAL_ERROR` (see D-1.1) | Uday |
| D-1.7 | Where Boomi listener processes get the tenant | U-07 review (tenancy) | s1: "Boomi/system callers -> API credential mapped to a tenant"; payload `tenant_id` informational | The `SG_Get_*` listeners take `tenant_id` in the request body and validate `^TEN-[A-Z0-9]+$` (400 if missing/malformed, no ERP call). The listener credential is one shared Basic-auth credential with no tenant binding | Record as a conscious v1 deviation: tenant supplied by the trusted backend/proxy, format-validated by Boomi. U-09 (API Management) binds each credential to a tenant and rejects a mismatching body `tenant_id` with 403; the listener is never exposed publicly. Re-open at S-04 (security review). Alternative if you want to avoid the deviation: one listener credential per tenant now | Uday, Nikhil |
| D-4.7 | UI hosted in Boomi Flow | Uday decision 2026-11-23 | s4 assumes the UI calls Django directly | Roop's React/Vite UI is built to one HTML file (`vite-plugin-singlefile`) and hosted in Flow; no native Flow app | Add a line to s4: API is HTTPS-only and called from a Flow-hosted page, so CORS must allow that origin (Uday adds it to `CORS_ALLOWED_ORIGINS` once Roop knows it), API base URL is absolute at build time, routing must work from a single HTML file (hash routing). Roop confirms how Flow accepts the file and that his account has Flow. R-06 in `tasks/ROOP.md` is re-scoped by Roop from "Flow app" to "build + host" | Roop, Uday |
| D-5.15 | Empty-result semantics on ERP reads | U-07 samples from the real mock ERP | s5 silent | Unknown supplier -> 404; unknown item or supplier with no open POs -> 200 with `count: 0`; wrong tenant -> 404 | Accept and document in s5. Callers must treat `count: 0` as "no data / unknown", never as zero stock or zero capacity (`capacity: null` = unknown) | Nikhil, Uday |
| D-5.16 | Chaos mode only covers writes | U-07 test plan | s5: `POST /erp/_chaos {mode: timeout\|500\|off}` | `_chaos` affects PATCH only; reads are never slowed or failed, so a read-side timeout or 5xx cannot be tested | Optional, low priority: add the same modes for `GET /erp/*` so the Boomi read-process error paths (DOWNSTREAM_ERROR 502, DOWNSTREAM_TIMEOUT 504) can be tested against the ERP. Until then the timeout test uses an unreachable URL in the environment extension. Nikhil decides | Nikhil |
| D-5.17 | New interface: Boomi `SG_Get_*` read processes | U-07 (`boomi/processes/README.md`) | not in CONTRACTS (s5 is the ERP, s4 is Django) | Four listener processes, called by Nikhil's N-08 correlation process and the Agentstudio tools (N-05, N-06). In: `tenant_id`, one id (`supplier_id` or `item_id`), optional `correlation_id`. Out: lists `{correlation_id, count, results}`, supplier `{correlation_id, supplier}`. Errors: s1 body; 400 pass-through for bad input, 404 pass-through, ERP 401 or 5xx -> 502 `DOWNSTREAM_ERROR`, timeout -> 504 `DOWNSTREAM_TIMEOUT` | Add a short s5b "Boomi ERP read processes" that points at `boomi/processes/README.md` and `boomi/processes/profiles/` as the detail (do not paste it). Nikhil is the consumer, so he approves | Nikhil, Uday |
| D-8.3 | Separate Boomi accounts per member | Uday decision 2026-11-20; `docs/BOOMI_ENV.md` section 9 | s8 assumes one Boomi account | Boomi processes cannot call processes in another account; Event Streams topics, API Management gateways and runtimes are per account. N-08 and the agents call Uday's `SG_Get_*` processes; U-08 topics and the U-09 gateway live in Uday's account | Decide the hand-off for v1: (a) Nikhil imports Uday's `boomi/processes/` exports into his account and runs them on his runtime against the mock ERP, or (b) accounts talk over HTTPS only (Uday's API Management endpoints / Django API), or (c) one person builds the whole Boomi chain in one account. Recommend (b) for the integration points and (a) as a fallback for local testing: it matches the API Management design in U-09 and keeps each member in their own account. Exports under `boomi/` in git are the hand-off format in every option. Must be decided before N-08 and U-08 | Uday, Nikhil, Roop |

## 10. Model field names - what U-03 actually needs (added 2026-11-23 from a repo cross-check)
Finding: U-03's acceptance says "field names match CONTRACTS / LLD 16.1", but the two disagree, and `frontend/src/api/types.ts` (Roop, marked PROVISIONAL) disagrees with both. Examples: Approval = `approver_id`/`decision_time` (LLD 16.1) vs `decided_by`/`decided_at` (frontend); AffectedPO = `affected_qty`/`estimated_delay`/`exposure_amount` (LLD) vs `qty`/`value`/`days_late_estimate`/`stock_cover_days` (frontend); Recommendation = `estimated_cost`/`lead_time` (LLD) vs `estimated_cost_delta`/`estimated_lead_time_days` (CONTRACTS s6); User = `name` (LLD, D-2.4) vs `username`/`full_name` (frontend). CONTRACTS has no entity field list for Tenant, User, RiskCase, AffectedPO, Recommendation, Approval, Execution, AuditEvent.

| # | Item | Raised by | Current v0.2 text | Proposal | RECOMMENDED | Approve |
|---|---|---|---|---|---|---|
| D-10.1 | Field-name source of truth for the 8 entities without CONTRACTS names | cross-check | none; LLD 16.1 and `frontend/src/api/types.ts` conflict | Add an "Entities" section to CONTRACTS (field names + types for the 12 U-03 models) | Rule: where a field appears in an API response, the API name wins (CONTRACTS s3-s6, else `frontend/src/api/types.ts`); otherwise the LLD 16.1 name. Uday drafts the table as the first step of U-03 within one working day of the call; Roop and Nikhil approve it in the PR (it is only names and types). The supplier record uses the ERP fields (`tax_id`, `city`, `country`, `quality_score`, `capacity_per_week`, `contact_email`, `disruption_history[{date,event_type,delay_days}]`); Roop aligns the frontend `disruption_history` item (`severity`, `description`, `risk_case_id`) or says which extra fields Django must add | Roop, Nikhil, Uday |
| D-10.2 | Status values not defined anywhere | cross-check | s7 defines RiskCase states only | Define Execution and Recommendation statuses | Execution: `PENDING \| RUNNING \| SUCCEEDED \| FAILED` (as the frontend already uses) plus `retry_count`. Recommendation: `PENDING \| APPROVED \| REJECTED \| CHANGES_REQUESTED` (derived from the s4 decision values; no source defines it, so Nikhil confirms because the workflow app sets it) | Nikhil, Roop, Uday |
| D-10.3 | Meaning of `estimated_lead_time_days` | `boomi/agents/recovery_planner/README.md` | s6 example only | Per-option-type meaning as the planner README proposes | Accept as written in that README, one sentence in s6 | Nikhil |
| D-10.4 | List pagination parameters | `frontend/src/api/types.ts` | s4: `{count,next,previous,results}` only | Frontend sends `page` and `page_size` | Accept `page` and `page_size` (max 100, default 25); see D-4.2 | Roop, Uday |

### Minimum for U-03 (approve these first if the full call slips)
D-0.1 (PO dates), D-0.2 (CONTRACTS names win), D-1.2 (money precision), D-1.3 (ID patterns), D-1.4 (`TEN-` prefix), D-2.4 (user fields), D-5.9 to D-5.11 (inventory, alternate and PO fields, if the Django models mirror the ERP snapshot), D-7.1 and D-7.2 (RiskCase states and transitions), D-10.1, D-10.2.
Not needed for U-03: sections 3, 6, 8 and the other D-5.x, D-2.1 to D-2.3 (JWT lifetimes: U-04), D-1.5 to D-1.7 and D-4.7 (error codes, Boomi, Flow hosting).

### Note on D-0.1 vs D-6.9 (two valid options, pick one on the call)
The sheet recommends changing the s6 evidence example to `due_date` + `item_id`. `ai/README.md` (line 40) says the validator already accepts header `expected_date` without `item_id` and line `due_date` with `item_id`. Both forms validate, so the example change is optional; the smaller change is to keep both names, keep the example, and add one sentence to s5: header `expected_date` = latest line `due_date`, and PATCH moves the header and every line.

## After the call (Uday, about 30 min)
1. Mark every item ACCEPT / MODIFY / REJECT / DEFER (PR description is enough).
2. Apply accepted items to `docs/CONTRACTS.md`: s1 (D-1.x incl. D-1.5 to D-1.7, D-0.2), s5b (D-5.17), s8 (D-8.1, D-8.3), a new Entities section (D-10.1, D-10.2), s2 (D-2.x, role column), s3 (D-3.x), s4 (`/me`, pagination, event response keys, internal snapshot), s5 (new endpoints, headers, PATCH/idempotency tables), s6 (normative-schema sentence, `agent_input` pointer, qty fix, evidence example), s7 (transition list), s8 (`AGENT_BACKEND`).
3. Retitle to v1 (FROZEN), update the Status and Rule lines, add a changelog row `1.0 | <date> | Roop + Nikhil + Uday | Frozen after S-01 call; accepted items: <ids>`.
4. Tell the owners of follow-up code: Roop (`/me` shape in `frontend/src/api/types.ts` and MSW mocks), Nikhil (schema/README/fixture tidy-ups), Uday (`SIMPLE_JWT`: 15 min / 1 day / no rotation, `INTERNAL_ERROR`, U-10 idempotency rules).
5. Tick the three acceptance boxes in `tasks/SHARED.md` S-01, set Status `review`, run `/handoff`. Ticking the "Proposed for S-01" boxes in N-01/N-02/N-03 follows once this is merged.
