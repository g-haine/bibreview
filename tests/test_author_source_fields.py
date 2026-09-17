from __future__ import annotations

from dataclasses import replace
import unittest

from bibreview.compat import legacy_record_to_publication, publication_to_legacy
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
            "title": ["Port-Hamiltonian systems"],
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

        publication = build_publication("10.1234/example", message, "port-hamiltonian-systems")
        author = publication.authors[0]

        self.assertEqual(author.given, "Ada")
        self.assertEqual(author.family, "Lovelace")
        self.assertEqual(
            dict(author.source_fields),
            {
                "ORCID": "example-orcid",
                "sequence": "first",
                "affiliation": [{"name": "Example Institute"}],
            },
        )

    def test_legacy_author_extras_survive_canonical_name_change(self) -> None:
        record = {
            "doi": "10.1234/example",
            "type": "journal-article",
            "title": "Example",
            "authors": [
                {
                    "given": "Ada",
                    "family": "Lovelace",
                    "ORCID": "example-orcid",
                    "affiliation": [{"name": "Example Institute"}],
                }
            ],
            "abstract": "",
            "journal": "",
            "year": "2026",
            "volume": "",
            "issue": "",
            "pages": "",
            "publisher": "",
            "event": "",
            "keywords": "",
            "dateY": "2026",
            "dateM": "9",
            "dateD": "17",
            "permalink": "example",
            "references": [],
        }
        item = legacy_record_to_publication(record)
        changed_author = replace(item.publication.authors[0], family="Byron")
        changed = replace(item, publication=replace(item.publication, authors=(changed_author,)))

        rendered = publication_to_legacy(changed)

        self.assertEqual(rendered["authors"][0]["family"], "Byron")
        self.assertEqual(rendered["authors"][0]["ORCID"], "example-orcid")
        self.assertEqual(
            rendered["authors"][0]["affiliation"],
            [{"name": "Example Institute"}],
        )


if __name__ == "__main__":
    unittest.main()
