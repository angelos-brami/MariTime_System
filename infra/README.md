# Infrastructure notes

`docker-compose.yml` now builds the API, workers, standalone web application, migration job, and
Caddy TLS edge with health-gated startup. Copy `.env.example` to a secret-managed deployment
environment, replace every placeholder, set the two public hosts, then run:

```bash
docker compose build
docker compose up -d
docker compose ps
```

The migration job must exit successfully and the API/web health checks must be healthy before
Caddy accepts traffic. Production must additionally enforce:

- a network-level egress allowlist for the ingestion worker;
- separate least-privilege database roles for ingestion, publication, and read-only services;
- managed secrets and short-lived credentials;
- encrypted daily backups and a weekly restore drill;
- private database and Redis networking;
- an outbound kill switch tested monthly.

Application-layer URL and source-rights checks are defense in depth, not a replacement for the worker's network boundary.

The repository now exposes separate ingestion, analysis, publication, delivery, and scheduler worker commands so production can give every process its own database login. Run `infra/postgres/least_privilege.sql` as the migration owner, create separate `LOGIN` roles in the deployment secret manager, grant each login exactly one matching group role, and pass its DSN as that container's `EASTMED_DATABASE_URL`. The legacy `eastmed-worker` command is local-development compatibility only and must not run in production.

`scripts/backup_database.py` creates atomic age-encrypted custom-format dumps without placing the database password in process arguments. The `age` private identity stays offline. Use the R7/R9 restore procedure weekly; neither the backup command nor an object-store upload proves restorability.
