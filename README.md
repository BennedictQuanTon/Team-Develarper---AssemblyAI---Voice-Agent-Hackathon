# The Lantern

The Lantern is a multilingual restaurant voice service. A provisioned table device (`/?table_id=T4`) connects to FastAPI, AssemblyAI Universal-3.5 Pro, local Ollama Qwen3-4B, deterministic SQLite order revisions, kitchen decisions, and local Kokoro TTS.

## Run

Install `requirements.txt`, copy `.env.example` to `.env`, and set the server-side `ASSEMBLYAI_API_KEY`. Install `requirements-local-voice.txt` only when running Kokoro locally. Start with `uvicorn backend.app.main:app --reload` and open `http://localhost:8000/?table_id=T4`.

Kitchen orders are available at `GET /api/kitchen/orders`; decisions are posted to `/api/kitchen/orders/{order_id}/decisions` with `expected_revision`.
