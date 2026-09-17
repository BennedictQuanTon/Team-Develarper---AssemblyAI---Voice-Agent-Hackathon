# Lantern V1 archive

This directory preserves the original Lantern restaurant voice waiter as reference material. V1 used AssemblyAI streaming, Gemini/Ollama, Cartesia streaming TTS, mutable in-memory `WaiterSession` state, and a mock frontend KDS. It was archived to make room for the multilingual, local Qwen/Kokoro, SQLite revision-safe runtime without deleting benchmark or implementation history.

The source commit was `7284f41ea098a616b6ad36bb5ecff5792757c624` on `chore/repository-reorganization`. The files are preserved for comparison and archaeology only: the active runtime never imports them, and they are not maintained, tested, or guaranteed runnable.
