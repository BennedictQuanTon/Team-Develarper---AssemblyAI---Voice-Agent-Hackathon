"""SQLite dialogue memory, the fits recommender and the spoken-modifier check (#33, #35)."""
import tempfile
import unittest
from pathlib import Path

from backend.app.domain.restaurant.mentions import spoken_modifiers
from backend.app.domain.restaurant.models import IntentItem, IntentProposal
from backend.app.domain.restaurant.recommend import guest_tags, recommend
from backend.app.domain.restaurant.repository import SQLiteOrderRepository
from backend.app.domain.restaurant.store import LanternStore
from backend.app.domain.restaurant.workflow import OrderWorkflow
from backend.app.services.realtime_session import RealtimeSession

TURNS = [
    "What would you recommend for a mild couple?",
    "We'll take those two please",
    "I'd like the crispy squid too",
    "Okay, make it a grilled seabass instead",
    "And stir-fried morning glory on the side",
    "That's all, please place the order",
]


def item(sku, *modifiers):
    return IntentItem(sku=sku, quantity=1, modifiers=list(modifiers))


def greedy_six_turns():
    """What qwen3:4b produced in #35 and #37: right actions, but every allowed modifier attached."""
    return [
        IntentProposal(action="recommend"),
        IntentProposal(action="create_or_update_order", ref="offered_all"),
        IntentProposal(action="create_or_update_order", items=[item("MAIN_SQUID", "no garlic", "extra lime")]),
        IntentProposal(action="create_or_update_order", ref="pending",
                       items=[item("MAIN_SEABASS", "no chili", "extra lime", "butter baste")]),
        IntentProposal(action="create_or_update_order",
                       items=[item("VG_MORNING", "no garlic", "no fish sauce", "with oyster sauce")]),
        IntentProposal(action="place_order"),
    ]


class ScriptedLLM:
    def __init__(self, intents):
        self.intents = list(intents)

    async def extract_intent(self, transcript, context):
        return self.intents.pop(0)


class RecommenderTests(unittest.TestCase):
    def setUp(self):
        self.store = LanternStore()

    def skus(self, transcript, allergies=None):
        return [dish.sku for dish in recommend(self.store, transcript, allergies)]

    def test_mild_couple_gets_the_scenario_pair(self):
        self.assertEqual(self.skus("What would you recommend for a mild couple?"), ["ST_POMELO", "MAIN_LEMCHICKEN"])

    def test_not_spicy_means_mild_not_spicy_ok(self):
        tags, _party = guest_tags("Something not too spicy for two please")
        self.assertIn("mild", tags)
        self.assertNotIn("spicy_ok", tags)

    def test_drinks_only_when_asked(self):
        self.assertTrue(all(self.store.get_item(sku).category not in {"Drink", "Dessert"}
                            for sku in self.skus("What do you recommend?")))
        self.assertTrue(all(self.store.get_item(sku).category == "Drink" for sku in self.skus("Which drink do you recommend?")))

    def test_declared_allergy_is_never_recommended(self):
        picks = self.skus("What would you recommend for a mild couple?", ["peanut"])
        self.assertNotIn("ST_POMELO", picks)
        self.assertTrue(all("peanut" not in self.store.get_item(sku).allergens for sku in picks))

    def test_spanish_request(self):
        tags, party = guest_tags("¿Qué nos recomienda para una pareja, algo suave?")
        self.assertEqual((sorted(tags), party), (["couple", "mild"], 2))


class SpokenModifierTests(unittest.TestCase):
    def test_keeps_only_what_the_guest_said(self):
        self.assertEqual(spoken_modifiers(["no chili", "extra lime", "butter baste"], "One grilled seabass, no chili please"), ["no chili"])
        self.assertEqual(spoken_modifiers(["no garlic", "no fish sauce", "with oyster sauce"], "And stir-fried morning glory on the side"), [])

    def test_the_dish_name_does_not_select_a_modifier(self):
        self.assertEqual(spoken_modifiers(["no peanut", "extra shrimp"], "the pomelo salad with shrimp", "Pomelo Salad with Shrimp"), [])
        # "shrimp" belongs to the dish name, so only the plural "peanuts" selects a modifier.
        self.assertEqual(spoken_modifiers(["no peanut", "extra shrimp"], "pomelo salad with extra shrimp, no peanuts", "Pomelo Salad with Shrimp"),
                         ["no peanut"])


class SessionHarness(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.repo = SQLiteOrderRepository(Path(directory.name) / "orders.sqlite3")
        self.addCleanup(self.repo.close)
        self.store = LanternStore()
        self.store.set_available("MAIN_SQUID", False)
        self.workflow = OrderWorkflow(self.repo, self.store)

    def session(self, intents, **resume):
        return RealtimeSession("T4", self.workflow, ScriptedLLM(intents), **resume)


class SixTurnGateTests(SessionHarness):
    async def test_six_turns_pass_every_gate_with_the_real_recommender(self):
        session = self.session(greedy_six_turns())
        replies = [await session.handle_transcript(text) for text in TURNS]
        order = session.current_order()
        self.assertEqual([line["sku"] for line in order["basket"]], ["ST_POMELO", "MAIN_LEMCHICKEN", "MAIN_SEABASS", "VG_MORNING"])
        self.assertEqual(order["total"], 36.5)
        self.assertTrue(all(line["modifiers"] == [] and line["quantity"] == 1 for line in order["basket"]))
        self.assertTrue(order["placed"])
        self.assertEqual(len(self.repo.list_orders()), 1)
        self.assertIn("Crispy Squid is sold out", replies[2]["response_text"])
        self.assertIn("$36.50", replies[5]["response_text"])
        self.assertTrue(all("{" not in reply["response_text"] for reply in replies))


class ReplyLanguageTests(SessionHarness):
    async def test_spanish_session_stays_spanish_when_the_model_says_english(self):
        session = self.session([
            IntentProposal(source_language="es", action="recommend"),
            IntentProposal(source_language="en", action="create_or_update_order", ref="offered_all"),
            IntentProposal(source_language="en", action="place_order"),
        ])
        replies = [await session.handle_transcript("¿Qué nos recomienda para una pareja, algo suave?", "en"),
                   await session.handle_transcript("Queremos esos dos, por favor", "en"),
                   await session.handle_transcript("Eso es todo, envíe el pedido", "en")]
        self.assertTrue(replies[1]["response_text"].startswith("Su pedido ahora incluye"))
        self.assertIn("Lo envié a la cocina", replies[2]["response_text"])
        self.assertEqual(replies[2]["language_code"], "es")

    async def test_transport_language_beats_an_english_label(self):
        session = self.session([IntentProposal(source_language="en", action="recommend")])
        reply = await session.handle_transcript("¿Qué nos recomienda?", "es")
        self.assertTrue(reply["response_text"].startswith("Le recomiendo"))

    async def test_english_guest_stays_english(self):
        session = self.session([IntentProposal(source_language="en", action="recommend")])
        reply = await session.handle_transcript("What do you recommend?", "en")
        self.assertTrue(reply["response_text"].startswith("I recommend"))


class DialoguePersistenceTests(SessionHarness):
    async def test_state_is_saved_every_turn(self):
        session = self.session([IntentProposal(action="recommend")])
        await session.handle_transcript(TURNS[0])
        saved = self.repo.load_dialogue(session.session_id, "T4")
        self.assertEqual(saved["last_offered"], ["ST_POMELO", "MAIN_LEMCHICKEN"])

    async def test_reload_mid_order_keeps_the_offer_and_the_refusal(self):
        intents = greedy_six_turns()
        first = self.session(intents[:3])
        for text in TURNS[:3]:
            await first.handle_transcript(text)
        self.assertEqual(first.dialogue.pending["refused"], "MAIN_SQUID")
        # The page reloads: main.py resumes the order and its guest session id.
        second = self.session(intents[3:], session_id=first.session_id, order_id=first.order_id)
        self.assertEqual(second.dialogue.to_dict(), first.dialogue.to_dict())
        for text in TURNS[3:]:
            await second.handle_transcript(text)
        order = second.current_order()
        self.assertEqual(order["total"], 36.5)
        self.assertTrue(order["placed"])

    async def test_another_table_cannot_load_the_state(self):
        session = self.session([IntentProposal(action="recommend")])
        await session.handle_transcript(TURNS[0])
        self.assertIsNone(self.repo.load_dialogue(session.session_id, "T5"))
        self.repo.save_dialogue(session.session_id, "T5", {"last_offered": ["SP_PHO"]})
        self.assertEqual(self.repo.load_dialogue(session.session_id, "T4")["last_offered"], ["ST_POMELO", "MAIN_LEMCHICKEN"])

    async def test_new_guest_starts_empty(self):
        self.assertEqual(self.session([]).dialogue.to_dict(), {"last_offered": [], "last_added": [], "pending": None, "last_refused": None})


if __name__ == "__main__":
    unittest.main()
