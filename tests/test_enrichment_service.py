from __future__ import annotations

import io
import unittest

from bibreview.pipeline.collect import collect
from bibreview.pipeline.enrich import EnrichmentService, crossref_enrichment
from bibreview.providers.base import Enrichment
from bibreview.reporting import Reporter


class StubPublisher:
    def __init__(self, value: Enrichment):
        self.value = value
        self.calls: list[str] = []

    def enrich(self, doi: str) -> Enrichment:
        self.calls.append(doi)
        return self.value


class StubFallback:
    def __init__(self, value: str):
        self.value = value
        self.calls: list[str] = []
        self.batch_calls: list[tuple[str, ...]] = []

    def abstract(self, doi: str) -> str:
        self.calls.append(doi)
        return self.value

    def abstract_many(self, dois: tuple[str, ...]):
        self.batch_calls.append(dois)
        return {doi: self.value for doi in dois}


class StubWorkProvider:
    def __init__(self, work: dict):
        self.work_record = work

    def work(self, doi: str) -> dict:
        return self.work_record


class CrossRefEnrichmentTests(unittest.TestCase):
    def test_extracts_and_cleans_crossref_fields(self) -> None:
        result = crossref_enrichment(
            {
                "abstract": "Abstract  CrossRef text ",
                "subject": [" Control ", "", None, "Energy"],
            }
        )
        self.assertEqual(result.abstract, "CrossRef text")
        self.assertEqual(result.keywords, ("Control", "Energy"))
        self.assertEqual(result.event, "")

    def test_missing_fields_are_empty(self) -> None:
        self.assertEqual(crossref_enrichment({}), Enrichment())


class EnrichmentServiceTests(unittest.TestCase):
    def test_collection_prefers_non_empty_publisher_fields(self) -> None:
        publisher = StubPublisher(
            Enrichment(
                abstract="<p>Publisher abstract</p>",
                keywords=("publisher",),
                event="Conference",
            )
        )
        fallback = StubFallback("Fallback abstract")
        service = EnrichmentService(publisher=publisher, fallback=fallback)

        result = service.for_collection(
            "10.1/test",
            {"abstract": "CrossRef abstract", "subject": ["CrossRef"]},
        )

        self.assertEqual(result.abstract, "Publisher abstract")
        self.assertEqual(result.keywords, ("publisher",))
        self.assertEqual(result.event, "Conference")
        self.assertEqual(publisher.calls, ["10.1/test"])
        self.assertEqual(fallback.calls, [])

    def test_collection_keeps_crossref_field_when_publisher_field_is_empty(self) -> None:
        publisher = StubPublisher(Enrichment(event="Proceedings"))
        service = EnrichmentService(publisher=publisher)

        result = service.for_collection(
            "10.1/test",
            {"abstract": "CrossRef text", "subject": ["Control", "Energy"]},
        )

        self.assertEqual(result.abstract, "CrossRef text")
        self.assertEqual(result.keywords, ("Control", "Energy"))
        self.assertEqual(result.event, "Proceedings")

    def test_discovery_accumulates_crossref_and_publisher_fields(self) -> None:
        publisher = StubPublisher(
            Enrichment(
                abstract=" Publisher text",
                keywords=("publisher", "extra"),
                event="Event",
            )
        )
        service = EnrichmentService(publisher=publisher)

        result = service.for_discovery(
            "10.1/test",
            {"abstract": "CrossRef text", "subject": ["Control"]},
        )

        self.assertEqual(result.abstract, "CrossRef text Publisher text")
        self.assertEqual(result.keywords, ("Control", "publisher", "extra"))
        self.assertEqual(result.event, "Event")

    def test_fallback_is_used_only_when_primary_abstract_is_empty(self) -> None:
        fallback = StubFallback("Fallback abstract")
        service = EnrichmentService(
            publisher=StubPublisher(Enrichment(keywords=("publisher",))),
            fallback=fallback,
        )

        result = service.for_collection("10.1/test", {"subject": ["CrossRef"]})

        self.assertEqual(result.abstract, "Fallback abstract")
        self.assertEqual(result.keywords, ("publisher",))
        self.assertEqual(fallback.calls, ["10.1/test"])

    def test_collection_many_batches_only_dois_that_still_need_fallback(self) -> None:
        publisher = StubPublisher(Enrichment())
        fallback = StubFallback("Fallback abstract")
        service = EnrichmentService(
            publisher=publisher,
            fallback=fallback,
        )

        result = service.for_collection_many(
            {
                "10.1/one": {"abstract": "CrossRef abstract"},
                "10.1/two": {},
                "10.1/three": {},
            }
        )

        self.assertEqual(
            result["10.1/one"].abstract,
            "CrossRef abstract",
        )
        self.assertEqual(
            result["10.1/two"].abstract,
            "Fallback abstract",
        )
        self.assertEqual(
            fallback.batch_calls,
            [("10.1/two", "10.1/three")],
        )
        self.assertEqual(
            publisher.calls,
            ["10.1/one", "10.1/two", "10.1/three"],
        )


    def test_collection_uses_fallback_when_crossref_markup_is_unsafe(self) -> None:
        stream = io.StringIO()
        fallback = StubFallback("Fallback abstract")
        service = EnrichmentService(
            fallback=fallback,
            reporter=Reporter(0, stream),
        )

        result = service.for_collection(
            "10.1/test",
            {
                "abstract": (
                    'A controller <jats:inline-graphic '
                    'xlink:href="graphic/math-0002.png"/> is proposed.'
                )
            },
        )

        self.assertEqual(result.abstract, "Fallback abstract")
        self.assertEqual(fallback.calls, ["10.1/test"])
        self.assertEqual(len(result.abstract_evidence), 1)
        self.assertEqual(result.abstract_evidence[0].source, "crossref")
        self.assertEqual(result.abstract_evidence[0].reason, "embedded-graphic")
        self.assertIn("math-0002.png", result.abstract_evidence[0].value)
        self.assertIn("CrossRef abstract for 10.1/test", stream.getvalue())
        self.assertIn("embedded-graphic", stream.getvalue())

    def test_collection_keeps_crossref_when_publisher_markup_is_unsafe(self) -> None:
        stream = io.StringIO()
        publisher = StubPublisher(
            Enrichment(
                abstract="(u<inf>0</inf>)<sup>T</sup>",
                keywords=("publisher",),
            )
        )
        service = EnrichmentService(
            publisher=publisher,
            reporter=Reporter(0, stream),
        )

        result = service.for_collection(
            "10.1/test",
            {"abstract": "<p>CrossRef text</p>"},
        )

        self.assertEqual(result.abstract, "CrossRef text")
        self.assertEqual(result.keywords, ("publisher",))
        self.assertEqual(len(result.abstract_evidence), 1)
        self.assertEqual(result.abstract_evidence[0].source, "publisher")
        self.assertEqual(result.abstract_evidence[0].reason, "script-markup")
        self.assertIn("<inf>0</inf>", result.abstract_evidence[0].value)
        self.assertIn("Publisher abstract for 10.1/test", stream.getvalue())
        self.assertIn("script-markup", stream.getvalue())

    def test_no_fallback_provider_leaves_abstract_empty(self) -> None:
        service = EnrichmentService()
        self.assertEqual(service.for_collection("10.1/test", {}).abstract, "")

    def test_invalid_publisher_contract_is_rejected(self) -> None:
        class InvalidPublisher:
            def enrich(self, doi: str):
                return {"abstract": "not Enrichment"}

        service = EnrichmentService(publisher=InvalidPublisher())
        with self.assertRaisesRegex(TypeError, "must return Enrichment"):
            service.for_collection("10.1/test", {})

    def test_collection_service_plugs_directly_into_collect_pipeline(self) -> None:
        work = {
            "title": ["Example"],
            "type": "journal-article",
            "author": [{"given": "Ada", "family": "Lovelace"}],
            "abstract": "CrossRef abstract",
            "subject": ["CrossRef"],
            "container-title": ["Journal"],
            "created": {"date-parts": [[2026, 9, 17]]},
            "published-print": {"date-parts": [[2026, 1, 1]]},
            "reference": [],
        }
        service = EnrichmentService(
            publisher=StubPublisher(
                Enrichment(
                    abstract="Publisher text",
                    keywords=("publisher",),
                    event="Conference",
                )
            )
        )

        result = collect(
            ["10.1/test"],
            provider=StubWorkProvider(work),
            enrichment_lookup=service.for_collection,
        )
        publication = result.publications[0]

        self.assertEqual(publication.abstract, "Publisher text")
        self.assertEqual(publication.keywords, ("publisher",))
        self.assertEqual(publication.event, "Conference")


if __name__ == "__main__":
    unittest.main()
