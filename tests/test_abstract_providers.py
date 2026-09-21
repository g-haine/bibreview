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

    def test_optional_api_key_is_sent_in_header(self):
        transport = Mock()
        transport.json.return_value = {"abstract": "A useful abstract."}
        provider = SemanticScholarProvider(transport, api_key=" secret-key ")

        provider.abstract("10.1/test")

        call = transport.json.call_args
        self.assertEqual(call.kwargs["headers"], {"x-api-key": "secret-key"})

    def test_missing_or_unexpected_payload_is_empty(self):
        transport = Mock()
        provider = SemanticScholarProvider(transport)
        for payload in (None, [], {}, {"abstract": None}):
            transport.json.return_value = payload
            self.assertEqual(provider.abstract("10.1/test"), "")


class MendeleyProviderTests(unittest.TestCase):
    def test_requires_non_empty_client_credentials(self):
        with self.assertRaisesRegex(ValueError, "client ID must not be empty"):
            MendeleyProvider(Mock(), client_id="  ", client_secret="secret")
        with self.assertRaisesRegex(ValueError, "client secret must not be empty"):
            MendeleyProvider(Mock(), client_id="client", client_secret="  ")

    def test_client_credentials_token_then_catalog_and_html_page(self):
        transport = Mock()
        transport.post_form_json.return_value = {
            "access_token": "access-token",
            "expires_in": 3600,
        }
        transport.json.return_value = [{"link": "https://publisher.test/article"}]
        words = " ".join(f"word{i}" for i in range(45))
        response = Mock()
        response.text = (
            '<div class="card"><h3 data-name="abstract-title">Abstract</h3>'
            f'<p data-name="content"><span>{words}</span></p></div>'
        )
        transport.request.return_value = response
        provider = MendeleyProvider(
            transport,
            client_id="application-id",
            client_secret="application-secret",
        )

        self.assertEqual(provider.abstract("10.1/TEST"), words)

        oauth = transport.post_form_json.call_args
        self.assertEqual(oauth.args[0], MendeleyProvider.TOKEN_URL)
        self.assertEqual(
            oauth.kwargs["data"],
            {"grant_type": "client_credentials", "scope": "all"},
        )
        self.assertEqual(
            oauth.kwargs["auth"],
            ("application-id", "application-secret"),
        )

        catalog = transport.json.call_args
        self.assertEqual(catalog.kwargs["params"], {"doi": "10.1/test", "view": "all"})
        self.assertEqual(
            catalog.kwargs["headers"]["Authorization"],
            "Bearer access-token",
        )

    def test_cached_token_is_reused_until_expiry(self):
        transport = Mock()
        transport.post_form_json.side_effect = [
            {"access_token": "token-1", "expires_in": 100},
            {"access_token": "token-2", "expires_in": 100},
        ]
        transport.json.return_value = []
        now = [0.0]
        provider = MendeleyProvider(
            transport,
            client_id="application-id",
            client_secret="application-secret",
            clock=lambda: now[0],
        )

        provider.abstract("10.1/one")
        now[0] = 10.0
        provider.abstract("10.1/two")
        self.assertEqual(transport.post_form_json.call_count, 1)

        now[0] = 95.0
        provider.abstract("10.1/three")
        self.assertEqual(transport.post_form_json.call_count, 2)
        self.assertEqual(
            transport.json.call_args.kwargs["headers"]["Authorization"],
            "Bearer token-2",
        )

    def test_catalog_401_forces_one_token_refresh(self):
        from bibreview.providers.http import HttpError

        transport = Mock()
        transport.post_form_json.side_effect = [
            {"access_token": "token-1", "expires_in": 3600},
            {"access_token": "token-2", "expires_in": 3600},
        ]
        transport.json.side_effect = [
            HttpError("unauthorized", status_code=401),
            [],
        ]
        provider = MendeleyProvider(
            transport,
            client_id="application-id",
            client_secret="application-secret",
        )

        self.assertEqual(provider.abstract("10.1/test"), "")
        self.assertEqual(transport.post_form_json.call_count, 2)
        self.assertEqual(transport.json.call_count, 2)
        self.assertEqual(
            transport.json.call_args.kwargs["headers"]["Authorization"],
            "Bearer token-2",
        )

    def test_invalid_oauth_response_is_rejected(self):
        transport = Mock()
        provider = MendeleyProvider(
            transport,
            client_id="application-id",
            client_secret="application-secret",
        )
        for payload in (
            None,
            {},
            {"access_token": ""},
            {"access_token": "token"},
            {"access_token": "token", "expires_in": "3600"},
        ):
            transport.post_form_json.return_value = payload
            with self.assertRaises(ValueError):
                provider.authenticate()

    def test_empty_catalog_or_missing_link_is_empty(self):
        transport = Mock()
        transport.post_form_json.return_value = {
            "access_token": "access-token",
            "expires_in": 3600,
        }
        provider = MendeleyProvider(
            transport,
            client_id="application-id",
            client_secret="application-secret",
        )
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
