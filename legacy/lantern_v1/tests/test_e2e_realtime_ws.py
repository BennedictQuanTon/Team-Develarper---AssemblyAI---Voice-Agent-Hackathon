"""End-to-end smoke over /ws/realtime with every external provider faked.

Speech recognition is scripted, speech synthesis is the stub, and the model is a scripted chat model
behind the real OllamaWaiterAgent, WaiterSession, prefetch, templates and metrics. Nothing here needs keys,
Ollama or the network.
"""

from __future__ import annotations

import json
from collections import deque
from pathlib import Path
from typing import Any
from unittest import mock

from fastapi.testclient import TestClient

from backend.app import main
from backend.app.domain.lantern import get_lantern_store
from backend.app.pipeline import realtime_session
from backend.app.pipeline.waiter_agent import OllamaWaiterAgent
from tests.fakes import ScriptedChatModel, ScriptedRealtimeStream, ai_text, ai_tool
from tests.support import IsolatedTestCase, run_with_timeout

EXPECTED_DISHES = {"Pomelo Salad with Shrimp", "Lemongrass Chicken", "Grilled Seabass", "Stir-fried Morning Glory"}


class RealtimeWebSocketTest(IsolatedTestCase):
    def setUp(self) -> None:
        super().setUp()
        for patcher in (
            mock.patch.object(realtime_session, "StubRealtimeStream", ScriptedRealtimeStream),
            mock.patch.object(realtime_session, "_context_filler_pcm_chunks", lambda *a, **k: [bytes(3200)]),
            mock.patch.object(main, "get_collection", side_effect=RuntimeError("no Chroma in tests")),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        ScriptedRealtimeStream.script = deque()
        self.addCleanup(lambda: get_lantern_store().set_available("MAIN_SQUID", True))

    def use_model(self, model: ScriptedChatModel, *, templates: bool) -> None:
        agent = OllamaWaiterAgent(chat_model=model, template_replies=templates)
        patcher = mock.patch.object(realtime_session, "build_waiter_agent", lambda *a, **k: agent)
        patcher.start()
        self.addCleanup(patcher.stop)

    @staticmethod
    def speak(ws: Any, transcript: str) -> list[dict[str, Any]]:
        """One guest turn: queue the transcript, end the turn, collect events until the waiter finishes."""
        ScriptedRealtimeStream.script.append(transcript)
        ws.send_json({"command": "endpoint"})
        events: list[dict[str, Any]] = []
        while not events or events[-1].get("type") != "turn_complete":
            events.append(ws.receive_json())
        return events

    def test_one_turn_event_order_labels_and_metrics(self) -> None:
        model = ScriptedChatModel(script=[
            ai_tool("recommend_dishes", {"tags": "mild,couple", "party_size": 2}),
            ai_text("I'd suggest the Pomelo Salad with Shrimp and the Lemongrass Chicken."),
        ])
        self.use_model(model, templates=False)

        def conversation() -> tuple[dict, list[dict]]:
            with TestClient(main.app).websocket_connect("/ws/realtime") as ws:
                ready = ws.receive_json()
                return ready, self.speak(ws, "What would you recommend for a mild couple?")

        ready, events = run_with_timeout(conversation, 60)
        self.assertEqual(ready["type"], "session_ready")
        types = [event["type"] for event in events]
        self.assertEqual(types[0], "final_transcript")
        self.assertEqual(types[-1], "turn_complete")
        spoken = [i for i, e in enumerate(events) if e["type"] == "audio_chunk" and not e.get("thinking")]
        self.assertTrue(spoken, types)
        self.assertLess(types.index("basket_update"), spoken[0], "the basket should update before the reply is spoken")

        done = events[-1]
        self.assertEqual(done["answer"], "I'd suggest the Pomelo Salad with Shrimp and the Lemongrass Chicken.")
        self.assertEqual(done["provider"]["llm"], "ollama_langchain_tools")
        self.assertEqual(done["provider"]["llm_model"], "qwen2.5:3b")
        self.assertEqual(done["timings_ms"]["llm_rounds"], 2)
        self.assertIn("ollama_server_ms", done["timings_ms"])

        rows = [json.loads(line) for line in (Path(self.tmp_dir) / "turns.jsonl").read_text("utf-8").splitlines()]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["provider"]["llm"], "ollama_langchain_tools")
        self.assertEqual(rows[0]["timings_ms"]["llm_rounds"], 2)

    def test_six_turn_order_with_a_sold_out_dish(self) -> None:
        for templates in (False, True):
            with self.subTest(template_replies=templates):
                get_lantern_store().set_available("MAIN_SQUID", True)
                ScriptedRealtimeStream.script = deque()
                self.run_scenario(templates)

    def run_scenario(self, templates: bool) -> None:
        def confirm(text: str) -> list:
            # With template replies a clean order change is confirmed without a second model call.
            return [] if templates else [ai_text(text)]

        script = [
            ai_tool("recommend_dishes", {"tags": "mild,couple", "party_size": 2}),
            ai_text("I'd suggest the Pomelo Salad with Shrimp and the Lemongrass Chicken."),
            ai_tool("order_items", {"items": [{"ref": "those two"}]}),
            *confirm("Added both."),
            ai_tool("order_items", {"items": [{"ref": "Crispy Squid"}]}),
            ai_text("Sorry, the squid is sold out tonight. Would spring rolls do instead?"),
            ai_tool("order_items", {"items": [{"ref": "Grilled Seabass"}]}),
            *confirm("One Grilled Seabass added."),
            ai_tool("order_items", {"items": [{"ref": "Stir-fried Morning Glory"}]}),
            *confirm("Morning glory added."),
            ai_tool("order_items", {"items": [], "place": True}),
            *confirm("Your order is placed."),
        ]
        model = ScriptedChatModel(script=script)
        agent = OllamaWaiterAgent(chat_model=model, template_replies=templates)

        def conversation() -> list[dict]:
            with mock.patch.object(realtime_session, "build_waiter_agent", lambda *a, **k: agent):
                with TestClient(main.app).websocket_connect("/ws/realtime") as ws:
                    ws.receive_json()
                    turns = [self.speak(ws, "What would you recommend for a mild couple?")]
                    turns.append(self.speak(ws, "We'll take those two please"))
                    ws.send_json({"command": "set_86", "sku": "MAIN_SQUID", "available": False})
                    while ws.receive_json().get("type") != "set_86_done":
                        pass
                    turns.append(self.speak(ws, "I'd like the crispy squid too"))
                    turns.append(self.speak(ws, "Okay, make it a grilled seabass instead"))
                    turns.append(self.speak(ws, "And stir-fried morning glory on the side"))
                    turns.append(self.speak(ws, "That's all, please place the order"))
                    return turns

        turns = run_with_timeout(conversation, 90)
        self.assertEqual(model.calls, len(script), "the model was called a different number of times than scripted")

        final = turns[-1][-1]
        basket = final["basket"]
        names = {line["name"] for line in basket["basket"]}
        self.assertEqual(names, EXPECTED_DISHES)
        self.assertNotIn("Crispy Squid", names)
        self.assertAlmostEqual(sum(line["line_total"] for line in basket["basket"]), 36.50)
        self.assertIs(basket["placed"], True)

        replies = [turn[-1]["answer"] for turn in turns]
        self.assertTrue(all("{" not in reply for reply in replies), replies)
        if templates:
            self.assertEqual(replies[-1], "Your order is in: one Lemongrass Chicken, one Pomelo Salad with Shrimp, "
                                          "one Grilled Seabass, and one Stir-fried Morning Glory. "
                                          "Your total is $36.50. Thank you!")
            self.assertEqual([turn[-1]["timings_ms"]["template_reply"] for turn in turns], [0, 1, 0, 1, 1, 1])
