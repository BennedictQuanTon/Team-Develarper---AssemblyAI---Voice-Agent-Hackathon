#!/usr/bin/env bash
# Stop Da Nang Voice Agent
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PORT="${APP_PORT:-8000}"
PID_DIR="$ROOT/.run"
PID_FILE="$PID_DIR/uvicorn.pid"

stopped=0

if [[ -f "$PID_FILE" ]]; then
  PID="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [[ -n "${PID}" ]] && kill -0 "$PID" 2>/dev/null; then
    # Kill process group if possible (covers --reload parent/child)
    kill "$PID" 2>/dev/null || true
    sleep 0.5
    if kill -0 "$PID" 2>/dev/null; then
      kill -9 "$PID" 2>/dev/null || true
    fi
    echo "Stopped pid $PID"
    stopped=1
  fi
  rm -f "$PID_FILE"
fi

# Fallback: anything still listening on the app port
if command -v lsof >/dev/null 2>&1; then
  PIDS="$(lsof -tiTCP:"$PORT" -sTCP:LISTEN 2>/dev/null || true)"
  if [[ -n "${PIDS}" ]]; then
    echo "$PIDS" | xargs kill -9 2>/dev/null || true
    echo "Freed port $PORT"
    stopped=1
  fi
fi

if [[ "$stopped" -eq 0 ]]; then
  echo "No running server found on port $PORT"
else
  echo "Project stopped."
fi
