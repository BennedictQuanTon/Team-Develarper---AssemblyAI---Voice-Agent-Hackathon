"""Settings defaults: the local model is the default provider, and nothing depends on the .env file."""

from __future__ import annotations

import os
from unittest import mock

from backend.app.config import get_settings
from backend.app.pipeline.llm_ollama import parse_keep_alive, parse_optional_bool
from tests.support import IsolatedTestCase


class DefaultsTest(IsolatedTestCase):
    def test_local_ollama_is_the_default_provider(self) -> None:
        with mock.patch.dict(os.environ, {"LLM_PROVIDER": ""}):
            os.environ.pop("LLM_PROVIDER")
            get_settings.cache_clear()
            self.assertEqual(get_settings().llm_provider, "ollama")

    def test_ollama_defaults(self) -> None:
        settings = get_settings()
        self.assertEqual(settings.ollama_model, "qwen2.5:3b")
        self.assertEqual(settings.ollama_keep_alive, "-1")
        self.assertEqual(settings.ollama_num_ctx, 4096)
        self.assertEqual(settings.ollama_num_predict, 256)
        self.assertEqual(settings.ollama_reasoning, "")

    def test_template_replies_are_off_until_the_ab_decides(self) -> None:
        with mock.patch.dict(os.environ, {"WAITER_TEMPLATE_REPLIES": ""}):
            os.environ.pop("WAITER_TEMPLATE_REPLIES")
            get_settings.cache_clear()
            self.assertFalse(get_settings().waiter_template_replies)

    def test_environment_overrides(self) -> None:
        with mock.patch.dict(os.environ, {"WAITER_TEMPLATE_REPLIES": "true", "OLLAMA_NUM_CTX": "8192"}):
            get_settings.cache_clear()
            settings = get_settings()
            self.assertTrue(settings.waiter_template_replies)
            self.assertEqual(settings.ollama_num_ctx, 8192)


class ParsersTest(IsolatedTestCase):
    def test_keep_alive(self) -> None:
        self.assertEqual(parse_keep_alive("-1"), -1)
        self.assertEqual(parse_keep_alive("300"), 300)
        self.assertEqual(parse_keep_alive(" 5m "), "5m")
        self.assertEqual(parse_keep_alive(""), -1)
        self.assertEqual(parse_keep_alive(None), -1)
        self.assertEqual(parse_keep_alive(120), 120)

    def test_optional_bool(self) -> None:
        self.assertIsNone(parse_optional_bool(""))
        self.assertIsNone(parse_optional_bool(None))
        self.assertIs(parse_optional_bool("false"), False)
        self.assertIs(parse_optional_bool("TRUE"), True)
        self.assertIs(parse_optional_bool(False), False)
        with self.assertRaises(ValueError):
            parse_optional_bool("maybe")
