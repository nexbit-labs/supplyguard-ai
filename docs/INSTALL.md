# Install and run SupplyGuard AI locally

## Prerequisites
- Docker Desktop (Compose v2) and git.
- Optional, only to run tests outside Docker: Python 3.12, Node >= 22.22.2 (`frontend/.nvmrc`).

## Quick start
```
git clone <repo-url> supplyguard-ai
cd supplyguard-ai
docker compose up --build
```
`.env` is optional: compose has defaults for everything. Only run `cp .env.example .env` (PowerShell: `Copy-Item .env.example .env`) when you want to change values.

The first frontend start runs `npm ci` inside the container and can take several minutes (about 4 on a fresh clone). The backend migrates the database on start and answers `/api/health` after about a minute, so wait for `docker compose ps` to show everything Up before opening the URLs.

## URLs
| Service | URL |
|---|---|
| Frontend | http://localhost:5173 |
| Backend health | http://localhost:8000/api/health |
| API schema | http://localhost:8000/api/schema/ |
| Django admin | http://localhost:8000/admin |
| Mock ERP health | http://localhost:8081/health |
| PostgreSQL | localhost:5432 (user/db/password `supplyguard` by default) |

Ports are overridable in `.env` or inline: `FRONTEND_PORT`, `BACKEND_PORT`, `MOCK_ERP_PORT`, `DB_PORT`. `BACKEND_PORT` is also used for `VITE_API_BASE_URL` (`http://localhost:${BACKEND_PORT}/api/v1`). If you change `FRONTEND_PORT` you must also set `CORS_ALLOWED_ORIGINS=http://localhost:<FRONTEND_PORT>`.

**Note:** `VITE_API_MOCK` defaults to `true`: the UI runs on its built-in mock API until U-04 (auth) is merged, because the real API cannot log anyone in yet. Then set `VITE_API_MOCK=false` in `.env`. All published ports bind to `127.0.0.1` only.

## Running tests
```
python scripts/run_checks.py
```
Backend tests use SQLite by default; set `DATABASE_URL=postgres://...` to run them against Postgres. Mock ERP uses pytest, frontend uses Vitest (`npm test` in `frontend/`).

## Troubleshooting
- Port already in use (8000 and 5432 are often taken by other projects): override the ports, e.g. `BACKEND_PORT=8001 DB_PORT=5433 docker compose up` (or set them in `.env`). If you change `FRONTEND_PORT`, also set `CORS_ALLOWED_ORIGINS=http://localhost:<FRONTEND_PORT>`.
- Reset the database: `docker compose down -v` (deletes the `pgdata` volume and the frontend `node_modules` volume).
- Rebuild after dependency changes: `docker compose up --build`.

## Tunnels and hosting
See `deploy/README.md` (Boomi tunnel, Render blueprint).
