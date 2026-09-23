# The Lantern System Overview

The repository’s active application is **The Lantern**, a browser-based restaurant voice waiter. The application keeps deterministic menu, table, availability, and ordering state on the backend; language and speech providers only assist the conversation layer.

```text
Browser guest UI
  -> WebSocket /ws/realtime
  -> RealtimeSessionController
  -> AssemblyAI streaming STT (or local stub)
  -> restaurant conversation and order tools
  -> Gemini or Ollama response generation
  -> Cartesia streaming TTS (or local stub)
  -> captions, live basket, and audio in the browser
```

## Ownership boundaries

- `backend/app/domain/restaurant/` owns restaurant seed loading and order-session state.
- `backend/app/pipeline/` owns the present real-time orchestration and provider integration. The public API and WebSocket contracts remain in `backend/app/main.py`.
- `data/restaurant/` contains active menu, floor table, and service schedule seed data.
- `frontend/` contains the Vite/TypeScript guest interface and runtime assets under `frontend/public/`.
- `eval/` contains deterministic smoke tests, optional live benchmarks, datasets, and report-generation helpers.
- `reports/` contains curated historical or generated benchmark evidence, never runtime logs.
- `legacy/travel/` contains a previous Da Nang RAG agent, data, and supporting materials. It is preserved for compatibility, not current product behavior.

## Compatibility policy

The public restaurant routes, `/ws/realtime`, and `/ws/ops` are preserved. The legacy `/rag/*` routes and `AGENT_MODE=rag` continue to point to `legacy.travel.rag` while that compatibility surface is retained. `friendly_guide` remains an alias for the canonical `friendly_waiter` voice profile.

## Runtime data versus evidence

Runtime JSONL telemetry is written to ignored `var/metrics/`. Benchmark and audit evidence is written below `reports/<product>/<suite>/<UTC-run-id>/`; each generated run begins with a manifest recording the source commit and dirty-worktree state.
