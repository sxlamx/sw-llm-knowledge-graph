#!/usr/bin/env bash
# dev.sh — start backend (FastAPI) and frontend (Vite) in parallel
# Usage:  bash scripts/dev.sh [--no-frontend] [--no-backend]
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
API_DIR="$ROOT/python-api"
FE_DIR="$ROOT/frontend"

# ── parse flags ────────────────────────────────────────────────────────────
RUN_BACKEND=1
RUN_FRONTEND=1
for arg in "$@"; do
  case "$arg" in
    --no-backend)  RUN_BACKEND=0  ;;
    --no-frontend) RUN_FRONTEND=0 ;;
  esac
done

# ── export .env vars safely (skip lines with special shell chars like JSON arrays) ──
ENV_FILE="$ROOT/.env"
if [[ -f "$ENV_FILE" ]]; then
  while IFS= read -r line || [[ -n "$line" ]]; do
    # Skip comments and blank lines
    [[ "$line" =~ ^[[:space:]]*# ]] && continue
    [[ -z "${line// }" ]] && continue
    # Only export simple KEY=VALUE lines (no JSON arrays / special chars)
    if [[ "$line" =~ ^[A-Za-z_][A-Za-z0-9_]*=[^[{].*$ ]] || [[ "$line" =~ ^[A-Za-z_][A-Za-z0-9_]*=$ ]]; then
      export "$line" 2>/dev/null || true
    fi
  done < "$ENV_FILE"
  echo "[dev] loaded $ENV_FILE"
else
  echo "[dev] WARNING: no .env found at $ENV_FILE"
fi

# ── cleanup on exit — kill both child processes ────────────────────────────
PIDS=()
cleanup() {
  echo ""
  echo "[dev] shutting down…"
  for pid in "${PIDS[@]}"; do
    kill "$pid" 2>/dev/null || true
  done
  wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# ── backend ────────────────────────────────────────────────────────────────
if [[ $RUN_BACKEND -eq 1 ]]; then
  echo "[dev] starting backend on http://localhost:8333"

  # Resolve the venv's uvicorn binary directly so subshells inherit the right Python
  if [[ -f "$API_DIR/.venv/bin/uvicorn" ]]; then
    UVICORN="$API_DIR/.venv/bin/uvicorn"
  elif [[ -f "$ROOT/.venv/bin/uvicorn" ]]; then
    UVICORN="$ROOT/.venv/bin/uvicorn"
  else
    echo "[dev] WARNING: no .venv found — creating one now…"
    python3 -m venv "$API_DIR/.venv"
    "$API_DIR/.venv/bin/pip" install --quiet -r "$API_DIR/requirements.txt"
    UVICORN="$API_DIR/.venv/bin/uvicorn"
  fi
  echo "[dev] using $UVICORN"

  (
    cd "$API_DIR"
    "$UVICORN" app.main:app \
      --host 0.0.0.0 \
      --port 8333 \
      --reload \
      --reload-dir app \
      2>&1 | sed 's/^/[api] /'
  ) &
  PIDS+=($!)
fi

# ── frontend ───────────────────────────────────────────────────────────────
if [[ $RUN_FRONTEND -eq 1 ]]; then
  echo "[dev] starting frontend on http://localhost:5333"

  # Install deps if node_modules is missing
  if [[ ! -d "$FE_DIR/node_modules" ]]; then
    echo "[dev] node_modules missing — running npm install…"
    (cd "$FE_DIR" && npm install)
  fi

  (
    cd "$FE_DIR"
    npm run dev 2>&1 | sed 's/^/[fe]  /'
  ) &
  PIDS+=($!)
fi

# ── wait ───────────────────────────────────────────────────────────────────
echo ""
echo "  Backend  →  http://localhost:8333"
echo "  Frontend →  http://localhost:5333"
echo "  API docs →  http://localhost:8333/docs"
echo ""
echo "  Press Ctrl+C to stop both."
echo ""

wait
