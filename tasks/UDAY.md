# Tasks - Uday (Backend & Integration · Django core, Boomi Integration, Event Streams, API Management, deployment)

**Background:** Data engineer (Python, AI/ML). Claude explains Django, Boomi and frontend concepts in data-pipeline terms (ETL steps, schemas, jobs, connectors).
**Stream:** Boomi environment, Django project + core models, auth / tenant isolation / RBAC, audit, event intake, Boomi ERP read processes, Event Streams, API Management, approved-action execution, error handling + retry, deployment.
**Owns:** `backend/` project + `apps/{tenants,accounts,core,audit,integrations}`, `boomi/processes/` (except `correlation/`), `boomi/api/`, `deploy/`, `docker-compose.yml`, `docs/BOOMI_ENV.md`, `docs/INSTALL.md`

> Status values: `todo` · `in-progress` · `blocked` · `review` · `done`. Only change the `Status`, `Done in`, checkboxes and Notes lines - keep the format, `scripts/progress.py` parses it.
> Model hint = what the advisor (Opus 5.5) should delegate the bulk of the work to. See `docs/AI_WORKFLOW.md`.

## Handoff
<!-- Overwrite this block at the end of EVERY session (run /handoff). Newest state only; history goes to progress/SESSION_LOG.md -->
- **Last updated:** 2026-11-19 (U-02 done and merged, review follow-ups applied)
- **Current task:** -
- **Branch:** `uday/U-02-backend-scaffold`
- **Where I left off:** U-02 merged into collaboration; follow-up fixes (error body, accounts.User, compose/Docker hardening) pushed on the same branch. Everyone who ran the old scaffold must `docker compose down -v` once.
- **Next step:** U-01 (manual Boomi checks) and S-01 (freeze contracts with Roop + Nikhil), which unblocks U-03.
- **Blockers:** U-03 waits on S-01; U-01 needs the Boomi UI.

---

### U-01: Boomi environment and service verification
- **Status:** review
- **Sprint:** S0 (19-20 Nov) | **Est:** 1d | **Depends:** -
- **Branch:** `uday/U-01-boomi-env`
- **Model hint:** haiku / low (checklist + docs); the Boomi UI clicks are manual
- **Deliverables:** `docs/BOOMI_ENV.md` - account ID, environments, runtime (cloud atom / local atom), which of Integration, Event Streams, Agentstudio, Flow, API Management, Data Hub are enabled, team member access, chosen fallbacks (CONTRACTS section 8).
- **Acceptance:**
  - [x] All 6 services checked and recorded as enabled / not enabled
  - [x] Roop and Nikhil have Boomi platform access
  - [x] Fallback decision recorded for every missing service
- **Done in:** -
- **Notes:** `docs/BOOMI_ENV.md` filled in read-only on 2026-11-19 (nothing created or changed in Boomi). Services: Integration, Event Streams, Agentstudio, API Management, Data Hub are access-verified only (not build-verified); Flow is not available in Uday's account. Decisions: (1) Roop and Nikhil use their own Boomi training accounts (per Uday), so no invites; (2) no native Flow app - the React/Vite UI is exported as HTML and hosted in Flow (Roop's account), so no Flow fallback build is needed (decided 2026-11-23). Follow-ups that do not gate U-07: confirm this account's expiry is after 31 Dec 2026 (not shown in the UI); choose reuse BIMBA-OPCUA-Test vs new SG-Dev + second runtime; Roop and Nikhil fill BOOMI_ENV.md section 9; separate accounts mean processes are not shared automatically - decide the hand-off (section 9) before U-07/N-08; R-06 in tasks/ROOP.md still says native Flow app and needs re-scoping by Roop. Checklist template drafted by Roop's Claude session; facts filled in by Claude (read-only, signed in as Uday).

### U-02: Backend scaffold
- **Status:** done
- **Sprint:** S0 (19-21 Nov) | **Est:** 1d | **Depends:** -
- **Branch:** `uday/U-02-backend-scaffold`
- **Model hint:** sonnet / medium
- **Deliverables:** `backend/` Django 5 + DRF + drf-spectacular + simplejwt + psycopg; settings from env (`django-environ`); app shells `backend/apps/{tenants,accounts,core,audit,integrations,intelligence,workflow,portal}` so Roop and Nikhil can start without touching settings; `/api/health`; pytest + pytest-django; ruff; Dockerfile; Postgres 16 in root `docker-compose.yml`; `.env.example`.
- **Acceptance:**
  - [x] `docker compose up backend db` serves `/api/health` and `/api/schema/`
  - [x] `pytest` green with one smoke test
  - [x] All app shells registered in settings
- **Done in:** 8dcf1b8
- **Notes:** Verified with Docker (Postgres 16): `/api/health` -> `{"status":"ok","db":"ok"}`, `/api/schema/` -> 200; pytest (17 tests) + ruff green. Review follow-ups done: compose passes `.env` through (`env_file`, optional); no compose default for `SECRET_KEY` and an empty value counts as unset (refuses to start with DEBUG=False); compose uses runserver (dev) and the image runs as non-root; `apps.core.exceptions.contract_exception_handler` returns the CONTRACTS section 1 error body (reads `request.correlation_id` once U-05 middleware sets it; Django's own HTML 404 for unknown URLs is not covered); custom `accounts.User` + migration 0001 exist now. If host port 8000 is taken, run `BACKEND_PORT=8001 docker compose up --build backend db`. Tests default to in-memory SQLite (`D:DATABASE_URL` in pyproject); set `DATABASE_URL` to Postgres in CI. AUTH_USER_MODEL is `accounts.User` (U-03 adds tenant FK + role as migration 0002). Anyone who ran the earlier scaffold must run `docker compose down -v` once to recreate the Postgres volume. Health endpoint is `/api/health` (no trailing slash, no tenant data).

### U-03: Core data model + seed loader
- **Status:** todo
- **Sprint:** S0 (21-23 Nov) | **Est:** 2d | **Depends:** U-02, S-01
- **Branch:** `uday/U-03-core-models`
- **Model hint:** opus / high for model design review, sonnet / medium implement
- **Deliverables:** models for all 12 LLD entities (Tenant, User, Supplier, PurchaseOrder (+lines), Inventory, DisruptionEvent, RiskCase, AffectedPO, Recommendation, Approval, Execution, AuditEvent) with `tenant` FK on every tenant-owned table, RiskCase states from CONTRACTS section 7, indexes on (tenant, status); `manage.py load_seed` reading `data/seed/` (Nikhil's N-02).
- **Acceptance:**
  - [ ] Migrations apply on empty Postgres
  - [ ] `load_seed` loads both tenants and is idempotent
  - [ ] Admin registered for all models
  - [ ] Field names match CONTRACTS / LLD 16.1
- **Done in:** -
- **Notes:** Highest-priority task in the team - unblocks R-02, N-04, N-07, N-10, U-06.

### U-04: Auth, tenant isolation, RBAC, users + settings APIs
- **Status:** todo
- **Sprint:** S1 (24-27 Nov) | **Est:** 2d | **Depends:** U-03
- **Branch:** `uday/U-04-auth-tenancy`
- **Model hint:** opus / high (security-critical)
- **Deliverables:** JWT with `tenant_id` + `role` claims (`/auth/token`, `/auth/refresh`, `/me`); `TenantScopedViewSet` mixin / manager filtering every queryset by `request.tenant` (used by everyone); role permission classes per CONTRACTS section 2; API-key auth class for `SYSTEM` callers; `GET/PATCH /settings`, `GET/POST /users`; denied cross-tenant attempts logged.
- **Acceptance:**
  - [ ] TC-10: user of TEN-ACME requesting a TEN-GLOBEX case -> 404/403 and audit row
  - [ ] tenant_id in body/query is ignored (test)
  - [ ] Permission matrix test for all 5 roles
- **Done in:** -
- **Notes:** -

### U-05: Audit service + correlation-id middleware
- **Status:** todo
- **Sprint:** S1 (24-26 Nov) | **Est:** 1d | **Depends:** U-03
- **Branch:** `uday/U-05-audit`
- **Model hint:** sonnet / medium
- **Deliverables:** `apps.audit.record(actor, action, entity, correlation_id, data)` used by all apps; middleware that reads/creates `X-Correlation-ID` and adds it to responses + logs; `GET /audit` with filters.
- **Acceptance:**
  - [ ] Correlation id visible in response headers and in audit rows
  - [ ] Audit rows are append-only (no update/delete API)
- **Done in:** -
- **Notes:** -

### U-06: Event intake + Boomi client (Django `apps/integrations`)
- **Status:** todo
- **Sprint:** S1 (26-30 Nov) | **Est:** 2d | **Depends:** U-04, U-05
- **Branch:** `uday/U-06-event-intake`
- **Model hint:** sonnet / medium; opus / high review for idempotency + tenant check
- **Deliverables:** `POST /events/disruptions`, `POST /demo/simulate-disruption`, `POST /internal/risk-cases` endpoint shell (calls Nikhil's impact engine N-04), `POST /internal/executions/{id}/result`, duplicate detection on `event_id`, `BoomiClient` (httpx, timeout, bounded retry with backoff on transient errors only), `GET /integrations/status`.
- **Acceptance:**
  - [ ] TC-01: valid event -> 202 + correlation_id, DisruptionEvent stored, Boomi called
  - [ ] TC-02: unknown supplier -> flagged for data correction, no RiskCase
  - [ ] TC-09: duplicate event_id -> same correlation_id, no second case
  - [ ] Tenant mismatch between API key and payload -> 403
  - [ ] pytest coverage for all of the above
- **Done in:** -
- **Notes:** -

### U-07: Boomi ERP read processes + canonical profiles
- **Status:** todo
- **Sprint:** S1 (25-30 Nov) | **Est:** 3d | **Depends:** U-01, N-03
- **Branch:** `uday/U-07-boomi-erp-read`
- **Model hint:** sonnet / medium via `boomi-builder` (use the boomi-integration skill if installed); opus / high for mapping review
- **Deliverables:** Boomi processes `SG_Get_Supplier`, `SG_Get_Open_POs_By_Supplier`, `SG_Get_Inventory`, `SG_Get_Alternate_Suppliers`; JSON profiles matching CONTRACTS; HTTP Client connection to the mock ERP (URL from environment extension). Specs/exports in `boomi/processes/`, test runs in `boomi/TEST_LOG.md`.
- **Acceptance:**
  - [ ] Each process test-run in Boomi returns canonical JSON
  - [ ] Connection URL and credentials are environment extensions
  - [ ] Specs/exports committed under `boomi/processes/`
- **Done in:** -
- **Notes:** Needs the mock ERP reachable from Boomi - tunnel from U-12.

### U-08: Event Streams topics + disruption subscriber
- **Status:** todo
- **Sprint:** S2 (1-3 Dec) | **Est:** 2d | **Depends:** U-01, U-07
- **Branch:** `uday/U-08-event-streams`
- **Model hint:** sonnet / medium via `boomi-builder`
- **Deliverables:** topics `supplier.disruption`, `shipment.delay`, `recovery.status`; publisher process; subscriber that starts Nikhil's correlation process (N-08). If Event Streams unavailable, fallback from CONTRACTS section 8.
- **Acceptance:**
  - [ ] Publishing a canonical event triggers the subscriber within 2 s
  - [ ] `correlation_id` preserved end to end
- **Done in:** -
- **Notes:** -

### U-09: Boomi API Management
- **Status:** todo
- **Sprint:** S2 (3-7 Dec) | **Est:** 1.5d | **Depends:** U-07, U-08
- **Branch:** `uday/U-09-api-management`
- **Model hint:** haiku / low (docs), sonnet / medium for policies
- **Deliverables:** API proxy in front of the Boomi listener processes; API key policy; rate limit; Django `BoomiClient` uses gateway URL + key from env; `boomi/api/` holds the API definition (OpenAPI).
- **Acceptance:**
  - [ ] Calls without key are rejected by the gateway
  - [ ] Django -> gateway -> Boomi process works end to end
- **Done in:** -
- **Notes:** -

### U-10: Approved-action execution process
- **Status:** todo
- **Sprint:** S3 (9-14 Dec) | **Est:** 2.5d | **Depends:** U-07, N-10
- **Branch:** `uday/U-10-execution`
- **Model hint:** sonnet / medium; opus / high review (no write without approval)
- **Deliverables:** process `SG_Execute_Approved_Action`: verifies approval reference, PATCHes mock ERP with `Idempotency-Key = execution_id`, re-reads PO to verify, `POST /internal/executions/{id}/result`, publishes `recovery.status`.
- **Acceptance:**
  - [ ] TC-07: approved option updates the PO; case becomes RECOVERED only after verification
  - [ ] TC-06: rejected option never reaches the ERP (asserted via mock ERP request log)
- **Done in:** -
- **Notes:** -

### U-11: Error handling, retry, dead-letter
- **Status:** todo
- **Sprint:** S3-S4 (12-17 Dec) | **Est:** 2d | **Depends:** U-06, U-10
- **Branch:** `uday/U-11-error-handling`
- **Model hint:** sonnet / medium; opus / high review
- **Deliverables:** error classification (validation / auth / business / transient / downstream) in Boomi + Django; bounded retry for transient only; failed-message store (sanitized); `POST /executions/{id}/retry` with eligibility check.
- **Acceptance:**
  - [ ] TC-08: ERP timeout -> EXECUTION_FAILED, retry succeeds once, no duplicate PO update
  - [ ] Validation/auth errors are never auto-retried
  - [ ] No secrets in logs (grep check in test)
- **Done in:** -
- **Notes:** -

### U-12: Local + hosted deployment
- **Status:** todo
- **Sprint:** S1 tunnel (by 28 Nov), S4 hosted (15-20 Dec) | **Est:** 2d | **Depends:** U-02, N-03
- **Branch:** `uday/U-12-deploy`
- **Model hint:** sonnet / medium
- **Deliverables:** root `docker-compose.yml` (postgres, backend, frontend, mock-erp); tunnel instructions so the Boomi cloud atom reaches the local backend + mock ERP in S1; hosted backend / frontend / mock-erp + managed Postgres in S4; `deploy/` configs; secrets only in host env vars; `docs/INSTALL.md`.
- **Acceptance:**
  - [ ] `docker compose up` gives a working stack on a fresh clone
  - [ ] Hosted URLs reachable by Boomi over HTTPS
  - [ ] `.env.example` complete, no real secrets committed
- **Done in:** -
- **Notes:** -
