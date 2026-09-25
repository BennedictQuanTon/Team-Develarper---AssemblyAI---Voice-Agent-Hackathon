import json
import re
import tempfile
import unittest
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from backend.app import main
from backend.app.domain.restaurant.models import IntentItem, IntentProposal
from backend.app.domain.restaurant.repository import SQLiteOrderRepository
from backend.app.domain.restaurant.store import LanternStore
from backend.app.domain.restaurant.workflow import OrderWorkflow
from backend.app.providers.llm.ollama import OllamaClient
from backend.app.services.dialogue import DialogueState, resolve
from backend.app.services.kitchen_events import KitchenEventBroker
from backend.app.services.realtime_session import RealtimeSession
from backend.app.services.response_renderer import render_clarification

RAW_STRINGS = re.compile(r"\b[A-Z]+(?:_[A-Z]+)+\b|unknown sku|requires at least one item|is not in the order$")
TURNS = [
    "What would you recommend for a mild couple?",
    "We'll take those two please",
    "I'd like the crispy squid too",
    "Okay, make it a grilled seabass instead",
    "And stir-fried morning glory on the side",
    "That's all, please place the order",
]


def items(*skus):
    return [IntentItem(sku=sku, quantity=1) for sku in skus]


def order_with(*skus, placed=False, status="draft"):
    return {"status": status, "placed": placed,
            "basket": [{"sku": sku, "quantity": 1, "modifiers": []} for sku in skus]}


class ResolverTests(unittest.TestCase):
    def setUp(self):
        self.store = LanternStore()
        self.dialogue = DialogueState()

    def resolve(self, intent, order=None, transcript=""):
        return resolve(intent, self.dialogue, order, self.store, transcript)

    def test_offered_refs_fill_items_from_last_offered(self):
        self.dialogue.last_offered = ["ST_POMELO", "MAIN_LEMCHICKEN"]
        for ref, expected in [("offered_all", ["ST_POMELO", "MAIN_LEMCHICKEN"]),
                              ("offered_first", ["ST_POMELO"]), ("offered_second", ["MAIN_LEMCHICKEN"])]:
            result = self.resolve(IntentProposal(action="create_or_update_order", ref=ref), transcript="those")
            self.assertEqual(result.kind, "submit")
            self.assertEqual([item.sku for item in result.intent.items], expected)

    def test_offered_ref_keeps_the_models_pick_from_a_longer_offer(self):
        self.dialogue.last_offered = ["DS_COCONUT", "DS_BANANAFRIED", "DS_PLANTAIN", "DRINK_LEMON", "DRINK_BEER"]
        intent = IntentProposal(action="create_or_update_order", ref="offered_all", items=items("DS_COCONUT", "DS_BANANAFRIED"))
        result = self.resolve(intent, transcript="We'll take those two please")
        self.assertEqual([item.sku for item in result.intent.items], ["DS_COCONUT", "DS_BANANAFRIED"])
        stray = IntentProposal(action="create_or_update_order", ref="offered_first", items=items("MAIN_SEABASS"))
        self.assertEqual([item.sku for item in self.resolve(stray).intent.items], ["DS_COCONUT"])

    def test_offered_ref_without_an_offer_asks_which(self):
        result = self.resolve(IntentProposal(action="create_or_update_order", ref="offered_all"))
        self.assertEqual((result.kind, result.message), ("clarify", "which_offered"))

    def test_instead_after_refusal_adds_and_removes_nothing(self):
        self.dialogue.set_pending("offer_substitute", refused="MAIN_SQUID", offered="MAIN_SEABASS")
        order = order_with("SP_PHO", "DRINK_LEMON")
        for replaces in ["DRINK_LEMON", "MAIN_SQUID", None]:
            intent = IntentProposal(action="replace_item", items=items("MAIN_SEABASS"), replaces_sku=replaces)
            result = self.resolve(intent, order, "Okay, make it a grilled seabass instead")
            self.assertEqual(result.kind, "submit")
            self.assertEqual(result.intent.action, "create_or_update_order", replaces)
            self.assertEqual([item.sku for item in result.intent.items], ["MAIN_SEABASS"])
            self.assertTrue(result.clear_pending)

    def test_instead_without_a_refusal_swaps_the_named_or_last_added_dish(self):
        order = order_with("SP_PHO", "MAIN_SQUID")
        self.dialogue.last_added = ["MAIN_SQUID"]
        seabass = IntentProposal(action="create_or_update_order", ref="pending", items=items("MAIN_SEABASS"))
        swapped = self.resolve(seabass, order, "Okay, make it a grilled seabass instead")
        self.assertEqual((swapped.intent.action, swapped.intent.replaces_sku), ("replace_item", "MAIN_SQUID"))
        beer = IntentProposal(action="create_or_update_order", ref="pending", items=items("DRINK_BEER"))
        named = self.resolve(beer, order, "Actually, a beer instead of the pho")
        self.assertEqual((named.intent.action, named.intent.replaces_sku), ("replace_item", "SP_PHO"))
        tea_order = order_with("SP_PHO", "DRINK_LEMON")
        by_word = self.resolve(beer, tea_order, "Actually, a beer instead of the tea")
        self.assertEqual(by_word.intent.replaces_sku, "DRINK_LEMON")
        missing = self.resolve(beer, order_with("SP_PHO", "MAIN_SQUID"), "Actually, a beer instead of the iced tea")
        self.assertEqual((missing.kind, missing.message), ("clarify", "which_swap"))
        self.dialogue.last_added = []
        unclear = self.resolve(seabass, order, "seabass instead")
        self.assertEqual(unclear.intent.action, "create_or_update_order")

    def test_named_swap_beats_the_pending_offer(self):
        self.dialogue.set_pending("offer_substitute", refused="MAIN_SQUID", offered="ST_SPRINGROLL")
        intent = IntentProposal(action="replace_item", items=items("ST_SPRINGROLL"), replaces_sku="MAIN_SEABASS")
        result = self.resolve(intent, order_with("MAIN_SEABASS"), "swap my seabass for the spring rolls")
        self.assertEqual(result.intent.action, "replace_item")
        self.assertEqual(result.intent.replaces_sku, "MAIN_SEABASS")

    def test_replacing_a_dish_not_in_the_order_adds_it(self):
        intent = IntentProposal(action="replace_item", items=items("MAIN_SEABASS"), replaces_sku="MAIN_PORKCLAY")
        result = self.resolve(intent, order_with("SP_PHO"), "seabass instead of the pork")
        self.assertEqual(result.intent.action, "create_or_update_order")

    def test_full_basket_restatement_is_a_readback_that_offers_to_place(self):
        order = order_with("SP_PHO", "DRINK_LEMON")
        intent = IntentProposal(action="create_or_update_order", items=items("SP_PHO", "DRINK_LEMON"))
        result = self.resolve(intent, order, "That's all, please place the order")
        self.assertEqual(result.kind, "readback")
        self.assertEqual(result.new_pending, {"kind": "confirm_place"})
        self.assertEqual(self.resolve(intent, order, "another pho and another tea").kind, "submit")
        self.assertEqual(self.resolve(IntentProposal(action="create_or_update_order", items=items("SP_PHO")),
                                      order_with("SP_PHO"), "one pho").kind, "submit")

    def test_cancel_needs_a_yes(self):
        order = order_with("SP_PHO")
        asked = self.resolve(IntentProposal(action="cancel_order"), order)
        self.assertEqual((asked.kind, asked.message), ("ask", "confirm_cancel"))
        self.dialogue.set_pending(**asked.new_pending)
        confirmed = self.resolve(IntentProposal(action="confirm"), order)
        self.assertEqual((confirmed.kind, confirmed.intent.action), ("submit", "cancel_order"))
        declined = self.resolve(IntentProposal(action="decline"), order)
        self.assertEqual((declined.kind, declined.message), ("ask", "kept_order"))

    def test_confirm_routes_to_whatever_is_pending(self):
        order = order_with("SP_PHO")
        nothing = self.resolve(IntentProposal(action="confirm"), order)
        self.assertEqual((nothing.kind, nothing.message), ("ask", "confirm_place"))
        self.dialogue.set_pending("confirm_place")
        self.assertEqual(self.resolve(IntentProposal(action="confirm"), order).kind, "place")
        self.dialogue.set_pending("offer_substitute", refused="MAIN_SQUID", offered="MAIN_SEABASS")
        added = self.resolve(IntentProposal(action="confirm"), order)
        self.assertEqual([item.sku for item in added.intent.items], ["MAIN_SEABASS"])
        kitchen = self.resolve(IntentProposal(action="confirm"), order_with("SP_PHO", placed=True, status="substitution_proposed"))
        self.assertEqual(kitchen.intent.action, "accept_substitute")

    def test_place_ignores_needs_clarification_and_clears_the_offer(self):
        self.dialogue.set_pending("offer_substitute", refused="MAIN_SQUID", offered="MAIN_SEABASS")
        result = self.resolve(IntentProposal(action="place_order", needs_clarification=True), order_with("SP_PHO"))
        self.assertEqual(result.kind, "place")
        self.assertTrue(result.clear_pending)

    def test_late_ref_pending_after_a_refusal_adds_instead_of_swapping(self):
        # #37 review / #39: squid refused, morning glory ordered (offer cleared), then "seabass instead".
        self.dialogue.set_pending("offer_substitute", refused="MAIN_SQUID", offered="MAIN_SEABASS")
        self.dialogue.pending = None
        self.dialogue.last_added = ["VG_MORNING"]
        order = order_with("VG_MORNING")
        for intent in [IntentProposal(action="create_or_update_order", ref="pending", items=items("MAIN_SEABASS")),
                       IntentProposal(action="replace_item", ref="pending", items=items("MAIN_SEABASS")),
                       IntentProposal(action="replace_item", ref="pending", items=items("MAIN_SEABASS"), replaces_sku="VG_MORNING")]:
            result = self.resolve(intent, order, "Actually a seabass instead")
            self.assertEqual((result.intent.action, result.intent.replaces_sku), ("create_or_update_order", None), intent)
        named = self.resolve(IntentProposal(action="create_or_update_order", ref="pending", items=items("MAIN_SEABASS")),
                             order, "a seabass instead of the morning glory")
        self.assertEqual(named.intent.replaces_sku, "VG_MORNING")

    def test_pending_expires_and_round_trips(self):
        self.dialogue.set_pending("offer_substitute", refused="MAIN_SQUID", offered="MAIN_SEABASS")
        self.dialogue.begin_turn()
        self.dialogue.begin_turn()
        self.assertIsNotNone(self.dialogue.pending)
        self.dialogue.begin_turn()
        self.assertIsNone(self.dialogue.pending)
        self.dialogue.set_pending("confirm_cancel")
        self.dialogue.last_offered = ["ST_POMELO"]
        copy = DialogueState.from_dict(json.loads(json.dumps(self.dialogue.to_dict())))
        self.assertEqual(copy, self.dialogue)
        self.dialogue.begin_turn()
        self.dialogue.begin_turn()
        self.assertIsNone(self.dialogue.pending)


class ScriptedLLM:
    """Returns the intent a model would produce for each turn, reading the real current_state."""

    def __init__(self, fns):
        self.fns = list(fns)

    async def extract_intent(self, transcript, context):
        return self.fns.pop(0)(context["current_state"])


def six_turns(turn4="pending", turn6="place"):
    yield lambda st: IntentProposal(action="recommend")
    yield lambda st: IntentProposal(action="create_or_update_order", ref="offered_all")
    yield lambda st: IntentProposal(action="create_or_update_order", items=items("MAIN_SQUID"))
    yield {
        "pending": lambda st: IntentProposal(action="create_or_update_order", ref="pending", items=items("MAIN_SEABASS")),
        # The old prompt rule: replaces_sku taken from STATE.items (the iced tea).
        "state_items": lambda st: IntentProposal(action="replace_item", items=items("MAIN_SEABASS"), replaces_sku=st["items"][-1]["sku"]),
        "refused": lambda st: IntentProposal(action="replace_item", items=items("MAIN_SEABASS"), replaces_sku="MAIN_SQUID"),
    }[turn4]
    yield lambda st: IntentProposal(action="create_or_update_order", items=items("VG_MORNING"))
    yield {
        "place": lambda st: IntentProposal(action="place_order"),
        "restate": lambda st: IntentProposal(action="create_or_update_order",
                                             items=[IntentItem(sku=line["sku"], quantity=line["quantity"]) for line in st["items"]]),
        "cancel": lambda st: IntentProposal(action="cancel_order"),
    }[turn6]


class StaleRefusalFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_seabass_instead_after_the_offer_lapsed_keeps_the_morning_glory(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = SQLiteOrderRepository(Path(directory) / "orders.sqlite3")
            store = LanternStore()
            store.set_available("MAIN_SQUID", False)
            session = RealtimeSession("T4", OrderWorkflow(repo, store), ScriptedLLM([
                lambda st: IntentProposal(action="create_or_update_order", items=items("MAIN_SQUID")),
                lambda st: IntentProposal(action="create_or_update_order", items=items("VG_MORNING")),
                lambda st: IntentProposal(action="create_or_update_order", ref="pending", items=items("MAIN_SEABASS")),
            ]))
            for text in ["Crispy squid please", "One morning glory", "Actually a seabass instead"]:
                await session.handle_transcript(text)
            order = session.current_order()
            self.assertEqual(sorted(line["sku"] for line in order["basket"]), ["MAIN_SEABASS", "VG_MORNING"])
            self.assertEqual(order["total"], 21.0)
            self.assertEqual(DialogueState.from_dict(session.dialogue.to_dict()).last_refused, "MAIN_SQUID")
            repo.close()


class SixTurnScenarioTests(unittest.IsolatedAsyncioTestCase):
    async def run_scenario(self, fns, extra=(), offered=None):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        repo = SQLiteOrderRepository(Path(directory.name) / "orders.sqlite3")
        self.addCleanup(repo.close)
        store = LanternStore()
        store.set_available("MAIN_SQUID", False)
        session = RealtimeSession("T4", OrderWorkflow(repo, store), ScriptedLLM(list(fns) + list(extra)))
        replies = []
        for number, text in enumerate(TURNS + ["yes"] * len(extra)):
            replies.append(await session.handle_transcript(text))
            if number == 0 and offered:
                session.dialogue.last_offered = offered  # stands in for the fits-based recommender
        for reply in replies:
            self.assertNotRegex(reply["response_text"], RAW_STRINGS)
        self.assertEqual(len(repo.list_orders()), 1)
        return session, repo, replies

    def assert_basket(self, session, expected_skus):
        order = session.current_order()
        self.assertEqual(sorted(line["sku"] for line in order["basket"]), sorted(expected_skus))
        self.assertTrue(all(line["quantity"] == 1 for line in order["basket"]))
        return order

    async def test_every_turn4_phrasing_keeps_the_offered_pair(self):
        for turn4 in ["pending", "state_items", "refused"]:
            with self.subTest(turn4=turn4):
                session, _repo, replies = await self.run_scenario(six_turns(turn4))
                pair = list(replies[0]["recommendations"][i]["sku"] for i in range(2))
                order = self.assert_basket(session, pair + ["MAIN_SEABASS", "VG_MORNING"])
                self.assertEqual(order["status"], "pending_kitchen")
                self.assertTrue(order["placed"])
                self.assertIn("I sent it to the kitchen", replies[-1]["response_text"])

    async def test_restated_basket_reads_back_then_yes_places_it(self):
        session, _repo, replies = await self.run_scenario(six_turns(turn6="restate"), extra=[lambda st: IntentProposal(action="confirm")])
        self.assertIn("Shall I place your order now?", replies[5]["response_text"])
        self.assertFalse(replies[5]["wrote_revision"])
        order = self.assert_basket(session, [replies[0]["recommendations"][i]["sku"] for i in range(2)] + ["MAIN_SEABASS", "VG_MORNING"])
        self.assertTrue(order["placed"])

    async def test_cancel_instead_of_place_never_destroys_the_order(self):
        session, _repo, replies = await self.run_scenario(six_turns(turn6="cancel"))
        self.assertEqual(replies[5]["response_text"], "Cancel the whole order?")
        order = session.current_order()
        self.assertEqual((order["status"], len(order["basket"])), ("draft", 4))

    async def test_scenario_reaches_the_36_50_gate_with_the_expected_pair(self):
        session, _repo, replies = await self.run_scenario(six_turns(), offered=["ST_POMELO", "MAIN_LEMCHICKEN"])
        order = self.assert_basket(session, ["ST_POMELO", "MAIN_LEMCHICKEN", "MAIN_SEABASS", "VG_MORNING"])
        self.assertEqual(order["total"], 36.5)
        self.assertEqual(order["status"], "pending_kitchen")
        self.assertIn("Crispy Squid is sold out", replies[2]["response_text"])
        self.assertIn("$36.50", replies[5]["response_text"])


class KitchenVisibilityTests(unittest.TestCase):
    def test_drafts_stay_off_the_kitchen_board_until_placed(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = SQLiteOrderRepository(Path(directory) / "orders.sqlite3")
            store = LanternStore()
            workflow = OrderWorkflow(repo, store)
            original = (main.repository, main.store, main.workflow, main.broker)
            main.repository, main.store, main.workflow, main.broker = repo, store, workflow, KitchenEventBroker()
            try:
                client = TestClient(main.app)
                draft = workflow.submit("T4", "guest-1", "one seabass", IntentProposal(action="create_or_update_order", items=items("MAIN_SEABASS")))
                self.assertEqual(draft["status"], "draft")
                self.assertEqual(client.get("/api/kitchen/orders").json()["orders"], [])
                early = client.post(f"/api/kitchen/orders/{draft['order_id']}/decisions", json={"action": "accept", "expected_revision": 1})
                self.assertEqual(early.status_code, 400)

                placed = workflow.place("T4", "guest-1", "place it", draft["order_id"])
                self.assertEqual((placed["status"], placed["current_revision"]), ("pending_kitchen", 2))
                self.assertEqual([o["order_id"] for o in client.get("/api/kitchen/orders").json()["orders"]], [draft["order_id"]])
                again = workflow.place("T4", "guest-1", "place it", draft["order_id"])
                self.assertTrue(again["already_placed"])
                self.assertEqual(again["current_revision"], 2)
                accepted = client.post(f"/api/kitchen/orders/{draft['order_id']}/decisions", json={"action": "accept", "expected_revision": 2})
                self.assertEqual(accepted.status_code, 200)

                later = workflow.submit("T4", "guest-1", "and a beer", IntentProposal(action="create_or_update_order", items=items("DRINK_BEER")), draft["order_id"])
                self.assertEqual(later["status"], "pending_kitchen")
                self.assertTrue(later["placed"])
            finally:
                main.repository, main.store, main.workflow, main.broker = original
                repo.close()


class GuestTextTests(unittest.TestCase):
    def test_errors_become_sentences_with_dish_names(self):
        text = render_clarification({"errors": ["MAIN_SQUID is not in the order"]}, "en", {"MAIN_SQUID": "Crispy Squid"})
        self.assertEqual(text, "Crispy Squid isn't in your order.")
        for error in ["unknown sku: FOO_BAR", "replace_item requires one new item and replaces_sku", "something new"]:
            self.assertNotRegex(render_clarification({"errors": [error]}, "en"), RAW_STRINGS)
        spanish = render_clarification({"errors": ["allergen conflict for ST_POMELO: peanut"]}, "es", {"ST_POMELO": "Pomelo Salad with Shrimp"})
        self.assertIn("Pomelo Salad with Shrimp", spanish)
        self.assertNotIn("ST_POMELO", spanish)


class PromptContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_prompt_drops_the_state_items_rule_and_requires_ref(self):
        captured = {}

        def handler(request):
            captured.update(json.loads(request.content))
            return httpx.Response(200, json={"response": json.dumps({"action": "place_order", "ref": "none", "items": []})})

        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        client = OllamaClient("http://ollama.test", "qwen3:4b", client=http)
        intent = await client.extract_intent("that's all", {"menu": [], "current_state": {"pending": None, "last_offered": []}})
        await http.aclose()
        self.assertEqual(intent.action, "place_order")
        self.assertNotIn("replaces_sku=Y from STATE.items", captured["prompt"])
        self.assertIn("place_order", captured["prompt"])
        self.assertEqual(captured["format"]["required"], ["action", "ref", "items"])
        self.assertIn("pending", captured["format"]["properties"]["ref"]["enum"])


if __name__ == "__main__":
    unittest.main()
