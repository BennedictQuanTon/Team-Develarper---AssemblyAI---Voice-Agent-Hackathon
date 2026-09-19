# Benchmarking

## New-architecture benchmark

The dedicated runner measures the implemented strengths of the active Lantern architecture: canonical validation, original-script persistence, SQLite restart recovery, monotonic and immutable revisions, stale kitchen-write rejection, multilingual Kokoro routing, and caption-only fallback. It also includes readiness and allergen-safety probes so an incomplete live stack cannot be presented as a successful end-to-end result.

Prepare a local environment:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
Copy-Item .env.example .env
```

For live local inference, install the voice dependencies and model:

```powershell
pip install -r requirements-local-voice.txt
ollama pull qwen3:4b
ollama list
```

Install the base requirements before the local-voice requirements. Reversing that order is not harmful; the environment can be repaired by running both commands afterward. On Windows, Japanese voice support additionally builds `pyopenjtalk` and therefore requires Visual Studio Build Tools with the C++ workload and NMake, or a WSL/Linux environment. English and Spanish do not require `pyopenjtalk`.

This checkout currently has CPU-only PyTorch. Use `KOKORO_DEVICE=cpu` until a CUDA-enabled PyTorch build is installed and `python -c "import torch; print(torch.cuda.is_available())"` prints `True`. Do not set `KOKORO_DEVICE=cuda` with a CPU-only build.

Put the real AssemblyAI project key only in the ignored `.env` file. Keep `ASSEMBLYAI_API_KEY=` empty in `.env.example`.

Run the deterministic benchmark without provider cost:

```powershell
python -m eval.benchmarks.restaurant.new_architecture
```

Run Ollama and Kokoro inference for English, Spanish, and Japanese:

```powershell
python -m eval.benchmarks.restaurant.new_architecture --live --languages en,es,ja
```

Use `--languages en,es` until the Windows Japanese build prerequisite is satisfied.

For an application-level latency result, keep the warmed backend running in terminal 1:

```powershell
.\.venv\Scripts\Activate.ps1
uvicorn backend.app.main:app --env-file .env --host 127.0.0.1 --port 8000
```

Then run the benchmark in terminal 2:

```powershell
.\.venv\Scripts\Activate.ps1
$env:KOKORO_DEVICE = "cpu"
python -m eval.benchmarks.restaurant.new_architecture --live --languages en,es --server-url http://127.0.0.1:8000
```

The application probe measures from submission of a final transcript to the first playable PCM chunk. It includes Qwen, deterministic validation, SQLite persistence, WebSocket delivery, and Kokoro. It deliberately excludes the duration of the guest's speech and AssemblyAI endpointing/transcription, and labels that boundary in `results.json`.

Each invocation prints its unique output directory and creates:

```text
reports/restaurant/new-architecture/<UTC-run-id>/
├── manifest.json
├── results.json
└── summary.md
```

The command returns exit code `0` only when every requested check passes. Exit code `2` means the report was still written, but it contains a safety gap, missing dependency, or failed live provider case. Read `summary.md` first and use `results.json` for the full timings and provider outputs.

The active AssemblyAI adapter implements realtime connect, PCM16 streaming, transcript callbacks, speech-start barge-in, flush, and close. Its transport contract is covered by deterministic tests. The automated suite does not submit a real speech recording because the repository has no committed 16 kHz PCM fixture, so it reports AssemblyAI execution separately instead of claiming speech-to-speech latency.

## Live microphone test

Start the backend as above. In another terminal:

```powershell
cd frontend
npm ci
npm run dev
```

Open `http://localhost:5173/?table_id=T4`, select **Start voice service**, allow microphone access, and wait for **Speak your order**. Say “One grilled seabass, no chili.” The page must show interim and final captions, a pending-kitchen revision, and then play the acknowledgement. The latency row reports the server's Qwen/validation/persistence time and first-audio time. Speak again during playback to verify that barge-in stops queued audio.

## Why V1 appeared faster

The historical local V1 report records an 800 ms *perceived* response because it played a local prerecorded filler about 50 ms after simulated ASR completed. That was not the generated answer. The same report records 2,022.47 ms average Qwen latency, 4,886.59 ms best measured end-to-end turn, and 6,553.48 ms average measured end-to-end turn. The historical live Gemini/Cartesia runs were slower still. V2 does not claim filler latency as answer latency: it warms Qwen and Kokoro once, bounds Qwen output, streams PCM in 100 ms chunks, and reports the actual first generated audio.

## Repository validation

Run deterministic repository checks with:

```powershell
python -m compileall -q backend legacy eval tools
python -m unittest discover -s tests -v
cd frontend
npm ci
npm run build
```

Historical V1 results remain immutable under `reports/`; the V1 archive is reference-only and should not be imported into the active benchmark.
