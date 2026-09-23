# New architecture change log

This migration is additive. Lantern V1 files were moved to `legacy/lantern_v1/` and recorded in `ARCHIVE_MANIFEST.json`; shared menu, floor, schedule, reports, design assets, and the separate travel archive remain active. New files add AssemblyAI U3.5, Ollama Qwen3-4B, Kokoro language routing, SQLite revisions, kitchen decision APIs, table provisioning, and a live WebSocket boundary.

Validation performed: Python compile checks, deterministic unit/contract tests, and frontend production build. Live AssemblyAI, Ollama, and Kokoro inference requires local credentials/models and is not performed automatically.
