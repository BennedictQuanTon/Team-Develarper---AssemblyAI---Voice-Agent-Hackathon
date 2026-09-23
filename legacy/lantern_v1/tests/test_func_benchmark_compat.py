"""The benchmarks still measure what they claim: the lead's script runs against the new agent, and the
A/B tooling reports providers, accuracy and percentiles correctly."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import httpx

import eval.benchmark_local_qwen as lead_benchmark
import eval.benchmark_ollama_waiter as ab
import eval.benchmark_today as today
from backend.app.domain.waiter import WaiterSession
from backend.app.pipeline.waiter_agent import OllamaWaiterAgent
from tests.fakes import FakeTTS, ScriptedChatModel, ai_text, ai_tool
from tests.support import IsolatedAsyncTestCase, IsolatedTestCase, fresh_store


class LeadBenchmarkCompatTest(IsolatedAsyncTestCase):
    async def test_constructing_the_agent_makes_no_request(self) -> None:
        agent = OllamaWaiterAgent(model="qwen2.5:3b")  # the network guard would raise on any request
        self.assertEqual(agent.model, "qwen2.5:3b")
        self.assertTrue(agent.available)

    async def test_lead_turn_runs_against_the_new_agent(self) -> None:
        model = ScriptedChatModel(script=[ai_text("Our Grilled Seabass and Grilled River Prawns are the favourites.")])
        agent = OllamaWaiterAgent(chat_model=model)
        session = WaiterSession(fresh_store(), session_id="lead-compat")
        record = await lead_benchmark.run_qwen_turn(agent, session, lead_benchmark.SCENARIO[0], FakeTTS())
        self.assertEqual(record["turn_number"], 1)
        self.assertEqual(model.calls, 1)


class BenchmarkTodayHelpersTest(IsolatedAsyncTestCase):
    async def test_provider_and_timings_come_from_the_server_event(self) -> None:
        events = [
            {"type": "final_transcript"},
            {"type": "turn_complete", "provider": {"llm": "ollama_langchain_tools", "llm_model": "qwen2.5:3b"},
             "timings_ms": {"ttfb_ms": 900, "e2e_turn_ms": 1500, "llm_rounds": 1, "ollama_load_ms": 3.2}},
        ]
        self.assertEqual(today.provider_from_events(events)["llm"], "ollama_langchain_tools")
        self.assertEqual(today.agent_timings_from_events(events), {"llm_rounds": 1, "ollama_load_ms": 3.2})
        self.assertIsNone(today.provider_from_events([{"type": "error"}]))

    async def test_an_existing_out_stem_is_never_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "reports").mkdir()
            (Path(tmp) / "reports" / "run1_eval.json").write_text("{}", encoding="utf-8")
            with mock.patch.object(today, "ROOT", Path(tmp)):
                result = await today.run_benchmark("ws://127.0.0.1:9/never-used", out_stem="run1")
        self.assertIn("refusing to overwrite", result["error"])


class AbHelpersTest(IsolatedTestCase):
    def test_nearest_rank_percentile(self) -> None:
        self.assertEqual(ab.percentile(list(range(1, 11)), 90), 9)
        self.assertEqual(ab.percentile([5.0], 90), 5.0)
        self.assertEqual(ab.percentile([3, 1, 2], 50), 2)
        self.assertIsNone(ab.percentile([], 90))

    def test_order_accuracy_gates(self) -> None:
        def basket(names, placed=True):
            prices = {"Pomelo Salad with Shrimp": 6.5, "Lemongrass Chicken": 9.0, "Grilled Seabass": 16.0,
                      "Stir-fried Morning Glory": 5.0, "Crispy Squid": 9.5}
            return {"placed": placed, "basket": [{"name": n, "line_total": prices[n]} for n in names]}

        good = ["Pomelo Salad with Shrimp", "Lemongrass Chicken", "Grilled Seabass", "Stir-fried Morning Glory"]
        self.assertTrue(ab.order_accuracy(basket(good), ["Added."])["passed"])
        with_squid = ab.order_accuracy(basket(good + ["Crispy Squid"]), ["ok"])
        self.assertFalse(with_squid["gates"]["sold_out_refused"])
        self.assertFalse(with_squid["gates"]["total_exact"])
        self.assertFalse(ab.order_accuracy(basket(good, placed=False), ["ok"])["gates"]["placed"])
        self.assertFalse(ab.order_accuracy(basket(good), ['{"name": "order_items"}'])["gates"]["no_json_spoken"])
        self.assertFalse(ab.order_accuracy(basket(good[:3]), ["ok"])["gates"]["dishes_exact"])

    def test_summary(self) -> None:
        runs = [
            {"scenario": "lead", "turns": [{"wall_ms": 100, "llm_rounds": 1}, {"wall_ms": 300, "llm_rounds": 2}]},
            {"scenario": "lead", "turns": [{"wall_ms": 200, "llm_rounds": 1}, {"wall_ms": 500, "llm_rounds": 2}]},
        ]
        summary = ab.summarize(runs)["lead"]
        self.assertEqual(summary["reps"], 2)
        self.assertEqual(summary["per_turn"][0]["median_ms"], 150.0)
        self.assertEqual(summary["conversation_median_ms"], 550.0)

    def test_template_replies_on_a_branch_without_them_is_refused(self) -> None:
        with mock.patch.object(ab, "OllamaWaiterAgent", lambda model, base_url: SimpleNamespace()):
            with self.assertRaises(SystemExit):
                ab.build_agent("qwen2.5:3b", "http://localhost:11434", template=True)


class AbScenarioTest(IsolatedAsyncTestCase):
    async def test_the_order_scenario_passes_its_gates_on_a_correct_conversation(self) -> None:
        model = ScriptedChatModel(script=[
            ai_tool("recommend_dishes", {"tags": "mild,couple", "party_size": 2}),
            ai_text("I'd suggest the Pomelo Salad with Shrimp and the Lemongrass Chicken."),
            ai_tool("order_items", {"items": [{"ref": "those two"}]}), ai_text("Added both."),
            ai_tool("order_items", {"items": [{"ref": "Crispy Squid"}]}), ai_text("Sorry, the squid is sold out."),
            ai_tool("order_items", {"items": [{"ref": "Grilled Seabass"}]}), ai_text("Seabass added."),
            ai_tool("order_items", {"items": [{"ref": "Stir-fried Morning Glory"}]}), ai_text("Added."),
            ai_tool("order_items", {"items": [], "place": True}), ai_text("Your order is placed."),
        ])
        run = await ab.run_scenario(OllamaWaiterAgent(chat_model=model), "order")
        self.assertTrue(run["accuracy"]["passed"], run["accuracy"])
        self.assertEqual(run["turns"][0]["prefetched"], ["recommend_dishes"])
        self.assertFalse(run["turns"][0]["llm_rounds_estimated"])

    async def test_a_branch_without_prefetch_is_called_the_old_way(self) -> None:
        calls = []

        class OldAgent:
            async def respond(self, ss, user_text):
                calls.append(user_text)
                return {"reply": "Hi.", "tool_calls": [{"tool": "get_floor"}], "basket": ss.snapshot()}

        turn = await ab.run_turn(OldAgent(), WaiterSession(fresh_store(), "old"), "a table please")
        self.assertEqual(calls, ["a table please"])
        self.assertEqual(turn["prefetched"], [])
        self.assertEqual(turn["llm_rounds"], 2)
        self.assertTrue(turn["llm_rounds_estimated"])

    async def test_gpu_gate_reads_api_ps(self) -> None:
        def ps(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"models": [
                {"name": "qwen2.5:3b", "size": 3_000, "size_vram": 3_000, "context_length": 4096},
                {"name": "qwen3:4b", "size": 5_000, "size_vram": 2_500},
            ]})

        async with httpx.AsyncClient(base_url="http://ollama", transport=httpx.MockTransport(ps)) as client:
            full = await ab.gpu_gate(client, "qwen2.5:3b")
            partial = await ab.gpu_gate(client, "qwen3:4b")
            missing = await ab.gpu_gate(client, "llama3:8b")
        self.assertTrue(full["fully_on_gpu"])
        self.assertEqual(full["context_length"], 4096)
        self.assertFalse(partial["fully_on_gpu"])
        self.assertFalse(missing["loaded"])

    async def test_existing_output_is_never_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "result.json"
            out.write_text("{}", encoding="utf-8")
            code = await ab.main_async(ab.parse_args(["--out", str(out)]))
        self.assertEqual(code, 2)
