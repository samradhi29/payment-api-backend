# LedgerCore validation and deployment notes

## Safety first

Credentials were exposed in a terminal transcript and an uploaded frontend archive. Rotate all exposed gateway secrets, merchant API keys, PayU salt, and webhook encryption key before running the validation script. Update local `.env` and `frontend/.env.local`; never upload or commit either file.

## Validation

Run `scripts/validate-ledgercore.ps1` from the project root in PowerShell. It:

- validates Compose configuration;
- starts the isolated `test-db` on host port 5433;
- migrates only that test database;
- runs main API, aggregator, and metrics tests;
- runs frontend lint and production build;
- builds all Docker images.

It does **not** migrate the main database or start the full stack. This is deliberate: the main database may already contain data or may have been initialized outside Alembic. Do not run `docker compose down -v`.

## Main database migration

Before migrating the main database:

1. Take a verified `pg_dump` backup.
2. Inspect the current revision with `docker compose --profile tools run --rm migrate current`.
3. If Alembic reports no revision but tables already exist, do not run `upgrade head` blindly. Inspect the schema and determine whether the database should be stamped at the baseline before applying later migrations.
4. Only after confirming the current schema/revision, run `docker compose --profile tools run --rm migrate`.

The API container health check uses `/ready`, which verifies database connectivity. The migration service remains opt-in rather than running automatically at every `docker compose up`.

## Starting the stack

After the main database is safely migrated and credentials are updated:

```powershell
docker compose up -d --build
docker compose ps
Invoke-RestMethod http://localhost:8000/ready
Invoke-RestMethod http://localhost:8100/health
Invoke-RestMethod http://localhost:8200/health
```

The dashboard is available at `http://localhost:3000`.
