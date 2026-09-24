from __future__ import annotations

from unittest.mock import Mock
import unittest

from bibreview.providers.audit import (
    CrossRefAuditSource,
    OpenAlexAuditSource,
    SemanticScholarAuditSource,
)


class CrossRefAuditSourceTests(unittest.TestCase):
    def test_normalizes_rich_bibliographic_metadata(self):
        provider = Mock()
        provider.work.return_value = {
            "DOI": "10.1000/EXAMPLE",
            "type": "journal-article",
            "title": ["A <i>Title</i>"],
            "author": [
                {"given": "Ada", "family": "Lovelace"},
                {"name": "Example Consortium"},
            ],
            "editor": [{"given": "Alan", "family": "Turing"}],
            "abstract": "<jats:p>Abstract A useful summary.</jats:p>",
            "container-title": ["Journal of Examples"],
            "published-online": {"date-parts": [[2024, 5, 1]]},
            "volume": "12",
            "issue": "3",
            "page": "10-20",
            "publisher": "Example Press",
            "event": {"name": "Example Conference"},
            "subject": ["Control", "Energy"],
            "created": {"date-parts": [[2024, 4, 2]]},
            "isbn-type": [{"type": "print", "value": "9780000000000"}],
        }

        evidence = CrossRefAuditSource(provider).evidence("10.1000/example")

        self.assertEqual(evidence.provider, "crossref")
        self.assertEqual(
            dict(evidence.identifiers),
            {"doi": "10.1000/example", "isbn": "9780000000000"},
        )
        self.assertEqual(evidence.fields["type"], "journal-article")
        self.assertEqual(evidence.fields["authors"], ("Ada Lovelace", "Example Consortium"))
        self.assertEqual(evidence.fields["editors"], ("Alan Turing",))
        self.assertEqual(evidence.fields["publication_year"], "2024")
        self.assertEqual(evidence.fields["container_title"], "Journal of Examples")
        self.assertEqual(evidence.fields["pages"], "10-20")
        self.assertEqual(evidence.fields["created_date"], "2024-04-02")
        self.assertEqual(evidence.fields["keywords"], ("Control", "Energy"))
        self.assertEqual(provider.work.call_args.args, ("10.1000/example",))

    def test_retains_unsafe_structured_abstract_verbatim(self):
        provider = Mock()
        raw = (
            'A controller <jats:inline-graphic '
            'xlink:href="graphic/math-0002.png"/> is proposed.'
        )
        provider.work.return_value = {
            "DOI": "10.1000/example",
            "abstract": raw,
        }

        evidence = CrossRefAuditSource(provider).evidence("10.1000/example")

        self.assertEqual(evidence.fields["abstract"], raw)

    def test_normalizes_lossless_structured_abstract(self):
        provider = Mock()
        provider.work.return_value = {
            "DOI": "10.1000/example",
            "abstract": (
                '<jats:p>A space <inline-formula>'
                '<mml:annotation encoding="application/x-tex">V</mml:annotation>'
                '</inline-formula>.</jats:p>'
            ),
        }

        evidence = CrossRefAuditSource(provider).evidence("10.1000/example")

        self.assertEqual(evidence.fields["abstract"], r"A space \(V\).")

    def test_uses_article_number_when_crossref_page_is_missing(self):
        provider = Mock()
        provider.work.return_value = {
            "DOI": "10.1000/EXAMPLE",
            "title": ["Article number"],
            "article-number": "034312",
        }

        evidence = CrossRefAuditSource(provider).evidence("10.1000/example")

        self.assertEqual(evidence.fields["pages"], "034312")

    def test_batches_multiple_dois_and_marks_missing_records_unavailable(self):
        provider = Mock()
        provider.works.return_value = {
            "10.1000/one": {
                "DOI": "10.1000/one",
                "title": ["One"],
                "published-online": {"date-parts": [[2024, 1, 1]]},
            }
        }
        source = CrossRefAuditSource(provider)

        result = source.evidence_many(("10.1000/ONE", "10.1000/two"))

        self.assertEqual(source.batch_size, 25)
        self.assertEqual(
            provider.works.call_args.args[0],
            ("10.1000/one", "10.1000/two"),
        )
        self.assertEqual(result["10.1000/one"].fields["title"], "One")
        self.assertEqual(
            result["10.1000/one"].fields["publication_year"],
            "2024",
        )
        self.assertEqual(result["10.1000/two"].status, "unavailable")
        self.assertEqual(result["10.1000/two"].detail, "record not found")

    def test_missing_work_is_unavailable_not_error(self):
        provider = Mock()
        provider.work.return_value = None

        evidence = CrossRefAuditSource(provider).evidence("10.1000/example")

        self.assertEqual(evidence.status, "unavailable")
        self.assertEqual(evidence.detail, "record not found")
        self.assertEqual(dict(evidence.fields), {})


    def test_not_available_abstract_is_normalized_as_missing(self):
        provider = Mock()
        provider.work.return_value = {
            "DOI": "10.1000/example",
            "abstract": "NOT AVAILABLE",
        }

        evidence = CrossRefAuditSource(provider).evidence("10.1000/example")

        self.assertEqual(evidence.fields["abstract"], "")


class OpenAlexAuditSourceTests(unittest.TestCase):
    def test_reconstructs_abstract_and_raw_author_order(self):
        provider = Mock()
        provider.work.return_value = {
            "doi": "https://doi.org/10.1000/EXAMPLE",
            "title": "OpenAlex title",
            "publication_year": 2024,
            "authorships": [
                {
                    "raw_author_name": "A. Lovelace",
                    "author": {"display_name": "Ada Lovelace"},
                },
                {
                    "raw_author_name": "",
                    "author": {"display_name": "Alan Turing"},
                },
            ],
            "primary_location": {
                "source": {"display_name": "Journal of Examples"}
            },
            "biblio": {
                "volume": "12",
                "issue": "3",
                "first_page": "10",
                "last_page": "20",
            },
            "abstract_inverted_index": {
                "Hamiltonian": [1],
                "Port": [0],
                "systems": [2],
            },
        }

        evidence = OpenAlexAuditSource(provider).evidence("10.1000/example")

        self.assertEqual(dict(evidence.identifiers), {"doi": "10.1000/example"})
        self.assertEqual(evidence.fields["authors"], ("A. Lovelace", "Alan Turing"))
        self.assertEqual(evidence.fields["abstract"], "Port Hamiltonian systems")
        self.assertEqual(evidence.fields["container_title"], "Journal of Examples")
        self.assertEqual(evidence.fields["publication_year"], "2024")
        self.assertEqual(evidence.fields["pages"], "10-20")

    def test_batches_multiple_dois_and_marks_missing_records_unavailable(self):
        provider = Mock()
        provider.works.return_value = {
            "10.1000/one": {
                "doi": "10.1000/one",
                "title": "One",
                "publication_year": 2024,
            }
        }
        source = OpenAlexAuditSource(provider)

        result = source.evidence_many(("10.1000/ONE", "10.1000/two"))

        self.assertEqual(source.batch_size, 100)
        self.assertEqual(provider.works.call_args.args[0], ("10.1000/one", "10.1000/two"))
        self.assertEqual(result["10.1000/one"].fields["title"], "One")
        self.assertEqual(result["10.1000/two"].status, "unavailable")
        self.assertEqual(result["10.1000/two"].detail, "record not found")

    def test_missing_work_is_non_retryable_evidence(self):
        provider = Mock()
        provider.work.return_value = None
        evidence = OpenAlexAuditSource(provider).evidence("10.1000/example")
        self.assertEqual(evidence.status, "unavailable")
        self.assertEqual(evidence.detail, "record not found")


    def test_not_available_reconstructed_abstract_is_normalized_as_missing(self):
        provider = Mock()
        provider.work.return_value = {
            "doi": "10.1000/example",
            "abstract_inverted_index": {
                "NOT": [0],
                "AVAILABLE": [1],
            },
        }

        evidence = OpenAlexAuditSource(provider).evidence("10.1000/example")

        self.assertEqual(evidence.fields["abstract"], "")


class SemanticScholarAuditSourceTests(unittest.TestCase):
    def test_normalizes_core_paper_fields(self):
        provider = Mock()
        provider.paper.return_value = {
            "title": "Semantic Scholar title",
            "abstract": "An abstract",
            "year": 2023,
            "authors": [{"name": "Ada Lovelace"}, {"name": "Alan Turing"}],
            "venue": "Journal of Examples",
            "externalIds": {"DOI": "10.1000/EXAMPLE", "CorpusId": 123},
        }

        evidence = SemanticScholarAuditSource(provider).evidence("10.1000/example")

        self.assertEqual(dict(evidence.identifiers), {"doi": "10.1000/example"})
        self.assertEqual(evidence.fields["title"], "Semantic Scholar title")
        self.assertEqual(evidence.fields["abstract"], "An abstract")
        self.assertEqual(evidence.fields["publication_year"], "2023")
        self.assertEqual(evidence.fields["authors"], ("Ada Lovelace", "Alan Turing"))
        self.assertEqual(evidence.fields["container_title"], "Journal of Examples")

    def test_not_available_abstract_is_normalized_as_missing(self):
        provider = Mock()
        provider.paper.return_value = {
            "abstract": "  not   available  ",
        }

        evidence = SemanticScholarAuditSource(provider).evidence("10.1000/example")

        self.assertEqual(evidence.fields["abstract"], "")

    def test_batches_multiple_dois_and_marks_missing_records_unavailable(self):
        provider = Mock()
        provider.papers.return_value = {
            "10.1000/one": {
                "title": "One",
                "externalIds": {"DOI": "10.1000/one"},
            }
        }
        source = SemanticScholarAuditSource(provider)

        result = source.evidence_many(("10.1000/ONE", "10.1000/two"))

        self.assertEqual(source.batch_size, 500)
        self.assertEqual(provider.papers.call_args.args[0], ("10.1000/one", "10.1000/two"))
        self.assertEqual(result["10.1000/one"].fields["title"], "One")
        self.assertEqual(result["10.1000/two"].status, "unavailable")
        self.assertEqual(result["10.1000/two"].detail, "record not found")

    def test_missing_paper_is_unavailable(self):
        provider = Mock()
        provider.paper.return_value = None
        evidence = SemanticScholarAuditSource(provider).evidence("10.1000/example")
        self.assertEqual(evidence.status, "unavailable")
        self.assertEqual(evidence.detail, "record not found")


if __name__ == "__main__":
    unittest.main()
