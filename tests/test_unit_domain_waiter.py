"""WaiterSession ordering rules: the deterministic truth every LLM path relies on."""

from __future__ import annotations

from backend.app.domain.waiter import WaiterSession
from tests.support import IsolatedTestCase, fresh_store


class OrderItemsTest(IsolatedTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.store = fresh_store()
        self.ss = WaiterSession(self.store, session_id="unit-domain")

    def names(self) -> list[str]:
        return [line.name for line in self.ss.basket]

    def test_lines_and_total(self) -> None:
        result = self.ss.order_items(
            [{"ref": "Grilled Seabass", "qty": 2, "modifiers": ["no chili"]}, {"ref": "MAIN_LEMCHICKEN"}]
        )
        self.assertEqual(result["order_lines"], ["2x Grilled Seabass (no chili)", "1x Lemongrass Chicken"])
        self.assertEqual(result["total"], 41.0)

    def test_restated_dish_is_not_a_second_helping(self) -> None:
        self.ss.order_items([{"ref": "Grilled Seabass"}])
        result = self.ss.order_items([{"ref": "Grilled Seabass"}, {"ref": "Beef Pho"}])
        self.assertEqual(self.names(), ["Grilled Seabass", "Beef Pho"])
        self.assertIn("already_in_order", result["items"][0])

    def test_explicit_qty_still_orders_more(self) -> None:
        self.ss.order_items([{"ref": "Grilled Seabass"}])
        self.ss.order_items([{"ref": "Grilled Seabass", "qty": 2}])
        self.assertEqual(sum(line.qty for line in self.ss.basket if line.name == "Grilled Seabass"), 3)

    def test_different_modifiers_make_a_new_line(self) -> None:
        self.ss.order_items([{"ref": "Lemongrass Chicken"}])
        self.ss.order_items([{"ref": "Lemongrass Chicken", "modifiers": ["mild"]}])
        self.assertEqual([(line.name, line.modifiers) for line in self.ss.basket],
                         [("Lemongrass Chicken", []), ("Lemongrass Chicken", ["mild"])])

    def test_those_two_adds_the_last_two_mentioned(self) -> None:
        self.ss._record_mentions([self.store.get_item("ST_POMELO"), self.store.get_item("MAIN_LEMCHICKEN")])
        self.ss.order_items([{"ref": "those two"}])
        self.assertEqual(self.names(), ["Pomelo Salad with Shrimp", "Lemongrass Chicken"])

    def test_pronoun_modifiers_apply_to_every_added_line(self) -> None:
        self.ss._record_mentions([self.store.get_item("ST_POMELO"), self.store.get_item("MAIN_LEMCHICKEN")])
        self.ss.order_items([{"ref": "those two", "modifiers": ["mild"]}])
        self.assertTrue(all(line.modifiers == ["mild"] for line in self.ss.basket))

    def test_sold_out_dish_is_refused_with_a_substitute(self) -> None:
        self.store.set_available("MAIN_SQUID", False)
        result = self.ss.order_items([{"ref": "Crispy Squid"}])
        entry = result["items"][0]
        self.assertTrue(entry["not_added"])
        self.assertIsNotNone(entry["suggested_substitute"])
        self.assertEqual(self.ss.basket, [])
        self.assertEqual(result["total"], 0.0)

    def test_unknown_dish_is_an_error(self) -> None:
        result = self.ss.order_items([{"ref": "Unicorn Steak"}])
        self.assertIn("error", result["items"][0])
        self.assertEqual(self.ss.basket, [])

    def test_place_true_places_the_order(self) -> None:
        result = self.ss.order_items([{"ref": "Beef Pho"}], place=True)
        self.assertIs(result["place_result"]["placed"], True)
        self.assertTrue(result["place_result"]["ticket_id"].startswith("LAN-"))
        self.assertTrue(self.ss.placed)

    def test_placing_an_empty_order_is_an_error(self) -> None:
        result = self.ss.order_items([], place=True)
        self.assertIn("error", result["place_result"])
        self.assertFalse(self.ss.placed)

    def test_sold_out_dish_inside_a_pronoun_ref_with_modifiers_is_reported(self) -> None:
        """Regression: applying modifiers after a pronoun ref used to drop the sold-out entry.

        A caller that only sees success here would confirm an order that silently lost a dish.
        """
        self.ss._record_mentions([self.store.get_item("ST_POMELO"), self.store.get_item("MAIN_SQUID")])
        self.store.set_available("MAIN_SQUID", False)
        result = self.ss.order_items([{"ref": "those two", "modifiers": ["mild"]}])
        added = result["items"][0]["added"]
        self.assertEqual(self.names(), ["Pomelo Salad with Shrimp"])
        self.assertTrue(any(isinstance(e, dict) and e.get("error") for e in added), added)


class MutatingResultsCarryTheOrderTest(IsolatedTestCase):
    """Every mutating tool reports the resulting order, so no round trip is spent asking for it."""

    def setUp(self) -> None:
        super().setUp()
        self.store = fresh_store()
        self.ss = WaiterSession(self.store, session_id="unit-summary")

    def test_each_mutating_tool(self) -> None:
        self.ss._record_mentions([self.store.get_item("SP_PHO")])
        calls = {
            "add_item": lambda: self.ss.add_item("MAIN_SEABASS"),
            "add_items_from_mention": lambda: self.ss.add_items_from_mention("that one"),
            "set_modifier": lambda: self.ss.set_modifier("MAIN_SEABASS", ["no chili"]),
            "remove_item": lambda: self.ss.remove_item("Grilled Seabass"),
            "order_items": lambda: self.ss.order_items([{"ref": "Crispy Spring Rolls"}]),
        }
        for tool, call in calls.items():
            with self.subTest(tool=tool):
                result = call()
                self.assertIn("order_lines", result)
                self.assertIn("total", result)

    def test_place_order_reads_the_order_back(self) -> None:
        self.ss.add_item("MAIN_SEABASS")
        result = self.ss.place_order()
        self.assertIs(result["placed"], True)
        self.assertEqual(result["lines"], ["1x Grilled Seabass"])
        self.assertEqual(result["total"], 16.0)

    def test_seat_party_carries_the_floor(self) -> None:
        table = self.store.find_free_table(2)
        result = self.ss.seat_party(table.id, 2)
        self.assertIn("seated", result)
        self.assertIn("free_tables", result)
