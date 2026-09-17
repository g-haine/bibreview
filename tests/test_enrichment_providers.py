from __future__ import annotations

from dataclasses import dataclass
import unittest

from bibreview.providers import (
    DoiProvider,
    ElsevierProvider,
    Enrichment,
    IeeeProvider,
    PublisherEnrichmentRouter,
    SpringerProvider,
    format_bibtex,
)


@dataclass
class Response:
    url: str = ""
    text: str = ""
    status_code: int = 200


class FakeTransport:
    def __init__(self) -> None:
        self.request_response = Response()
        self.json_response = None
        self.requests = []
        self.json_calls = []

    def request(self, url, **kwargs):
        self.requests.append((url, kwargs))
        return self.request_response

    def json(self, url, **kwargs):
        self.json_calls.append((url, kwargs))
        return self.json_response


class DoiProviderTests(unittest.TestCase):
    def test_landing_url_accepts_denied_redirected_publishers(self) -> None:
        transport = FakeTransport()
        transport.request_response = Response(
            url="https://link.springer.com/article/10.1/test", status_code=403
        )
        provider = DoiProvider(transport)

        self.assertEqual(
            provider.landing_url("DOI: 10.1/TEST"),
            "https://link.springer.com/article/10.1/test",
        )
        url, kwargs = transport.requests[0]
        self.assertIn("10.1%2Ftest", url)
        self.assertEqual(kwargs["accepted_redirect_statuses"], frozenset({401, 403}))

    def test_bibtex_uses_content_negotiation_and_formatter(self) -> None:
        transport = FakeTransport()
        transport.request_response = Response(
            url="https://doi.org/10.1/test",
            text="@article{key, title={A title}, pages={1–9}, month={jan}, url={x}, }",
        )
        provider = DoiProvider(transport)

        result = provider.bibtex("10.1/test")

        _, kwargs = transport.requests[0]
        self.assertEqual(kwargs["headers"], {"Accept": "application/x-bibtex;q=1.0"})
        self.assertIn("title={{A title}}", result)
        self.assertIn("pages={1--9}", result)
        self.assertNotIn("month", result)
        self.assertNotIn("url=", result)
        self.assertTrue(result.endswith("\n"))

    def test_citation_normalizes_numbering_and_trailing_marker(self) -> None:
        transport = FakeTransport()
        transport.request_response = Response(text="header\n1. Ada Lovelace (2024).\n")
        provider = DoiProvider(transport)

        self.assertEqual(provider.citation("10.1/TEST"), "Ada Lovelace (2024)")
        url, kwargs = transport.requests[0]
        self.assertEqual(url, "https://citation.doi.org/format")
        self.assertEqual(kwargs["params"]["doi"], "10.1/test")

    def test_formatter_returns_explicit_fallback_for_non_bibtex_text(self) -> None:
        self.assertEqual(format_bibtex("not bibtex"), "No BibTeX found!\n")


class PublisherProviderTests(unittest.TestCase):
    def test_elsevier_mapping_and_credentials(self) -> None:
        transport = FakeTransport()
        transport.json_response = {
            "full-text-retrieval-response": {
                "coredata": {
                    "dc:description": "<p>Text</p>",
                    "dcterms:subject": [{"$": "Control"}],
                    "authkeywords": "Energy;control",
                    "prism:issueName": "Conference",
                }
            }
        }
        result = ElsevierProvider(transport, api_key=" secret ").enrich("10.1/TEST")

        self.assertEqual(result, Enrichment("Text", ("control", "energy"), "Conference"))
        url, kwargs = transport.json_calls[0]
        self.assertIn("10.1%2Ftest", url)
        self.assertEqual(kwargs["headers"]["X-ELS-APIKey"], "secret")

    def test_springer_mapping_and_credentials(self) -> None:
        transport = FakeTransport()
        transport.json_response = {
            "records": [{
                "abstract": "<p>Text</p>",
                "keyword": "Energy; Control; control",
                "conferenceInfo": [{"confSeriesName": "Conference"}],
            }]
        }
        result = SpringerProvider(transport, api_key=" secret ").enrich("10.1/TEST")

        self.assertEqual(result, Enrichment("Text", ("control", "energy"), "Conference"))
        url, kwargs = transport.json_calls[0]
        self.assertEqual(url, "https://api.springernature.com/meta/v2/json")
        self.assertEqual(kwargs["params"]["api_key"], "secret")
        self.assertEqual(kwargs["params"]["q"], "doi:10.1/test")

    def test_ieee_mapping_and_credentials(self) -> None:
        transport = FakeTransport()
        transport.json_response = {
            "articles": [{
                "abstract": "<p>Text</p>",
                "author_terms": ["Control"],
                "index_terms": {"ieee_terms": ["Energy", "control"]},
                "content_type": "Conferences",
                "publication_title": "Conference",
            }]
        }
        result = IeeeProvider(transport, api_key=" secret ").enrich("10.1/TEST")

        self.assertEqual(result, Enrichment("Text", ("control", "energy"), "Conference"))
        url, kwargs = transport.json_calls[0]
        self.assertIn("10.1%2Ftest", url)
        self.assertEqual(kwargs["params"]["apikey"], "secret")

    def test_empty_or_unexpected_provider_payloads_are_empty_enrichment(self) -> None:
        for provider_type in (ElsevierProvider, SpringerProvider, IeeeProvider):
            transport = FakeTransport()
            transport.json_response = None
            provider = provider_type(transport, api_key="secret")
            self.assertEqual(provider.enrich("10.1/test"), Enrichment())

    def test_api_keys_are_required_but_not_loaded_from_environment(self) -> None:
        for provider_type in (ElsevierProvider, SpringerProvider, IeeeProvider):
            with self.assertRaises(ValueError):
                provider_type(FakeTransport(), api_key="  ")


class RouterTests(unittest.TestCase):
    def test_routes_known_publishers_and_leaves_unknown_hosts_empty(self) -> None:
        class FakeDoi:
            landing = ""

            def landing_url(self, doi):
                return self.landing

        class Provider:
            def __init__(self, name):
                self.name = name
                self.calls = []

            def enrich(self, doi):
                self.calls.append(doi)
                return Enrichment(event=self.name)

        doi = FakeDoi()
        elsevier = Provider("elsevier")
        springer = Provider("springer")
        ieee = Provider("ieee")
        router = PublisherEnrichmentRouter(
            doi, elsevier=elsevier, springer=springer, ieee=ieee
        )

        cases = [
            ("https://www.sciencedirect.com/science/article/pii/x", "elsevier"),
            ("https://link.springer.com/article/x", "springer"),
            ("https://ieeexplore.ieee.org/document/x", "ieee"),
            ("https://example.org/paper", ""),
        ]
        for landing, expected in cases:
            doi.landing = landing
            self.assertEqual(router.enrich("10.1/test").event, expected)

        self.assertEqual(elsevier.calls, ["10.1/test"])
        self.assertEqual(springer.calls, ["10.1/test"])
        self.assertEqual(ieee.calls, ["10.1/test"])

    def test_known_publisher_without_configured_provider_is_nonfatal(self) -> None:
        class FakeDoi:
            def landing_url(self, doi):
                return "https://link.springer.com/article/x"

        router = PublisherEnrichmentRouter(FakeDoi())
        self.assertEqual(router.enrich("10.1/test"), Enrichment())


if __name__ == "__main__":
    unittest.main()
