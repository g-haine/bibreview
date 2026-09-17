import unittest

from bibreview.compat import legacy_record_to_publication, publication_to_legacy


class LegacyReferenceToleranceTests(unittest.TestCase):
    def test_malformed_reference_doi_is_preserved_but_not_canonicalized(self):
        record = {
            "doi": "10.1000/main",
            "type": "journal-article",
            "title": "Example",
            "authors": [{"given": "A", "family": "Author"}],
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
            "references": [
                {"doi": "10.1016/j.geomphys. 2021.104201", "title": "Legacy reference"}
            ],
        }

        item = legacy_record_to_publication(record)

        self.assertEqual({}, dict(item.publication.references[0].identifiers))
        self.assertEqual(record, publication_to_legacy(item))


if __name__ == "__main__":
    unittest.main()
