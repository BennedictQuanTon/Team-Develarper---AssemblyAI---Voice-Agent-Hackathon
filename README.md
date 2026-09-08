# AssemblyAI Realtime Voice Agent — Da Nang Tourism (English)

Realtime Agent track: **AssemblyAI Realtime STT** + custom orchestration (local Chroma RAG → Gemini → Cartesia TTS).

## Phases

| Phase | Status | Needs API keys |
|-------|--------|----------------|
| 0 Scaffold | done | No |
| 1 JSON KB + ingest | done | No |
| 2 Hybrid RAG + cache | done | No |
| 3 Pipeline stubs + UI + metrics | done | No |
| 4 Wire live clients | pending | Yes |
| 5 E2E test + reports | pending | Yes |

## Quick start (Phase 0)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill keys only from Phase 4 onward
uvicorn backend.app.main:app --reload --host 0.0.0.0 --port 8000
```

Health check: [http://127.0.0.1:8000/health](http://127.0.0.1:8000/health)

## Ingest knowledge base (Phase 1)

```bash
source .venv/bin/activate
pip install -r requirements.txt
PYTHONPATH=. python -m rag.ingest_json
PYTHONPATH=. python -m rag.ingest_json --peek-only
```

Writes local Chroma to `data/chroma/` and BM25 to `data/bm25/index.pkl`.

## Hybrid RAG ask + cache bench (Phase 2)

```bash
# Text ask (extractive answer, no LLM key)
PYTHONPATH=. python -m rag.ask "What is Ba Na Hills?" --twice

# Cache hit vs miss report
PYTHONPATH=. python -m eval.run_cache_bench

# HTTP
PYTHONPATH=. uvicorn backend.app.main:app --reload --port 8000
curl -s -X POST http://127.0.0.1:8000/rag/ask \
  -H 'content-type: application/json' \
  -d '{"query":"Dragon Bridge fire show"}' | python3 -m json.tool
```

## Orchestration UI (Phase 3)

```bash
PYTHONPATH=. uvicorn backend.app.main:app --reload --port 8000
# open http://127.0.0.1:8000/
# or:
curl -s -X POST http://127.0.0.1:8000/turn \
  -H 'content-type: application/json' \
  -d '{"text":"When is the Dragon Bridge fire show?","profile":"friendly_guide"}' | python3 -m json.tool

PYTHONPATH=. python -m eval.run_waterfall
```

Stub ASR/LLM/TTS (no vendor keys). Real hybrid RAG. Metrics → `reports/turns.jsonl`.

## Layout

```
backend/app/     # FastAPI gateway, pipeline, metrics
rag/             # ingest + retrieve (Phase 1+)
data/danang_en/  # English JSON knowledge base
config/          # voice profiles, app settings
eval/            # datasets + eval runners
frontend/        # mic UI (Phase 3+)
reports/         # latency / accuracy outputs (Phase 5)
```

## Environment

See `.env.example`. Do not commit real secrets.
