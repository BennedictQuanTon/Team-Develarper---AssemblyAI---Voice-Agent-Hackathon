#!/usr/bin/env bash
# Start Da Nang Voice Agent (FastAPI + UI)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

HOST="${APP_HOST:-127.0.0.1}"
PORT="${APP_PORT:-8000}"
PID_DIR="$ROOT/.run"
PID_FILE="$PID_DIR/uvicorn.pid"
LOG_FILE="$PID_DIR/uvicorn.log"

mkdir -p "$PID_DIR"

if [[ ! -d "$ROOT/.venv" ]]; then
  echo "Missing .venv — run: python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt"
  exit 1
fi

if [[ -f "$PID_FILE" ]]; then
  OLD_PID="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [[ -n "${OLD_PID}" ]] && kill -0 "$OLD_PID" 2>/dev/null; then
    echo "Already running (pid $OLD_PID) → http://${HOST}:${PORT}/"
    exit 0
  fi
  rm -f "$PID_FILE"
fi

if command -v lsof >/dev/null 2>&1; then
  if lsof -tiTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "Port $PORT is already in use. Stop it first: ./scripts/stop.sh"
    exit 1
  fi
fi

# shellcheck disable=SC1091
source "$ROOT/.venv/bin/activate"
export PYTHONPATH="$ROOT"

nohup uvicorn backend.app.main:app \
  --host "$HOST" \
  --port "$PORT" \
  --reload \
  >"$LOG_FILE" 2>&1 &
echo $! >"$PID_FILE"

sleep 1
if kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  echo "Started (pid $(cat "$PID_FILE"))"
  echo "UI:     http://${HOST}:${PORT}/"
  echo "Health: http://${HOST}:${PORT}/health"
  echo "Logs:   $LOG_FILE"
  echo "Stop:   ./scripts/stop.sh"
else
  echo "Failed to start — see $LOG_FILE"
  rm -f "$PID_FILE"
  exit 1
fi
