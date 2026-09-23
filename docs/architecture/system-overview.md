# System overview

The active Lantern runtime is a single FastAPI process. Provisioned table devices send 16 kHz PCM to `/ws/realtime?table_id=T4`. Provider adapters isolate AssemblyAI Universal-3.5 Pro transcription, Ollama Qwen3-4B intent extraction, and Kokoro local synthesis. `OrderWorkflow` validates model proposals against `data/restaurant/menu.json`; `SQLiteOrderRepository` persists immutable revisions and kitchen decisions. `/ws/ops` broadcasts backend order snapshots and decision events.

The old Gemini/Cartesia/mutable-session runtime is preserved under `legacy/lantern_v1/` and is never imported by active code.
