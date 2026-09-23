#!/usr/bin/env bash
# The Lantern restaurant voice-ordering demo playbook.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ "${1:-}" == "--start" ]] || [[ "${1:-}" == "--restart" ]]; then
  ./scripts/stop.sh >/dev/null 2>&1 || true
  ./scripts/start.sh
  echo
fi

cat <<'EOF'
============================================================
  THE LANTERN — RESTAURANT VOICE ORDERING DEMO
============================================================

Before speaking
  - Open http://127.0.0.1:8000/ in Chrome and allow microphone access.
  - Start a fresh restaurant session and use the Friendly Waiter profile.

Turn 1 — ask for a recommendation
  "What do you recommend for a first-time guest?"
  Expected: a short, menu-grounded recommendation and a quick audio filler.

Turn 2 — add a dish
  "Please add the recommended dish to my order."
  Expected: the dish is in the live basket and the reply confirms the change.

Turn 3 — finalize
  "Place my order for table 4."
  Expected: an order confirmation with the table number and a clean session state.

Pass criteria
  [ ] No long silence before a substantive response.
  [ ] Only available menu items are added.
  [ ] The basket and confirmation agree on dish names and quantities.
  [ ] The order is assigned to the requested valid table.

Commands
  ./scripts/stop.sh && ./scripts/start.sh
  ./scripts/demo_restaurant.sh
EOF
