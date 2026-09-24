from __future__ import annotations

import unittest

from bibreview.identity import IdentityError
from bibreview.pipeline.collect import Enrichment, build_publication, collect, prepare_dois


class FakeProvider:
    def __init__(self, works=None):
        self.works = works or {}
        self.calls = []

    def work(self, doi):
        self.calls.append(doi)
        return self.works.get(doi)


def message(title="Fluid-structure systems"):
    return {
        "title": [title],
        "type": "journal-article",
        "author": [
            {"given": "Ada", "family": "Lovelace", "ORCID": "0000-0000"},
            {"given": "", "family": ""},
        ],
        "abstract": "Abstract CrossRef text",
        "container-title": ["Journal"],
        "created": {"date-parts": [[2024, 3, 8]]},
        "published-print": {"date-parts": [[2025, 1, 1]]},
        "volume": "7",
        "issue": "1",
        "page": "1-9",
        "publisher": "Publisher",
        "subject": ["Control", "Energy"],
        "isbn-type": [{"type": "print", "value": "978-1-234"}],
        "reference": [],
    }


class PrepareDoisTests(unittest.TestCase):
    def test_normalizes_deduplicates_and_filters_known_dois(self):
        self.assertEqual(
            prepare_dois(
                ["10.1/NEW", "https://doi.org/10.1/new", "", "10.1/other"],
                ["doi: 10.1/OTHER"],
            ),
            ("10.1/new",),
        )

    def test_invalid_doi_is_rejected(self):
        with self.assertRaises(IdentityError):
            prepare_dois(["not-a-doi"])


class BuildPublicationTests(unittest.TestCase):
    def test_builds_canonical_publication_from_crossref(self):
        data = message("Fluid <mml:math>x</mml:math> structure")
        data["editor"] = [
            {
                "given": "Grace",
                "family": "Hopper",
                "sequence": "first",
            }
        ]
        data["reference"] = [
            {"DOI": "10.2/REF"},
            {"author": "A", "article-title": "Title", "year": 2020},
        ]

        publication = build_publication(
            "10.1/TEST",
            data,
            "fluid-structure",
            enrichment_lookup=lambda doi, work: Enrichment(
                abstract="Abstract Enriched text",
                keywords=("control", "energy"),
                event="Conference",
            ),
            citation_lookup=lambda doi: f"Citation for {doi}",
        )

        self.assertEqual(publication.doi, "10.1/test")
        self.assertEqual(publication.identifiers["isbn"], "978-1-234")
        # Verify generic metadata normalization: strip MathML tags, retain text content.
        self.assertEqual(publication.title, "Fluid x structure")
        self.assertEqual([(a.given, a.family) for a in publication.authors], [("Ada", "Lovelace")])
        self.assertEqual(
            [(editor.given, editor.family) for editor in publication.editors],
            [("Grace", "Hopper")],
        )
        self.assertEqual(
            dict(publication.editors[0].source_fields),
            {"sequence": "first"},
        )
        self.assertEqual(publication.abstract, "Enriched text")
        self.assertEqual(publication.publication_year, "2025")
        self.assertEqual(publication.created_date.isoformat(), "2024-03-08")
        self.assertEqual(publication.pages, "1--9")
        self.assertEqual(publication.event, "Conference")
        self.assertEqual(publication.keywords, ("control", "energy"))
        self.assertEqual(publication.references[0].identifiers["doi"], "10.2/ref")
        self.assertEqual(publication.references[0].citation, "Citation for 10.2/ref")
        self.assertEqual(publication.references[1].identifiers, {})
        self.assertEqual(publication.references[1].citation, "A, Title. (2020)")

    def test_article_number_fills_pages_when_crossref_page_is_missing(self):
        data = message()
        data["page"] = ""
        data["article-number"] = "034312-A"

        publication = build_publication("10.1/test", data, "article-number")

        self.assertEqual(publication.pages, "034312-A")

    def test_editor_only_crossref_record_is_supported(self):
        data = message()
        data["author"] = []
        data["editor"] = [{"given": "Peter", "family": "Benner"}]
        publication = build_publication("10.1/test", data, "edited-volume")
        self.assertEqual(publication.authors, ())
        self.assertEqual(
            [(editor.given, editor.family) for editor in publication.editors],
            [("Peter", "Benner")],
        )

    def test_missing_authors_and_editors_is_rejected(self):
        data = message()
        data["author"] = []
        data["editor"] = []
        with self.assertRaisesRegex(ValueError, "at least one author or editor"):
            build_publication("10.1/test", data, "invalid-record")

    def test_build_publication_calls_optional_enrichment_once(self):
        calls = []

        def enrich(doi, work):
            calls.append(doi)
            return Enrichment(
                abstract="Enriched abstract",
                keywords=("keyword",),
                event="Conference",
            )

        publication = build_publication(
            "10.1/test",
            message(),
            "fluid-structure",
            enrichment_lookup=enrich,
        )

        self.assertEqual(calls, ["10.1/test"])
        self.assertEqual(publication.abstract, "Enriched abstract")
        self.assertEqual(publication.keywords, ("keyword",))
        self.assertEqual(publication.event, "Conference")

    def test_provider_abstract_placeholder_is_never_canonicalized(self):
        data = message()
        data["abstract"] = "Not Available"

        publication = build_publication(
            "10.1/test",
            data,
            "fluid-structure",
            enrichment_lookup=lambda doi, work: Enrichment(
                abstract="NOT AVAILABLE"
            ),
        )

        self.assertEqual(publication.abstract, "")

    def test_default_enrichment_uses_crossref_fields(self):
        publication = build_publication("10.1/test", message(), "fluid-structure")
        # Canonical in-memory data does not preserve incidental leading whitespace.
        self.assertEqual(publication.abstract, "CrossRef text")
        self.assertEqual(publication.keywords, ("Control", "Energy"))
        self.assertEqual(publication.event, "")


    def test_default_enrichment_normalizes_lossless_structured_abstract(self):
        data = message()
        data["abstract"] = (
            '<jats:p>A space <inline-formula>'
            '<mml:annotation encoding="application/x-tex">V</mml:annotation>'
            '</inline-formula>.</jats:p>'
        )

        publication = build_publication("10.1/test", data, "safe-structured")

        self.assertEqual(publication.abstract, r"A space \(V\).")

    def test_default_enrichment_does_not_flatten_unsafe_structured_abstract(self):
        data = message()
        data["abstract"] = (
            'A controller <jats:inline-graphic '
            'xlink:href="graphic/math-0002.png"/> is proposed.'
        )

        publication = build_publication("10.1/test", data, "unsafe-structured")

        self.assertEqual(publication.abstract, "")

    def test_unsafe_injected_enrichment_falls_back_to_safe_crossref_abstract(self):
        data = message()
        publication = build_publication(
            "10.1/test",
            data,
            "unsafe-enrichment",
            enrichment_lookup=lambda doi, work: Enrichment(
                abstract="(u<inf>0</inf>)<sup>T</sup>"
            ),
        )

        self.assertEqual(publication.abstract, "CrossRef text")

    def test_missing_creation_date_is_rejected(self):
        data = message()
        data["created"] = {"date-parts": [[2024, 3]]}
        with self.assertRaisesRegex(ValueError, "missing CrossRef creation date"):
            build_publication("10.1/test", data, "fluid-structure")

    def test_invalid_reference_doi_is_not_promoted(self):
        data = message()
        data["reference"] = [
            {"DOI": "10.1016/j.geomphys. 2021.104201", "unstructured": "Malformed-source citation"}
        ]
        publication = build_publication("10.1/test", data, "fluid-structure")
        self.assertEqual(publication.references[0].identifiers, {})
        self.assertEqual(publication.references[0].citation, "Malformed-source citation")


class CollectionTests(unittest.TestCase):
    def test_collect_filters_known_tracks_absent_and_resolves_slug_collisions(self):
        provider = FakeProvider(
            {
                "10.1/new": message(),
                "10.1/other": message(),
            }
        )
        result = collect(
            ["10.1/KNOWN", "10.1/NEW", "10.1/missing", "10.1/OTHER"],
            provider=provider,
            known=["10.1/known"],
            used_slugs=["fluid-structure-systems"],
        )

        self.assertEqual(result.candidates, ("10.1/new", "10.1/missing", "10.1/other"))
        self.assertEqual(result.unavailable, ("10.1/missing",))
        self.assertEqual(provider.calls, list(result.candidates))
        self.assertEqual(
            [item.publication.permalink for item in result.items],
            ["fluid-structure-systems0", "fluid-structure-systems00"],
        )
        self.assertEqual(len({publication.id for publication in result.publications}), 2)

    def test_collect_can_attach_bibtex_without_owning_bibtex_provider(self):
        provider = FakeProvider({"10.1/new": message()})
        result = collect(
            ["10.1/new"],
            provider=provider,
            bibtex_lookup=lambda doi: f"@article{{{doi}}}\n",
        )
        self.assertEqual(result.items[0].bibtex, "@article{10.1/new}\n")

    def test_empty_bibtex_is_treated_as_unavailable(self):
        provider = FakeProvider({"10.1/new": message()})
        result = collect(
            ["10.1/new"],
            provider=provider,
            bibtex_lookup=lambda doi: "",
        )
        self.assertIsNone(result.items[0].bibtex)

    def test_provider_failure_propagates_before_any_persistence_layer(self):
        class FailingProvider:
            def work(self, doi):
                raise RuntimeError("offline")

        with self.assertRaisesRegex(RuntimeError, "offline"):
            collect(["10.1/new"], provider=FailingProvider())

    def test_collection_result_uses_canonical_publication_fields(self):
        provider = FakeProvider({"10.1/new": message()})
        result = collect(["10.1/new"], provider=provider)
        publication = result.publications[0]
        self.assertFalse(hasattr(publication, "journal"))
        self.assertEqual(publication.container_title, "Journal")


if __name__ == "__main__":
    unittest.main()
