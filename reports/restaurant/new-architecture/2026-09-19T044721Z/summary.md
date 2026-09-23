# Lantern new-architecture benchmark

Overall status: **PASS**

## Demonstrated strengths

- PASS — **canonical_validation**: 4/4 cases passed
- PASS — **stale_revision_rejected**: stale expected_revision created no decision
- PASS — **monotonic_revisions**: revisions advanced 1 -> 2
- PASS — **immutable_history**: revision 1 retained quantity=1 after revision 2
- PASS — **sqlite_restart_recovery**: revision history recovered after connection restart
- PASS — **multilingual_tts_routing**: 8 configured languages plus caption-only fallback

## Safety probes

- PASS — **allergen-cross-check**: ['allergen conflict for MAIN_RIVERPRAWN: shellfish']

## Live readiness

- READY — **python_version**: 3.11.15
- READY — **env_file**: .env exists
- READY — **assemblyai_key**: configured
- READY — **assemblyai_package**: installed
- READY — **assemblyai_model**: universal-3-5-pro
- READY — **assemblyai_stream_wiring**: implemented methods: ['close', 'connect', 'send_audio']; required: ['close', 'connect', 'send_audio']
- READY — **ollama_provider**: ollama
- READY — **ollama_configuration**: qwen3:4b
- READY — **ollama_runtime**: available models: ['qwen3:4b']
- READY — **ollama_model_downloaded**: requested=qwen3:4b; available models: ['qwen3:4b']
- READY — **tts_provider**: kokoro
- READY — **kokoro_model**: hexgrad/Kokoro-82M
- READY — **kokoro_package**: installed
- READY — **misaki_package**: installed
- READY — **kokoro_cuda**: torch=2.14.0+cpu; cuda_available=False
- READY — **table_t4**: T4 is provisioned in floor data

## Live provider execution

Live status: **PASS**

- Ollama reachable: True; model ready: True
- LLM cases passed: 4/4
- TTS cases passed: 2/2
- AssemblyAI: not executed — streaming transport is implemented and contract-tested, but this suite has no committed 16 kHz PCM speech fixture
- Application post-transcript first audio: 2648.98 ms (target < 4000 ms)
- Application validated workflow update: 1810.12 ms

## V1 comparison context

- V1 local Qwen average LLM latency: 2022.47 ms
- V1 best measured E2E turn: 4886.59 ms
- V1 average measured E2E turn: 6553.48 ms
- V1 perceived filler response: 800.0 ms (prerecorded acknowledgement, not the real answer)

See `results.json` for case-level timings and outputs.
