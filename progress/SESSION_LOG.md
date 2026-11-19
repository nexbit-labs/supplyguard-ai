# Session Log (append-only)

One entry per working session, newest at the bottom. Written by `/handoff` and `/done`. Never edit old entries.

Template:
```
## YYYY-MM-DD HH:MM - <member> - <TASK-ID>
- Branch: <branch> @ <short hash>
- Did: <1-3 bullets>
- Tests: <suite: pass/fail counts>
- Status now: <todo|in-progress|review|blocked|done>
- Next: <one concrete action>
- Blockers: <none | who/what>
- Models used: advisor opus; <subagents used>
```

---

## 2026-11-19 - roop - SETUP
- Branch: collaboration @ initial commit
- Did: Created project structure, CLAUDE.md, task split for Roop / Nikhil / Uday (13/12/13 tasks, ~22-25 dev-days each), contracts v0.1, subagents, slash commands, progress tracker, CI for progress board.
- Tests: `python scripts/progress.py --brief` runs
- Status now: setup done; S-01 contracts freeze in progress
- Next: Roop R-01 + S-01 call; Nikhil N-01; Uday U-01
- Blockers: none
- Models used: advisor opus

## 2026-11-19 - roop - SETUP (re-assignment)
- Branch: collaboration
- Did: Re-assigned tasks by background, removed the "team lead" role. Roop = React screens + UI read APIs + Flow approval screens + CI + demo + Marketplace (12 tasks, ~22.5 d). Nikhil = dataset, mock ERP, impact engine, agents, validator, correlation, state machine, what-if, Data Hub, AI + E2E tests (14 tasks, ~24 d). Uday = Boomi env, Django core, auth/tenancy, audit, event intake, Boomi ERP reads, Event Streams, API Management, execution, error handling, deployment (12 tasks, ~22 d). Task IDs renumbered per owner; CONTRACTS owner column updated (v0.2, no shape changes). CLAUDE.md now tells Claude to explain each task in the member's own terms.
- Tests: progress.py parses all 43 tasks; every task ID referenced in docs exists; no dependency cycles
- Status now: setup done; S-01 contracts freeze (driver Uday) in progress
- Next: Roop R-01; Nikhil N-01 then N-02; Uday U-01 + U-02
- Blockers: none
- Models used: advisor opus

## 2026-11-19 - nikhil - N-02
- Branch: nikhil/N-02-seed-data
- Did: Deterministic generator `data/generate_seed.py` (seeded, `--as-of` re-anchoring with `--out` guard): 2 tenants, 100 suppliers (3 deliberate duplicates, otherwise unique names), 500 POs, 187 inventory rows over 3 DCs, 101 alternates, 20 in-transit shipments; 11 scenario files with expected affected POs / exposure / alternates. Demo: SUP-1008 48 h -> PO-1001 + PO-1002, INR 491,400.00, alternates SUP-2001/2002. README with impact rule, field-name table for U-03/N-03, S-01 proposals. Reviewer pass: fixed TC-05 (no split-order data, precise expectation), name collisions, TC-01 boundary, README/test drift checks.
- Tests: `python -m pytest data/tests -q` -> 33 passed
- Status now: review
- Next: S-01 proposals (daily_demand, CONTRACTS-over-LLD names, s6 example qty); PRs for N-01/N-02; start N-03
- Blockers: S-01 ratification; U-03 needs the field table
- Models used: advisor opus; reviewer (opus, general-purpose agent)

## 2026-11-19 - nikhil - N-03
- Branch: nikhil/N-03-mock-erp (stacked on nikhil/N-02-seed-data)
- Did: FastAPI mock ERP: CONTRACTS s5 reads (+ GET PO by id, shipments), PATCH with reschedule / reassign / split rules and required Idempotency-Key (replay, KEY_REUSED / IN_FLIGHT / RESET_DURING_REQUEST conflicts), chaos timeout (async hang then apply) and 500, reset, bounded request log with write_applied, optional API key, test endpoints switch, CONTRACTS error body. Dockerfile (non-root, healthcheck) built and smoke-tested. Reviewer pass: fixed hung-PATCH-after-reset bug, tenant required on PATCH, exact log filter, date rule, PO id collisions, catch-all error body.
- Tests: `cd mock-erp && python -m pytest tests -q` -> 46 passed (3 runs)
- Status now: review
- Next: compose snippet + U-10 notes to Uday; S-01 proposals; PRs N-01 -> N-02 -> N-03
- Blockers: docker-compose.yml (Uday); S-01 ratification
- Models used: advisor opus; reviewer (opus, general-purpose agent)

## 2026-11-19 - nikhil - N-01
- Branch: nikhil/N-01-agent-schemas
- Did: Agent I/O JSON Schemas (risk_analysis, recovery_plan, agent_input, common) in Draft 2020-12; schema_registry.py with validator_for() and bundled_schema() for the N-09 Claude fallback; fixture generator, 5 valid + 27 invalid fixtures each failing on one expected keyword; README with evidence-addressing rule for N-07. Reviewer pass: fixed nullable-estimate rule, evidence addressing, per-option-type change fields, bundling, uuid correlation ids.
- Tests: `python -m pytest ai/tests -q` -> 82 passed
- Status now: review
- Next: raise the "Proposed for S-01" list at the contracts call; PR to collaboration; start N-02
- Blockers: S-01 ratification of schema additions
- Models used: advisor opus; reviewer (opus, general-purpose agent - project subagents not loaded this session)
## 2026-11-19 - roop - SETUP (merge policy)
- Branch: roop/S-06-merge-policy (separate worktree; another session is working on R-01 in the main checkout)
- Did: Team policy: every member is a repo admin, Claude verifies and merges PRs into `collaboration` itself (rebase, conflict rules, tests green, reviewer APPROVE). `/done` now merges and continues with the next ready task automatically; new `/merge-prs` merges every ready PR/branch for the team. Conflict rules written into CLAUDE.md and GIT_WORKFLOW.md.
- Tests: n/a (docs only)
- Status now: policy live on `collaboration`
- Next: run `/merge-prs` when N-01 / R-01 are ready
- Blockers: none
- Models used: advisor opus

## 2026-11-19 - roop - MERGE N-01 (/merge-prs policy)
- Branch: nikhil/N-01-agent-schemas -> collaboration (merge commit; branch kept because N-01 stays `review` until S-01)
- Did: Resolved append-only conflicts (SESSION_LOG kept both, PROGRESS regenerated); reviewer verdict APPROVE; follow-ups written into N-01 Notes and S-01 Notes (PO expected_date vs due_date).
- Tests: ai/tests 82 passed
- Status now: N-01 review (merged), R-01 in-progress (not merged, active session)
- Next: Nikhil fixes reviewer follow-ups before S-01 call
- Blockers: none
- Models used: advisor opus; reviewer opus

## 2026-11-19 - roop - SETUP (auto-merge + auto-pull)
- Branch: roop/S-02-automerge (separate worktree)
- Did: Added `scripts/gitflow.py` (automerge / sync-branches / local-sync) and `scripts/run_checks.py`; GitHub Actions `auto-merge.yml` (merge `review`/`done` task branches into collaboration when clean + green, delete `done` branches, then sync) and `branch-sync.yml` (merge collaboration into every open task branch); SessionStart hook auto-pulls locally. Team switched from rebase to merge (no force pushes). Docs + commands updated.
- Tests: simulated GitHub with a bare repo: in-progress branch skipped, review branch merged with SESSION_LOG conflict auto-resolved, re-run skipped as already merged, sync pushed collaboration into 2 branches, local-sync pulled bot merge, dirty tree skipped, real conflict reported (rc=2); ai checks 82 passed.
- Status now: automation live on collaboration
- Next: R-01 merges automatically once its /done marks it `review`/`done` and pushes
- Blockers: none
- Models used: advisor opus

## 2026-11-19 12:30 - roop - R-01
- Branch: roop/R-01-frontend-scaffold @ cfaec62
- Did: Built `frontend/` (Vite 8 + React 18 + TS 5.9 + Tailwind + Router + TanStack Query + Recharts): app shell with 9 screens, light/dark tokens, login + JWT refresh (single-flight), typed API from CONTRACTS + ai/schemas, MSW mocks (mock users in README), UI kit. Nested branches --scaffold/--auth/--review-fixes/--schema-fixes (subagents) and --api/--ui (visible sessions). auto-merge.yml now runs Node 22.22.2.
- Tests: frontend 10 files / 76 passed; build OK; run_checks ai/data/frontend PASS (backend not present)
- Status now: done
- Next: R-09 once U-02 lands; R-02 after U-03/U-04
- Blockers: Uday U-02/U-03/U-04; backend asks in ROOP.md Handoff (refresh lifetime/rotation, /me shape)
- Models used: advisor opus; implementer sonnet x5, reviewer opus x2, 2 visible sonnet sessions

## 2026-11-19 - uday - U-02
- Branch: uday/U-02-backend-scaffold
- Did: Backend scaffold (Django 5 + DRF + JWT + OpenAPI, 8 app shells, /api/health, compose with Postgres 16). Review follow-ups: contract error body handler (CONTRACTS s1), custom `accounts.User` + 0001 migration, SECRET_KEY guard, compose env_file + dev runserver, non-root image.
- Tests: backend 20 passed (also with a .env copied from .env.example); ruff clean; docker compose on Postgres 16 verified.
- Status now: done
- Next: U-01 (manual Boomi checks), S-01 (freeze contracts) which unblocks U-03
- Blockers: **Everyone who ran the first U-02 scaffold must run `docker compose down -v` once** (AUTH_USER_MODEL changed to accounts.User; old Postgres volume gives InconsistentMigrationHistory). Roop: API errors now use the CONTRACTS s1 body. Open for S-01: no 500/429 code in CONTRACTS (429/405 map to VALIDATION_ERROR for now).
- Models used: advisor sonnet; reviewer opus x2

## 2026-11-19 - roop - SETUP (autopilot)
- Branch: roop/S-02-autopilot (separate worktree)
- Did: `progress.py <member> --ready` (exit 0 + READY line, or exit 3 + what the next tasks wait on); new `/autopilot <member>` command for `/loop` hands-free mode (pull, check, work ready task, mark human-only steps `blocked: needs <member>`, wait 5-30 min); dependencies in `review` now count as satisfied (already merged). Docs updated.
- Tests: --ready checked for all members: roop READY R-09, nikhil WAITING on U-03/U-01, uday READY U-01
- Status now: autopilot available
- Next: each member starts `/loop /autopilot <name>`
- Blockers: none
- Models used: advisor opus

## 2026-11-19 - nikhil - N-01 (autopilot, run by Roop's machine)
- Branch: nikhil/N-01-agent-schemas
- Did: Autopilot round: nothing ready (N-04 waits on U-03, N-05 on U-01). Used the wait to fix N-01 reviewer follow-ups in `ai/`: optional PO header `expected_date` in the agent_input snapshot (proposed for CONTRACTS s5), complete S-01 tightening list and tenant rule in `ai/README.md`, valid fixture with every option_type + invalid SPLIT_ORDER/INVENTORY_TRANSFER/REVISED_SCHEDULE/PO-date cases, fixture inventory aligned to `data/seed/`, `bundled_schema` docstring, option-type coverage test.
- Tests: run_checks.py exit 0 - ai 93, data 33, mock-erp 46, backend 3, frontend 76 passed
- Status now: review (unchanged)
- Next: S-01 call ratifies the proposals; N-04 after U-03, N-05 after U-01
- Blockers: Uday U-01/U-03; S-01
- Models used: advisor opus; reviewer opus x1

## 2026-11-19 - roop - SETUP (autopilot removed)
- Branch: roop/S-02-remove-autopilot (separate worktree)
- Did: Removed the `/autopilot` command and its docs (CLAUDE.md, AI_WORKFLOW, README) - team decided it costs too many tokens while tasks wait on human steps; Nikhil/Uday autopilot sessions stopped. Kept `progress.py <member> --ready` as a plain status check and "review deps count as satisfied".
- Tests: progress.py --ready runs (roop READY R-09)
- Status now: work is started manually with `/resume <member>`
- Next: S-01 contracts call; U-01 Boomi check by a human
- Blockers: none
- Models used: advisor opus

## 2026-11-19 - roop - SETUP (model routing enforced)
- Branch: roop/S-02-model-routing (separate worktree)
- Did: Advisor = Opus at medium effort (project default `model: opus`, `effortLevel: medium`); PreToolUse hook `scripts/hooks/advisor_guard.py` blocks the main session from editing code so it must delegate; `implementer`/`boomi-builder` run with `isolation: worktree` in nested branches `<task-branch>--<sub>` cut from HEAD (`worktree.baseRef: head` - before this, subagent worktrees were cut from the default branch); new `reviewer-lite` (Sonnet) is the default review, Opus `reviewer` only for sensitive areas; commands get explicit effort (handoff/status haiku low, sync sonnet low, resume/done/merge-prs opus medium); mandatory plan table.
- Tests: guard hook: main-session code edit blocked (exit 2), subagent allowed, task/docs allowed, Windows paths handled; settings.json valid
- Status now: live for new sessions on collaboration
- Next: members restart Claude Code to pick up the settings
- Blockers: none
- Models used: advisor opus; claude-code-guide (docs check)
