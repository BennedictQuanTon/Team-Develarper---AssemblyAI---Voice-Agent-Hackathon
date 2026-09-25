import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app import main
from backend.app.domain.restaurant.models import IntentItem, IntentProposal
from backend.app.domain.restaurant.repository import SQLiteOrderRepository
from backend.app.domain.restaurant.store import LanternStore
from backend.app.domain.restaurant.workflow import OrderWorkflow
from backend.app.services.kitchen_events import KitchenEventBroker
from backend.app.services.realtime_session import RealtimeSession


class ScriptedLLM:
    def __init__(self, intents):
        self.intents = iter(intents)
        self.contexts = []

    async def extract_intent(self, transcript, context):
        self.contexts.append(context)
        return next(self.intents)


def intent(action, sku=None, quantity=1):
    return IntentProposal(action=action, items=[IntentItem(sku=sku, quantity=quantity)] if sku else [])


class ConversationFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_multiturn_order_menu_recommend_remove_cancel_and_soldout(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = SQLiteOrderRepository(Path(directory) / "orders.sqlite3")
            store = LanternStore()
            llm = ScriptedLLM([
                intent("create_or_update_order", "MAIN_SEABASS"),
                intent("create_or_update_order", "VG_MORNING"),
                intent("recommend"), intent("menu_query"),
                intent("remove_item", "MAIN_SEABASS"),
                intent("cancel_order"), intent("confirm"),
                intent("create_or_update_order", "MAIN_SQUID"),
            ])
            session = RealtimeSession("T4", OrderWorkflow(repo, store), llm)
            first = await session.handle_transcript("one sea bass")
            second = await session.handle_transcript("add one morning glory")
            recommendation = await session.handle_transcript("what do you recommend?")
            menu = await session.handle_transcript("what's on the menu?")
            removed = await session.handle_transcript("remove sea bass")
            asked = await session.handle_transcript("cancel the order")
            cancelled = await session.handle_transcript("yes")
            store.set_available("MAIN_SQUID", False)
            soldout = await session.handle_transcript("can I get the crispy squid?")

            self.assertEqual(first["order_id"], second["order_id"])
            self.assertEqual(asked["status"], "awaiting_reply")
            self.assertEqual(asked["response_text"], "Cancel the whole order?")
            self.assertEqual([first["current_revision"], second["current_revision"], removed["current_revision"], cancelled["current_revision"]], [1, 2, 3, 4])
            self.assertEqual({line["sku"] for line in second["basket"]}, {"MAIN_SEABASS", "VG_MORNING"})
            self.assertEqual([line["sku"] for line in removed["basket"]], ["VG_MORNING"])
            self.assertEqual(cancelled["status"], "cancelled")
            self.assertEqual(len(repo.list_orders()), 1)
            self.assertEqual(len(repo.get_order(first["order_id"])["revisions"]), 4)
            self.assertEqual(recommendation["status"], "recommend")
            self.assertEqual(menu["status"], "menu_query")
            self.assertIn("sold out", soldout["response_text"])
            self.assertTrue(soldout["alternatives"])
            self.assertEqual(llm.contexts[1]["current_state"]["order_id"], first["order_id"])
            self.assertEqual(len(llm.contexts[2]["current_state"]["items"]), 2)
            repo.close()

    async def test_kitchen_substitute_guest_acceptance_and_stale_revision(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = SQLiteOrderRepository(Path(directory) / "orders.sqlite3")
            workflow = OrderWorkflow(repo, LanternStore())
            llm = ScriptedLLM([intent("create_or_update_order", "MAIN_SEABASS"), intent("place_order"), intent("accept_substitute")])
            session = RealtimeSession("T4", workflow, llm)
            created = await session.handle_transcript("one sea bass")
            self.assertEqual(created["status"], "draft")
            placed = await session.handle_transcript("that's all, please place the order")
            self.assertEqual((placed["status"], placed["current_revision"]), ("pending_kitchen", 2))
            decision = {"action": "propose_substitute", "expected_revision": 2,
                        "substitutions": [{"from_sku": "MAIN_SEABASS", "to_sku": "MAIN_LEMCHICKEN"}]}
            workflow.validate_kitchen_decision(created["order_id"], decision)
            proposed = repo.decide(created["order_id"], decision)
            guest_update = await session.handle_kitchen_decision(proposed, decision)
            self.assertEqual(guest_update["status"], "substitution_proposed")
            self.assertIn("Would you accept", guest_update["response_text"])
            self.assertEqual(len(llm.contexts), 2)
            accepted = await session.handle_transcript("yes, that works")
            self.assertEqual(accepted["current_revision"], 3)
            self.assertEqual(accepted["basket"][0]["sku"], "MAIN_LEMCHICKEN")
            self.assertEqual(llm.contexts[2]["current_state"]["latest_decision"]["action"], "propose_substitute")
            with self.assertRaisesRegex(ValueError, "stale revision"):
                repo.decide(created["order_id"], {"action": "accept", "expected_revision": 2})
            repo.close()

    async def test_existing_allergy_still_blocks_later_addition(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = SQLiteOrderRepository(Path(directory) / "orders.sqlite3")
            workflow = OrderWorkflow(repo, LanternStore())
            first = IntentProposal(action="create_or_update_order", items=[IntentItem(sku="MAIN_SEABASS", quantity=1)], allergies=["peanut"])
            order = workflow.submit("T4", "guest-1", "sea bass, peanut allergy", first)
            second = workflow.submit("T4", "guest-1", "add pomelo salad", intent("create_or_update_order", "ST_POMELO"), order["order_id"])
            self.assertEqual(second["status"], "clarification_required")
            self.assertEqual(repo.get_order(order["order_id"])["current_revision"], 1)
            repo.close()

    async def test_correction_replaces_instead_of_duplicating(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = SQLiteOrderRepository(Path(directory) / "orders.sqlite3")
            workflow = OrderWorkflow(repo, LanternStore())
            original = workflow.submit("T4", "guest-1", "one chicken", intent("create_or_update_order", "MAIN_LEMCHICKEN"))
            correction = IntentProposal(action="replace_item", replaces_sku="MAIN_LEMCHICKEN",
                                        items=[IntentItem(sku="MAIN_SEABASS", quantity=1)])
            updated = workflow.submit("T4", "guest-1", "make it seabass instead", correction, original["order_id"])
            self.assertEqual(updated["current_revision"], 2)
            self.assertEqual([line["sku"] for line in updated["basket"]], ["MAIN_SEABASS"])
            repo.close()


class KitchenDeliveryTests(unittest.TestCase):
    def test_every_guest_turn_reaches_the_ops_activity_feed(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = SQLiteOrderRepository(Path(directory) / "orders.sqlite3")
            store = LanternStore()
            workflow = OrderWorkflow(repo, store)
            original = (main.repository, main.store, main.workflow, main.broker, main.llm, main.tts)

            class SilentTTS:
                sample_rate = 24000

                async def stream_async(self, *_args):
                    if False:
                        yield b""

            main.repository, main.store, main.workflow, main.broker, main.llm, main.tts = repo, store, workflow, KitchenEventBroker(), None, SilentTTS()
            try:
                client = TestClient(main.app)
                with client.websocket_connect("/ws/ops") as ops, client.websocket_connect("/ws/realtime?table_id=T4") as guest:
                    self.assertEqual(ops.receive_json()["type"], "kitchen_snapshot")
                    guest.receive_json()  # session_ready
                    guest.send_json({"type": "transcript", "text": "Hello there", "language_code": "en"})
                    while guest.receive_json()["type"] != "turn_complete":
                        pass
                    turn = ops.receive_json()
                    self.assertEqual((turn["type"], turn["table_id"], turn["transcript"]), ("agent_turn", "T4", "Hello there"))
                    self.assertEqual(turn["status"], "clarification_required")
                    self.assertFalse(turn["placed"])
                    self.assertIsInstance(turn["pipeline_ms"], float)
            finally:
                main.repository, main.store, main.workflow, main.broker, main.llm, main.tts = original
                repo.close()

    def test_kitchen_decision_reaches_guest_websocket(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = SQLiteOrderRepository(Path(directory) / "orders.sqlite3")
            store = LanternStore()
            workflow = OrderWorkflow(repo, store)
            order = repo.create_revision("order-1", "T4", "guest-1", "en", "sea bass", [{"sku": "MAIN_SEABASS", "quantity": 1}], [])
            original = (main.repository, main.store, main.workflow, main.broker, main.llm, main.tts)

            class SilentTTS:
                sample_rate = 24000

                async def stream_async(self, *_args):
                    if False:
                        yield b""

            main.repository, main.store, main.workflow, main.broker, main.llm, main.tts = repo, store, workflow, KitchenEventBroker(), None, SilentTTS()
            try:
                # TestClient without a lifespan avoids warming live Ollama/Kokoro.
                client = TestClient(main.app)
                with client.websocket_connect("/ws/realtime?table_id=T4&order_id=order-1") as guest:
                    ready = guest.receive_json()
                    self.assertEqual(ready["order"]["order_id"], "order-1")
                    response = client.post("/api/kitchen/orders/order-1/decisions", json={"action": "accept", "expected_revision": order["current_revision"]})
                    self.assertEqual(response.status_code, 200)
                    update = guest.receive_json()
                    self.assertEqual(update["source"], "kitchen")
                    self.assertEqual(update["status"], "committed")
                    self.assertIn("confirmed", update["response_text"])
            finally:
                main.repository, main.store, main.workflow, main.broker, main.llm, main.tts = original
                repo.close()
