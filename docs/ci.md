# SIM CI verification

Project rule: tests run only in CI/CD, never locally or on dot PC. `.github/workflows/ci.yml` runs on pull requests to `main` and pushes to `main`. It has independent backend/PostgreSQL and frontend jobs, each bounded to 25 minutes with overlapping runs cancelled. GitHub-hosted runner minutes may be consumed; no new paid service, secret, runner or production access is configured by this change.

Backend uses Python 3.12, pinned `uv==0.13.5`, `uv sync --frozen`, full pytest and Ruff. The job provisions a temporary PostgreSQL 16.6 service with an ephemeral CI-only password and runs `backend/scripts/ci_postgres_smoke.py`. The script refuses a nonempty or non-test database, checks migrations through `0008`, PostgreSQL send-intent contention, SKIP LOCKED job claim/generation fencing, `0008` downgrade/upgrade and the exact `0007` nonempty-intent downgrade guard. Any SQL/infrastructure error is a failure. No production URL or connector credential is passed.

Frontend uses Node 22, `npm ci` from package-lock, production build, lint, TypeScript app/spec and ChromeHeadless Karma tests. Browser availability is checked; absence fails the job rather than silently skipping tests.

For each PR, inspect the exact workflow run for the final head SHA and both job conclusions. A workflow file existing or a queued run is not test evidence. This workflow does not deploy. PostgreSQL CI on a disposable empty DB is not a production backup/restore, production-volume load test or real channel acceptance.
