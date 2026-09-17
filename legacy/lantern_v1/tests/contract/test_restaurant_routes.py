from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

from backend.app.main import app


class RestaurantRouteContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(app)

    def test_api_identity_and_restaurant_routes(self) -> None:
        api = self.client.get("/api")
        self.assertEqual(api.status_code, 200)
        self.assertIn("Lantern", api.json()["message"])

        menu = self.client.get("/menu")
        self.assertEqual(menu.status_code, 200)
        self.assertGreater(menu.json()["count"], 0)
        self.assertEqual(menu.json()["restaurant"], "The Lantern")

        floor = self.client.get("/floor")
        self.assertEqual(floor.status_code, 200)
        self.assertGreater(len(floor.json()["tables"]), 0)
        self.assertIn("free", floor.json()["status_counts"])

    def test_realtime_websocket_sends_session_ready(self) -> None:
        with self.client.websocket_connect("/ws/realtime") as websocket:
            payload = websocket.receive_json()

        self.assertEqual(payload["type"], "session_ready")
        self.assertIn("session_id", payload)
