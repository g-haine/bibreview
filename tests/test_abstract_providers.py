from __future__ import annotations

from unittest.mock import Mock
import unittest

from bibreview.providers.mendeley import MendeleyProvider, mendeley_abstract
from bibreview.providers.semantic_scholar import SemanticScholarProvider


class SemanticScholarProviderTests(unittest.TestCase):
    def test_returns_abstract_and_encodes_doi(self):
        transport = Mock()
        transport.json.return_value = {"abstract": "  A useful abstract.  "}
        provider = SemanticScholarProvider(transport)

        self.assertEqual(provider.abstract("10.1/A?B"), "A useful abstract.")
        call = transport.json.call_args
        self.assertIn("DOI:10.1%2Fa%3Fb", call.args[0])
        self.assertEqual(call.kwargs["params"], {"fields": "abstract"})

    def test_missing_or_unexpected_payload_is_empty(self):
        transport = Mock()
        provider = SemanticScholarProvider(transport)
        for payload in (None, [], {}, {"abstract": None}):
            transport.json.return_value = payload
            self.assertEqual(provider.abstract("10.1/test"), "")


class MendeleyProviderTests(unittest.TestCase):
    def test_requires_non_empty_token(self):
        with self.assertRaisesRegex(ValueError, "token must not be empty"):
            MendeleyProvider(Mock(), token="  ")

    def test_catalog_then_html_page(self):
        transport = Mock()
        transport.json.return_value = [{"link": "https://publisher.test/article"}]
        words = " ".join(f"word{i}" for i in range(45))
        response = Mock()
        response.text = (
            '<div class="card"><h3 data-name="abstract-title">Abstract</h3>'
            f'<p data-name="content"><span>{words}</span></p></div>'
        )
        transport.request.return_value = response
        provider = MendeleyProvider(transport, token="secret")

        self.assertEqual(provider.abstract("10.1/TEST"), words)
        catalog = transport.json.call_args
        self.assertEqual(catalog.kwargs["params"], {"doi": "10.1/test", "view": "all"})
        self.assertEqual(
            catalog.kwargs["headers"]["Authorization"], "Bearer secret"
        )
        page = transport.request.call_args
        self.assertEqual(page.args[0], "https://publisher.test/article")
        self.assertEqual(page.kwargs["headers"]["User-Agent"], "BibReview abstract-fallback")

    def test_empty_catalog_or_missing_link_is_empty(self):
        transport = Mock()
        provider = MendeleyProvider(transport, token="secret")
        for payload in (None, {}, [], [{}], [{"link": ""}]):
            transport.json.return_value = payload
            self.assertEqual(provider.abstract("10.1/test"), "")
        transport.request.assert_not_called()


class MendeleyMarkupTests(unittest.TestCase):
    def test_card_and_meta_shapes(self):
        words = " ".join(f"word{i}" for i in range(45))
        card = (
            '<div class="card"><h3 data-name="abstract-title">Abstract</h3>'
            f'<p data-name="content"><span>{words}</span></p></div>'
        )
        meta = f'<meta name="citation_abstract" content="{words}">'
        self.assertEqual(mendeley_abstract(card), words)
        self.assertEqual(mendeley_abstract(meta), words)

    def test_short_or_truncated_description_is_rejected(self):
        short = " ".join(f"word{i}" for i in range(20))
        teaser = " ".join(f"word{i}" for i in range(45)) + "..."
        self.assertEqual(
            mendeley_abstract(f'<meta name="description" content="{short}">'), ""
        )
        self.assertEqual(
            mendeley_abstract(f'<meta name="description" content="{teaser}">'), ""
        )


if __name__ == "__main__":
    unittest.main()
