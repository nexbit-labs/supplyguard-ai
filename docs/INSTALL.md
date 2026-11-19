# Install and run SupplyGuard AI locally

## Prerequisites
- Docker Desktop (Compose v2) and git.
- Optional, only to run tests outside Docker: Python 3.12, Node >= 22.22.2 (`frontend/.nvmrc`).

## Quick start
```
git clone <repo-url> supplyguard-ai
cd supplyguard-ai
cp .env.example .env        # Windows PowerShell: Copy-Item .env.example .env
docker compose up --build
```
First start takes a few minutes (frontend runs `npm ci`). The backend migrates the database on start.

## URLs
| Service | URL |
|---|---|
| Frontend | http://localhost:5173 |
| Backend health | http://localhost:8000/api/health |
| API schema | http://localhost:8000/api/schema/ |
| Django admin | http://localhost:8000/admin |
| Mock ERP health | http://localhost:8081/health |
| PostgreSQL | localhost:5432 (user/db/password `supplyguard` by default) |

Ports are overridable in `.env`: `FRONTEND_PORT`, `BACKEND_PORT`, `MOCK_ERP_PORT`, `DB_PORT`. The frontend calls the backend directly at `http://localhost:${BACKEND_PORT}/api/v1`; set `VITE_API_MOCK=true` in `.env` to use the in-browser MSW mocks instead.

## Running tests
```
python scripts/run_checks.py
```
Backend tests use SQLite by default; set `DATABASE_URL=postgres://...` to run them against Postgres. Mock ERP uses pytest, frontend uses Vitest (`npm test` in `frontend/`).

## Troubleshooting
- Port already in use: change the matching `*_PORT` in `.env`, then `docker compose up` again (also update `CORS_ALLOWED_ORIGINS` if you change `FRONTEND_PORT`).
- Reset the database: `docker compose down -v` (deletes the `pgdata` volume and the frontend `node_modules` volume).
- Rebuild after dependency changes: `docker compose up --build`.

## Tunnels and hosting
See `deploy/README.md` (Boomi tunnel, Render blueprint).
