import tempfile
import unittest
import json
from pathlib import Path

import httpx

from backend.app.domain.restaurant.models import IntentProposal, IntentItem
from backend.app.domain.restaurant.repository import SQLiteOrderRepository
from backend.app.domain.restaurant.store import LanternStore
from backend.app.domain.restaurant.validation import validate_intent
from backend.app.providers.tts.kokoro import KokoroProvider
from backend.app.providers.llm.ollama import OllamaClient
from backend.app.providers.asr.assemblyai_stream import CONNECT_TIMEOUT_S, AssemblyAIRealtimeProvider
from backend.app.services.realtime_session import RealtimeSession
from eval.benchmarks.restaurant.new_architecture import load_dataset, run_offline


class NewArchitectureTests(unittest.TestCase):
    def test_kokoro_mapping_and_caption_fallback(self):
        provider = KokoroProvider("hexgrad/Kokoro-82M")
        self.assertEqual(provider.voice_for("ja"), ("j", "jf_alpha"))
        self.assertFalse(provider.synthesize("bonjour", "xx").supported)

    def test_validation_rejects_unknown_and_modifier(self):
        store = LanternStore()
        intent = IntentProposal(items=[IntentItem(sku="UNKNOWN", quantity=1)])
        self.assertTrue(validate_intent(intent, store))
        empty_order = IntentProposal(action="create_or_update_order", items=[])
        self.assertIn("create_or_update_order requires at least one item", validate_intent(empty_order, store))

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

    def test_new_architecture_benchmark_demonstrates_core_strengths(self):
        result = run_offline(load_dataset())
        self.assertEqual(result["status"], "PASS")
        strengths = {item["name"]: item["passed"] for item in result["strengths"]}
        self.assertTrue(strengths["stale_revision_rejected"])
        self.assertTrue(strengths["immutable_history"])
        self.assertTrue(strengths["sqlite_restart_recovery"])
        self.assertTrue(result["safety_probes"][0]["passed"])

class ProviderLatencyContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_clarification_never_persists_an_empty_revision(self):
        class FakeLlm:
            async def extract_intent(self, _transcript, _context):
                return IntentProposal(action="clarify", clarification_question="Which dish?")

        class FakeWorkflow:
            store = LanternStore()
            submitted = False

            def submit(self, *_args):
                self.submitted = True
                raise AssertionError("clarifications must not be persisted")

        workflow = FakeWorkflow()
        result = await RealtimeSession("T4", workflow, FakeLlm()).handle_transcript("something")
        self.assertEqual(result["status"], "clarification_required")
        self.assertFalse(workflow.submitted)

    async def test_ollama_uses_bounded_non_thinking_schema_request(self):
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured.update(json.loads(request.content))
            return httpx.Response(
                200,
                json={
                    "response": json.dumps(
                        {
                            "source_language": "en",
                            "action": "create_or_update_order",
                            "items": [{"sku": "MAIN_SEABASS", "quantity": 1, "modifiers": ["no chili"]}],
                            "allergies": [],
                            "dietary_constraints": [],
                            "substitution_response": None,
                            "needs_clarification": False,
                            "clarification_question": None,
                        }
                    )
                },
            )

        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        client = OllamaClient("http://ollama.test", "qwen3:4b", client=http)
        intent = await client.extract_intent(
            "one seabass no chili",
            {"menu": [{"sku": "MAIN_SEABASS", "name": "Grilled Seabass", "modifiers": ["no chili"], "description": "must not enter compact prompt"}]},
        )
        self.assertEqual(intent.items[0].sku, "MAIN_SEABASS")
        self.assertFalse(captured["think"])
        self.assertEqual(captured["keep_alive"], -1)
        self.assertEqual(captured["options"]["temperature"], 0)
        self.assertLessEqual(captured["options"]["num_predict"], 224)
        self.assertIsInstance(captured["format"], dict)
        self.assertNotIn("must not enter compact prompt", captured["prompt"])
        await http.aclose()

    async def test_assemblyai_adapter_buffers_pcm_and_disconnects(self):
        class FakeClient:
            def __init__(self, **_kwargs):
                self.handlers = {}
                self.streamed = []
                self.connected = False
                self.disconnected = False

            def on(self, event, callback):
                self.handlers[event] = callback

            async def connect(self, _params):
                self.connected = True

            async def stream(self, chunk):
                self.streamed.append(chunk)

            async def disconnect(self):
                self.disconnected = True

            async def force_endpoint(self):
                return None

        fake = FakeClient()
        factory_kwargs = {}

        def factory(**kwargs):
            factory_kwargs.update(kwargs)
            return fake

        provider = AssemblyAIRealtimeProvider("test-key", client_factory=factory)
        await provider.connect()
        await provider.send_audio(bytes(3200))
        await provider.close()
        options = factory_kwargs["options"]
        self.assertEqual(options.api_key, "test-key")
        self.assertEqual(options.connect_timeout, CONNECT_TIMEOUT_S)
        self.assertGreater(CONNECT_TIMEOUT_S, 1.0)
        self.assertEqual(options.max_connection_retries, 2)
        self.assertEqual(options.connection_retry_delay, 0.5)
        self.assertTrue(fake.connected)
        self.assertEqual([len(chunk) for chunk in fake.streamed], [3200])
        self.assertTrue(fake.disconnected)


if __name__ == "__main__":
    unittest.main()
