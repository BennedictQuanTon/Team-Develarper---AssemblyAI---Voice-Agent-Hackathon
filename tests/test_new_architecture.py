import tempfile
import unittest
from pathlib import Path

from backend.app.domain.restaurant.models import IntentProposal, IntentItem
from backend.app.domain.restaurant.repository import SQLiteOrderRepository
from backend.app.domain.restaurant.store import LanternStore
from backend.app.domain.restaurant.validation import validate_intent
from backend.app.providers.tts.kokoro import KokoroProvider


class NewArchitectureTests(unittest.TestCase):
    def test_kokoro_mapping_and_caption_fallback(self):
        provider = KokoroProvider("hexgrad/Kokoro-82M")
        self.assertEqual(provider.voice_for("ja"), ("j", "jf_alpha"))
        self.assertFalse(provider.synthesize("bonjour", "xx").supported)

    def test_validation_rejects_unknown_and_modifier(self):
        store = LanternStore()
        intent = IntentProposal(items=[IntentItem(sku="UNKNOWN", quantity=1)])
        self.assertTrue(validate_intent(intent, store))

    def test_sqlite_revisions_and_stale_decision(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = SQLiteOrderRepository(Path(directory) / "lantern.sqlite3")
            order = repo.create_revision("o1", "T4", "g1", "ja", "注文", [{"sku": "MAIN_SEABASS", "quantity": 1}], [])
            self.assertEqual(order["current_revision"], 1)
            with self.assertRaises(ValueError):
                repo.decide("o1", {"expected_revision": 0, "action": "accept"})
            updated = repo.decide("o1", {"expected_revision": 1, "action": "accept"})
            self.assertEqual(updated["status"], "committed")
            repo.close()


if __name__ == "__main__":
    unittest.main()
