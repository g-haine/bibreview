from datetime import date
import unittest

from bibreview.identity import new_publication_id
from bibreview.model import Author, Publication, Reference


class ModelTests(unittest.TestCase):
    def test_publication_does_not_require_doi(self):
        publication = Publication(
            id=new_publication_id(),
            title="A publication without DOI",
            authors=(Author(given="Ada", family="Lovelace"),),
            publication_year="2026",
            created_date=date(2026, 1, 1),
        )
        self.assertIsNone(publication.doi)

    def test_doi_is_normalized_as_external_identifier(self):
        publication = Publication(
            id=new_publication_id(),
            identifiers={"doi": "https://doi.org/10.1234/ABC"},
        )
        self.assertEqual(publication.doi, "10.1234/abc")

    def test_reference_does_not_require_doi(self):
        reference = Reference(citation="A book without DOI")
        self.assertEqual(dict(reference.identifiers), {})

    def test_publication_id_is_independent_from_metadata(self):
        identifier = new_publication_id()
        first = Publication(id=identifier, title="Old title")
        second = Publication(id=identifier, title="Corrected title", identifiers={"doi": "10.1/x"})
        self.assertEqual(first.id, second.id)


if __name__ == "__main__":
    unittest.main()
