# SIM CI verification

Project rule: tests run only in CI/CD, never locally or on dot PC. `.github/workflows/ci.yml` runs on pull requests to any base branch (stacked pull requests are verified too) and on pushes to `main`. It has independent backend/PostgreSQL and frontend jobs, each bounded to 25 minutes with overlapping runs cancelled, the `change-gate` job for pull requests and the `deploy` job for pushes to `main` (see below).

Backend uses Python 3.12, pinned `uv==0.13.5`, `uv sync --frozen`, full pytest and Ruff. The job provisions a temporary PostgreSQL 16.6 service with an ephemeral CI-only password and runs `backend/scripts/ci_postgres_smoke.py`. The script refuses a nonempty or non-test database, checks migrations through `0008`, PostgreSQL send-intent contention, SKIP LOCKED job claim/generation fencing, `0008` downgrade/upgrade and the exact `0007` nonempty-intent downgrade guard. Any SQL/infrastructure error is a failure. No production URL or connector credential is passed.

Frontend uses Node 22 and `npm ci` from package-lock, then ESLint (`npm run lint`), TypeScript for app, tests and Vite config (`npm run typecheck`), Vitest unit/component tests in jsdom (`npm test`, no browser binary needed), the production build (`npm run build`, React + Vite) and a check that the bundle is in `frontend/dist/sim/browser`, the path read by the backend, the release packager and the `deploy` job.

For each PR, inspect the exact workflow run for the final head SHA and both job conclusions. A workflow file existing or a queued run is not test evidence. Pull request runs never deploy; only the `deploy` job on a push to `main` does. PostgreSQL CI on a disposable empty DB is not a production backup/restore, production-volume load test or real channel acceptance.

## CI bundle validation

The frontend job builds the browser UI, runs lint, TypeScript and browser tests, then packages the tracked backend runtime/build files and that same CI-built UI. Both the frontend job and the independent backend/PostgreSQL job must pass for the workflow to pass. Every job checks out the exact PR head or push commit SHA. No upload/download artifact action, release publication or staging transport is used.

`infra/build_release_artifact.py` checks the exact commit, rejects symlinks, special files, test paths, common secret names and private-key markers, and enforces file/count/size limits. It writes a tar and `manifest.json` only in the temporary CI runner, reopens the tar, verifies every member against the manifest, then prints the source SHA, Git tree SHA, member count, tar SHA256 and manifest SHA256. The manifest also records both lockfile SHA256 values and each member's SHA256. CI logs print hashes and counts, never archive contents. The temporary files are not published or retained after the job.

Successful bundle validation is a CI check, not a downloadable release artifact or evidence that SIM was released. The `deploy` job does not use this bundle: it rebuilds the admin from the same SHA and ships the committed tree (see below).

## Production deploy job

`deploy` in `.github/workflows/ci.yml` runs only on pushes to `main` after the `backend` and `frontend` jobs pass. It is the only CI job that touches production. It:

1. Takes a fresh database and secrets backup on the server with `/usr/local/bin/sim-backup.sh` (the backup tree under `/var/backups/vera` is pulled by the NAS).
2. Syncs the committed tree and the CI-built admin bundle to `/var/www/sim`, keeping `infra/.env` and `broker-project.json`, and writes `REVISION`.
3. Stops `sim-worker` before migrations, so an old worker never runs with new migrations.
4. Runs `docker compose build && up -d`, then force-recreates `api`. Migrations run in the `migrate` service before API and worker start. The recreate matters for frontend-only changes: step 2 replaces `frontend/dist`, and an `api` container that compose did not recreate would keep a bind mount to the deleted directory and return 500 for every admin page.
5. Runs the readiness smoke (`/api/ready` and the admin `/login` page), and prints the Alembic head and `REVISION`.

Secrets: `SIM_DEPLOY_SSH_KEY`, `SIM_DEPLOY_KNOWN_HOSTS`, `SIM_DEPLOY_HOST`, `SIM_DEPLOY_PORT`. The key is dedicated to this job and pinned to the server host key. Rotate it by replacing the key in `authorized_keys` on the server and updating the secret.

Rollback: restore the database from the backup in `/var/backups/vera/sim/daily/<date>/sim.dump` (`pg_restore -c`), then redeploy the previous commit. The job does not roll back by itself.

## Tests and docs gate

Job `change-gate` ("Tests and docs required") runs on every pull request
(`backend/scripts/change_gate.py`, unit tests in `backend/tests/test_change_gate.py`). The job checks
out the PR merge commit, so the gate script from `main` also runs for branches created before it
existed. For the files changed between the PR base and head:

- product code changed (`backend/sim/**`, `frontend/src/**` except `*.test.ts(x)` and
  `frontend/src/test/**`) without a changed test file (`backend/tests/**`, `*.test.ts(x)`,
  `frontend/src/test/**`) fails the job;
- product code changed without a change in `docs/**` or `README.md` fails the job.

A purely cosmetic change may opt out with `tests-not-needed` or `docs-not-needed` in a commit
message of the PR; reviewers see it. Coverage thresholds are not enforced yet.

## Branch protection of `main`

Since 2026-11-12 `main` is protected. A pull request merges only when these required status checks
passed: "Backend and PostgreSQL", "Frontend" and "Tests and docs required" (the branch need not be
up to date with `main`, CI already runs on the merge commit). Force pushes and deleting `main` are
off. Admins are not forced (`enforce_admins: false`), so an emergency fix stays possible; it still
goes through CI and the `deploy` gates. Current settings:
`gh api repos/zapleoceo/sim/branches/main/protection`.
