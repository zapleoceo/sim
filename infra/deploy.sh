#!/usr/bin/env bash
# Deploy Sim to prod: build the admin locally, rsync code to the server, rebuild and restart.
# Usage: infra/deploy.sh            (from the repo root)
# Server .env lives only on the server (/var/www/sim/infra/.env) and is never synced.
set -euo pipefail

HOST="${SIM_DEPLOY_HOST:-hetzner-root}"
DEST="/var/www/sim"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

echo "== tests"
(cd "$ROOT/backend" && uv run pytest -q && uv run ruff check .)

echo "== build admin"
# Reinstall only when node_modules is out of sync with the lockfile (npm ci wipes the folder,
# which fails on Windows while a local dev server holds native binaries open).
(cd "$ROOT/frontend" && { npm ls --depth=0 >/dev/null 2>&1 || npm ci --no-audit --no-fund; } && npm run build)

echo "== sync (committed HEAD only — uncommitted edits never reach prod)"
if [ -n "$(git -C "$ROOT" status --porcelain -- backend infra frontend/src)" ]; then
  echo "working tree has uncommitted changes in backend/infra/frontend — commit first" >&2
  exit 1
fi
REV="$(git -C "$ROOT" rev-parse --short HEAD)"
ssh "$HOST" "mkdir -p $DEST && cd $DEST && find . -mindepth 1 -maxdepth 1 ! -name infra ! -name broker-project.json -exec rm -rf {} + && find infra -mindepth 1 ! -name .env -delete 2>/dev/null || true"
git -C "$ROOT" archive --format=tar HEAD | ssh "$HOST" "tar -x -C $DEST"
tar -C "$ROOT" -cf - frontend/dist/sim/browser | ssh "$HOST" "tar -x -C $DEST"
ssh "$HOST" "echo $REV > $DEST/REVISION"

echo "== up"
ssh "$HOST" "cd $DEST/infra && docker compose build && docker compose up -d --remove-orphans && docker compose ps"

echo "== smoke"
ssh "$HOST" 'for i in $(seq 1 30); do docker exec sim-api python -m sim.readiness && curl -fsS --max-time 5 http://127.0.0.1:8010/api/ready && exit 0; sleep 2; done; exit 1'
echo
