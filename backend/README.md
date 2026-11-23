# backend/ - Django REST API

Scaffolded by **U-02** (Uday). Ownership inside this folder:
| Path | Owner |
|---|---|
| project settings, `apps/tenants`, `apps/accounts`, `apps/core`, `apps/audit`, `apps/integrations` | Uday |
| `apps/intelligence`, `apps/workflow` | Nikhil |
| `apps/portal` (read APIs for the UI screens) | Roop |

Contracts: `docs/CONTRACTS.md` section 4. Run (after U-01): `docker compose up backend db`.

## Seed data
`python manage.py load_seed [--data-dir PATH] [--demo-users]` loads `data/seed/` (tenants, suppliers,
purchase orders + lines, inventory, shipments) in one transaction. It is idempotent: run it again and
it reports 0 created / 0 updated. In Docker the compose file mounts `./data` read-only at `/data`, so the default seed folder resolves; run `docker compose exec backend python manage.py load_seed`. Any validation failure aborts everything with a `CommandError`
naming the file, the record id and the rule.
- `--data-dir PATH` loads another seed folder (e.g. one made with `data/generate_seed.py --as-of ... --out ...`);
  records are updated in place. Default comes from `SEED_DATA_DIR` (settings), else `<repo>/data/seed`.
- `--demo-users` also creates `<tenant>.admin`, `<tenant>.procurement`, `<tenant>.viewer` per tenant
  (e.g. `acme.admin`). The password comes only from the env var `DEMO_USER_PASSWORD` (must pass Django
  password validation, is never printed, existing users keep their password). Without the flag no users are created.
