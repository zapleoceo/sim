# SIM CI verification

Project rule: tests run only in CI/CD, never locally or on dot PC. `.github/workflows/ci.yml` runs on pull requests to `main` and pushes to `main`. It has independent backend/PostgreSQL and frontend jobs, each bounded to 25 minutes with overlapping runs cancelled. GitHub-hosted runner minutes may be consumed; no new paid service, secret, runner or production access is configured by this change.

Backend uses Python 3.12, pinned `uv==0.13.5`, `uv sync --frozen`, full pytest and Ruff. The job provisions a temporary PostgreSQL 16.6 service with an ephemeral CI-only password and runs `backend/scripts/ci_postgres_smoke.py`. The script refuses a nonempty or non-test database, checks migrations through `0008`, PostgreSQL send-intent contention, SKIP LOCKED job claim/generation fencing, `0008` downgrade/upgrade and the exact `0007` nonempty-intent downgrade guard. Any SQL/infrastructure error is a failure. No production URL or connector credential is passed.

Frontend uses Node 22, `npm ci` from package-lock, production build, lint, TypeScript app/spec and ChromeHeadless Karma tests. Browser availability is checked; absence fails the job rather than silently skipping tests.

For each PR, inspect the exact workflow run for the final head SHA and both job conclusions. A workflow file existing or a queued run is not test evidence. This workflow does not deploy. PostgreSQL CI on a disposable empty DB is not a production backup/restore, production-volume load test or real channel acceptance.

## CI release artifact

The release job runs only after both backend/PostgreSQL and frontend jobs pass. All jobs check out the same PR head or push commit SHA. The frontend job uploads its built `dist/sim/browser` files for the release job; the release job never rebuilds them. `infra/build_release_artifact.py` packages only tracked backend Docker/build/runtime files and those CI-built frontend files. It rejects symlinks, special files, test paths, common secret names and private-key markers, and enforces file/count/size limits.

The release job uploads `sim-release-<full-source-SHA>` for 14 days. It contains `sim-release-<full-source-SHA>.tar` and `manifest.json`. The manifest records the exact source commit/tree, lockfile SHA256 values, tar SHA256 and SHA256 for each member. The job reopens and verifies the tar before upload. The Actions artifact ID and digest appear in the job summary and artifacts API. The artifact's digest covers GitHub's uploaded artifact container; the manifest's `archive_sha256` covers the tar itself. Verify both before staging, and match `source_commit` to the intended release SHA.

This artifact is a deploy input, not evidence that SIM was released. Staging rollout still needs an authorized server route, a backup, worker shutdown before migration 0007, and readiness checks. The workflow contains no staging credentials and runs no deployment command.
