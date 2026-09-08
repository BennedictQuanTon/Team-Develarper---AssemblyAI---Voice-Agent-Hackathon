#!/usr/bin/env bash
# Show whether the voice agent is running
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${APP_PORT:-8000}"
HOST="${APP_HOST:-127.0.0.1}"
PID_FILE="$ROOT/.run/uvicorn.pid"

if [[ -f "$PID_FILE" ]]; then
  PID="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [[ -n "${PID}" ]] && kill -0 "$PID" 2>/dev/null; then
    echo "Running (pid $PID) → http://${HOST}:${PORT}/"
    exit 0
  fi
fi

if command -v lsof >/dev/null 2>&1 && lsof -tiTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "Port $PORT is in use (no pid file). Try: ./scripts/stop.sh then ./scripts/start.sh"
  exit 0
fi

echo "Not running. Start with: ./scripts/start.sh"
exit 1
