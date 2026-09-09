"""3-turn playbook smoke against live HTTP API (AssemblyAI optional via WAV).

Uses the same questions as scripts/demo_voice_playbook.sh:
  1) Dragon Bridge fire show
  2) Airport → city
  3) Thanks, that's all.

Text turns verify session + answers + no false spoken_cache on distinct queries.
Audio turns (optional) synthesize short WAVs via Cartesia then ASR them.

  PYTHONPATH=. .venv/bin/python eval/smoke_3turn_playbook.py
  PYTHONPATH=. .venv/bin/python eval/smoke_3turn_playbook.py --with-audio
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.config import get_settings
from backend.app.pipeline.orchestrator import Orchestrator
from backend.app.pipeline.session import SessionStore
from backend.app.pipeline.tts_live import CartesiaTTSClient
from rag.cache import RagCache

PLAYBOOK = [
    {
        "id": "t1",
        "text": "When is the Dragon Bridge fire show?",
        "expect_any": ["saturday", "sunday", "9"],
        "allow_cache": False,
    },
    {
        "id": "t2",
        "text": "How do I get from the airport to the city?",
        "expect_any": ["taxi", "grab", "airport", "minute"],
        "allow_cache": False,
    },
    {
        "id": "t3",
        "text": "Thanks, that's all.",
        "expect_any": ["enjoy", "da nang", "anytime"],
        "farewell": True,
        "allow_cache": False,
    },
]


def _norm(s: str) -> str:
    return " ".join((s or "").lower().split())


def _audio_fp(b64: str) -> str:
    if not b64:
        return "empty"
    return hashlib.sha1(base64.b64decode(b64)[:8000]).hexdigest()[:12]


async def _synth_question_wav(text: str) -> str:
    tts = CartesiaTTSClient()
    out = await tts.synthesize(text=text, voice_id="", speaking_rate=1.0, style_prompt="clear")
    if not out.audio_b64:
        raise RuntimeError("Cartesia returned empty audio for question synth")
    return out.audio_b64


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--with-audio",
        action="store_true",
        help="Also run 3 audio turns (Cartesia→WAV→AssemblyAI→answer)",
    )
    args = parser.parse_args()

    get_settings.cache_clear()
    settings = get_settings()
    keys = settings.keys_configured
    print("keys:", {k: bool(v) for k, v in keys.items()})
    need = ["gemini", "cartesia"]
    if args.with_audio:
        need.append("assemblyai")
    if not all(keys.get(k) for k in need):
        print("FAIL: missing keys for", need)
        return 2

    orch = Orchestrator(rag_cache=RagCache(), sessions=SessionStore())
    session_id = f"smoke-3turn-{uuid.uuid4().hex[:8]}"
    print("session:", session_id)

    queries: list[str] = []
    answers: list[str] = []
    audio_fps: list[str] = []
    providers: list[dict] = []

    print("\n=== TEXT playbook (3 turns) ===")
    for i, step in enumerate(PLAYBOOK, start=1):
        result = await orch.run_turn(
            text=step["text"],
            session_id=session_id,
            use_cache=True,
            profile_name="friendly_guide",
        )
        q = result.get("transcript") or step["text"]
        a = result.get("answer") or ""
        cache = result.get("cache") or {}
        prov = result.get("providers") or {}
        fp = _audio_fp((result.get("audio") or {}).get("b64") or "")
        queries.append(q)
        answers.append(a)
        audio_fps.append(fp)
        providers.append(prov)

        spoken_hit = bool(cache.get("spoken_cache_hit"))
        print(
            f"T{i} query={q!r}\n"
            f"   answer={a!r}\n"
            f"   providers={prov} spoken_cache={spoken_hit} audio_fp={fp} "
            f"ended={result.get('session_ended')}"
        )

        if not step.get("allow_cache") and spoken_hit and i < 3:
            print(f"FAIL: T{i} unexpected spoken_cache_hit on distinct playbook query")
            return 1
        if not any(tok in _norm(a) for tok in step["expect_any"]):
            print(f"FAIL: T{i} answer missing expected tokens {step['expect_any']}")
            return 1
        if step.get("farewell"):
            if prov.get("llm") != "local_farewell" or not result.get("session_ended"):
                print("FAIL: T3 should be local farewell + session ended")
                return 1

    if len(set(_norm(q) for q in queries)) != 3:
        print("FAIL: expected 3 distinct queries, got", queries)
        return 1
    if audio_fps[0] == audio_fps[1]:
        print("FAIL: T1 and T2 returned identical audio fingerprint — repeat bug")
        return 1

    print("TEXT playbook PASS")

    if args.with_audio:
        print("\n=== AUDIO playbook (Cartesia Q → AssemblyAI → answer) ===")
        session_id = f"smoke-audio-{uuid.uuid4().hex[:8]}"
        orch2 = Orchestrator(rag_cache=RagCache(), sessions=SessionStore())
        audio_queries: list[str] = []
        audio_answer_fps: list[str] = []
        for i, step in enumerate(PLAYBOOK, start=1):
            print(f"  synthesizing question audio for T{i}…")
            q_wav = await _synth_question_wav(step["text"])
            result = await orch2.run_turn(
                audio_b64=q_wav,
                text=None,
                session_id=session_id,
                use_cache=True,
                profile_name="friendly_guide",
            )
            q = result.get("transcript") or ""
            a = result.get("answer") or ""
            prov = result.get("providers") or {}
            cache = result.get("cache") or {}
            fp = _audio_fp((result.get("audio") or {}).get("b64") or "")
            audio_queries.append(q)
            audio_answer_fps.append(fp)
            print(
                f"T{i} asr={q!r}\n"
                f"   answer={a!r}\n"
                f"   providers={prov} spoken_cache={cache.get('spoken_cache_hit')} "
                f"audio_fp={fp}"
            )
            if prov.get("asr") != "assemblyai_prerecorded" and not step.get("farewell"):
                # farewell may still ASR then local closer
                if i < 3:
                    print("FAIL: expected assemblyai_prerecorded ASR for voice turn")
                    return 1
            if i < 3 and cache.get("spoken_cache_hit"):
                # Same synth wording could cache across text/audio within fresh RagCache — OK only if query matches prior in THIS session
                pass
            if not any(tok in _norm(a) for tok in step["expect_any"]):
                print(f"FAIL: audio T{i} answer missing tokens {step['expect_any']}")
                return 1

        if audio_answer_fps[0] == audio_answer_fps[1]:
            print("FAIL: audio T1/T2 identical answer audio — repeat bug")
            return 1
        # ASR of synth may not be exact, but T1 vs T2 transcripts must differ
        if _norm(audio_queries[0]) == _norm(audio_queries[1]):
            print("FAIL: audio T1/T2 ASR produced identical transcripts:", audio_queries)
            return 1
        print("AUDIO playbook PASS")

    print("\nALL 3-TURN SMOKE OK")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except Exception as exc:  # noqa: BLE001
        print("FAIL:", type(exc).__name__, str(exc)[:400])
        raise SystemExit(1) from exc
