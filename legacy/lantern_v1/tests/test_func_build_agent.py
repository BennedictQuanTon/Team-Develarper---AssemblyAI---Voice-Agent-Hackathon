"""Which waiter agent a session gets: local Ollama by default, never a broken session."""

from __future__ import annotations

import os
import sys
import time
from unittest import mock

from backend.app.config import get_settings
from backend.app.pipeline import llm_ollama, waiter_agent
from backend.app.pipeline.llm_ollama import OllamaProbe
from backend.app.pipeline.waiter_agent import (
    OllamaWaiterAgent,
    RuleBasedWaiterAgent,
    WaiterAgent,
    build_waiter_agent,
    refresh_ollama_probe,
    requested_provider,
    waiter_llm_status,
)
from tests.support import IsolatedAsyncTestCase, IsolatedTestCase

HEALTHY = OllamaProbe(True, True, "ok", 0.0)
DOWN = OllamaProbe(False, False, "Ollama not reachable at http://localhost:11434 (ConnectError)", 0.0)
NOT_PULLED = OllamaProbe(True, False, "model 'qwen2.5:3b' is not pulled; run `ollama pull qwen2.5:3b`", 0.0)


def with_gemini_key(case: IsolatedTestCase) -> None:
    patcher = mock.patch.dict(os.environ, {"GEMINI_API_KEY": "dummy-key-for-tests"})
    patcher.start()
    case.addCleanup(patcher.stop)
    get_settings.cache_clear()


class BuildWaiterAgentTest(IsolatedTestCase):
    def test_never_probed_uses_ollama(self) -> None:
        self.assertIsInstance(build_waiter_agent(), OllamaWaiterAgent)

    def test_healthy_probe_uses_ollama(self) -> None:
        llm_ollama.set_last_probe(HEALTHY)
        self.assertIsInstance(build_waiter_agent(), OllamaWaiterAgent)

    def test_ollama_down_without_keys_falls_back_to_rules(self) -> None:
        llm_ollama.set_last_probe(DOWN)
        self.assertIsInstance(build_waiter_agent(), RuleBasedWaiterAgent)

    def test_ollama_down_with_a_gemini_key_falls_back_to_gemini(self) -> None:
        with_gemini_key(self)
        llm_ollama.set_last_probe(DOWN)
        self.assertIsInstance(build_waiter_agent(), WaiterAgent)

    def test_model_not_pulled_falls_back(self) -> None:
        llm_ollama.set_last_probe(NOT_PULLED)
        self.assertIsInstance(build_waiter_agent(), RuleBasedWaiterAgent)

    def test_langchain_ollama_missing_falls_back(self) -> None:
        with mock.patch.dict(sys.modules, {"langchain_ollama": None}):
            self.assertIsInstance(build_waiter_agent(), RuleBasedWaiterAgent)

    def test_gemini_requested(self) -> None:
        self.assertIsInstance(build_waiter_agent("gemini"), RuleBasedWaiterAgent)
        with_gemini_key(self)
        self.assertIsInstance(build_waiter_agent("gemini"), WaiterAgent)

    def test_unknown_provider_is_treated_as_ollama(self) -> None:
        self.assertEqual(requested_provider(provider="llama-magic"), "ollama")
        self.assertIsInstance(build_waiter_agent("llama-magic"), OllamaWaiterAgent)


class HealthStatusTest(IsolatedTestCase):
    def test_reports_requested_and_active_when_ollama_is_down(self) -> None:
        llm_ollama.set_last_probe(DOWN)
        status = waiter_llm_status()
        self.assertEqual(status["provider_requested"], "ollama")
        self.assertEqual(status["provider_active"], "rulebased")
        self.assertIs(status["ollama"]["reachable"], False)
        self.assertIn("not reachable", status["ollama"]["detail"])
        self.assertIs(status["template_replies"], False)

    def test_reports_the_local_model_when_healthy(self) -> None:
        llm_ollama.set_last_probe(HEALTHY)
        status = waiter_llm_status()
        self.assertEqual(status["provider_active"], "ollama_langchain_tools")
        self.assertEqual(status["model"], "qwen2.5:3b")
        self.assertIs(status["ollama"]["model_present"], True)


class RefreshProbeTest(IsolatedAsyncTestCase):
    async def test_a_failed_probe_is_retried_and_ollama_picked_up(self) -> None:
        llm_ollama.set_last_probe(OllamaProbe(False, False, "down", time.time() - 60))
        healthy_now = OllamaProbe(True, True, "ok", time.time())
        with mock.patch.object(llm_ollama, "probe_ollama", mock.AsyncMock(return_value=healthy_now)) as probe:
            result = await refresh_ollama_probe()
        probe.assert_awaited_once()
        self.assertTrue(result.healthy)
        self.assertIsInstance(build_waiter_agent(), OllamaWaiterAgent)

    async def test_a_recent_failure_is_not_retried_every_connection(self) -> None:
        llm_ollama.set_last_probe(OllamaProbe(False, False, "down", time.time()))
        with mock.patch.object(llm_ollama, "probe_ollama", mock.AsyncMock()) as probe:
            await refresh_ollama_probe()
        probe.assert_not_awaited()

    async def test_healthy_or_unprobed_servers_are_left_alone(self) -> None:
        with mock.patch.object(llm_ollama, "probe_ollama", mock.AsyncMock()) as probe:
            self.assertIsNone(await refresh_ollama_probe())
            llm_ollama.set_last_probe(OllamaProbe(True, True, "ok", time.time() - 3600))
            await refresh_ollama_probe()
        probe.assert_not_awaited()

    async def test_gemini_servers_never_probe_ollama(self) -> None:
        with mock.patch.dict(os.environ, {"LLM_PROVIDER": "gemini"}):
            get_settings.cache_clear()
            llm_ollama.set_last_probe(OllamaProbe(False, False, "down", time.time() - 60))
            with mock.patch.object(llm_ollama, "probe_ollama", mock.AsyncMock()) as probe:
                self.assertIsNone(await refresh_ollama_probe())
            probe.assert_not_awaited()


class ModuleStateTest(IsolatedTestCase):
    def test_fallback_is_logged_once_per_reason(self) -> None:
        llm_ollama.set_last_probe(DOWN)
        with self.assertLogs(waiter_agent.logger, level="WARNING") as logs:
            build_waiter_agent()
            build_waiter_agent()
        self.assertEqual(len([line for line in logs.output if "falling back" in line]), 1)
