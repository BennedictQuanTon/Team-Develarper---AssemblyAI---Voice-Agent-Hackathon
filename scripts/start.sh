#!/usr/bin/env bash
# Start The Lantern Voice Agent (FastAPI Backend + Vite Apple UI)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

HOST="${APP_HOST:-127.0.0.1}"
PORT="${APP_PORT:-8000}"
PID_DIR="$ROOT/.run"
PID_FILE="$PID_DIR/uvicorn.pid"
LOG_FILE="$PID_DIR/uvicorn.log"

mkdir -p "$PID_DIR"

# 1. Check Python Virtual Environment
if [[ ! -d "$ROOT/.venv" ]]; then
  echo "❌ Missing .venv — please set up Python virtualenv first:"
  echo "   python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt"
  exit 1
fi

# 2. Check .env configuration
if [[ ! -f "$ROOT/.env" ]]; then
  if [[ -f "$ROOT/.env.example" ]]; then
    echo "⚠️ .env not found. Creating from .env.example..."
    cp "$ROOT/.env.example" "$ROOT/.env"
    echo "ℹ️ Please configure your API keys in .env"
  fi
fi

# 3. Check / build frontend distribution if missing
if [[ ! -f "$ROOT/frontend/dist/index.html" ]]; then
  echo "📦 Frontend build not found. Building with Vite..."
  if [[ ! -d "$ROOT/frontend/node_modules" ]]; then
    echo "   Installing frontend dependencies..."
    (cd "$ROOT/frontend" && npm install)
  fi
  (cd "$ROOT/frontend" && npm run build)
  echo "✅ Frontend built successfully."
fi

# 4. Check if already running
if [[ -f "$PID_FILE" ]]; then
  OLD_PID="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [[ -n "${OLD_PID}" ]] && kill -0 "$OLD_PID" 2>/dev/null; then
    echo "🏮 The Lantern is already running (PID: $OLD_PID)"
    echo "🌐 Web UI: http://${HOST}:${PORT}/"
    exit 0
  fi
  rm -f "$PID_FILE"
fi

# 5. Check if port is occupied
if command -v lsof >/dev/null 2>&1; then
  if lsof -tiTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "⚠️ Port $PORT is already in use by another process."
    echo "   To stop it and restart: ./scripts/stop.sh && ./scripts/start.sh"
    exit 1
  fi
fi

# 6. Launch Uvicorn in background
source "$ROOT/.venv/bin/activate"
export PYTHONPATH="$ROOT"

nohup "$ROOT/.venv/bin/python" -m uvicorn backend.app.main:app \
  --host "$HOST" \
  --port "$PORT" \
  >"$LOG_FILE" 2>&1 &

NEW_PID=$!
echo "$NEW_PID" >"$PID_FILE"

# 7. Verification wait
echo -n "🚀 Starting The Lantern Voice Agent..."
for _ in {1..10}; do
  if kill -0 "$NEW_PID" 2>/dev/null; then
    if curl -s "http://${HOST}:${PORT}/health" >/dev/null 2>&1; then
      echo " OK!"
      echo "========================================================"
      echo " 🏮 The Lantern Voice Agent is LIVE"
      echo "========================================================"
      echo " 🌐 Web UI:     http://${HOST}:${PORT}/"
      echo " 🩺 Health:     http://${HOST}:${PORT}/health"
      echo " 📋 Logs:       tail -f .run/uvicorn.log"
      echo " 🛑 Stop:       ./scripts/stop.sh"
      echo "========================================================"
      exit 0
    fi
  else
    break
  fi
  sleep 0.5
  echo -n "."
done

echo " FAILED!"
echo "❌ Failed to start properly. Check logs:"
tail -n 20 "$LOG_FILE"
rm -f "$PID_FILE"
exit 1
