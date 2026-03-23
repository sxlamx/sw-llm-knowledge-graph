#!/usr/bin/env bash
# restart.sh — kill any running dev processes then start fresh
# Usage:  bash scripts/restart.sh [--no-frontend] [--no-backend]
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "[restart] killing existing dev processes…"

# Kill anything bound to our ports
kill_port() {
  local port=$1
  local pids
  pids=$(lsof -ti tcp:"$port" 2>/dev/null || true)
  if [[ -n "$pids" ]]; then
    echo "[restart]   port $port → killing PIDs $pids"
    echo "$pids" | xargs kill -9 2>/dev/null || true
  fi
}

kill_port 8000  # FastAPI / uvicorn
kill_port 5333  # Vite dev server

# Also kill any lingering uvicorn / vite processes by name
pkill -f "uvicorn app.main:app" 2>/dev/null && echo "[restart]   killed uvicorn" || true
pkill -f "vite"                 2>/dev/null && echo "[restart]   killed vite"    || true

# Short pause so sockets are released
sleep 1

echo "[restart] starting dev servers…"
exec bash "$ROOT/scripts/dev.sh" "$@"
