# SupplyGuard AI - Test report (N-13: AI governance + approval tests)

Rules proved end to end through the real HTTP API (SYSTEM API keys for Boomi, human JWTs for users, `load_seed` data):
**AI never invents facts - missing data is stated - only PROCUREMENT_MANAGER approves - reject never executes.**

## Result
Governance suite (`backend/tests/governance/`, `python -m pytest tests/governance --junitxml=...`): **55 tests - 52 passed, 2 xfail (strict, = findings), 1 skipped (Postgres only), 0 failed.**
Whole backend suite (`python -m pytest -q` from `backend/`): see "Full run" at the bottom. The PASS/FAIL column below was generated from the junit XML of that run.

| Check | What was tested | Test file | Tests | Result |
|---|---|---|---|---|
| TC-04 inventory sufficient: no shortage claimed | Impact says shortage=false; a plan that claims a shortage is unsupported and not approvable | `backend/tests/governance/test_tc04_tc05_tc06.py` | test_tc04_impact_reports_no_shortage, test_tc04_plan_claiming_a_shortage_is_unsupported | 2 pass |
| TC-04b alternate offered although stock covers the need | validator should flag it (see F-02) | `backend/tests/governance/test_tc04_tc05_tc06.py` | test_tc04_alternate_where_stock_covers_the_need_is_flagged | 0 pass, 1 xfail (finding) |
| TC-05 no alternates -> CLOSED | Real RP-TC05 v1.3.0 run (`options: []` + reason): CLOSED, never PENDING_APPROVAL; empty options with no reason is an invalid run | `backend/tests/governance/test_tc04_tc05_tc06.py` | test_tc05_no_alternates_closes_case_never_pending_approval, test_tc05_empty_options_without_reason_is_rejected | 2 pass |
| TC-06 reject never executes | PM REJECT with comments: zero execution trigger calls, no Execution, Approval stored, comments in audit, case state per N-10 (PENDING_APPROVAL while options remain, REJECTED after the last) | `backend/tests/governance/test_tc04_tc05_tc06.py` | test_tc06_reject_does_not_execute_and_is_audited, test_tc06_rejecting_the_last_option_rejects_the_case_still_no_execution | 2 pass |
| Guardrails: recovery plan, one invented fact per row | 14 rows (capacity x2, price, unknown supplier, disrupted supplier, qty > affected, transfer > available, wrong cost delta, wrong date, date not derivable, wrong change field, duplicate option, duplicate rank, risk_case_id mismatch) + every-option-invented + unqualified supplier + valid control | `backend/tests/governance/test_guardrails.py` | test_invented_fact_in_recovery_plan[*], test_every_option_invented_..., test_unqualified_supplier_is_unsupported, test_control_valid_demo_plan_... | 17 pass |
| Guardrails: invented capacity in recovery-plan free text | Capacity figure in assumptions text must be flagged (see F-01) | `backend/tests/governance/test_guardrails.py` | test_invented_capacity_in_recovery_plan_free_text | 0 pass, 1 xfail (finding) |
| Guardrails: risk analysis, one invented fact per row | unknown capacity not in missing_data, invented capacity in free text, invented price, unknown entity, wrong score, risk_case_id mismatch + valid control | `backend/tests/governance/test_guardrails.py` | test_invented_fact_in_risk_analysis[*], test_control_valid_demo_analysis_is_accepted | 7 pass |
| Unauthorized approval: roles, key, anonymous, tenant | OPS_MANAGER / VIEWER / ADMIN 403, SYSTEM key 403, anonymous 401, other tenant PM 404 + `security.cross_tenant_denied` audit row | `backend/tests/governance/test_unauthorized_approval.py` | test_non_pm_roles_get_403[*], test_system_api_key_gets_403, test_anonymous_gets_401, test_other_tenants_pm_gets_404_... | 6 pass |
| Approval state rules | unsupported option 422, stale round 409, any case state but PENDING_APPROVAL 409 (9 states), double approve = 1 Approval + 1 Execution + 1 trigger, denied attempt never a decision | `backend/tests/governance/test_unauthorized_approval.py` | test_unsupported_option_..., test_stale_round_..., test_case_not_pending_approval_gets_409[*], test_double_approve_..., test_denied_attempt_... | 13 pass |
| Concurrent double approve over HTTP | two parallel approves: 201 + 409, one Approval, one Execution (IdSequence reseeded) | `backend/tests/governance/test_unauthorized_approval.py` | test_parallel_double_approve_over_http_one_winner | 0 pass, 1 skipped (Postgres only) |
| AC-03 evidence artifact | writes docs/evidence/AC-03_demo.json; fresh build reproduces it byte for byte | `backend/tests/governance/test_ac03_evidence.py` | test_ac03_evidence_artifact_is_written_and_deterministic, test_ac03_committed_artifact_matches_a_fresh_build | 2 pass |
| AC-07 audit trail | event -> case -> analysis -> decision -> execution readable in order through GET /api/v1/audit | `backend/tests/governance/test_ac07_audit.py` | test_ac07_full_trail_is_readable_in_order | 1 pass |

## AC-03 - facts vs estimates vs missing data
Evidence file: [`docs/evidence/AC-03_demo.json`](evidence/AC-03_demo.json), written by `test_ac03_evidence_artifact_is_written_and_deterministic` from the real stored case (ERP snapshot, saved Risk Analyst v1.3.0 analysis, impact engine, stored recommendations) and the case what-if endpoint (mock ERP, no network).
- `facts`: ERP snapshot values (supplier, affected POs, inventory, alternates) + the agent's stated facts + their evidence pointers (`source/entity/field/value`).
- `estimates`: impact engine numbers (score, exposure, delays), the agent's estimates, recovery-option cost/lead estimates and the what-if result, every block labelled `ESTIMATE`.
- `missing_data`: agent `missing_data` (e.g. SUP-1008 capacity), case `data_quality_flags`, what-if `data_gaps`, per-option `data_gaps`.
Deterministic: fixed snapshot clock, event time instead of "now", no run ids; `test_ac03_committed_artifact_matches_a_fresh_build` rebuilds it on a fresh DB and compares byte for byte.
The guardrail rows prove the other half of AC-03: capacity left out of `missing_data` or stated in the summary makes the analysis invalid (`CAPACITY_NOT_IN_MISSING_DATA`, `CAPACITY_INVENTED`).

## AC-07 - audit history
`test_ac07_full_trail_is_readable_in_order`: case creation, analysis, status moves (ANALYZING, RECOMMENDATION_READY, PENDING_APPROVAL, APPROVED, EXECUTING) and the decision with its comments are read back through `GET /api/v1/audit`, in order. Also covered by the guardrail suite (`analysis.invalid` row per invalid run) and TC-06 (comments in `recommendation.decided`).
Audit rows for denied approvals: only the cross-tenant 404 (`security.cross_tenant_denied`, TC-10) is specified to leave a row and is asserted. 403 / 409 / 422 decision attempts leave no decision row (asserted: never recorded as `recommendation.decided`) and no Approval / Execution.

## Findings (real gaps found, validator code NOT changed)
| ID | Finding | Test (xfail, strict) |
|---|---|---|
| F-01 | Capacity figures stated in a recovery option's free text (`assumptions`, `trade_offs`, `action_steps`) are not fact-checked. Only the risk analysis text is scanned (`CAPACITY_INVENTED`). E.g. "SUP-2002 can supply 8000 units per week" (capacity unknown in the ERP) is accepted as a supported option. The saved v1.3.0 DEMO run already contains a related line ("has sufficient capacity available"). | `test_invented_capacity_in_recovery_plan_free_text` |
| F-02 | TC-04: the validator flags an option that *claims* a shortage (`EVIDENCE_MISMATCH` against `impact.shortage=false`), but not an ALTERNATE_SUPPLIER option for a PO whose impact says stock covers the need (shortage=false): it is `supported` and approvable. | `test_tc04_alternate_where_stock_covers_the_need_is_flagged` |

## Known gaps
- Postgres-only tests are skipped on SQLite and run in CI (Postgres): `test_parallel_double_approve_over_http_one_winner` here, plus `tests/test_workflow_concurrency.py`. They reseed `IdSequence` like that file.
- Nothing here calls the live Agentstudio or Boomi: agent outputs are the saved real runs in `boomi/agents/*/eval/runs/` (v1.3.0), mutated to carry one invented fact; the execution trigger is a recording fake and the ERP for what-if is the in-process mock ERP.
- Risk Analyst DEMO run is aligned to the live impact numbers (saved run was graded on a hand-built input), as in `test_analysis_persist.py`.
- Latency, ERP timeout and duplicate-event cases (TC-01/02/03/07/08/09) belong to N-14 (`tests/e2e/`).

## Full run
`python -m pytest -q` from `backend/` (SQLite): 1212 passed, 6 skipped, 2 xfailed (was 1160 passed before N-13; governance adds 52 passed + 1 skipped + 2 xfailed). `python scripts/run_checks.py`: ai, data, mock-erp, backend, frontend all PASS.
