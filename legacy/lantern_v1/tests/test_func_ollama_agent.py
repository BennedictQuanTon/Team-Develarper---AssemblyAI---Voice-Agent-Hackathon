"""OllamaWaiterAgent.respond, driven by a scripted chat model: the turn loop without a real model."""

from __future__ import annotations

import asyncio

from langchain_core.exceptions import OutputParserException
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage

from backend.app.domain.waiter import WaiterSession
from backend.app.pipeline import llm_ollama
from backend.app.pipeline.waiter_agent import (
    SYSTEM_INSTRUCTION,
    TOOL_SCHEMAS,
    OllamaWaiterAgent,
    RuleBasedWaiterAgent,
    WaiterAgent,
    _coerce_tool_args,
    waiter_provider_label,
)
from tests.fakes import ScriptedChatModel, ai_text, ai_tool
from tests.support import IsolatedAsyncTestCase, IsolatedTestCase, fresh_store

FALLBACK = "Sorry, could you say that again?"


class AgentTestCase(IsolatedAsyncTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.store = fresh_store()
        self.ss = WaiterSession(self.store, session_id="func-agent")

    def agent(self, *script, templates: bool = False, max_rounds: int = 6) -> tuple[OllamaWaiterAgent, ScriptedChatModel]:
        model = ScriptedChatModel(script=list(script))
        return OllamaWaiterAgent(chat_model=model, template_replies=templates, max_rounds=max_rounds), model


class TurnLoopTest(AgentTestCase):
    async def test_a_reply_with_no_tools_takes_one_round(self) -> None:
        agent, model = self.agent(ai_text("Our Grilled Seabass is lovely tonight."))
        result = await agent.respond(self.ss, "what's good?")
        self.assertEqual(result["reply"], "Our Grilled Seabass is lovely tonight.")
        self.assertEqual(result["tool_calls"], [])
        self.assertEqual(result["timings"]["llm_rounds"], 1)
        self.assertEqual(self.ss.mentioned[-1]["name"], "Grilled Seabass")

    async def test_tool_results_are_linked_to_their_call(self) -> None:
        agent, model = self.agent(ai_tool("order_items", {"items": [{"ref": "Beef Pho"}]}, id="c1"), ai_text("Added."))
        result = await agent.respond(self.ss, "one pho please")
        tool_message = model.received[1][-1]
        self.assertIsInstance(tool_message, ToolMessage)
        self.assertEqual(tool_message.tool_call_id, "c1")
        self.assertEqual(result["tool_calls"][0]["tool"], "order_items")
        self.assertEqual(set(result["tool_calls"][0]), {"tool", "args", "result"})
        self.assertEqual([line.name for line in self.ss.basket], ["Beef Pho"])
        self.assertEqual(result["timings"]["llm_rounds"], 2)

    async def test_several_rounds(self) -> None:
        agent, _ = self.agent(
            ai_tool("recommend_dishes", {"tags": "mild", "party_size": 2}),
            ai_tool("order_items", {"items": [{"ref": "the first"}]}),
            ai_text("Done."),
        )
        result = await agent.respond(self.ss, "recommend something and add the first")
        self.assertEqual(result["timings"]["llm_rounds"], 3)
        self.assertEqual(len(self.ss.basket), 1)

    async def test_running_out_of_rounds_reads_the_order_back(self) -> None:
        self.ss.add_item("SP_PHO")
        agent, model = self.agent(*[ai_tool("get_floor", {})] * 3, max_rounds=3)
        result = await agent.respond(self.ss, "hmm")
        self.assertEqual(result["timings"]["llm_rounds"], 3)
        self.assertEqual(result["reply"], "So far I have one Beef Pho, $7.50. Anything else?")

    async def test_an_unknown_tool_is_reported_back_to_the_model(self) -> None:
        agent, model = self.agent(ai_tool("delete_everything", {}), ai_text("Sorry about that."))
        result = await agent.respond(self.ss, "hi")
        self.assertIn("unknown tool", model.received[1][-1].content)
        self.assertEqual(result["reply"], "Sorry about that.")


class SmallModelFailuresTest(AgentTestCase):
    async def test_one_bad_tool_call_gets_a_corrective_retry(self) -> None:
        agent, model = self.agent(OutputParserException("bad json"), ai_text("Hello!"))
        result = await agent.respond(self.ss, "hi")
        self.assertEqual(result["reply"], "Hello!")
        self.assertEqual(result["timings"]["parse_errors"], 1)
        self.assertIsInstance(model.received[1][-1], HumanMessage)

    async def test_two_bad_tool_calls_give_up_politely(self) -> None:
        agent, _ = self.agent(OutputParserException("bad"), OutputParserException("bad again"))
        result = await agent.respond(self.ss, "hi")
        self.assertEqual(result["reply"], FALLBACK)
        self.assertEqual(result["timings"]["parse_errors"], 2)

    async def test_an_empty_answer_gets_one_nudge(self) -> None:
        agent, _ = self.agent(ai_text(""), ai_text("Welcome!"))
        result = await agent.respond(self.ss, "hi")
        self.assertEqual(result["reply"], "Welcome!")
        self.assertEqual(result["timings"]["empty_responses"], 1)

    async def test_two_empty_answers_give_up_politely(self) -> None:
        agent, _ = self.agent(ai_text(""), ai_text(""))
        self.assertEqual((await agent.respond(self.ss, "hi"))["reply"], FALLBACK)

    async def test_a_tool_call_written_as_text_is_executed_not_spoken(self) -> None:
        agent, _ = self.agent(
            ai_text('{"name": "order_items", "arguments": {"items": [{"ref": "Beef Pho"}]}}'),
            ai_text("Added your pho."),
        )
        result = await agent.respond(self.ss, "a pho")
        self.assertEqual(result["reply"], "Added your pho.")
        self.assertEqual(result["timings"]["salvaged_tool_calls"], 1)
        self.assertEqual([line.name for line in self.ss.basket], ["Beef Pho"])

    async def test_place_sent_as_the_string_false_does_not_place_the_order(self) -> None:
        agent, _ = self.agent(ai_tool("order_items", {"items": [{"ref": "Beef Pho"}], "place": "false"}), ai_text("ok"))
        await agent.respond(self.ss, "a pho")
        self.assertFalse(self.ss.placed)
        self.assertEqual(len(self.ss.basket), 1)


class TemplateRepliesTest(AgentTestCase):
    async def test_a_clean_order_change_needs_one_model_call(self) -> None:
        agent, model = self.agent(ai_tool("order_items", {"items": [{"ref": "Beef Pho"}]}), templates=True)
        result = await agent.respond(self.ss, "one pho")
        self.assertEqual(model.calls, 1)
        self.assertEqual(result["timings"]["template_reply"], 1)
        self.assertEqual(result["reply"], "Got it. That's one Beef Pho, $7.50 so far. Anything else?")

    async def test_a_sold_out_dish_goes_back_to_the_model(self) -> None:
        self.store.set_available("MAIN_SQUID", False)
        agent, model = self.agent(
            ai_tool("order_items", {"items": [{"ref": "Crispy Squid"}]}),
            ai_text("Sorry, the squid is sold out. Spring rolls instead?"),
            templates=True,
        )
        result = await agent.respond(self.ss, "the squid")
        self.assertEqual(model.calls, 2)
        self.assertEqual(result["timings"]["template_reply"], 0)

    async def test_templates_off_always_asks_the_model(self) -> None:
        agent, model = self.agent(ai_tool("order_items", {"items": [{"ref": "Beef Pho"}]}), ai_text("Added."))
        await agent.respond(self.ss, "one pho")
        self.assertEqual(model.calls, 2)


class PromptAndMetricsTest(AgentTestCase):
    async def test_ollama_timings_are_reported(self) -> None:
        agent, _ = self.agent(ai_tool("get_floor", {}, load_duration=2_000_000_000), ai_text("Table 3 is free."))
        timings = (await agent.respond(self.ss, "a table"))["timings"]
        for key in ("llm_rounds", "llm_total_ms", "tool_ms", "bucket_wait_ms", "prefetch_used", "template_reply",
                    "ollama_load_ms", "ollama_prompt_eval_ms", "ollama_eval_ms", "ollama_server_ms",
                    "ollama_prompt_tokens", "ollama_output_tokens", "ollama_first_prompt_tokens", "llm_overhead_ms"):
            self.assertIn(key, timings)
        self.assertEqual(timings["ollama_load_ms"], 2005.0)
        self.assertEqual(timings["ollama_first_prompt_tokens"], 40)
        self.assertEqual(timings["ollama_prompt_tokens"], 80)

    async def test_tools_are_bound_once_and_in_schema_order(self) -> None:
        agent, model = self.agent(ai_text("one"), ai_text("two"))
        await agent.respond(self.ss, "a")
        await agent.respond(self.ss, "b")
        self.assertEqual(model.bind_tools_calls, 1)
        self.assertEqual([tool["function"]["name"] for tool in model.bound_tools], [s["name"] for s in TOOL_SCHEMAS])

    async def test_prefetch_goes_after_an_unchanged_system_prompt(self) -> None:
        agent, model = self.agent(ai_text("one"), ai_text("two"))
        await agent.respond(self.ss, "a table for two")
        await agent.respond(self.ss, "a table for two", {"get_floor": {"free_tables": []}})
        without, with_prefetch = model.received[0], model.received[1]
        self.assertIsInstance(without[0], SystemMessage)
        self.assertEqual(without[0].content, SYSTEM_INSTRUCTION)
        self.assertEqual(with_prefetch[0].content, SYSTEM_INSTRUCTION)
        self.assertTrue(with_prefetch[1].content.startswith("Reference only"))
        self.assertTrue(with_prefetch[1].content.endswith("a table for two"))


class FailurePropagationTest(AgentTestCase):
    async def test_barge_in_cancellation_propagates(self) -> None:
        """Swallowing CancelledError would let an interrupted turn keep talking over the guest."""
        agent, model = self.agent(ai_text("never sent"))
        model.block_until(asyncio.Event())
        task = asyncio.create_task(agent.respond(self.ss, "hi"))
        await asyncio.sleep(0.05)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task

    async def test_an_unreachable_ollama_is_marked_down_and_re_raised(self) -> None:
        agent, _ = self.agent(ConnectionError("connection refused"))
        with self.assertRaises(ConnectionError):
            await agent.respond(self.ss, "hi")
        probe = llm_ollama.get_last_probe()
        self.assertIsNotNone(probe)
        self.assertFalse(probe.reachable)


class HelpersTest(IsolatedTestCase):
    def test_coerce_order_items_arguments(self) -> None:
        self.assertEqual(_coerce_tool_args("order_items", {"items": '[{"ref": "Beef Pho"}]'})["items"], [{"ref": "Beef Pho"}])
        self.assertEqual(_coerce_tool_args("order_items", {"items": {"ref": "Beef Pho"}})["items"], [{"ref": "Beef Pho"}])
        self.assertIs(_coerce_tool_args("order_items", {"items": [], "place": "false"})["place"], False)
        self.assertIs(_coerce_tool_args("order_items", {"items": [], "place": "TRUE"})["place"], True)
        self.assertEqual(_coerce_tool_args("get_floor", '{"x": 1}'), {"x": 1})
        self.assertEqual(_coerce_tool_args("get_floor", "not json"), {})

    def test_provider_labels(self) -> None:
        ollama_agent = OllamaWaiterAgent(chat_model=ScriptedChatModel())
        self.assertEqual(waiter_provider_label(ollama_agent), "ollama_langchain_tools")
        self.assertEqual(waiter_provider_label(WaiterAgent(api_key="dummy")), "gemini_tools")
        self.assertEqual(waiter_provider_label(RuleBasedWaiterAgent()), "rulebased")
