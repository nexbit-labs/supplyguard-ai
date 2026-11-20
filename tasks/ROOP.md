# Tasks - Roop (Frontend · React SaaS UI, approval screens, demo & Marketplace)

**Background:** React / frontend developer. Claude explains backend, Boomi and data concepts in frontend terms (components, props, state, API calls, forms).
**Stream:** All React screens, the read APIs those screens use, the Boomi Flow approval screens, CI, demo story, Marketplace package.
**Owns:** `frontend/`, `backend/apps/portal/` (UI read APIs), `boomi/flow/`, `boomi/marketplace/`, `.github/workflows/ci.yml`, `docs/DEMO_SCRIPT.md`

> Status values: `todo` · `in-progress` · `blocked` · `review` · `done`. Only change the `Status`, `Done in`, checkboxes and Notes lines - keep the format, `scripts/progress.py` parses it.
> Model hint = what the advisor (Opus 5.5) should delegate the bulk of the work to. See `docs/AI_WORKFLOW.md`.

## Handoff
<!-- Overwrite this block at the end of EVERY session (run /handoff). Newest state only; history goes to progress/SESSION_LOG.md -->
- **Last updated:** 2026-11-19 (R-03 screens built on mocks)
- **Current task:** R-03 Executive Overview + Supplier Intelligence - `in-progress` (waits on R-02 for real data); R-09 in `review`
- **Branch:** `roop/R-03-overview-suppliers` (nested `--overview`, `--supplier-list`, `--supplier-detail` merged, kept on GitHub)
- **Where I left off:** All 3 screens done against MSW mocks; review nits fixed in `--polish` and merged; 101 tests, lint, typecheck, build green.
- **Next step:** When Uday's U-03/U-04 land start R-02, then switch R-03 to real API and mark it review.
- **Blockers:** R-02 waits on Uday U-03/U-04. R-09 needs team decision: `ci-ok` required on `main` only. Uday: pin ruff, `ruff format backend`, short refresh lifetime + rotation off, `/me` shape provisional. Team: drop `[skip ci]` from bot commits.

---

### R-01: Frontend scaffold + design system
- **Status:** done
- **Sprint:** S0-S1 (19-25 Nov) | **Est:** 2d | **Depends:** -
- **Branch:** `roop/R-01-frontend-scaffold`
- **Model hint:** sonnet / medium
- **Deliverables:** `frontend/` Vite + React 18 + TypeScript + Tailwind + React Router + TanStack Query + Recharts; app shell (sidebar with the 9 screens, tenant/user badge, light/dark tokens); login page + token refresh; API client typed from `docs/CONTRACTS.md` section 4 (switch to types generated from `/api/schema/` with openapi-typescript once U-02 is live); MSW mock handlers so screens work before the backend exists (`VITE_API_MOCK=true`); Vitest + Testing Library.
- **Acceptance:**
  - [x] `npm run build` and `npm test` green (10 files / 76 tests; `python scripts/run_checks.py` frontend PASS)
  - [x] Login + protected routes work against MSW mocks (`frontend/src/auth/__tests__/auth.test.tsx`)
  - [x] Setting `VITE_API_MOCK=false` points the same code at the real backend (`src/main.tsx` skips MSW worker; `src/api/config.test.ts`; `loginNoMock.test.tsx`; Vite proxy `/api` -> :8000)
- **Done in:** cfaec62
- **Notes:** Built via nested branches `--scaffold` (subagent), `--api` + `--ui` (visible sessions), `--auth`, `--review-fixes`, `--schema-fixes` (subagents). Opus review x2: CHANGES REQUIRED -> all MAJORs fixed -> APPROVE WITH NITS. Stack: React 18.3, Vite 8, Vitest 5, TS 5.9 (TS 7 avoided: no JS API for openapi-typescript), Node >=22.22.2 (`.nvmrc`); auto-merge.yml now on Node 22.22.2. Types hand-written from CONTRACTS + `ai/schemas` (ajv test validates mock analysis/plan); swap to openapi-typescript after U-02. Deferred: cross-tab refresh-token rotation + short refresh lifetime (Uday, backend), refresh token in localStorage (hackathon trade-off, README), `/settings` ADMIN gating (R-08). Mock users in `frontend/README.md`.

### R-02: Read APIs for the UI (Django `apps/portal`)
- **Status:** todo
- **Sprint:** S1 (26-30 Nov) | **Est:** 2d | **Depends:** U-03, U-04
- **Branch:** `roop/R-02-read-apis`
- **Model hint:** sonnet / medium (Claude explains DRF as "the backend side of the screens you build")
- **Deliverables:** `backend/apps/portal/`: `GET /suppliers`, `GET /suppliers/{id}`, `GET /dashboard/summary`, `GET /risk-cases`, `GET /risk-cases/{id}` per CONTRACTS section 4; filters + pagination; built on Uday's tenant-scoped base classes (U-04); OpenAPI tags; pytest.
- **Acceptance:**
  - [ ] Every endpoint tenant-scoped (uses U-04 mixin) and tested
  - [ ] `/risk-cases/{id}` returns event, affected POs, analysis, recommendations, approvals, executions, timeline in one call
  - [ ] MSW mocks in `frontend/` updated to match the real responses
- **Done in:** -
- **Notes:** -

### R-03: Executive Overview + Supplier Intelligence screens
- **Status:** review
- **Sprint:** S2 (1-5 Dec) | **Est:** 2d | **Depends:** R-01, R-02
- **Branch:** `roop/R-03-overview-suppliers`
- **Model hint:** sonnet / medium
- **Deliverables:** KPI tiles (suppliers, open POs, active shipments, exposure, open cases, recovered), trend charts; supplier list with filters + risk badges; supplier detail with KPIs and disruption history.
- **Acceptance:**
  - [ ] Real seed data shown; loading / empty / error states handled
- **Done in:** -
- **Notes:** Started early on MSW mocks (R-02 waits on U-03/U-04). Built in 3 visible parallel sessions: `--overview` (6 KPI tiles + Recharts exposure/cases trends), `--supplier-list` (URL filters search/status/risk, risk badges, pagination), `--supplier-detail` (`/suppliers/:id`, KPIs, disruption history, 404 view), all merged; lint/typecheck/96 tests/build green; reviewer-lite APPROVE WITH NITS. Polish session (`--polish`) fixed all nits: shared `src/features/suppliers/status.ts` + `src/lib/format.ts`, sr-only chart summaries, fake-timer debounce test, out-of-range `?page` clamp, severity-tinted risk meter; 101 tests green. Left: after R-02 swap to real API + check field names, tick "real seed data". (Nits were: one supplier-status meta shared by list+detail, shared date/INR formatters in `src/lib/format.ts`, sr-only text summary for charts, fake timers in list debounce test, clamp out-of-range `?page`.) Note for R-04: case links use `/risk?case=<RC-id>`. Landed in collaboration on mocks (status review); real-data acceptance items stay unticked until backend APIs exist.

### R-04: Risk Command Center + case detail
- **Status:** review
- **Sprint:** S2-S3 (4-10 Dec) | **Est:** 2.5d | **Depends:** R-02, U-06
- **Branch:** `roop/R-04-risk-center`
- **Model hint:** sonnet / medium
- **Deliverables:** live case inbox (poll every 3 s), severity filters, "Simulate Supplier Disruption" button (calls `/demo/simulate-disruption`), case detail: event, affected POs table, AI analysis with facts / estimates / assumptions / missing data visually distinct, evidence links, status timeline.
- **Acceptance:**
  - [ ] New case appears within 5 s of the simulate click
  - [ ] Estimates and missing data visibly labelled (AC-03)
- **Done in:** -
- **Notes:** Started early on MSW mocks (R-02, U-06 not done); swap to real API later. Plan: inbox (`/risk` list, severity/status URL filters, 3 s poll, ADMIN simulate button, `?case=` redirect; owns `src/api/queries.ts` poll option + stateful simulate mock) | nested session | sonnet/medium | `roop/R-04-risk-center--inbox`; case detail (`/risk/:id`: header, event, affected POs, approvals/executions, status timeline) | nested session | sonnet/medium | `roop/R-04-risk-center--detail`; AI analysis panel (facts/estimates/assumptions/missing data distinct, evidence list, AC-03) | nested session | sonnet/medium | `roop/R-04-risk-center--analysis`; wire panel into detail after merge | message `--detail` session | sonnet/medium | `--detail`; tests | test-runner | haiku/low | -; review | reviewer-lite | sonnet/medium | -. Landed in collaboration on mocks (status review); real-data acceptance items stay unticked until backend APIs exist.

### R-05: AI Recommendations + Approval Center
- **Status:** todo
- **Sprint:** S3 (8-12 Dec) | **Est:** 2d | **Depends:** R-04, N-10
- **Branch:** `roop/R-05-approvals`
- **Model hint:** sonnet / medium
- **Deliverables:** ranked option cards with cost / lead-time comparison, trade-offs, evidence; pending approvals queue; Approve / Reject / Request changes with comment (role-gated); execution status + retry button (U-11).
- **Acceptance:**
  - [ ] Buttons hidden/disabled for roles that cannot decide
  - [ ] UNSUPPORTED options shown but not approvable
- **Done in:** -
- **Notes:** -

### R-06: Boomi Flow approval workflow
- **Status:** todo
- **Sprint:** S3 (9-14 Dec) | **Est:** 2.5d | **Depends:** N-07, N-10, U-01
- **Branch:** `roop/R-06-flow-approval`
- **Model hint:** sonnet / medium via `boomi-builder` (Claude explains Flow as "a low-code screen + form builder with a state diagram")
- **Deliverables:** Flow app: approval page showing evidence + options, Approve / Reject / Request changes, comment, escalation after N hours; each decision calls `POST /recommendations/{id}/decision` (Nikhil's endpoint, N-10), which triggers execution (U-10) on approve. Direction and setup documented in `boomi/flow/README.md`. Fallback if Flow is not licensed: SaaS Approval Center (R-05) only.
- **Acceptance:**
  - [ ] Approve in Flow -> execution starts; Reject -> no execution
  - [ ] Decision visible in the SaaS Approval Center and audit within 5 s
- **Done in:** -
- **Notes:** -

### R-07: What-if Simulator UI
- **Status:** todo
- **Sprint:** S4 (15-17 Dec) | **Est:** 1.5d | **Depends:** N-11, R-01
- **Branch:** `roop/R-07-what-if-ui`
- **Model hint:** sonnet / medium
- **Deliverables:** supplier picker, duration slider, event type; results: affected POs, inventory gap chart, exposure, best option, assumptions panel.
- **Acceptance:**
  - [ ] "Supplier A out 7 days" renders in < 3 s
- **Done in:** -
- **Notes:** -

### R-08: Integration Center, Audit Center, Tenant Settings
- **Status:** todo
- **Sprint:** S4 (16-20 Dec) | **Est:** 2d | **Depends:** U-04, U-05, U-06
- **Branch:** `roop/R-08-admin-screens`
- **Model hint:** sonnet / medium
- **Deliverables:** connection health cards (`/integrations/status`); audit table with filters + correlation-id trace view; settings (risk thresholds, users, roles) for ADMIN.
- **Acceptance:**
  - [ ] Searching a correlation id shows event -> analysis -> approval -> execution in order (AC-07)
- **Done in:** -
- **Notes:** -

### R-09: CI pipeline
- **Status:** review
- **Sprint:** S1 (24-26 Nov) | **Est:** 0.5d | **Depends:** R-01, U-02
- **Branch:** `roop/R-09-ci`
- **Model hint:** haiku / low
- **Deliverables:** `.github/workflows/ci.yml`: backend ruff + pytest with Postgres service, frontend lint + typecheck + test + build, mock-erp tests; runs on PRs to `collaboration` and `main`.
- **Acceptance:**
  - [ ] CI green on `collaboration`; added as required check on both protected branches
- **Done in:** -
- **Notes:** Built on nested branches `--lint`, `--workflow`, `--gate` (subagents). `ci.yml` jobs: backend (ruff + pytest on Postgres 16), frontend (lint/typecheck/test/build, Node from `.nvmrc`), python-suites matrix (ai, data, mock-erp), `ci-ok` aggregate. Triggers: task-branch pushes, PRs + pushes to collaboration/main, nightly 02:00 UTC, manual. ESLint 9 flat config added (`npm run lint`, 0 warnings). Auto-merge gate (`scripts/run_checks.py`) now also runs frontend lint + backend ruff. Opus review x2: CHANGES REQUIRED -> APPROVE WITH NITS (nits fixed). Local `run_checks.py`: all 5 suites PASS. Why `review` not `done`: acceptance "required check on both protected branches" conflicts with bots pushing directly to collaboration (GITHUB_TOKEN cannot bypass) -> proposed SHARED change: require `ci-ok` on `main` only; prove collaboration green via nightly/manual run. Deferred: bot `[skip ci]` in `scripts/gitflow.py` + `progress.yml` blocks milestone PR checks (team); ruff unpinned in `backend/requirements-dev.txt` (Uday).

### R-10: UX polish + demo mode
- **Status:** todo
- **Sprint:** S5 (21-26 Dec) | **Est:** 1.5d | **Depends:** R-03, R-04, R-05, R-07, R-08
- **Branch:** `roop/R-10-polish`
- **Model hint:** sonnet / medium; opus / high for a final UI review
- **Deliverables:** consistent spacing/typography, responsive down to 1280 px, skeleton loaders, toasts, "illustrative synthetic data" label, demo reset button (ADMIN), screenshots for the Marketplace listing.
- **Acceptance:**
  - [ ] Full demo path clicked through with zero console errors
- **Done in:** -
- **Notes:** -

### R-11: Demo script, deck, video storyboard
- **Status:** todo
- **Sprint:** S6 (27-31 Dec) | **Est:** 2d | **Depends:** S-03
- **Branch:** `roop/R-11-demo-script`
- **Model hint:** opus / high for the story, haiku / low for formatting
- **Deliverables:** `docs/DEMO_SCRIPT.md` (7-8 min, 7 scenes from `docs/PRODUCT_STRATEGY.md`), slide deck outline, video shot list, Q&A prep (architecture, AI governance, Marketplace) - Nikhil and Uday supply the technical answers.
- **Acceptance:**
  - [ ] Two timed rehearsals under 8 min
  - [ ] Every Boomi service has a visible moment
- **Done in:** -
- **Notes:** -

### R-12: Marketplace accelerator package
- **Status:** todo
- **Sprint:** S5-S6 (24-30 Dec) | **Est:** 2d | **Depends:** U-10, R-06, N-09
- **Branch:** `roop/R-12-marketplace`
- **Model hint:** haiku / low for docs assembly, opus / high final review
- **Deliverables:** `boomi/marketplace/`: listing text, install guide, config templates, sample data, architecture diagram, screenshots; Boomi packaged components list (Uday and Nikhil package their own processes/agents, Roop assembles).
- **Acceptance:**
  - [ ] Package installs into a clean Boomi account following only the guide (dry run by Nikhil or Uday)
- **Done in:** -
- **Notes:** -
