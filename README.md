# The Lantern

The Lantern is a multilingual restaurant voice service. A provisioned table device (`/?table_id=T4`) connects to FastAPI, AssemblyAI Universal-3.5 Pro, local Ollama Qwen3-4B, deterministic SQLite order revisions, kitchen decisions, and local Kokoro TTS.

## Run

Install `requirements.txt`, copy `.env.example` to `.env`, and set the server-side `ASSEMBLYAI_API_KEY`. Install `requirements-local-voice.txt` only when running Kokoro locally. Start the API with `uvicorn backend.app.main:app --env-file .env --reload`, then run `npm run dev` in `frontend/` and open `http://localhost:5173/?table_id=T4`.

Kitchen orders are available at `GET /api/kitchen/orders`; decisions are posted to `/api/kitchen/orders/{order_id}/decisions` with `expected_revision`.

## Benchmark

Run `python -m eval.benchmarks.restaurant.new_architecture` for a deterministic architecture benchmark, or add `--live --languages en,es --server-url http://127.0.0.1:8000` to exercise local Qwen, Kokoro, and the running application WebSocket. Every run writes an immutable `manifest.json`, `results.json`, and `summary.md` under `reports/restaurant/new-architecture/<UTC-run-id>/`. See `docs/operations/benchmarking.md` for setup, Japanese prerequisites, measurement boundaries, and live microphone validation.

## Architecture history

The pre-V2 waiter and its later latency/LangChain experiments are preserved under `legacy/lantern_v1/`. They remain useful for benchmark provenance and implementation comparison, but the active application does not import them. Historical performance evidence is stored in timestamped directories under `reports/restaurant/`.
