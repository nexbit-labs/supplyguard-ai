# Deployment notes (U-12)

Local stack: see `docs/INSTALL.md`. This file covers exposing local services to Boomi (S1) and hosting (S4).

## S1 - Tunnel the local mock ERP / backend to the Boomi cloud atom

The Boomi cloud atom cannot reach `localhost`. Expose the local services over HTTPS.

1. Secure the mock ERP first. In `.env` set a long random key and restart:
   ```
   MOCK_ERP_API_KEY=<long random string>
   MOCK_ERP_ENABLE_TEST_ENDPOINTS=false   # true only if the demo needs reset/chaos endpoints
   ```
   `docker compose up -d mock-erp`. NEVER tunnel with an empty `MOCK_ERP_API_KEY`.
2. Start a tunnel (recommended: cloudflared quick tunnel, no account needed):
   ```
   cloudflared tunnel --url http://localhost:8081      # mock ERP
   cloudflared tunnel --url http://localhost:8000      # backend (separate terminal, if Boomi must call it)
   ```
   Alternative: `ngrok http 8081`. Each run prints a random `https://...` URL that changes on restart.
3. Check: `curl https://<url>/health`.
4. Record the URL as a Boomi **environment extension** (Manage > Environment Extensions > Connections / process properties), e.g. `MOCK_ERP_URL` and `MOCK_ERP_API_KEY` (password type). Do not commit URLs or keys to git; update the extension whenever the tunnel URL changes.
5. For the backend tunnel add its hostname to `ALLOWED_HOSTS` in `.env` and restart the backend.
6. Stop the tunnel when not demoing.

## S4 - Hosted plan (Render)

`deploy/render.yaml` is a Render Blueprint: managed Postgres 16, backend (docker, `./backend`), mock-erp (docker, `mock-erp/Dockerfile`, repo-root context), frontend static site.

**UNTESTED until a Render account exists.** Expect small fixes on first apply.

Steps:
1. Render dashboard > New > Blueprint > pick the repo, set blueprint path `deploy/render.yaml`.
2. Fill the `sync: false` secrets: `SECRET_KEY`, `MOCK_ERP_API_KEY`; set `ALLOWED_HOSTS`, `CORS_ALLOWED_ORIGINS` (frontend URL) and `VITE_API_BASE_URL` (backend URL + `/api/v1`) once the service URLs are known, then redeploy.
3. Migrations run via `preDeployCommand`.
4. Put the hosted mock-erp URL + key into the Boomi environment extensions.

Follow-ups / known gaps:
- With `DEBUG=False`, Django admin static files are not served by gunicorn. Add `whitenoise` (middleware + `STORAGES`, `collectstatic` in the build) in `backend/` - not done to avoid new dependencies. The API itself is unaffected.
- The backend Dockerfile may run dev-oriented commands; the blueprint overrides the start command with gunicorn without `--reload`.
- Render free Postgres expires; use a paid plan for anything beyond the demo.
