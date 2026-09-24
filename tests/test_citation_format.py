import unittest

from bibreview.citation_format import (
    crossref_work_to_csl,
    format_crossref_citations,
)


class CrossRefCslConversionTests(unittest.TestCase):
    def test_converts_crossref_article_to_csl_json(self) -> None:
        item = crossref_work_to_csl(
            "10.1000/TEST",
            {
                "type": "journal-article",
                "title": ["Control systems"],
                "container-title": ["Systems & Control Letters"],
                "short-container-title": ["Syst Control Lett"],
                "volume": "10",
                "issue": "2",
                "page": "1-9",
                "publisher": "Example Publisher",
                "author": [
                    {"given": "Ada", "family": "Lovelace"},
                    {"given": "Alan", "family": "Turing"},
                ],
                "issued": {"date-parts": [[2024, 3, 5]]},
                "URL": "https://doi.org/10.1000/test",
            },
        )

        self.assertEqual(item["id"], "10.1000/test")
        self.assertEqual(item["type"], "article-journal")
        self.assertEqual(item["DOI"], "10.1000/test")
        self.assertEqual(item["title"], "Control systems")
        self.assertEqual(
            item["container-title"],
            "Systems & Control Letters",
        )
        self.assertEqual(item["container-title-short"], "Syst Control Lett")
        self.assertEqual(item["volume"], "10")
        self.assertEqual(item["issue"], "2")
        self.assertEqual(item["page"], "1-9")
        self.assertEqual(
            item["author"],
            [
                {"given": "Ada", "family": "Lovelace"},
                {"given": "Alan", "family": "Turing"},
            ],
        )
        self.assertEqual(
            item["issued"],
            {"date-parts": [[2024, 3, 5]]},
        )

    def test_formats_book_with_bundled_springer_style(self) -> None:
        result = format_crossref_citations(
            {
                "10.1000/book": {
                    "type": "book",
                    "title": ["The future of modern genomics"],
                    "author": [
                        {"given": "J", "family": "South"},
                        {"given": "B", "family": "Blass"},
                    ],
                    "issued": {"date-parts": [[2001]]},
                    "publisher": "Blackwell",
                    "publisher-location": "London",
                    "DOI": "10.1000/book",
                }
            }
        )

        self.assertEqual(
            result["10.1000/book"],
            "South J, Blass B (2001) The future of modern genomics. "
            "Blackwell, London",
        )

    def test_formats_journal_article_with_doi(self) -> None:
        result = format_crossref_citations(
            {
                "10.1000/test": {
                    "type": "journal-article",
                    "title": ["Control systems"],
                    "container-title": ["Systems and Control Letters"],
                    "short-container-title": ["Syst Control Lett"],
                    "volume": "10",
                    "issue": "2",
                    "page": "1-9",
                    "author": [
                        {"given": "Ada", "family": "Lovelace"},
                    ],
                    "issued": {"date-parts": [[2024]]},
                    "DOI": "10.1000/test",
                }
            }
        )

        self.assertEqual(
            result["10.1000/test"],
            "Lovelace A (2024) Control systems. "
            "Syst Control Lett 10(2):1–9. "
            "https://doi.org/10.1000/test",
        )

    def test_formatting_normalizes_mapping_keys(self) -> None:
        result = format_crossref_citations(
            {
                "10.1000/TEST": {
                    "type": "book",
                    "title": ["A book"],
                    "author": [{"family": "Author"}],
                    "issued": {"date-parts": [[2024]]},
                    "publisher": "Publisher",
                }
            }
        )

        self.assertIn("10.1000/test", result)
        self.assertNotIn("10.1000/TEST", result)


if __name__ == "__main__":
    unittest.main()
