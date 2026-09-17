"""Offline tests for the CrossRef provider adapter."""

from __future__ import annotations

import unittest
from unittest.mock import Mock

from bibreview.identity import IdentityError
from bibreview.providers.crossref import CrossRefError, CrossRefProvider


class CrossRefProviderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.transport = Mock()
        self.provider = CrossRefProvider(self.transport, mailto="contact@example.test")

    def test_returns_crossref_message(self) -> None:
        self.transport.json.return_value = {
            "status": "ok",
            "message": {"title": ["Example"]},
        }
        result = self.provider.work("DOI: 10.1000/ABC")
        self.assertEqual(result, {"title": ["Example"]})
        url = self.transport.json.call_args.args[0]
        kwargs = self.transport.json.call_args.kwargs
        self.assertEqual(url, "https://api.crossref.org/works/10.1000%2Fabc")
        self.assertEqual(kwargs["params"], {"mailto": "contact@example.test"})
        self.assertEqual(kwargs["context"], "CrossRef metadata for DOI 10.1000/abc")

    def test_absent_work_returns_none(self) -> None:
        self.transport.json.return_value = None
        self.assertIsNone(self.provider.work("10.1000/missing"))

    def test_mailto_is_optional(self) -> None:
        provider = CrossRefProvider(self.transport)
        self.transport.json.return_value = {"status": "ok", "message": {}}
        provider.work("10.1000/test")
        self.assertEqual(self.transport.json.call_args.kwargs["params"], {})

    def test_unexpected_success_shape_is_rejected(self) -> None:
        for data in [[], {}, {"status": "error"}, {"status": "ok", "message": []}]:
            with self.subTest(data=data):
                self.transport.json.reset_mock()
                self.transport.json.return_value = data
                with self.assertRaisesRegex(CrossRefError, "unexpected response"):
                    self.provider.work("10.1000/test")

    def test_invalid_doi_is_rejected_before_network_access(self) -> None:
        with self.assertRaises(IdentityError):
            self.provider.work("not-a-doi")
        self.transport.json.assert_not_called()

    def test_reserved_doi_characters_are_encoded(self) -> None:
        self.transport.json.return_value = {"status": "ok", "message": {}}
        self.provider.work("10.1000/a?b#c")
        self.assertEqual(
            self.transport.json.call_args.args[0],
            "https://api.crossref.org/works/10.1000%2Fa%3Fb%23c",
        )


if __name__ == "__main__":
    unittest.main()
