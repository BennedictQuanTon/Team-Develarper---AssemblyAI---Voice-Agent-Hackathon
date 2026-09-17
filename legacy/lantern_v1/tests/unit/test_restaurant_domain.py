from __future__ import annotations

import unittest

from backend.app.domain.restaurant.order_session import WaiterSession
from backend.app.domain.restaurant.store import LanternStore


class RestaurantDomainTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = LanternStore()
        self.session = WaiterSession(self.store, session_id="unit-test")

    def test_mentioned_dishes_can_be_added_and_placed(self) -> None:
        recommendations = self.session.recommend_dishes(["mild", "couple"], party_size=2, limit=2)

        self.assertEqual(len(recommendations["items"]), 2)
        added = self.session.add_items_from_mention("those two")
        self.assertEqual(len(added["added"]), 2)
        self.assertGreater(added["total"], 0)

        modifier = self.session.set_modifier("1", ["no green onions"])
        self.assertEqual(modifier["updated"]["modifiers"], ["no green onions"])

        placed = self.session.place_order()
        self.assertTrue(placed["placed"])
        self.assertTrue(placed["ticket_id"].startswith("LAN-"))
        self.assertIsNotNone(placed["table_id"])

    def test_sold_out_item_is_never_added(self) -> None:
        item = self.store.get_item("MAIN_SQUID")
        self.assertIsNotNone(item)
        self.store.set_available("MAIN_SQUID", False)

        result = self.session.add_item("MAIN_SQUID")

        self.assertTrue(result["not_added"])
        self.assertEqual(self.session.snapshot()["basket"], [])
        self.assertIn("sold out", result["error"].lower())

    def test_snapshot_restore_discards_interrupted_turn_changes(self) -> None:
        before = self.session.snapshot()
        self.session.add_item("MAIN_SQUID")
        self.assertEqual(len(self.session.snapshot()["basket"]), 1)

        self.session.restore(before)

        self.assertEqual(self.session.snapshot(), before)
