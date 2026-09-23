# Lantern V1 archive

This directory preserves the original Lantern restaurant voice waiter as reference material. V1 used AssemblyAI streaming, Gemini/Ollama, Cartesia streaming TTS, mutable in-memory `WaiterSession` state, and a mock frontend KDS. It was archived to make room for the multilingual, local Qwen/Kokoro, SQLite revision-safe runtime without deleting benchmark or implementation history.

The original archive source was commit `7284f41ea098a616b6ad36bb5ecff5792757c624` on `chore/repository-reorganization`. The later `perf/local-ollama-langchain` work at `884be1f` is preserved here as well, including the sliding-window Gemini limiter, AssemblyAI timeout fix, LangChain/Ollama waiter, response templates, benchmark harness, and its isolated tests.

The files are preserved for comparison and archaeology only: the active runtime never imports them, the root CI suite does not collect these tests, and this archive is not guaranteed runnable without setting `PYTHONPATH` and installing `legacy/lantern_v1/requirements.txt`. The same-machine Ollama benchmark showed that neither tested local model completed the full order correctly; see `reports/restaurant/ollama-langchain-ab/2026-09-20T012552Z/`.
