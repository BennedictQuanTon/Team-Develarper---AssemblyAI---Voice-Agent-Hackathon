"""The real ChatOllama against a mocked Ollama HTTP API: what actually goes over the wire.

These catch mismatches between LangChain, the ollama client and Ollama's `/api/chat` protocol without
running Ollama.
"""

from __future__ import annotations

import json

import httpx

from backend.app.domain.waiter import WaiterSession
from backend.app.pipeline import llm_ollama
from backend.app.pipeline.waiter_agent import SYSTEM_INSTRUCTION, TOOL_SCHEMAS, OllamaWaiterAgent, ollama_params
from tests.fakes import ndjson_chat_handler
from tests.support import IsolatedAsyncTestCase, fresh_store

TOOL_NAMES = [schema["name"] for schema in TOOL_SCHEMAS]


class OllamaWireTest(IsolatedAsyncTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.ss = WaiterSession(fresh_store(), session_id="integration-http")

    def agent(self, responses: list[dict], *, templates: bool = False) -> tuple[OllamaWaiterAgent, list[dict]]:
        handler, requests = ndjson_chat_handler(responses)
        model = llm_ollama.build_chat_model(
            ollama_params(), async_client_kwargs={"transport": httpx.MockTransport(handler)}
        )
        return OllamaWaiterAgent(chat_model=model, template_replies=templates), requests

    async def test_request_payload(self) -> None:
        agent, requests = self.agent([{"content": "Welcome to The Lantern."}])
        result = await agent.respond(self.ss, "hello")
        self.assertEqual(result["reply"], "Welcome to The Lantern.")

        request = requests[0]
        self.assertEqual(request["path"], "/api/chat")
        body = request["body"]
        self.assertEqual(body["model"], "qwen2.5:3b")
        self.assertEqual(body["keep_alive"], -1)
        self.assertEqual(body["options"]["num_ctx"], 4096)
        self.assertEqual(body["options"]["num_predict"], 256)
        self.assertEqual(body["options"]["temperature"], 0.3)
        self.assertNotIn("think", {k: v for k, v in body.items() if v is not None})
        self.assertEqual(body["messages"][0], {"role": "system", "content": SYSTEM_INSTRUCTION})

    async def test_tools_on_the_wire(self) -> None:
        agent, requests = self.agent([{"content": "Hi."}])
        await agent.respond(self.ss, "hello")
        tools = requests[0]["body"]["tools"]
        self.assertEqual([tool["function"]["name"] for tool in tools], TOOL_NAMES)
        order_items = next(tool for tool in tools if tool["function"]["name"] == "order_items")
        item_schema = order_items["function"]["parameters"]["properties"]["items"]["items"]
        self.assertTrue({"ref", "qty", "modifiers"} <= set(item_schema["properties"]))

    async def test_nested_order_items_arguments_round_trip(self) -> None:
        agent, requests = self.agent([
            {"tool_calls": [{"name": "order_items", "arguments": {
                "items": [{"ref": "Grilled Seabass", "qty": 2, "modifiers": ["no chili"]}], "place": False,
            }}]},
            {"content": "Two seabass, no chili."},
        ])
        result = await agent.respond(self.ss, "two seabass no chili")
        args = result["tool_calls"][0]["args"]
        self.assertEqual(args["items"], [{"ref": "Grilled Seabass", "qty": 2, "modifiers": ["no chili"]}])
        self.assertIs(args["place"], False)
        self.assertEqual([(l.qty, l.name, l.modifiers) for l in self.ss.basket], [(2, "Grilled Seabass", ["no chili"])])

        second_round = requests[1]["body"]["messages"]
        tool_messages = [m for m in second_round if m["role"] == "tool"]
        self.assertEqual(len(tool_messages), 1)
        self.assertEqual(json.loads(tool_messages[0]["content"])["total"], 32.0)

    async def test_system_prompt_and_tools_are_identical_across_turns(self) -> None:
        """Ollama only reuses its cached prompt prefix when these bytes match."""
        agent, requests = self.agent([{"content": "One."}, {"content": "Two."}])
        await agent.respond(self.ss, "first")
        await agent.respond(self.ss, "second", {"get_floor": {"free_tables": []}})

        def prefix(body: dict) -> str:
            return json.dumps({"system": body["messages"][0], "tools": body["tools"]}, sort_keys=True)

        self.assertEqual(prefix(requests[0]["body"]), prefix(requests[1]["body"]))

    async def test_server_timings_come_from_the_final_chunk(self) -> None:
        agent, _ = self.agent([{"content": "Hi.", "metadata": {"load_duration": 1_500_000_000, "eval_count": 7}}])
        timings = (await agent.respond(self.ss, "hello"))["timings"]
        self.assertEqual(timings["ollama_load_ms"], 1500.0)
        self.assertEqual(timings["ollama_output_tokens"], 7)

    async def test_template_reply_skips_the_second_request(self) -> None:
        agent, requests = self.agent(
            [{"tool_calls": [{"name": "order_items", "arguments": {"items": [{"ref": "Beef Pho"}]}}]}], templates=True
        )
        result = await agent.respond(self.ss, "a pho")
        self.assertEqual(len(requests), 1)
        self.assertEqual(result["reply"], "Got it. That's one Beef Pho, $7.50 so far. Anything else?")

    async def test_reasoning_flag_is_sent_only_when_configured(self) -> None:
        handler, requests = ndjson_chat_handler([{"content": "Hi."}])
        params = llm_ollama.OllamaParams(model="qwen3:4b", reasoning=False)
        model = llm_ollama.build_chat_model(params, async_client_kwargs={"transport": httpx.MockTransport(handler)})
        await OllamaWaiterAgent(chat_model=model).respond(self.ss, "hello")
        self.assertIs(requests[0]["body"].get("think"), False)

    async def test_warm_up_sends_the_same_system_prompt_and_tools(self) -> None:
        handler, requests = ndjson_chat_handler([{"content": "Hello."}])
        model = llm_ollama.build_chat_model(
            ollama_params(), async_client_kwargs={"transport": httpx.MockTransport(handler)}
        )
        runnable = model.bind_tools(llm_ollama.to_openai_tools(TOOL_SCHEMAS))
        timings = await llm_ollama.warm_up(runnable, SYSTEM_INSTRUCTION)
        body = requests[0]["body"]
        self.assertEqual(body["messages"][0]["content"], SYSTEM_INSTRUCTION)
        self.assertEqual([t["function"]["name"] for t in body["tools"]], TOOL_NAMES)
        self.assertIn("warmup_ms", timings)


class ProbeTest(IsolatedAsyncTestCase):
    @staticmethod
    def tags(*names: str) -> httpx.MockTransport:
        return httpx.MockTransport(lambda request: httpx.Response(200, json={"models": [{"name": n} for n in names]}))

    async def test_model_present(self) -> None:
        probe = await llm_ollama.probe_ollama("http://localhost:11434", "qwen2.5:3b", transport=self.tags("qwen2.5:3b"))
        self.assertTrue(probe.healthy)

    async def test_model_not_pulled(self) -> None:
        probe = await llm_ollama.probe_ollama("http://localhost:11434", "qwen2.5:3b", transport=self.tags("llama3:8b"))
        self.assertTrue(probe.reachable)
        self.assertFalse(probe.model_present)
        self.assertIn("ollama pull qwen2.5:3b", probe.detail)

    async def test_ollama_down(self) -> None:
        def refuse(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused", request=request)

        probe = await llm_ollama.probe_ollama("http://localhost:11434", "qwen2.5:3b", transport=httpx.MockTransport(refuse))
        self.assertFalse(probe.reachable)
        self.assertFalse(probe.healthy)
