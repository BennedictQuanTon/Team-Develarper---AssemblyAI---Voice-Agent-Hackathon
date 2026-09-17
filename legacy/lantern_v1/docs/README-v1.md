# The Lantern Voice Waiter

The Lantern is a real-time restaurant voice waiter built for the AssemblyAI Voice Agent Hackathon. A guest can speak naturally while the browser streams PCM audio to the FastAPI backend. The system transcribes the turn, uses deterministic restaurant tools for menu, availability, tables, modifiers, and orders, then streams a spoken response back to the browser.

The default product mode is **The Lantern restaurant waiter**. A previous Da Nang travel RAG agent is preserved under [`legacy/travel/`](legacy/travel/README.md) for compatibility and historical reference; it is not the primary product.

## What the application does

- Streams microphone audio over one WebSocket connection at `/ws/realtime`.
- Uses AssemblyAI Realtime STT when configured, with a local stub fallback when keys are absent.
- Uses deterministic restaurant state from `data/restaurant/` for prices, availability, allergen flags, table state, modifiers, recommendations, and order totals.
- Uses Gemini or Ollama as the language front end for restaurant tool calls.
- Uses Cartesia streaming TTS when configured, with a local stub fallback when keys are absent.
- Supports live captions, basket updates, operational floor/menu monitoring, and barge-in handling.

## Repository map

```text
backend/app/                 FastAPI app, restaurant domain, voice runtime, metrics
config/voice/                Voice profiles
data/restaurant/             Active menu, floor, and schedule seed data
eval/                        Smoke tests, benchmarks, datasets, and reporting helper
frontend/                    Vite/TypeScript browser application
legacy/travel/               Previous travel RAG code, knowledge base, and historical plans
reports/                     Immutable benchmark artifacts and authored audits
scripts/                     Supported start/stop/status/demo entry points
tools/                       Asset generation and metric/report utilities
docs/                        Architecture, design, operations, and reorganization records
```

See [`docs/REPOSITORY_REORGANIZATION_PLAN.md`](docs/REPOSITORY_REORGANIZATION_PLAN.md) for the migration rationale and target architecture.

## Requirements

- Python 3.10 or later.
- Node.js and npm for the frontend build.
- Optional provider keys for live speech and language behavior:
  - `ASSEMBLYAI_API_KEY`
  - `GEMINI_API_KEY`
  - `CARTESIA_API_KEY`
- Optional local Ollama for `LLM_PROVIDER=ollama`.

## Local setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
```

On Windows PowerShell, activate the environment with:

```powershell
.\.venv\Scripts\Activate.ps1
```

The application uses stubs for providers whose keys are not configured. Do not commit `.env` or paste secrets into chat.

## Run the application

```bash
uvicorn backend.app.main:app --host 127.0.0.1 --port 8000 --reload
```

Open `http://127.0.0.1:8000/` for the guest interface.

Supported operational scripts are:

```bash
./scripts/start.sh
./scripts/status.sh
./scripts/stop.sh
./scripts/demo_restaurant.sh
```

The shell scripts target Unix-like environments. On Windows, use the explicit `uvicorn` command above or run the scripts through a compatible shell.

## Configuration

The settings model is in [`backend/app/config.py`](backend/app/config.py). Important variables include:

| Variable | Default purpose |
| --- | --- |
| `AGENT_MODE` | `waiter` is the active restaurant mode. `rag` is legacy travel compatibility mode. |
| `VOICE_PROFILE` | `friendly_waiter` by default. `friendly_guide` remains an alias for older local environments. |
| `LLM_PROVIDER` | `gemini` by default; set `ollama` for the local waiter-model path. |
| `ASSEMBLYAI_API_KEY` | Enables live streaming transcription. |
| `GEMINI_API_KEY` | Enables Gemini responses and restaurant tool calling. |
| `CARTESIA_API_KEY` | Enables Cartesia speech synthesis. |
| `METRICS_DIR` | Defaults to ignored `var/metrics/`; runtime JSONL is not stored in `reports/`. |

## HTTP and WebSocket interfaces

The core public interfaces are intentionally stable during the reorganization:

- `GET /health` — service and configuration status.
- `GET /api` — API overview.
- `GET /menu` and `GET /menu/available` — restaurant menu state.
- `GET /floor` — current restaurant floor state.
- `POST /menu/set-available` — update an item’s availability.
- `POST /session/reset` — reset the legacy non-streaming session state.
- `WS /ws/realtime` — full-duplex guest voice flow.
- `WS /ws/ops` — restaurant operations snapshots and controls.
- `POST /rag/ask` and `POST /rag/retrieve` — legacy travel RAG compatibility endpoints.

## Validation

The committed keyless regression suite covers restaurant behavior and public route/WebSocket essentials:

```bash
python -m unittest discover -s tests -v
```

When pytest is installed, it uses the configuration in `pyproject.toml`:

```bash
python -m pytest
```

Build the frontend separately:

```bash
cd frontend
npm ci
npm run build
```

Live provider smoke tests and benchmarks are opt-in because they can consume API quota or rate limits. They should always write a new run directory under `reports/<product>/<suite>/<UTC-run-id>/` rather than overwriting historical evidence.

## Benchmark artifacts

Generated benchmark outputs use this layout:

```text
reports/<product>/<suite>/<UTC-run-id>/
├── manifest.json
├── results.json
└── summary.md
```

The manifest records the source revision, dirty state, suite identity, timestamps, and caller-supplied metadata. See [`reports/README.md`](reports/README.md) for the artifact contract.

## Legacy travel material

The Da Nang dataset, RAG implementation, original benchmark reports, and planning material have been retained for traceability. They should not be treated as current Lantern benchmark evidence. Any deletion, extraction to a separate repository, or revival as an independently supported product requires a separate decision.
