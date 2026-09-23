"""Template replies: render only clean order changes, and only from values the tools returned.

Fixtures come from real WaiterSession calls, so a change in a tool's result shape breaks these tests
instead of silently breaking the templates.
"""

from __future__ import annotations

import re

from backend.app.domain.waiter import WaiterSession
from backend.app.pipeline.waiter_templates import (
    is_clean_success,
    render_confirmation,
    speak_line,
    speak_order_lines,
)
from tests.support import IsolatedTestCase, fresh_store


def call(tool: str, result: dict, args: dict | None = None) -> dict:
    return {"tool": tool, "args": args or {}, "result": result}


class RendersCleanChangesTest(IsolatedTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.store = fresh_store()
        self.ss = WaiterSession(self.store, session_id="unit-templates")

    def test_added_dishes(self) -> None:
        result = self.ss.order_items([{"ref": "Grilled Seabass", "modifiers": ["no chili"]}, {"ref": "Beef Pho"}])
        text = render_confirmation([call("order_items", result)])
        self.assertEqual(
            text, "Got it. That's one Grilled Seabass with no chili and one Beef Pho, $23.50 so far. Anything else?"
        )

    def test_placed_in_the_same_call(self) -> None:
        result = self.ss.order_items([{"ref": "Grilled Seabass"}], place=True)
        text = render_confirmation([call("order_items", result)])
        self.assertEqual(text, "Your order is in: one Grilled Seabass. Your total is $16.00. Thank you!")

    def test_place_order_tool(self) -> None:
        self.ss.add_item("SP_PHO", 2)
        text = render_confirmation([call("place_order", self.ss.place_order())])
        self.assertEqual(text, "Your order is in: two Beef Pho. Your total is $15.00. Thank you!")

    def test_takeout_mentions_the_eta(self) -> None:
        self.ss.ticket_type = "takeout"
        self.ss.add_item("SP_PHO")
        text = render_confirmation([call("place_order", self.ss.place_order())])
        self.assertRegex(text, r"It'll be ready in about \d+ minutes\.$")

    def test_removed_with_items_left(self) -> None:
        self.ss.add_item("SP_PHO")
        self.ss.add_item("MAIN_SEABASS")
        text = render_confirmation([call("remove_item", self.ss.remove_item("Beef Pho"))])
        self.assertEqual(text, "Removed. Now you have one Grilled Seabass, $16.00. Anything else?")

    def test_removed_the_last_item(self) -> None:
        self.ss.add_item("SP_PHO")
        self.assertEqual(
            render_confirmation([call("remove_item", self.ss.remove_item("Beef Pho"))]),
            "Removed. Your order is empty now.",
        )

    def test_restated_dish(self) -> None:
        self.ss.order_items([{"ref": "Beef Pho"}])
        text = render_confirmation([call("order_items", self.ss.order_items([{"ref": "Beef Pho"}]))])
        self.assertEqual(text, "That's already in your order: one Beef Pho, $7.50. Anything else?")

    def test_pronoun_ref(self) -> None:
        self.ss._record_mentions([self.store.get_item("ST_POMELO"), self.store.get_item("MAIN_LEMCHICKEN")])
        text = render_confirmation([call("add_items_from_mention", self.ss.add_items_from_mention("those two"))])
        self.assertEqual(
            text, "Got it. That's one Pomelo Salad with Shrimp and one Lemongrass Chicken, $15.50 so far. Anything else?"
        )

    def test_reads_the_final_basket_from_the_last_call(self) -> None:
        first = self.ss.order_items([{"ref": "Beef Pho"}])
        second = self.ss.order_items([{"ref": "Crispy Spring Rolls"}])
        text = render_confirmation([call("order_items", first), call("order_items", second)])
        self.assertIn("one Beef Pho and one Crispy Spring Rolls, $12.50", text)


class FallsBackToTheModelTest(IsolatedTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.store = fresh_store()
        self.ss = WaiterSession(self.store, session_id="unit-templates-fallback")

    def assertNoTemplate(self, *round_log: dict) -> None:
        self.assertIsNone(render_confirmation(list(round_log)))

    def test_sold_out(self) -> None:
        self.store.set_available("MAIN_SQUID", False)
        self.assertNoTemplate(call("order_items", self.ss.order_items([{"ref": "Crispy Squid"}])))
        self.assertNoTemplate(call("add_item", self.ss.add_item("MAIN_SQUID")))

    def test_sold_out_dish_inside_a_pronoun_ref_with_modifiers(self) -> None:
        self.ss._record_mentions([self.store.get_item("ST_POMELO"), self.store.get_item("MAIN_SQUID")])
        self.store.set_available("MAIN_SQUID", False)
        result = self.ss.order_items([{"ref": "those two", "modifiers": ["mild"]}])
        self.assertNoTemplate(call("order_items", result))

    def test_clarification_needed(self) -> None:
        self.assertNoTemplate(call("add_items_from_mention", self.ss.add_items_from_mention("those two")))

    def test_unknown_dish(self) -> None:
        self.assertNoTemplate(call("order_items", self.ss.order_items([{"ref": "Unicorn Steak"}])))

    def test_placing_an_empty_order(self) -> None:
        self.assertNoTemplate(call("order_items", self.ss.order_items([], place=True)))
        self.assertNoTemplate(call("place_order", self.ss.place_order()))

    def test_nothing_happened(self) -> None:
        self.assertNoTemplate(call("order_items", self.ss.order_items([])))
        self.assertNoTemplate(call("remove_item", self.ss.remove_item("Beef Pho")))

    def test_excluded_tools(self) -> None:
        self.ss.add_item("MAIN_SEABASS")
        self.assertNoTemplate(call("set_modifier", self.ss.set_modifier("MAIN_SEABASS", ["no chili"])))
        table = self.store.find_free_table(2)
        self.assertNoTemplate(call("seat_party", self.ss.seat_party(table.id, 2)))

    def test_a_round_that_also_looked_something_up(self) -> None:
        self.assertNoTemplate(
            call("search_menu", self.ss.search_menu("pho")),
            call("order_items", self.ss.order_items([{"ref": "Beef Pho"}])),
        )

    def test_unknown_tool_and_empty_round(self) -> None:
        self.assertNoTemplate(call("delete_everything", {"order_lines": [], "total": 0}))
        self.assertIsNone(render_confirmation([]))
        self.assertFalse(is_clean_success("order_items", "not a dict"))


class NeverInventsValuesTest(IsolatedTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.store = fresh_store()
        self.ss = WaiterSession(self.store, session_id="unit-templates-values")

    def test_prices_and_dishes_come_from_the_tool_result(self) -> None:
        rounds = [
            [call("order_items", self.ss.order_items([{"ref": "Grilled River Prawns", "qty": 2}]))],
            [call("order_items", self.ss.order_items([{"ref": "Green Papaya Salad", "modifiers": ["mild", "no peanut"]}]))],
            [call("remove_item", self.ss.remove_item("Green Papaya Salad"))],
            [call("order_items", self.ss.order_items([{"ref": "Bun Bo Hue"}], place=True))],
        ]
        for round_log in rounds:
            with self.subTest(tool=round_log[-1]["tool"]):
                text = render_confirmation(round_log)
                self.assertIsNotNone(text)
                result = round_log[-1]["result"]
                source = result.get("place_result") or result
                totals = {f"{float(source['total']):.2f}"}
                self.assertTrue(set(re.findall(r"\$(\d+\.\d{2})", text)) <= totals, text)
                for line in source.get("order_lines") or source.get("lines") or []:
                    dish = re.match(r"^\d+x (.+?)(?: \(|$)", line).group(1)
                    self.assertIn(dish, text)

    def test_a_four_dish_confirmation_stays_short(self) -> None:
        result = self.ss.order_items(
            [{"ref": "Beef Pho"}, {"ref": "Crispy Spring Rolls"}, {"ref": "Lemongrass Tofu"}, {"ref": "Jasmine Hot Tea"}]
        )
        self.assertLessEqual(len(render_confirmation([call("order_items", result)]).split()), 35)


class SpeechFormattingTest(IsolatedTestCase):
    def test_lines(self) -> None:
        self.assertEqual(speak_line("2x Grilled Seabass (no chili, extra lime)"),
                         "two Grilled Seabass with no chili and extra lime")
        self.assertEqual(speak_line("12x Beef Pho"), "12 Beef Pho")
        self.assertEqual(speak_line("something unexpected"), "something unexpected")

    def test_lists(self) -> None:
        self.assertEqual(speak_order_lines(["1x A"]), "one A")
        self.assertEqual(speak_order_lines(["1x A", "2x B"]), "one A and two B")
        self.assertEqual(speak_order_lines(["1x A", "2x B", "3x C"]), "one A, two B, and three C")
        self.assertEqual(speak_order_lines([]), "")
