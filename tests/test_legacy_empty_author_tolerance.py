import unittest

from bibreview.compat import legacy_record_to_publication, publication_to_legacy


class LegacyEmptyAuthorToleranceTests(unittest.TestCase):
    def test_empty_legacy_author_is_preserved_but_not_canonicalized(self):
        record = {
            "doi": "10.1000/main",
            "type": "journal-article",
            "title": "Example",
            "authors": [{}, {"given": "A", "family": "Author"}],
            "abstract": "",
            "journal": "Journal",
            "year": "2021",
            "volume": "1",
            "issue": "",
            "event": "",
            "isbn": "",
            "pages": "1--2",
            "publisher": "Publisher",
            "keywords": "",
            "dateY": "2021",
            "dateM": "1",
            "dateD": "1",
            "permalink": "example",
            "references": [],
        }

        item = legacy_record_to_publication(record)

        self.assertEqual(1, len(item.publication.authors))
        self.assertEqual(record, publication_to_legacy(item))


if __name__ == "__main__":
    unittest.main()
