from __future__ import annotations

import unittest

from bibreview.model import Author
from bibreview.pipeline.collect import build_publication


class AuthorSourceFieldTests(unittest.TestCase):
    def test_author_copies_and_exposes_source_fields_read_only(self) -> None:
        source = {
            "ORCID": "example-orcid",
            "affiliation": [{"name": "Example Institute"}],
        }
        author = Author(given="Ada", family="Lovelace", source_fields=source)

        source["ORCID"] = "changed"
        self.assertEqual(author.source_fields["ORCID"], "example-orcid")
        with self.assertRaises(TypeError):
            author.source_fields["new"] = "value"  # type: ignore[index]

    def test_collection_preserves_crossref_author_source_fields(self) -> None:
        message = {
            "title": ["Multiphysics systems"],
            "type": "journal-article",
            "author": [
                {
                    "given": "Ada",
                    "family": "Lovelace",
                    "ORCID": "example-orcid",
                    "sequence": "first",
                    "affiliation": [{"name": "Example Institute"}],
                }
            ],
            "created": {"date-parts": [[2026, 9, 17]]},
        }

        publication = build_publication("10.1234/example", message, "multiphysics-systems")
        author = publication.authors[0]

        self.assertEqual(author.given, "Ada")
        self.assertEqual(author.family, "Lovelace")
        self.assertIsNone(author.literal)
        self.assertEqual(
            dict(author.source_fields),
            {
                "ORCID": "example-orcid",
                "sequence": "first",
                "affiliation": [{"name": "Example Institute"}],
            },
        )

    def test_collection_preserves_literal_author_name(self) -> None:
        message = {
            "title": ["Multiphysics systems"],
            "type": "journal-article",
            "author": [
                {
                    "name": "Example Research Consortium",
                    "sequence": "additional",
                }
            ],
            "created": {"date-parts": [[2026, 9, 17]]},
        }

        publication = build_publication("10.1234/example", message, "multiphysics-systems")
        author = publication.authors[0]

        self.assertEqual(author.literal, "Example Research Consortium")
        self.assertIsNone(author.given)
        self.assertIsNone(author.family)
        self.assertEqual(dict(author.source_fields), {"sequence": "additional"})



if __name__ == "__main__":
    unittest.main()
