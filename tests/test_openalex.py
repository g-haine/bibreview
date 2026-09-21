"""Offline tests for the OpenAlex discovery adapter."""

from __future__ import annotations

import unittest
from unittest.mock import Mock

from bibreview.providers.openalex import OpenAlexError, OpenAlexProvider


class OpenAlexProviderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.transport = Mock()

    def test_fetches_one_work_by_doi_with_selected_fields(self) -> None:
        self.transport.json.return_value = {"id": "https://openalex.org/W1"}
        provider = OpenAlexProvider(self.transport, api_key="secret")

        result = provider.work("10.1000/A?B")

        self.assertEqual(result, {"id": "https://openalex.org/W1"})
        call = self.transport.json.call_args
        self.assertIn("https://doi.org/10.1000%2Fa%3Fb", call.args[0])
        self.assertIn("authorships", call.kwargs["params"]["select"])
        self.assertEqual(call.kwargs["params"]["api_key"], "secret")

    def test_work_returns_none_for_absent_record_and_rejects_bad_shape(self) -> None:
        provider = OpenAlexProvider(self.transport)
        self.transport.json.return_value = None
        self.assertIsNone(provider.work("10.1000/test"))

        self.transport.json.return_value = []
        with self.assertRaisesRegex(OpenAlexError, "unexpected work response"):
            provider.work("10.1000/test")

    def test_discovers_unique_normalized_dois_across_pages(self) -> None:
        self.transport.json.side_effect = [
            {
                "results": [
                    {"doi": "https://doi.org/10.1000/ABC"},
                    {"doi": "10.1000/abc"},
                    {"doi": None},
                ],
                "meta": {"next_cursor": "page-2"},
            },
            {
                "results": [
                    {"doi": "https://doi.org/10.1000/DEF"},
                    {"doi": "not-a-doi"},
                ],
                "meta": {"next_cursor": None},
            },
        ]
        provider = OpenAlexProvider(self.transport, api_key="secret")
        result = provider.discover("fluid-structure interaction", max_pages=5)
        self.assertEqual(result, ("10.1000/abc", "10.1000/def"))
        first = self.transport.json.call_args_list[0]
        self.assertEqual(first.args[0], "https://api.openalex.org/works")
        self.assertEqual(
            first.kwargs["params"]["filter"],
            "title_and_abstract.search:fluid-structure interaction",
        )
        self.assertEqual(first.kwargs["params"]["api_key"], "secret")
        self.assertEqual(first.kwargs["params"]["cursor"], "*")
        self.assertEqual(self.transport.json.call_count, 2)

    def test_repeated_cursor_stops_pagination(self) -> None:
        self.transport.json.side_effect = [
            {"results": [], "meta": {"next_cursor": "same"}},
            {"results": [], "meta": {"next_cursor": "same"}},
        ]
        provider = OpenAlexProvider(self.transport)
        self.assertEqual(provider.discover("query", max_pages=10), ())
        self.assertEqual(self.transport.json.call_count, 2)

    def test_page_limit_is_respected(self) -> None:
        self.transport.json.side_effect = [
            {"results": [{"doi": "10.1000/a"}], "meta": {"next_cursor": "two"}},
            {"results": [{"doi": "10.1000/b"}], "meta": {"next_cursor": "three"}},
        ]
        provider = OpenAlexProvider(self.transport)
        self.assertEqual(provider.discover("query", max_pages=1), ("10.1000/a",))
        self.assertEqual(self.transport.json.call_count, 1)

    def test_rejects_empty_query_and_non_positive_page_limit(self) -> None:
        provider = OpenAlexProvider(self.transport)
        with self.assertRaisesRegex(ValueError, "query"):
            provider.discover("   ")
        with self.assertRaisesRegex(ValueError, "max_pages"):
            provider.discover("query", max_pages=0)
        self.transport.json.assert_not_called()

    def test_unexpected_page_shape_is_rejected(self) -> None:
        provider = OpenAlexProvider(self.transport)
        for data in [None, [], {}, {"results": [], "meta": []}]:
            with self.subTest(data=data):
                self.transport.json.reset_mock()
                self.transport.json.return_value = data
                with self.assertRaisesRegex(OpenAlexError, "unexpected page response"):
                    provider.discover("query")


if __name__ == "__main__":
    unittest.main()
