# AssemblyAI Realtime Voice Agent — Da Nang Tourism (English)

Realtime Agent track: **AssemblyAI Realtime STT** + custom orchestration (local Chroma RAG → Gemini → Cartesia TTS).

## Phases

| Phase | Status | Needs API keys |
|-------|--------|----------------|
| 0 Scaffold | done | No |
| 1 JSON KB + ingest | done | No |
| 2 Hybrid RAG + cache | pending | No |
| 3 Pipeline stubs + UI + metrics | pending | No |
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
