"""prefetch_for_case: read-only lookups handed to the model so it skips a round trip."""

from __future__ import annotations

from unittest import mock

from backend.app.domain.waiter import WaiterSession
from backend.app.pipeline.waiter_agent import PREFETCH_TOOLS, _query_terms, prefetch_for_case
from tests.support import IsolatedTestCase, fresh_store

READ_ONLY_TOOLS = {"search_menu", "recommend_dishes", "check_availability", "get_floor", "readback"}


class QueryTermsTest(IsolatedTestCase):
    def test_keeps_content_words(self) -> None:
        self.assertEqual(_query_terms("Hey, do you have any grilled squid tonight?"), "hey grilled squid tonight")

    def test_drops_stopwords_short_words_and_punctuation(self) -> None:
        self.assertEqual(_query_terms("Do you have it?"), "")
        self.assertEqual(_query_terms('"Pho!"'), "pho")


class PrefetchTest(IsolatedTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.store = fresh_store()
        self.ss = WaiterSession(self.store, session_id="unit-prefetch")

    def test_only_read_only_tools_are_prefetched(self) -> None:
        for case, tools in PREFETCH_TOOLS.items():
            with self.subTest(case=case):
                self.assertTrue(set(tools) <= READ_ONLY_TOOLS, tools)

    def test_order_turns_get_no_prefetch(self) -> None:
        """Order turns always call a mutating tool that returns the order; a prefetched readback
        put the total in context twice and the model summed the copies."""
        self.assertNotIn("case_order_process", PREFETCH_TOOLS)
        self.ss.add_item("MAIN_SEABASS")
        self.assertEqual(prefetch_for_case(self.ss, "case_order_process", "place the order"), {})

    def test_mention_stack_is_restored_exactly(self) -> None:
        self.ss._record_mentions([self.store.get_item("SP_PHO"), self.store.get_item("VG_TOFU")])
        before = list(self.ss.mentioned)
        result = prefetch_for_case(self.ss, "case_specialty_rec", "what are your house specialties")
        self.assertIn("recommend_dishes", result)
        self.assertEqual(self.ss.mentioned, before)

    def test_search_prefetch_does_not_shift_the_mention_stack(self) -> None:
        before = list(self.ss.mentioned)
        result = prefetch_for_case(self.ss, "case_dish_check", "do you have grilled squid")
        self.assertIn("search_menu", result)
        self.assertEqual(self.ss.mentioned, before)

    def test_stopword_only_dish_check_skips_the_search(self) -> None:
        self.assertEqual(prefetch_for_case(self.ss, "case_dish_check", "do you have any"), {})

    def test_a_failing_lookup_never_breaks_the_turn(self) -> None:
        with mock.patch.object(self.ss, "get_floor", side_effect=RuntimeError("boom")):
            self.assertEqual(prefetch_for_case(self.ss, "case_table_check", "a table for three"), {})

    def test_unknown_case_prefetches_nothing(self) -> None:
        self.assertEqual(prefetch_for_case(self.ss, "case_general", "hello"), {})

    def test_basket_is_untouched(self) -> None:
        self.ss.add_item("MAIN_SEABASS", 2, ["no chili"])
        before = self.ss.snapshot()
        for case in ("case_specialty_rec", "case_dish_check", "case_table_check", "case_order_process"):
            prefetch_for_case(self.ss, case, "grilled squid table for two")
        self.assertEqual(self.ss.snapshot()["basket"], before["basket"])
