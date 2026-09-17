"""Server startup: probing and warming Ollama never blocks or breaks the app, and /health tells the truth."""

from __future__ import annotations

import asyncio
import os
import time
from unittest import mock

from fastapi.testclient import TestClient

from backend.app import main
from backend.app.config import get_settings
from backend.app.pipeline import llm_ollama
from backend.app.pipeline.llm_ollama import OllamaProbe
from backend.app.pipeline.waiter_agent import SYSTEM_INSTRUCTION, get_waiter_llm_runtime
from tests.support import IsolatedTestCase, run_with_timeout


class StartupTest(IsolatedTestCase):
    def setUp(self) -> None:
        super().setUp()
        # Keep Chroma out of these tests: no index files, no telemetry.
        for patcher in (
            mock.patch.object(main, "warmup_retriever", side_effect=RuntimeError("no RAG index in tests")),
            mock.patch.object(main, "get_collection", side_effect=RuntimeError("no Chroma in tests")),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def enable_warmup(self) -> None:
        patcher = mock.patch.dict(os.environ, {"OLLAMA_WARMUP": "true"})
        patcher.start()
        self.addCleanup(patcher.stop)
        get_settings.cache_clear()

    def probe_returns(self, probe: OllamaProbe) -> None:
        patcher = mock.patch.object(llm_ollama, "probe_ollama", mock.AsyncMock(return_value=probe))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_ollama_down_still_starts_and_health_says_so(self) -> None:
        self.probe_returns(OllamaProbe(False, False, "Ollama not reachable at http://localhost:11434", time.time()))

        def conversation() -> dict:
            with TestClient(main.app) as client:
                return client.get("/health").json()

        health = run_with_timeout(conversation, 60)
        self.assertEqual(health["status"], "ok")
        self.assertEqual(health["llm"]["provider_requested"], "ollama")
        self.assertEqual(health["llm"]["provider_active"], "rulebased")
        self.assertIs(health["llm"]["ollama"]["reachable"], False)
        self.assertIs(health["llm"]["ollama"]["warm"], False)

    def test_warm_up_runs_in_the_background_and_is_cancelled_on_shutdown(self) -> None:
        self.enable_warmup()
        self.probe_returns(OllamaProbe(True, True, "ok", time.time()))
        calls: list[tuple] = []

        async def slow_warm_up(runnable, system, **kwargs):
            calls.append((runnable, system))
            await asyncio.sleep(3600)

        def conversation() -> dict:
            with mock.patch.object(llm_ollama, "warm_up", slow_warm_up):
                with TestClient(main.app) as client:
                    return client.get("/health").json()

        started = time.perf_counter()
        health = run_with_timeout(conversation, 60)
        self.assertLess(time.perf_counter() - started, 30, "startup waited for the warm-up")
        self.assertEqual(health["llm"]["provider_active"], "ollama_langchain_tools")
        self.assertIs(health["llm"]["ollama"]["warm"], False)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][1], SYSTEM_INSTRUCTION)
        self.assertIsNone(get_waiter_llm_runtime().warmup_task)

    def test_finished_warm_up_is_reported(self) -> None:
        self.enable_warmup()
        self.probe_returns(OllamaProbe(True, True, "ok", time.time()))

        async def quick_warm_up(runnable, system, **kwargs):
            return {"warmup_ms": 42.0, "load_ms": 30.0}

        def conversation() -> dict:
            with mock.patch.object(llm_ollama, "warm_up", quick_warm_up):
                with TestClient(main.app) as client:
                    for _ in range(40):
                        health = client.get("/health").json()
                        if health["llm"]["ollama"]["warm"]:
                            return health
                        time.sleep(0.05)
                    return health

        health = run_with_timeout(conversation, 60)
        self.assertIs(health["llm"]["ollama"]["warm"], True)
        self.assertEqual(health["llm"]["ollama"]["warmup_ms"], 42.0)
