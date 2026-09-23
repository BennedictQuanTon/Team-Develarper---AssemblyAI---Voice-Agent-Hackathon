"""llm_ollama: the pieces that keep Ollama's prompt prefix stable and its output usable."""

from __future__ import annotations

import json

from langchain_core.messages import HumanMessage, SystemMessage

from backend.app.pipeline import llm_ollama
from backend.app.pipeline.llm_ollama import (
    OllamaParams,
    build_chat_model,
    build_messages,
    get_bound_model,
    model_matches,
    ollama_timings_ms,
    salvage_tool_calls,
    to_openai_tools,
)
from backend.app.pipeline.waiter_agent import SYSTEM_INSTRUCTION, TOOL_SCHEMAS
from tests.support import IsolatedTestCase

TOOL_NAMES = [schema["name"] for schema in TOOL_SCHEMAS]


class ToolsTest(IsolatedTestCase):
    def test_order_and_names_are_preserved(self) -> None:
        tools = to_openai_tools(TOOL_SCHEMAS)
        self.assertEqual([tool["function"]["name"] for tool in tools], TOOL_NAMES)
        self.assertTrue(all(tool["type"] == "function" for tool in tools))

    def test_serialisation_is_byte_identical_every_time(self) -> None:
        """Ollama renders tools into the system block; any byte change costs a full prompt reprocess."""
        first = json.dumps(to_openai_tools(TOOL_SCHEMAS))
        second = json.dumps(to_openai_tools(TOOL_SCHEMAS))
        self.assertEqual(first, second)

    def test_wrapping_does_not_alias_the_shared_schemas(self) -> None:
        tools = to_openai_tools(TOOL_SCHEMAS)
        tools[0]["function"]["parameters"]["properties"]["mutated"] = True
        self.assertNotIn("mutated", TOOL_SCHEMAS[0]["parameters"]["properties"])


class MessagesTest(IsolatedTestCase):
    def test_system_prompt_is_identical_with_or_without_prefetch(self) -> None:
        plain = build_messages(SYSTEM_INSTRUCTION, "a table for two")
        with_context = build_messages(SYSTEM_INSTRUCTION, "a table for two", "Reference only - floor: {}")
        self.assertIsInstance(plain[0], SystemMessage)
        self.assertEqual(plain[0].content, with_context[0].content)

    def test_prefetch_goes_after_the_prefix_in_the_human_turn(self) -> None:
        messages = build_messages("SYS", "do you have squid", "CONTEXT")
        self.assertIsInstance(messages[1], HumanMessage)
        self.assertEqual(messages[1].content, "CONTEXT\n\ndo you have squid")
        self.assertEqual(build_messages("SYS", "hi")[1].content, "hi")


class TimingsTest(IsolatedTestCase):
    def test_nanoseconds_become_milliseconds(self) -> None:
        timings = ollama_timings_ms(
            {
                "load_duration": 2_500_000_000,
                "prompt_eval_duration": 12_345_678,
                "eval_duration": 400_000_000,
                "total_duration": 3_000_000_000,
                "prompt_eval_count": 37,
                "eval_count": 21,
            }
        )
        self.assertEqual(
            timings,
            {
                "load_ms": 2500.0,
                "prompt_eval_ms": 12.346,
                "eval_ms": 400.0,
                "server_total_ms": 3000.0,
                "prompt_tokens": 37,
                "output_tokens": 21,
            },
        )

    def test_missing_or_bogus_fields_are_skipped(self) -> None:
        self.assertEqual(ollama_timings_ms(None), {})
        self.assertEqual(ollama_timings_ms({"eval_count": True, "load_duration": "slow"}), {})


class SalvageTest(IsolatedTestCase):
    def test_bare_json_call(self) -> None:
        calls = salvage_tool_calls('{"name": "get_floor", "arguments": {}}', TOOL_NAMES)
        self.assertEqual([(c["name"], c["args"]) for c in calls], [("get_floor", {})])
        self.assertTrue(calls[0]["id"])

    def test_tagged_calls_and_string_arguments(self) -> None:
        text = (
            'Sure!\n<tool_call>{"name": "order_items", "arguments": "{\\"items\\": [{\\"ref\\": \\"Beef Pho\\"}]}"}'
            "</tool_call>"
        )
        calls = salvage_tool_calls(text, TOOL_NAMES)
        self.assertEqual(calls[0]["args"], {"items": [{"ref": "Beef Pho"}]})

    def test_fenced_list_of_calls(self) -> None:
        text = '```json\n[{"name": "readback", "arguments": {}}, {"name": "get_floor", "parameters": {}}]\n```'
        self.assertEqual([c["name"] for c in salvage_tool_calls(text, TOOL_NAMES)], ["readback", "get_floor"])

    def test_prose_and_unknown_tools_are_left_alone(self) -> None:
        self.assertEqual(salvage_tool_calls("Our seabass is lovely tonight.", TOOL_NAMES), [])
        self.assertEqual(salvage_tool_calls('Try this: {"name": "get_floor", "arguments": {}}', TOOL_NAMES), [])
        self.assertEqual(salvage_tool_calls('{"name": "delete_database", "arguments": {}}', TOOL_NAMES), [])
        self.assertEqual(salvage_tool_calls("<tool_call>{broken</tool_call>", TOOL_NAMES), [])
        self.assertEqual(salvage_tool_calls("", TOOL_NAMES), [])


class ModelCacheTest(IsolatedTestCase):
    def test_same_params_share_one_bound_model(self) -> None:
        tools = to_openai_tools(TOOL_SCHEMAS)
        params = OllamaParams(model="qwen2.5:3b")
        self.assertIs(get_bound_model(params, tools), get_bound_model(OllamaParams(model="qwen2.5:3b"), tools))

    def test_different_params_or_tools_build_a_new_model(self) -> None:
        tools = to_openai_tools(TOOL_SCHEMAS)
        base = get_bound_model(OllamaParams(model="qwen2.5:3b"), tools)
        self.assertIsNot(base, get_bound_model(OllamaParams(model="qwen3:4b"), tools))
        self.assertIsNot(base, get_bound_model(OllamaParams(model="qwen2.5:3b"), tools[:-1]))

    def test_reset_clears_the_cache(self) -> None:
        tools = to_openai_tools(TOOL_SCHEMAS)
        params = OllamaParams(model="qwen2.5:3b")
        first = get_bound_model(params, tools)
        llm_ollama.reset_model_cache()
        self.assertIsNot(first, get_bound_model(params, tools))

    def test_reasoning_is_only_sent_when_set(self) -> None:
        self.assertIsNone(build_chat_model(OllamaParams(model="qwen2.5:3b")).reasoning)
        self.assertIs(build_chat_model(OllamaParams(model="qwen3:4b", reasoning=False)).reasoning, False)

    def test_model_parameters_reach_chatollama(self) -> None:
        model = build_chat_model(OllamaParams(model="qwen2.5:3b", num_ctx=2048, num_predict=128, keep_alive="5m"))
        self.assertEqual((model.num_ctx, model.num_predict, model.keep_alive), (2048, 128, "5m"))
        self.assertFalse(model.validate_model_on_init)


class ModelMatchTest(IsolatedTestCase):
    def test_exact_and_latest_tags(self) -> None:
        self.assertTrue(model_matches("qwen2.5:3b", {"qwen2.5:3b", "llama3:8b"}))
        self.assertFalse(model_matches("qwen2.5:3b", {"qwen2.5:7b"}))
        self.assertTrue(model_matches("qwen2.5", {"qwen2.5:latest"}))
        self.assertFalse(model_matches("qwen2.5", {"qwen2.5:3b"}))
