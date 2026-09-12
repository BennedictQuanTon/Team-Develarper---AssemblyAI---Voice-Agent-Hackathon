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
  fi
elif command -v lsof >/dev/null 2>&1 && lsof -tiTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "🟡 Port $PORT is occupied (process running without PID file)."
  echo "   Try: ./scripts/stop.sh then ./scripts/start.sh"
else
  echo "🔴 The Lantern backend is NOT running on port $PORT."
  echo "   Start with: ./scripts/start.sh"
fi

echo "--------------------------------------------------------"
if curl -s -m 2 "http://localhost:11434/api/tags" >/dev/null 2>&1; then
  echo "🟢 Ollama Service: RUNNING (http://localhost:11434)"
  MODELS="$(curl -s "http://localhost:11434/api/tags" 2>/dev/null | grep -o '"name":"[^"]*"' | cut -d '"' -f4 | tr '\n' ', ' | sed 's/,$//')"
  echo "   🦙 Available models: ${MODELS:-none}"
else
  echo "🔴 Ollama Service: NOT running on port 11434"
fi
echo "========================================================"
exit 0
