import unittest
import uuid

from bibreview.identity import (
    IdentityError,
    new_publication_id,
    normalize_doi,
    shared_strong_identifier,
    strong_identifiers,
)


class IdentityTests(unittest.TestCase):
    def test_normalizes_doi_url_and_case(self):
        self.assertEqual(
            normalize_doi(" HTTPS://DOI.ORG/10.1234/ABC.Def "),
            "10.1234/abc.def",
        )

    def test_rejects_invalid_doi(self):
        with self.assertRaises(IdentityError):
            normalize_doi("not-a-doi")

    def test_new_id_is_uuid_and_not_deterministic(self):
        first = new_publication_id()
        second = new_publication_id()
        uuid.UUID(first)
        uuid.UUID(second)
        self.assertNotEqual(first, second)

    def test_matches_only_shared_strong_identifiers(self):
        match = shared_strong_identifier(
            {"doi": "10.1234/ABC"}, {"doi": "https://doi.org/10.1234/abc"}
        )
        self.assertEqual(match, ("doi", "10.1234/abc"))
        self.assertIsNone(shared_strong_identifier({}, {}))

    def test_isbn_is_metadata_but_not_an_automatic_merge_key(self):
        self.assertEqual(strong_identifiers({"isbn": "978-0-00-000000-0"}), ())
        self.assertIsNone(
            shared_strong_identifier(
                {"isbn": "978-0-00-000000-0"},
                {"isbn": "978-0-00-000000-0"},
            )
        )


if __name__ == "__main__":
    unittest.main()
