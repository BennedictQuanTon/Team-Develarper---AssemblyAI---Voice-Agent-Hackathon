"""Full-duplex real-time session controller coordinating AssemblyAI STT -> RAG -> Gemini -> Cartesia TTS."""

from __future__ import annotations

import asyncio
import base64
import logging
import time
import uuid
from typing import Any

from fastapi import WebSocket

from backend.app.config import Settings, get_settings
from backend.app.metrics.spans import MetricsWriter, TurnSpans, new_turn_id
from backend.app.pipeline.asr_stream import AssemblyAIRealtimeStream, StubRealtimeStream
from backend.app.pipeline.filler import CLOSING_TEXT, is_farewell
from backend.app.pipeline.llm import StubLLMClient
from backend.app.pipeline.llm_live import GeminiLLMClient
from backend.app.pipeline.profiles import get_voice_profile
from backend.app.pipeline.session import MAX_TURNS, SessionStore
from backend.app.pipeline.tts_stream import CartesiaStreamingTTS, StubStreamingTTS
from backend.app.pipeline.waiter_agent import build_waiter_agent
from backend.app.domain.lantern import get_lantern_store
from backend.app.domain.waiter import WaiterSession
from rag.cache import RagCache
from rag.retrieve import DEFAULT_TOP_K, hybrid_retrieve

logger = logging.getLogger(__name__)


class RealtimeSessionController:
    """Manages an active bi-directional WebSocket session for one user."""

    def __init__(
        self,
        websocket: WebSocket,
        *,
        settings: Settings | None = None,
        rag_cache: RagCache | None = None,
        sessions: SessionStore | None = None,
        profile_name: str | None = None,
        session_id: str | None = None,
    ) -> None:
        self.ws = websocket
        self.settings = settings or get_settings()
        self.rag_cache = rag_cache or RagCache()
        self.sessions = sessions or SessionStore()
        self.session_id = session_id or f"rt-{uuid.uuid4().hex[:8]}"
        self.profile = get_voice_profile(profile_name)

        keys = self.settings.keys_configured
        self.has_aai = keys["assemblyai"]
        self.has_gemini = keys["gemini"]
        self.has_cartesia = keys["cartesia"]

        # Component clients
        self.llm = GeminiLLMClient() if self.has_gemini else StubLLMClient()
        self.tts = (
            CartesiaStreamingTTS(sample_rate=16000)
            if self.has_cartesia
            else StubStreamingTTS(sample_rate=16000)
        )

        # Waiter agent (voice-ordering mode) with its own per-session basket
        self.waiter_agent = build_waiter_agent()
        self.waiter_session = WaiterSession(get_lantern_store(), session_id=self.session_id)
        self.agent_mode = self.settings.agent_mode

        self.metrics = MetricsWriter(self.settings.metrics_dir / "turns.jsonl")

        self.is_agent_speaking = False
        self._speech_frames_while_speaking = 0
        self.current_generation_task: asyncio.Task | None = None
        self.cancel_event = asyncio.Event()
        self._last_interim_text = ""
        self._last_interim_time = 0.0
        self._watchdog_task: asyncio.Task | None = None

        # AssemblyAI stream
        if self.has_aai:
            self.asr = AssemblyAIRealtimeStream(
                sample_rate=16000,
                on_speech_started=self._on_user_speech_started,
                on_turn=self._on_asr_turn,
                on_error=self._on_asr_error,
            )
        else:
            self.asr = StubRealtimeStream(
                on_speech_started=self._on_user_speech_started,
                on_turn=self._on_asr_turn,
                on_error=self._on_asr_error,
            )

    @staticmethod
    def _compute_rms(pcm_data: bytes) -> float:
        import array, math
        if len(pcm_data) < 2:
            return 0.0
        trimmed = pcm_data[: len(pcm_data) - (len(pcm_data) % 2)]
        samples = array.array("h")
        samples.frombytes(trimmed)
        if not samples:
            return 0.0
        return math.sqrt(sum(s * s for s in samples) / len(samples))

    async def start(self) -> None:
        await self.asr.connect()
        self._watchdog_task = asyncio.create_task(self._silence_watchdog())
        await self._send_json(
            {
                "type": "session_ready",
                "session_id": self.session_id,
                "profile": self.profile["name"],
                "has_assemblyai": self.has_aai,
                "has_cartesia": self.has_cartesia,
            }
        )

    async def _silence_watchdog(self) -> None:
        """Watchdog to force endpoint if user spoke a sentence and stopped speaking for > 900ms."""
        while True:
            try:
                await asyncio.sleep(0.15)
                if (
                    not self.is_agent_speaking
                    and self._last_interim_text
                    and (time.perf_counter() - self._last_interim_time) > 0.9
                ):
                    txt = self._last_interim_text
                    self._last_interim_text = ""
                    logger.info("⏱️ Watchdog auto-endpointing after silence on text: %s", txt)
                    if hasattr(self.asr, "force_endpoint"):
                        await self.asr.force_endpoint()
            except asyncio.CancelledError:
                break
            except Exception as exc:  # noqa: BLE001
                logger.warning("Watchdog error: %s", exc)

    async def handle_pcm_audio(self, pcm_data: bytes) -> None:
        """Receive 16kHz s16le PCM audio from user's microphone and stream to AssemblyAI."""
        if self.is_agent_speaking and len(pcm_data) >= 320:
            rms = self._compute_rms(pcm_data)
            if rms > 650.0:
                self._speech_frames_while_speaking += 1
                if self._speech_frames_while_speaking >= 2:
                    logger.info("⚡ Instant Energy VAD Barge-In (RMS=%.1f)", rms)
                    await self._trigger_barge_in("energy_vad")
            else:
                self._speech_frames_while_speaking = 0
        else:
            self._speech_frames_while_speaking = 0

        await self.asr.send_audio(pcm_data)

    async def handle_text_command(self, payload: dict[str, Any]) -> None:
        """Handle control messages from client UI."""
        cmd = payload.get("command") or payload.get("type")
        if cmd == "interrupt" or cmd == "barge_in":
            await self._trigger_barge_in("client_button")
        elif cmd == "endpoint" or cmd == "force_endpoint":
            self._last_interim_text = ""
            if hasattr(self.asr, "force_endpoint"):
                await self.asr.force_endpoint()
        elif cmd == "reset":
            self._last_interim_text = ""
            self.sessions.reset(self.session_id)
            await self._send_json({"type": "session_reset", "session_id": self.session_id})

    async def close(self) -> None:
        if self._watchdog_task and not self._watchdog_task.done():
            self._watchdog_task.cancel()
        await self._cancel_current_turn()
        await self.asr.close()

    async def _on_user_speech_started(self) -> None:
        """Invoked immediately when AssemblyAI VAD detects user speaking (BARGE-IN!)."""
        if self.is_agent_speaking:
            await self._trigger_barge_in("assemblyai_vad")
        else:
            await self._send_json({"type": "speech_started"})

    async def _trigger_barge_in(self, reason: str) -> None:
        if not self.is_agent_speaking:
            return
        logger.info("⚡ BARGE-IN TRIGGERED (%s) on session %s", reason, self.session_id)
        self.is_agent_speaking = False
        self._speech_frames_while_speaking = 0
        await self._cancel_current_turn()
        await self._send_json({"type": "barge_in", "reason": reason})

    async def _cancel_current_turn(self) -> None:
        self.cancel_event.set()
        if self.current_generation_task and not self.current_generation_task.done():
            self.current_generation_task.cancel()
            try:
                await self.current_generation_task
            except asyncio.CancelledError:
                pass
        self.current_generation_task = None

    async def _on_asr_turn(self, transcript: str, is_final: bool) -> None:
        if not transcript:
            return

        if self.is_agent_speaking:
            logger.info("⚡ ASR Turn Barge-In on transcript: %s", transcript)
            await self._trigger_barge_in("asr_turn")

        if not is_final:
            self._last_interim_text = transcript
            self._last_interim_time = time.perf_counter()
            # Live interim captioning to user
            await self._send_json({"type": "interim_transcript", "text": transcript})
            return

        # Final turn received!
        self._last_interim_text = ""
        await self._send_json({"type": "final_transcript", "text": transcript})
        # Schedule turn execution
        await self._cancel_current_turn()
        self.cancel_event = asyncio.Event()
        self.current_generation_task = asyncio.create_task(
            self._execute_turn_pipeline(transcript, self.cancel_event)
        )

    async def _execute_turn_pipeline(
        self, query: str, cancel_ev: asyncio.Event
    ) -> None:
        t_start = time.perf_counter()
        session = self.sessions.get(self.session_id)
        turn_id = new_turn_id()

        if session.ended:
            await self._send_json(
                {
                    "type": "turn_complete",
                    "answer": "This conversation has ended. Start a new one anytime.",
                    "session_ended": True,
                }
            )
            return

        # Waiter mode: run the voice waiter (RAG-independent ordering path)
        if self.agent_mode == "waiter":
            await self._execute_waiter_turn(query, cancel_ev)
            return

        # 1) Farewell Fast-Path (Zero API cost & Instant closing)
        if is_farewell(query):
            self.sessions.end(self.session_id)
            await self._send_json(
                {
                    "type": "final_answer",
                    "answer": CLOSING_TEXT,
                    "session_ended": True,
                }
            )
            # Synthesize short closing speech
            async def _closing_stream():
                yield CLOSING_TEXT

            self.is_agent_speaking = True
            async for pcm_chunk in self.tts.stream_utterance(
                _closing_stream(),
                context_id=turn_id,
                cancel_event=cancel_ev,
            ):
                if cancel_ev.is_set():
                    break
                await self._send_json(
                    {
                        "type": "audio_chunk",
                        "pcm_b64": base64.b64encode(pcm_chunk).decode("ascii"),
                    }
                )
            self.is_agent_speaking = False
            await self._send_json({"type": "turn_complete", "answer": CLOSING_TEXT, "ended": True})
            return

        # 2) Hybrid RAG (~50ms)
        t_rag0 = time.perf_counter()
        retrieval = await asyncio.to_thread(
            hybrid_retrieve,
            query,
            chroma_dir=self.settings.chroma_persist_dir,
            bm25_path=self.settings.bm25_index_path,
            top_k=DEFAULT_TOP_K,
            cache=self.rag_cache,
            use_cache=True,
        )
        rag_ms = round((time.perf_counter() - t_rag0) * 1000, 2)
        await self._send_json(
            {
                "type": "rag_done",
                "rag_ms": rag_ms,
                "chunk_ids": [c["chunk_id"] for c in retrieval["chunks"]],
            }
        )

        if cancel_ev.is_set():
            return

        # 3) LLM Stream -> Cartesia Stream
        self.is_agent_speaking = True
        llm_accumulated: list[str] = []
        is_last = (session.turn_count + 1) >= MAX_TURNS

        # Bridge LLM streaming tokens to TTS
        token_queue: asyncio.Queue[str | None] = asyncio.Queue()

        async def _llm_producer() -> None:
            try:
                async for token in self.llm.stream(
                    user_text=query,
                    context_chunks=retrieval["chunks"],
                    style_prompt=self.profile["style_prompt"],
                    answer_template=self.profile["answer_template"],
                    history=session.history[-2:],
                    last_turn=is_last,
                ):
                    if cancel_ev.is_set():
                        break
                    llm_accumulated.append(token)
                    await token_queue.put(token)
            except Exception as exc:  # noqa: BLE001
                logger.warning("LLM stream error: %s", exc)
            finally:
                await token_queue.put(None)

        producer_task = asyncio.create_task(_llm_producer())

        async def _token_consumer():
            while True:
                tok = await token_queue.get()
                if tok is None:
                    break
                yield tok

        first_chunk_sent = False
        t_first_audio = 0.0

        try:
            async for pcm_chunk in self.tts.stream_utterance(
                _token_consumer(),
                context_id=turn_id,
                voice_id=self.profile["cartesia_voice_id"],
                speaking_rate=self.profile["speaking_rate"],
                cancel_event=cancel_ev,
            ):
                if cancel_ev.is_set():
                    break
                if not first_chunk_sent:
                    first_chunk_sent = True
                    t_first_audio = round((time.perf_counter() - t_start) * 1000, 2)
                    logger.info("⚡ VOICE-TO-VOICE TTFB: %.1f ms", t_first_audio)

                await self._send_json(
                    {
                        "type": "audio_chunk",
                        "pcm_b64": base64.b64encode(pcm_chunk).decode("ascii"),
                        "ttfb_ms": t_first_audio,
                    }
                )
        finally:
            if not producer_task.done():
                producer_task.cancel()
                try:
                    await producer_task
                except asyncio.CancelledError:
                    pass

        self.is_agent_speaking = False

        if cancel_ev.is_set():
            logger.info("Turn %s was cancelled by user barge-in", turn_id)
            return

        final_answer = "".join(llm_accumulated).strip()
        e2e_turn_ms = round((time.perf_counter() - t_start) * 1000, 2)

        # Update Session
        session = self.sessions.append_turn(self.session_id, query, final_answer)

        # Record Spans
        spans = TurnSpans(
            turn_id=turn_id,
            session_id=self.session_id,
            query=query,
            transcript=query,
            answer=final_answer,
            profile=self.profile["name"],
            provider={
                "asr": "assemblyai_realtime" if self.has_aai else "stub_realtime",
                "llm": "gemini_stream" if self.has_gemini else "stub_llm",
                "tts": "cartesia_websocket" if self.has_cartesia else "stub_tts",
            },
            timings_ms={
                "rag_ms": rag_ms,
                "ttfb_ms": t_first_audio,
                "e2e_turn_ms": e2e_turn_ms,
            },
            cache={"retrieval_cache_hit": bool(retrieval.get("cache_hit"))},
            chunk_ids=[c["chunk_id"] for c in retrieval["chunks"]],
            phase=5,
        )
        self.metrics.write_turn(spans)

        await self._send_json(
            {
                "type": "turn_complete",
                "answer": final_answer,
                "ttfb_ms": t_first_audio,
                "e2e_turn_ms": e2e_turn_ms,
                "session_ended": session.ended,
                "turn_count": session.turn_count,
            }
        )

    async def _on_asr_error(self, error_msg: str) -> None:
        await self._send_json({"type": "error", "message": error_msg})

    async def _execute_waiter_turn(self, query: str, cancel_ev: asyncio.Event) -> None:
        """Voice-waiter turn: Gemini tool loop over deterministic menu/floor state,
        then stream the spoken reply through Cartesia with barge-in support."""
        t_start = time.perf_counter()
        session = self.sessions.get(self.session_id)
        turn_id = new_turn_id()

        # Run the agent (may take a few hundred ms; network-bound)
        try:
            result = await self.waiter_agent.respond(self.waiter_session, query)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Waiter agent error: %s", exc)
            result = {
                "reply": "Sorry, I didn't catch that. Could you say it again?",
                "tool_calls": [],
                "basket": self.waiter_session.snapshot(),
            }

        reply = (result.get("reply") or "").strip()
        tool_calls = result.get("tool_calls") or []

        # Emit live basket state for the ops view (after tools mutated state)
        await self._send_json(
            {
                "type": "basket_update",
                "basket": self.waiter_session.snapshot(),
                "tool_calls": [{"tool": t["tool"], "args": t["args"]} for t in tool_calls],
            }
        )

        if cancel_ev.is_set():
            return

        if not reply:
            reply = "One moment."

        # Stream reply -> TTS with barge-in
        self.is_agent_speaking = True
        first_chunk_sent = False
        t_first_audio = 0.0

        async def _reply_stream():
            for word in reply.split(" "):
                yield word + " "

        try:
            async for pcm_chunk in self.tts.stream_utterance(
                _reply_stream(),
                context_id=turn_id,
                voice_id=self.profile["cartesia_voice_id"],
                speaking_rate=self.profile["speaking_rate"],
                cancel_event=cancel_ev,
            ):
                if cancel_ev.is_set():
                    break
                if not first_chunk_sent:
                    first_chunk_sent = True
                    t_first_audio = round((time.perf_counter() - t_start) * 1000, 2)
                    logger.info("⚡ WAITER VOICE TTFB: %.1f ms", t_first_audio)
                await self._send_json(
                    {
                        "type": "audio_chunk",
                        "pcm_b64": base64.b64encode(pcm_chunk).decode("ascii"),
                        "ttfb_ms": t_first_audio,
                    }
                )
        finally:
            self.is_agent_speaking = False

        if cancel_ev.is_set():
            logger.info("Waiter turn %s cancelled by barge-in (basket preserved)", turn_id)
            return

        e2e_turn_ms = round((time.perf_counter() - t_start) * 1000, 2)
        session = self.sessions.append_turn(self.session_id, query, reply)

        spans = TurnSpans(
            turn_id=turn_id,
            session_id=self.session_id,
            query=query,
            transcript=query,
            answer=reply,
            profile=self.profile["name"],
            provider={
                "asr": "assemblyai_realtime" if self.has_aai else "stub_realtime",
                "llm": "gemini_tools" if self.has_gemini else "rulebased",
                "tts": "cartesia_websocket" if self.has_cartesia else "stub_tts",
            },
            timings_ms={
                "ttfb_ms": t_first_audio,
                "e2e_turn_ms": e2e_turn_ms,
            },
            cache={"n_tool_calls": len(tool_calls)},
            chunk_ids=[],
            phase=6,
        )
        self.metrics.write_turn(spans)

        await self._send_json(
            {
                "type": "turn_complete",
                "answer": reply,
                "ttfb_ms": t_first_audio,
                "e2e_turn_ms": e2e_turn_ms,
                "session_ended": session.ended,
                "turn_count": session.turn_count,
                "basket": self.waiter_session.snapshot(),
            }
        )

    async def _send_json(self, data: dict[str, Any]) -> None:
        try:
            await self.ws.send_json(data)
        except Exception:
            pass
