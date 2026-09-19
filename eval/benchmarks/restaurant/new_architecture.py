"""Benchmark the implemented strengths and readiness of Lantern V2.

Offline mode is deterministic and free. ``--live`` additionally exercises the
local Ollama and Kokoro providers when they are available. Every invocation
creates an immutable report directory through :mod:`eval.reporting`.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import os
import platform
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

import httpx
import websockets
from pydantic import ValidationError

from backend.app.domain.restaurant.models import IntentProposal
from backend.app.domain.restaurant.repository import SQLiteOrderRepository
from backend.app.domain.restaurant.store import LanternStore
from backend.app.domain.restaurant.validation import validate_intent
from backend.app.providers.asr.assemblyai_stream import AssemblyAIRealtimeProvider
from backend.app.providers.llm.ollama import OllamaClient
from backend.app.providers.tts.kokoro import KokoroProvider, VOICE_MAP
from backend.app.services.language_router import resolve_language
from eval.reporting import create_run, write_results, write_summary

DATASET = ROOT / "eval" / "datasets" / "restaurant" / "new_architecture_strengths.v1.json"
V1_COMPARISON = {
    "source": "reports/restaurant/llm-provider-comparison/historical-pre-2026-09-13/results.json",
    "model": "qwen2.5:3b",
    "avg_llm_ms": 2022.47,
    "best_e2e_ms": 4886.59,
    "avg_e2e_ms": 6553.48,
    "perceived_filler_ttfb_ms": 800.0,
    "note": "The 800ms figure was a prerecorded filler; the measured answer E2E average was 6553.48ms.",
}


def _setting(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _result(name: str, passed: bool, detail: str, **metrics: Any) -> dict[str, Any]:
    return {"name": name, "passed": passed, "detail": detail, "metrics": metrics}


def load_dataset(path: Path = DATASET) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def inspect_readiness(languages: list[str] | None = None) -> dict[str, Any]:
    key = _setting("ASSEMBLYAI_API_KEY")
    key_present = bool(key and "your_" not in key.lower() and "<" not in key)
    required_asr_methods = {"connect", "send_audio", "close"}
    present_asr_methods = {name for name in required_asr_methods if hasattr(AssemblyAIRealtimeProvider, name)}
    ollama_base_url = _setting("OLLAMA_BASE_URL", "http://localhost:11434")
    ollama_model = _setting("OLLAMA_MODEL", "qwen3:4b")
    ollama_reachable = False
    ollama_models: list[str] = []
    ollama_detail = "not reachable"
    try:
        response = httpx.get(f"{ollama_base_url.rstrip('/')}/api/tags", timeout=3)
        response.raise_for_status()
        ollama_reachable = True
        ollama_models = [model.get("name", "") for model in response.json().get("models", [])]
        ollama_detail = f"available models: {ollama_models}"
    except Exception as exc:
        ollama_detail = str(exc)
    ollama_model_ready = any(name == ollama_model or name.startswith(f"{ollama_model}:") for name in ollama_models)
    torch_cuda = False
    torch_detail = "torch is not installed"
    if importlib.util.find_spec("torch"):
        import torch

        torch_cuda = torch.cuda.is_available()
        torch_detail = f"torch={torch.__version__}; cuda_available={torch_cuda}"

    kokoro_device = _setting("KOKORO_DEVICE", "auto")
    device_compatible = kokoro_device != "cuda" or torch_cuda
    checks = [
        _result("python_version", sys.version_info[:2] >= (3, 10) and sys.version_info[:2] < (3, 14), platform.python_version()),
        _result("env_file", (ROOT / ".env").exists(), ".env exists" if (ROOT / ".env").exists() else "copy .env.example to .env"),
        _result("assemblyai_key", key_present, "configured" if key_present else "ASSEMBLYAI_API_KEY is empty or a placeholder"),
        _result("assemblyai_package", importlib.util.find_spec("assemblyai") is not None, "installed" if importlib.util.find_spec("assemblyai") else "install requirements.txt"),
        _result("assemblyai_model", _setting("ASSEMBLYAI_SPEECH_MODEL", "universal-3-5-pro") == "universal-3-5-pro", _setting("ASSEMBLYAI_SPEECH_MODEL", "universal-3-5-pro")),
        _result("assemblyai_stream_wiring", present_asr_methods == required_asr_methods, f"implemented methods: {sorted(present_asr_methods)}; required: {sorted(required_asr_methods)}"),
        _result("ollama_provider", _setting("LLM_PROVIDER", "ollama") == "ollama", _setting("LLM_PROVIDER", "ollama")),
        _result("ollama_configuration", ollama_model == "qwen3:4b", ollama_model),
        _result("ollama_runtime", ollama_reachable, ollama_detail),
        _result("ollama_model_downloaded", ollama_model_ready, f"requested={ollama_model}; {ollama_detail}"),
        _result("tts_provider", _setting("TTS_PROVIDER", "kokoro") == "kokoro", _setting("TTS_PROVIDER", "kokoro")),
        _result("kokoro_model", _setting("KOKORO_MODEL_ID", "hexgrad/Kokoro-82M") == "hexgrad/Kokoro-82M", _setting("KOKORO_MODEL_ID", "hexgrad/Kokoro-82M")),
        _result("kokoro_package", importlib.util.find_spec("kokoro") is not None, "installed" if importlib.util.find_spec("kokoro") else "install requirements-local-voice.txt"),
        _result("misaki_package", importlib.util.find_spec("misaki") is not None, "installed" if importlib.util.find_spec("misaki") else "install requirements-local-voice.txt"),
        _result("kokoro_device_compatible", device_compatible, f"configured={kokoro_device}; {torch_detail}"),
        _result("table_t4", LanternStore().get_table("T4") is not None, "T4 is provisioned in floor data"),
    ]
    if languages and "ja" in languages:
        checks.append(
            _result(
                "japanese_voice_dependency",
                importlib.util.find_spec("pyopenjtalk") is not None,
                "pyopenjtalk installed" if importlib.util.find_spec("pyopenjtalk") else "install pyopenjtalk using MSVC C++ Build Tools/NMake or run under WSL/Linux",
            )
        )
    blockers = [check["name"] for check in checks if not check["passed"]]
    return {"checks": checks, "blockers": blockers, "ready_for_full_live": not blockers}


def run_offline(dataset: dict[str, Any]) -> dict[str, Any]:
    started = time.perf_counter()
    store = LanternStore()
    cases: list[dict[str, Any]] = []
    safety_probes: list[dict[str, Any]] = []

    with tempfile.TemporaryDirectory(prefix="lantern-benchmark-") as directory:
        db_path = Path(directory) / "lantern.sqlite3"
        repo = SQLiteOrderRepository(db_path)

        for case in dataset["cases"]:
            case_started = time.perf_counter()
            try:
                intent = IntentProposal.model_validate(case["intent"])
                errors = validate_intent(intent, store)
                accepted = not errors
                expected_accepted = case["expect_validation"] == "accept"
                order = None
                if accepted:
                    order = repo.create_revision(
                        f"bench-{case['id']}", "T4", "benchmark", intent.source_language,
                        case["transcript"], [item.model_dump() for item in intent.items], intent.allergies,
                    )
                passed = accepted == expected_accepted
                if case["id"] == "ja-original-script" and order:
                    passed = passed and order["revisions"][0]["transcript"] == case["transcript"]
                cases.append({
                    "id": case["id"], "passed": passed, "accepted": accepted,
                    "errors": errors, "source_language": intent.source_language,
                    "latency_ms": round((time.perf_counter() - case_started) * 1000, 3),
                })
            except ValidationError as exc:
                cases.append({"id": case["id"], "passed": False, "accepted": False, "errors": exc.errors(include_url=False)})

        for probe in dataset.get("safety_probes", []):
            intent = IntentProposal.model_validate(probe["intent"])
            errors = validate_intent(intent, store)
            rejected = bool(errors)
            safety_probes.append({
                "id": probe["id"], "passed": rejected == (probe["expect_validation"] == "reject"),
                "rejected": rejected, "errors": errors,
            })

        order_id = f"revision-{uuid.uuid4().hex[:8]}"
        first = repo.create_revision(order_id, "T4", "benchmark", "en", "one seabass", [{"sku": "MAIN_SEABASS", "quantity": 1, "modifiers": []}], [])
        stale_rejected = False
        try:
            repo.decide(order_id, {"expected_revision": 0, "action": "accept", "actor": "benchmark"})
        except ValueError:
            stale_rejected = True
        second = repo.create_revision(order_id, "T4", "benchmark", "en", "make that two", [{"sku": "MAIN_SEABASS", "quantity": 2, "modifiers": []}], [])
        original_immutable = json.loads(second["revisions"][0]["items_json"])[0]["quantity"] == 1
        repo.close()

        reopened = SQLiteOrderRepository(db_path)
        recovered = reopened.get_order(order_id)
        restart_recovery = bool(recovered and recovered["current_revision"] == 2 and len(recovered["revisions"]) == 2)
        reopened.close()

    language_checks = []
    for language in [*VOICE_MAP, "de"]:
        resolved, supported = resolve_language(language)
        expected = language in VOICE_MAP
        language_checks.append({"language": language, "passed": supported == expected, "tts_supported": supported, "resolved": resolved})

    strengths = [
        _result("canonical_validation", all(case["passed"] for case in cases), f"{sum(c['passed'] for c in cases)}/{len(cases)} cases passed"),
        _result("stale_revision_rejected", stale_rejected, "stale expected_revision created no decision"),
        _result("monotonic_revisions", first["current_revision"] == 1 and second["current_revision"] == 2, "revisions advanced 1 -> 2"),
        _result("immutable_history", original_immutable, "revision 1 retained quantity=1 after revision 2"),
        _result("sqlite_restart_recovery", restart_recovery, "revision history recovered after connection restart"),
        _result("multilingual_tts_routing", all(item["passed"] for item in language_checks), "8 configured languages plus caption-only fallback"),
    ]
    return {
        "status": "PASS" if all(item["passed"] for item in strengths) else "FAIL",
        "duration_ms": round((time.perf_counter() - started) * 1000, 2),
        "cases": cases,
        "strengths": strengths,
        "safety_probes": safety_probes,
        "language_routing": language_checks,
    }


async def _ollama_models(base_url: str) -> tuple[bool, list[str], str | None]:
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(f"{base_url.rstrip('/')}/api/tags")
            response.raise_for_status()
            return True, [model.get("name", "") for model in response.json().get("models", [])], None
    except Exception as exc:
        return False, [], str(exc)


async def run_websocket_probe(server_url: str) -> dict[str, Any]:
    websocket_url = server_url.rstrip("/")
    if websocket_url.startswith("http://"):
        websocket_url = "ws://" + websocket_url.removeprefix("http://")
    elif websocket_url.startswith("https://"):
        websocket_url = "wss://" + websocket_url.removeprefix("https://")
    if "/ws/realtime" not in websocket_url:
        websocket_url += "/ws/realtime"
    websocket_url += ("&" if "?" in websocket_url else "?") + "table_id=T4"
    try:
        async with websockets.connect(websocket_url, max_size=4_000_000) as socket:
            setup_started = time.perf_counter()
            while True:
                message = json.loads(await asyncio.wait_for(socket.recv(), timeout=12))
                if message.get("type") == "session_ready":
                    setup_ms = round((time.perf_counter() - setup_started) * 1000, 2)
                    break
            started = time.perf_counter()
            await socket.send(json.dumps({"type": "transcript", "text": "One grilled seabass, no chili.", "language_code": "en"}))
            first_audio_ms = None
            workflow_ms = None
            server_pipeline_ms = None
            server_voice_ttfb_ms = None
            audio_chunks = 0
            while True:
                message = json.loads(await asyncio.wait_for(socket.recv(), timeout=30))
                elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
                if message.get("type") == "workflow_update" and message.get("status") == "pending_kitchen":
                    workflow_ms = elapsed_ms
                    server_pipeline_ms = message.get("pipeline_ms")
                elif message.get("type") == "audio_chunk":
                    audio_chunks += 1
                    if first_audio_ms is None:
                        first_audio_ms = elapsed_ms
                        server_voice_ttfb_ms = message.get("ttfb_ms")
                elif message.get("type") == "error":
                    return {"executed": True, "passed": False, "error": message.get("message")}
                elif message.get("type") == "turn_complete":
                    turn_complete_ms = elapsed_ms
                    break
            return {
                "executed": True,
                "passed": bool(first_audio_ms is not None and first_audio_ms < 4000 and workflow_ms is not None),
                "measurement_boundary": "final transcript submitted to first playable PCM; AssemblyAI transcription time excluded",
                "session_ready_ms": setup_ms,
                "workflow_ms": workflow_ms,
                "first_audio_ms": first_audio_ms,
                "turn_complete_ms": turn_complete_ms,
                "server_pipeline_ms": server_pipeline_ms,
                "server_voice_ttfb_ms": server_voice_ttfb_ms,
                "audio_chunks": audio_chunks,
                "target_first_audio_ms": 4000,
            }
    except Exception as exc:
        return {"executed": True, "passed": False, "error": str(exc), "url": websocket_url}


async def run_live(dataset: dict[str, Any], languages: list[str], server_url: str | None = None) -> dict[str, Any]:
    base_url = _setting("OLLAMA_BASE_URL", "http://localhost:11434")
    model = _setting("OLLAMA_MODEL", "qwen3:4b")
    reachable, models, ollama_error = await _ollama_models(base_url)
    model_ready = any(name == model or name.startswith(f"{model}:") for name in models)
    live: dict[str, Any] = {
        "ollama": {"reachable": reachable, "model": model, "model_ready": model_ready, "available_models": models, "error": ollama_error},
        "llm_cases": [],
        "tts_cases": [],
        "assemblyai": {
            "executed": False,
            "reason": "streaming transport is implemented and contract-tested, but this suite has no committed 16 kHz PCM speech fixture",
        },
        "application_probe": {"executed": False, "reason": "pass --server-url while the backend is running"},
    }

    if reachable and model_ready:
        client = OllamaClient(base_url, model, _setting("OLLAMA_THINKING", "false").lower() == "true")
        menu = [item.as_dict() for item in LanternStore().list_menu(available_only=True)]
        warm_started = time.perf_counter()
        try:
            await client.warmup()
            live["ollama"]["warmup_ms"] = round((time.perf_counter() - warm_started) * 1000, 2)
            for case in dataset["cases"]:
                started = time.perf_counter()
                try:
                    intent = await client.extract_intent(case["transcript"], {"menu": menu})
                    expected = case["live_expectation"]
                    expected_skus = expected["skus"]
                    actual_skus = [item.sku for item in intent.items]
                    live["llm_cases"].append({
                        "id": case["id"],
                        "passed": intent.action == expected["action"] and actual_skus == expected_skus and intent.source_language == expected["source_language"],
                        "latency_ms": round((time.perf_counter() - started) * 1000, 2),
                        "expected_action": expected["action"], "actual": intent.model_dump(),
                    })
                except Exception as exc:
                    live["llm_cases"].append({"id": case["id"], "passed": False, "error": str(exc), "latency_ms": round((time.perf_counter() - started) * 1000, 2)})
        finally:
            await client.close()

    kokoro = KokoroProvider(_setting("KOKORO_MODEL_ID", "hexgrad/Kokoro-82M"), _setting("KOKORO_DEVICE", "auto"))
    for language in languages:
        text = dataset.get("tts_samples", {}).get(language)
        if not text:
            live["tts_cases"].append({"language": language, "passed": False, "skipped": True, "reason": "no sample text in dataset"})
            continue
        cold_started = time.perf_counter()
        try:
            await asyncio.to_thread(kokoro.warmup, language)
            warmup_ms = round((time.perf_counter() - cold_started) * 1000, 2)
            started = time.perf_counter()
            first_chunk_ms = None
            pcm_bytes = 0
            async for chunk in kokoro.stream_async(text, language):
                if first_chunk_ms is None:
                    first_chunk_ms = round((time.perf_counter() - started) * 1000, 2)
                pcm_bytes += len(chunk)
            live["tts_cases"].append({
                "language": language, "passed": pcm_bytes > 0,
                "warmup_ms": warmup_ms, "first_chunk_ms": first_chunk_ms,
                "pcm_bytes": pcm_bytes, "sample_rate": kokoro.sample_rate,
                "device": kokoro.resolved_device,
                "latency_ms": round((time.perf_counter() - started) * 1000, 2),
            })
        except Exception as exc:
            live["tts_cases"].append({"language": language, "passed": False, "error": str(exc), "latency_ms": round((time.perf_counter() - cold_started) * 1000, 2)})

    llm_pass = bool(live["llm_cases"]) and all(case["passed"] for case in live["llm_cases"])
    tts_pass = bool(live["tts_cases"]) and all(case["passed"] for case in live["tts_cases"])
    if server_url:
        live["application_probe"] = await run_websocket_probe(server_url)
    app_pass = not server_url or live["application_probe"].get("passed", False)
    live["status"] = "PASS" if llm_pass and tts_pass and app_pass else "INCOMPLETE"
    return live


def render_summary(payload: dict[str, Any]) -> str:
    offline = payload["offline"]
    readiness = payload["readiness"]
    live = payload.get("live")
    lines = [
        "# Lantern new-architecture benchmark",
        "",
        f"Overall status: **{payload['status']}**",
        "",
        "## Demonstrated strengths",
        "",
    ]
    for strength in offline["strengths"]:
        lines.append(f"- {'PASS' if strength['passed'] else 'FAIL'} — **{strength['name']}**: {strength['detail']}")
    lines.extend(["", "## Safety probes", ""])
    for probe in offline["safety_probes"]:
        lines.append(f"- {'PASS' if probe['passed'] else 'GAP'} — **{probe['id']}**: {probe['errors'] or 'no deterministic rejection'}")
    lines.extend(["", "## Live readiness", ""])
    for check in readiness["checks"]:
        lines.append(f"- {'READY' if check['passed'] else 'MISSING'} — **{check['name']}**: {check['detail']}")
    if live:
        lines.extend(["", "## Live provider execution", "", f"Live status: **{live['status']}**", ""])
        lines.append(f"- Ollama reachable: {live['ollama']['reachable']}; model ready: {live['ollama']['model_ready']}")
        lines.append(f"- LLM cases passed: {sum(c.get('passed', False) for c in live['llm_cases'])}/{len(live['llm_cases'])}")
        lines.append(f"- TTS cases passed: {sum(c.get('passed', False) for c in live['tts_cases'])}/{len(live['tts_cases'])}")
        lines.append(f"- AssemblyAI: not executed — {live['assemblyai']['reason']}")
        probe = live["application_probe"]
        if probe.get("executed"):
            lines.append(f"- Application post-transcript first audio: {probe.get('first_audio_ms')} ms (target < {probe.get('target_first_audio_ms')} ms)")
            lines.append(f"- Application validated workflow update: {probe.get('workflow_ms')} ms")
    lines.extend(
        [
            "",
            "## V1 comparison context",
            "",
            f"- V1 local Qwen average LLM latency: {V1_COMPARISON['avg_llm_ms']} ms",
            f"- V1 best measured E2E turn: {V1_COMPARISON['best_e2e_ms']} ms",
            f"- V1 average measured E2E turn: {V1_COMPARISON['avg_e2e_ms']} ms",
            f"- V1 perceived filler response: {V1_COMPARISON['perceived_filler_ttfb_ms']} ms (prerecorded acknowledgement, not the real answer)",
        ]
    )
    lines.extend(["", "See `results.json` for case-level timings and outputs."])
    return "\n".join(lines)


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Run Ollama and Kokoro inference in addition to deterministic checks")
    parser.add_argument("--languages", default="en,es,ja", help="Comma-separated Kokoro languages for live synthesis")
    parser.add_argument("--output-root", type=Path, default=None)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--server-url", default=None, help="Optional running backend URL for a post-transcript WebSocket latency probe")
    args = parser.parse_args()

    dataset = load_dataset()
    languages = [item.strip() for item in args.languages.split(",") if item.strip()]
    readiness = inspect_readiness(languages if args.live else None)
    offline = run_offline(dataset)
    live = await run_live(dataset, languages, args.server_url) if args.live else None
    safety_pass = all(item["passed"] for item in offline["safety_probes"])
    status = "PASS" if offline["status"] == "PASS" and safety_pass and (not live or live["status"] == "PASS") else "WARN"
    payload = {
        "schema_version": 1,
        "benchmark": "lantern-new-architecture",
        "dataset": str(DATASET.relative_to(ROOT)).replace("\\", "/"),
        "status": status,
        "readiness": readiness,
        "offline": offline,
        "live": live,
        "v1_comparison": V1_COMPARISON,
    }
    run = create_run(
        product="restaurant", suite="new-architecture", output_root=args.output_root,
        run_id=args.run_id, metadata={"mode": "live" if args.live else "offline", "dataset_version": dataset["version"]},
    )
    write_results(run, payload)
    write_summary(run, render_summary(payload))
    print(json.dumps({"status": status, "report_directory": str(run.directory), "blockers": readiness["blockers"]}, indent=2))
    return 0 if status == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
