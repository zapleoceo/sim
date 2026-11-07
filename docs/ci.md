# SIM CI verification

Project rule: tests run only in CI/CD, never locally or on dot PC. `.github/workflows/ci.yml` runs on pull requests to `main` and pushes to `main`. It has independent backend/PostgreSQL and frontend jobs, each bounded to 25 minutes with overlapping runs cancelled. GitHub-hosted runner minutes may be consumed; no new paid service, secret, runner or production access is configured by this change.

Backend uses Python 3.12, pinned `uv==0.13.5`, `uv sync --frozen`, full pytest and Ruff. The job provisions a temporary PostgreSQL 16.6 service with an ephemeral CI-only password and runs `backend/scripts/ci_postgres_smoke.py`. The script refuses a nonempty or non-test database, checks migrations through `0008`, PostgreSQL send-intent contention, SKIP LOCKED job claim/generation fencing, `0008` downgrade/upgrade and the exact `0007` nonempty-intent downgrade guard. Any SQL/infrastructure error is a failure. No production URL or connector credential is passed.

Frontend uses Node 22, `npm ci` from package-lock, production build, lint, TypeScript app/spec and ChromeHeadless Karma tests. Browser availability is checked; absence fails the job rather than silently skipping tests.

For each PR, inspect the exact workflow run for the final head SHA and both job conclusions. A workflow file existing or a queued run is not test evidence. This workflow does not deploy. PostgreSQL CI on a disposable empty DB is not a production backup/restore, production-volume load test or real channel acceptance.

## CI bundle validation

The frontend job builds the browser UI, runs lint, TypeScript and browser tests, then packages the tracked backend runtime/build files and that same CI-built UI. Both the frontend job and the independent backend/PostgreSQL job must pass for the workflow to pass. Every job checks out the exact PR head or push commit SHA. No upload/download artifact action, release publication or staging transport is used.

`infra/build_release_artifact.py` checks the exact commit, rejects symlinks, special files, test paths, common secret names and private-key markers, and enforces file/count/size limits. It writes a tar and `manifest.json` only in the temporary CI runner, reopens the tar, verifies every member against the manifest, then prints the source SHA, Git tree SHA, member count, tar SHA256 and manifest SHA256. The manifest also records both lockfile SHA256 values and each member's SHA256. CI logs print hashes and counts, never archive contents. The temporary files are not published or retained after the job.

Successful bundle validation is a CI check, not a downloadable release artifact or evidence that SIM was released. A later release workflow still needs authorized artifact storage and staging transport. Staging rollout also needs a backup, worker shutdown before migration 0007 and readiness checks. No staging credentials or deployment command are present.
