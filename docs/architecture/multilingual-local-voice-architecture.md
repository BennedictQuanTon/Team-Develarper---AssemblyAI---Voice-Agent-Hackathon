# Lantern multilingual local voice architecture

Each provisioned table device connects with `?table_id=T4`. The backend validates the table, receives 16 kHz PCM, transcribes with AssemblyAI Universal-3.5 Pro, asks local Qwen3-4B for structured intent, validates against the shared menu, persists immutable SQLite revisions, and publishes kitchen events. Verified kitchen decisions are localized and synthesized by Kokoro at 24 kHz for supported languages; unsupported languages receive captions only.

The model may interpret and localize, but only deterministic workflow code writes orders. Kitchen writes carry `expected_revision`; stale writes are rejected with HTTP 409. V1 remains under `legacy/lantern_v1/` and is outside the active import graph.
