from datetime import date
import unittest

from bibreview.identity import new_publication_id
from bibreview.model import Author, Editor, Publication, Reference


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

    def test_editor_only_publication_does_not_require_doi(self):
        publication = Publication(
            id=new_publication_id(),
            title="An edited volume without DOI",
            editors=(Editor(given="Grace", family="Hopper"),),
        )
        self.assertEqual(publication.authors, ())
        self.assertEqual(publication.editors[0].family, "Hopper")
        self.assertIsNone(publication.doi)

    def test_publication_requires_author_or_editor(self):
        with self.assertRaisesRegex(
            ValueError,
            "at least one author or editor",
        ):
            Publication(id=new_publication_id(), title="Invalid")

    def test_doi_is_normalized_as_external_identifier(self):
        publication = Publication(
            id=new_publication_id(),
            identifiers={"doi": "https://doi.org/10.1234/ABC"},
            authors=(Author(literal="Example Author"),),
        )
        self.assertEqual(publication.doi, "10.1234/abc")

    def test_reference_does_not_require_doi(self):
        reference = Reference(citation="A book without DOI")
        self.assertEqual(dict(reference.identifiers), {})

    def test_publication_id_is_independent_from_metadata(self):
        identifier = new_publication_id()
        author = Author(literal="Example Author")
        first = Publication(id=identifier, title="Old title", authors=(author,))
        second = Publication(
            id=identifier,
            title="Corrected title",
            identifiers={"doi": "10.1/x"},
            authors=(author,),
        )
        self.assertEqual(first.id, second.id)

    def test_literal_author_name_is_supported(self):
        author = Author(literal="Example Research Consortium")
        self.assertIsNone(author.given)
        self.assertIsNone(author.family)
        self.assertEqual(author.literal, "Example Research Consortium")

    def test_literal_editor_name_is_supported(self):
        editor = Editor(literal="Example Editorial Board")
        self.assertIsNone(editor.given)
        self.assertIsNone(editor.family)
        self.assertEqual(editor.literal, "Example Editorial Board")


if __name__ == "__main__":
    unittest.main()
