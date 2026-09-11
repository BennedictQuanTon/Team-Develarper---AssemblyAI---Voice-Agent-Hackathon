#!/usr/bin/env bash
# Check status of The Lantern Voice Agent
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${APP_PORT:-8000}"
HOST="${APP_HOST:-127.0.0.1}"
PID_FILE="$ROOT/.run/uvicorn.pid"

if [[ -f "$PID_FILE" ]]; then
  PID="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [[ -n "${PID}" ]] && kill -0 "$PID" 2>/dev/null; then
    echo "🟢 The Lantern is RUNNING (PID: $PID)"
    echo "   🌐 Web UI: http://${HOST}:${PORT}/"
    echo "   🩺 Health: http://${HOST}:${PORT}/health"
    if command -v curl >/dev/null 2>&1; then
      HEALTH_INFO="$(curl -s "http://${HOST}:${PORT}/health" 2>/dev/null || true)"
      if [[ -n "$HEALTH_INFO" ]]; then
        echo "   📊 Status: $HEALTH_INFO"
      fi
    fi
    exit 0
  fi
fi

if command -v lsof >/dev/null 2>&1 && lsof -tiTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "🟡 Port $PORT is occupied (process running without PID file)."
  echo "   Try: ./scripts/stop.sh then ./scripts/start.sh"
  exit 0
fi

echo "🔴 The Lantern is NOT running."
echo "   Start with: ./scripts/start.sh"
exit 1
