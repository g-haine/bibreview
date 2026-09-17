from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

from bibreview.compat import (
    CompatibilityError,
    legacy_record_to_publication,
    legacy_records,
    load_legacy_bibliography,
    publication_to_legacy,
)
from bibreview.identity import migration_id_from_doi


LEGACY_RECORD = {
    "doi": "10.1000/example",
    "type": "journal-article",
    "title": "A port-Hamiltonian example",
    "authors": [
        {
            "given": "Ada",
            "family": "Lovelace",
            "sequence": "first",
            "ORCID": "https://orcid.org/0000-0000-0000-0001",
        }
    ],
    "abstract": "Abstract text",
    "journal": "Journal of Examples",
    "year": "2026",
    "volume": "12",
    "issue": "3",
    "event": "",
    "isbn": "9780000000000",
    "pages": "10--20",
    "publisher": "Example Press",
    "keywords": "port-Hamiltonian, energy systems",
    "dateY": "2026",
    "dateM": "4",
    "dateD": "7",
    "permalink": "a-port-hamiltonian-example",
    "references": [
        {"doi": "10.1000/reference", "title": "Reference with DOI"},
        {"doi": "null", "title": "Reference without DOI"},
    ],
    "legacy_extra": {"preserve": True},
}


class CompatibilityTests(unittest.TestCase):
    def test_legacy_record_gets_deterministic_migration_id(self) -> None:
        item = legacy_record_to_publication(LEGACY_RECORD)
        self.assertEqual(item.publication.id, migration_id_from_doi("10.1000/example"))
        self.assertEqual(item.publication.doi, "10.1000/example")

    def test_round_trip_preserves_legacy_record_exactly(self) -> None:
        item = legacy_record_to_publication(LEGACY_RECORD)
        self.assertEqual(publication_to_legacy(item), LEGACY_RECORD)

    def test_round_trip_preserves_source_specific_author_and_unknown_fields(self) -> None:
        item = legacy_record_to_publication(LEGACY_RECORD)
        output = publication_to_legacy(item)
        self.assertEqual(output["authors"][0]["sequence"], "first")
        self.assertEqual(output["authors"][0]["ORCID"], LEGACY_RECORD["authors"][0]["ORCID"])
        self.assertEqual(output["isbn"], LEGACY_RECORD["isbn"])
        self.assertEqual(output["legacy_extra"], {"preserve": True})

    def test_reference_without_doi_is_supported(self) -> None:
        item = legacy_record_to_publication(LEGACY_RECORD)
        self.assertEqual(item.publication.references[1].identifiers, {})
        self.assertEqual(item.publication.references[1].citation, "Reference without DOI")

    def test_canonical_change_is_written_without_losing_legacy_extras(self) -> None:
        item = legacy_record_to_publication(LEGACY_RECORD)
        changed = replace(item.publication, title="Updated title")
        migrated = replace(item, publication=changed)
        output = publication_to_legacy(migrated)
        self.assertEqual(output["title"], "Updated title")
        self.assertEqual(output["isbn"], LEGACY_RECORD["isbn"])
        self.assertEqual(output["authors"][0]["sequence"], "first")

    def test_internal_id_is_added_only_when_explicitly_requested(self) -> None:
        item = legacy_record_to_publication(LEGACY_RECORD)
        self.assertNotIn("id", publication_to_legacy(item))
        output = publication_to_legacy(item, include_internal_id=True)
        self.assertEqual(output["id"], item.publication.id)

    def test_existing_internal_id_allows_doi_less_legacy_record(self) -> None:
        record = dict(LEGACY_RECORD)
        record["doi"] = None
        record["id"] = "7c4a7a7f-81e5-4d44-b9bd-7e78a5dbef8d"
        item = legacy_record_to_publication(record)
        self.assertIsNone(item.publication.doi)
        self.assertEqual(item.publication.id, record["id"])

    def test_doi_less_record_without_internal_id_is_rejected(self) -> None:
        record = dict(LEGACY_RECORD)
        record["doi"] = None
        with self.assertRaisesRegex(CompatibilityError, "reproducible migration id"):
            legacy_record_to_publication(record)

    def test_loading_bibliography_is_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "biblio.json"
            original = (json.dumps([LEGACY_RECORD], ensure_ascii=False, indent=2) + "\n").encode("utf-8")
            path.write_bytes(original)
            items = load_legacy_bibliography(path)
            self.assertEqual(len(items), 1)
            self.assertEqual(path.read_bytes(), original)

    def test_multiple_records_can_be_rendered_without_ids(self) -> None:
        second = dict(LEGACY_RECORD)
        second["doi"] = "10.1000/second"
        second["title"] = "Second publication"
        items = [legacy_record_to_publication(LEGACY_RECORD), legacy_record_to_publication(second)]
        rendered = legacy_records(items)
        self.assertEqual([record["doi"] for record in rendered], ["10.1000/example", "10.1000/second"])
        self.assertTrue(all("id" not in record for record in rendered))


if __name__ == "__main__":
    unittest.main()
