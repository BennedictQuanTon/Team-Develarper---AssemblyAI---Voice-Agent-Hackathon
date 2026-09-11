#!/usr/bin/env bash
# Stop The Lantern Voice Agent cleanly
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PORT="${APP_PORT:-8000}"
PID_DIR="$ROOT/.run"
PID_FILE="$PID_DIR/uvicorn.pid"

stopped=0

# 1. Stop via PID file
if [[ -f "$PID_FILE" ]]; then
  PID="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [[ -n "${PID}" ]] && kill -0 "$PID" 2>/dev/null; then
    kill "$PID" 2>/dev/null || true
    sleep 0.5
    if kill -0 "$PID" 2>/dev/null; then
      kill -9 "$PID" 2>/dev/null || true
    fi
    echo "🛑 Stopped process (PID: $PID)"
    stopped=1
  fi
  rm -f "$PID_FILE"
fi

# 2. Release port 8000 if still held
if command -v lsof >/dev/null 2>&1; then
  PIDS="$(lsof -tiTCP:"$PORT" -sTCP:LISTEN 2>/dev/null || true)"
  if [[ -n "${PIDS}" ]]; then
    echo "$PIDS" | xargs kill -9 2>/dev/null || true
    echo "🔓 Freed backend port $PORT"
    stopped=1
  fi

  # Also check Vite dev port 3000 if active
  PIDS_3000="$(lsof -tiTCP:3000 -sTCP:LISTEN 2>/dev/null || true)"
  if [[ -n "${PIDS_3000}" ]]; then
    echo "$PIDS_3000" | xargs kill -9 2>/dev/null || true
    echo "🔓 Freed Vite dev port 3000"
    stopped=1
  fi
fi

if [[ "$stopped" -eq 0 ]]; then
  echo "ℹ️ No running server found on port $PORT."
else
  echo "========================================================"
  echo " ✅ The Lantern Voice Agent stopped successfully."
  echo "========================================================"
fi
