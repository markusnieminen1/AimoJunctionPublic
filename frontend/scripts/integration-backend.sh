#!/usr/bin/env bash
# Run the frontend against a PRIVATE, isolated copy of the FastAPI backend for local integration checks.
#
#   frontend/scripts/integration-backend.sh            # seed + start backend :8011 + vite :8010, Ctrl-C stops both
#   WORKDIR=/some/dir BE_PORT=8011 FE_PORT=8010 frontend/scripts/integration-backend.sh
#   APP_NOW=2026-11-14T10:31:00+02:00 ...              # e.g. restart later to make open quotes expire
#   CATALOG_JSON=/path/to/catalog_sample.json ...      # optional custom catalog JSON (defaults to synthetic sample fixture)
#   CO2_CSV=/path/to/co2.csv ...                       # optional CO2 CSV
#   RELIABILITY_CSV=/path/to/reliability.csv ...       # optional delivery reliability CSV
#   DEMO_SEED=1 ...         # also run backend/scripts/demo_seed.py (Hotel Aurora Oy, demo@aimoannos.test / demo1234,
#                           # 3 agents); credentials go to $WORKDIR/src/.demo_credentials, never to backend/.
#   NO_FRONTEND=1 ...       # backend only (e.g. to point a mock-free vite or Playwright at it yourself)
#
# Isolation (does not touch a shared backend on :8001, its database, its logs or any file in backend/):
#   - backend/app, scripts and test fixtures are COPIED into $WORKDIR/src and run with backend/.venv's python,
#     so logs go to $WORKDIR/src/logs and no .env file is found;
#   - every setting is passed as an env var: SQLite DB in $WORKDIR, AIMO_MODE=fixture, fake LLM providers,
#     frozen APP_NOW, throwaway JWT secret.
# Nothing here is a secret. Needs: backend/.venv (uv sync), Node 20+.
set -euo pipefail

FRONTEND_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO="$(cd "$FRONTEND_DIR/.." && pwd)"
BACKEND="$REPO/backend"
WORKDIR="${WORKDIR:-${TMPDIR:-/tmp}/aimoannos-integration}"
BE_PORT="${BE_PORT:-8011}"
FE_PORT="${FE_PORT:-8010}"
PY="$BACKEND/.venv/bin/python"

[ -x "$PY" ] || { echo "backend/.venv missing: run 'cd backend && uv sync' first" >&2; exit 1; }
for p in "$BE_PORT" "$FE_PORT"; do
  if curl -s -o /dev/null "http://127.0.0.1:$p/"; then echo "port $p is busy" >&2; exit 1; fi
done

mkdir -p "$WORKDIR/src/tests/fixtures"
rsync -a --delete --exclude __pycache__ "$BACKEND/app" "$BACKEND/scripts" "$BACKEND/pyproject.toml" "$WORKDIR/src/"
cp -r "$BACKEND/tests/fixtures/aimo" "$BACKEND/tests/fixtures/products_sample.json" "$WORKDIR/src/tests/fixtures/"

export DATABASE_URL="sqlite:///$WORKDIR/aimo.db"
export AIMO_MODE=fixture AIMO_API_KEY= AIMO_BASE_URL=http://127.0.0.1:9
export LLM_PROVIDER=fake FAST_LLM_PROVIDER=fake ANTHROPIC_API_KEY= FEATHERLESS_API_KEY=
export APP_ENV=dev JWT_SECRET="integration-throwaway-$(date +%s)" PORT="$BE_PORT"
export APP_NOW="${APP_NOW:-2026-11-14T10:00:00+02:00}"

cd "$WORKDIR/src"
if [ ! -s "$WORKDIR/aimo.db" ]; then
  SEED_ARGS=(--products "${CATALOG_JSON:-tests/fixtures/products_sample.json}")
  [ -n "${CO2_CSV:-}" ] && SEED_ARGS+=(--co2 "$CO2_CSV")
  [ -n "${RELIABILITY_CSV:-}" ] && SEED_ARGS+=(--reliability "$RELIABILITY_CSV")
  echo "Seeding catalog fixture into isolated SQLite DB..."
  "$PY" scripts/seed_catalog.py "${SEED_ARGS[@]}"
  if [ "${DEMO_SEED:-0}" = "1" ]; then
    "$PY" scripts/demo_seed.py
  fi
fi

"$PY" -m uvicorn app.main:app --host 127.0.0.1 --port "$BE_PORT" > "$WORKDIR/uvicorn.log" 2>&1 &
BE_PID=$!
cleanup() { kill "$BE_PID" ${FE_PID:+"$FE_PID"} 2>/dev/null || true; }
trap cleanup EXIT INT TERM

for _ in $(seq 1 30); do curl -s "http://127.0.0.1:$BE_PORT/api/health" >/dev/null && break; sleep 0.5; done
curl -s "http://127.0.0.1:$BE_PORT/api/health"; echo

if [ "${NO_FRONTEND:-0}" = "1" ]; then
  echo "Backend only: http://127.0.0.1:$BE_PORT (logs $WORKDIR/uvicorn.log). Ctrl-C stops it."
  wait "$BE_PID"
  exit 0
fi

export NVM_DIR="$HOME/.nvm"
# shellcheck disable=SC1091
[ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"
cd "$FRONTEND_DIR"
VITE_USE_MOCKS=false API_PROXY_TARGET="http://127.0.0.1:$BE_PORT" npx vite --port "$FE_PORT" &
FE_PID=$!
echo "Frontend: http://127.0.0.1:$FE_PORT  (real backend :$BE_PORT, logs $WORKDIR/uvicorn.log). Ctrl-C stops both."
echo "Register a company in the UI; agent token: curl -s -X POST http://127.0.0.1:$BE_PORT/api/agent-auth/token -H 'content-type: application/json' -d '{\"agent_id\":\"agt_...\",\"agent_secret\":\"aks_...\"}'"
wait "$FE_PID"
