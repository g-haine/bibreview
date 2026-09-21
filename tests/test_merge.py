from __future__ import annotations

import unittest

from bibreview.identity import new_publication_id
from bibreview.model import Author, Publication
from bibreview.pipeline.merge import MergeError, merge_publications


def publication(*, identifier: str | None = None, doi: str | None = None,
                title: str = "Title", isbn: str | None = None) -> Publication:
    identifiers = {}
    if doi is not None:
        identifiers["doi"] = doi
    if isbn is not None:
        identifiers["isbn"] = isbn
    return Publication(
        id=identifier or new_publication_id(),
        identifiers=identifiers,
        title=title,
        authors=(Author(literal="Example Author"),),
    )


class MergeTests(unittest.TestCase):
    def test_appends_new_publications_in_incoming_order(self) -> None:
        old = publication(title="Old")
        first = publication(title="First")
        second = publication(title="Second")
        result = merge_publications([old], [first, second])
        self.assertEqual(result.publications, (old, first, second))
        self.assertEqual(result.added_ids, (first.id, second.id))
        self.assertEqual(result.updated_ids, ())

    def test_exact_doi_refresh_keeps_persisted_internal_id(self) -> None:
        old = publication(doi="10.1/item", title="Old title")
        incoming = publication(doi="https://doi.org/10.1/ITEM", title="Corrected title")
        result = merge_publications([old], [incoming])
        self.assertEqual(len(result.publications), 1)
        refreshed = result.publications[0]
        self.assertEqual(refreshed.id, old.id)
        self.assertEqual(refreshed.title, "Corrected title")
        self.assertEqual(result.updated_ids, (old.id,))

    def test_doi_less_publications_do_not_collapse(self) -> None:
        first = publication(title="One")
        second = publication(title="Two")
        result = merge_publications([], [first, second])
        self.assertEqual(result.publications, (first, second))

    def test_matching_isbn_does_not_merge_publications(self) -> None:
        first = publication(isbn="978-0-00-000000-0", title="One")
        second = publication(isbn="978-0-00-000000-0", title="Two")
        result = merge_publications([first], [second])
        self.assertEqual(len(result.publications), 2)

    def test_identical_refresh_is_reported_unchanged(self) -> None:
        old = publication(doi="10.1/item")
        same = Publication(
            id=new_publication_id(),
            identifiers={"doi": "10.1/item"},
            title="Title",
            authors=(Author(literal="Example Author"),),
        )
        result = merge_publications([old], [same])
        self.assertEqual(result.unchanged_ids, (old.id,))
        self.assertEqual(result.updated_ids, ())

    def test_conflicting_id_and_doi_matches_are_rejected(self) -> None:
        first = publication(doi="10.1/first")
        second = publication(doi="10.1/second")
        incoming = Publication(
            id=first.id,
            identifiers={"doi": "10.1/second"},
            title="Conflict",
            authors=(Author(literal="Example Author"),),
        )
        with self.assertRaisesRegex(MergeError, "conflicts across existing records"):
            merge_publications([first, second], [incoming])

    def test_duplicate_existing_doi_is_rejected(self) -> None:
        first = publication(doi="10.1/shared")
        second = publication(doi="10.1/shared")
        with self.assertRaisesRegex(MergeError, "duplicate strong identifier"):
            merge_publications([first, second], [])


if __name__ == "__main__":
    unittest.main()
