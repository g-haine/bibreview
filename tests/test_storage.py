from __future__ import annotations

from datetime import date
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from bibreview.identity import new_publication_id
from bibreview.model import Author, Publication, Reference
from bibreview.storage import (
    BibliographyMetadata,
    StorageError,
    atomic_write,
    atomic_write_batch,
    bibliography_data,
    json_bytes,
    read_bibliography,
    read_bibliography_document,
    read_json,
    write_bibliography,
    write_json,
)


class StorageTests(unittest.TestCase):
    def test_json_bytes_matches_readable_project_format(self) -> None:
        self.assertEqual(json_bytes([{"title": "Énergie"}]), b'[\n  {\n    "title": "\xc3\x89nergie"\n  }\n]\n')

    def test_read_json_validates_root_type(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "data.json"
            path.write_text('{"not": "a list"}\n', encoding="utf-8")
            with self.assertRaisesRegex(StorageError, "expected list"):
                read_json(path, list)

    def test_write_json_replaces_existing_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "data.json"
            path.write_text('["old"]\n', encoding="utf-8")
            write_json(path, ["new"])
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), ["new"])
            self.assertTrue(path.read_bytes().endswith(b"\n"))

    def test_atomic_write_does_not_leave_staging_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "data.txt"
            atomic_write(path, b"content\n")
            self.assertEqual(path.read_bytes(), b"content\n")
            self.assertEqual([candidate.name for candidate in root.iterdir()], ["data.txt"])

    def test_batch_staging_failure_preserves_all_existing_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "first.txt"
            second = root / "second.txt"
            first.write_bytes(b"old-first\n")
            second.write_bytes(b"old-second\n")
            original = tempfile.NamedTemporaryFile
            count = 0

            def fail_second(*args, **kwargs):
                nonlocal count
                count += 1
                if count == 2:
                    raise OSError("staging failed")
                return original(*args, **kwargs)

            with patch("bibreview.storage.tempfile.NamedTemporaryFile", side_effect=fail_second):
                with self.assertRaisesRegex(OSError, "staging failed"):
                    atomic_write_batch({first: b"new-first\n", second: b"new-second\n"})

            self.assertEqual(first.read_bytes(), b"old-first\n")
            self.assertEqual(second.read_bytes(), b"old-second\n")
            self.assertEqual(sorted(path.name for path in root.iterdir()), ["first.txt", "second.txt"])

    def test_canonical_bibliography_round_trip(self) -> None:
        publication = Publication(
            id=new_publication_id(),
            identifiers={"doi": "10.1234/Example", "isbn": "978-0-00-000000-0"},
            type="journal-article",
            title="Fluid-structure example",
            authors=(
                Author(
                    given="Ada",
                    family="Lovelace",
                    source_fields={
                        "ORCID": "example-orcid",
                        "affiliation": [{"name": "Example Institute"}],
                    },
                ),
                Author(
                    literal="Example Research Consortium",
                    source_fields={"sequence": "additional"},
                ),
            ),
            abstract="Abstract",
            container_title="Journal",
            publication_year="2026",
            volume="1",
            issue="2",
            pages="1--9",
            publisher="Publisher",
            event="Conference",
            keywords=("control", "energy"),
            created_date=date(2026, 9, 17),
            permalink="fluid-structure-example",
            references=(
                Reference(identifiers={"doi": "10.1234/ref"}, citation="Reference"),
                Reference(citation="Reference without DOI"),
            ),
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bibliography.json"
            write_bibliography(
                path,
                [publication],
                metadata=BibliographyMetadata(
                    last_update=date(2026, 9, 18),
                ),
            )
            loaded = read_bibliography(path)
            document = read_bibliography_document(path)
            raw = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(loaded, (publication,))
        self.assertEqual(
            document.metadata.last_update,
            date(2026, 9, 18),
        )
        self.assertEqual(raw["metadata"]["schema_version"], 1)
        self.assertEqual(raw["metadata"]["last_update"], "2026-09-18")
        self.assertEqual(len(raw["publications"]), 1)
        self.assertEqual(
            bibliography_data(loaded)[0]["authors"][0]["source_fields"]["ORCID"],
            "example-orcid",
        )
        self.assertEqual(loaded[0].authors[1].literal, "Example Research Consortium")
        self.assertEqual(loaded[0].identifiers["isbn"], "978-0-00-000000-0")

    def test_canonical_reader_accepts_earlier_bare_list_format(self) -> None:
        publication = Publication(id=new_publication_id(), title="Example")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bibliography.json"
            path.write_bytes(json_bytes(bibliography_data([publication])))
            document = read_bibliography_document(path)

        self.assertEqual(document.publications, (publication,))
        self.assertIsNone(document.metadata.last_update)
        self.assertEqual(document.metadata.schema_version, 1)

    def test_canonical_reader_rejects_invalid_document_metadata(self) -> None:
        publication = Publication(id=new_publication_id(), title="Example")
        payload = {
            "metadata": {
                "schema_version": 1,
                "last_update": "not-a-date",
            },
            "publications": bibliography_data([publication]),
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bibliography.json"
            path.write_bytes(json_bytes(payload))
            with self.assertRaisesRegex(StorageError, "last_update"):
                read_bibliography_document(path)

    def test_canonical_reader_rejects_unknown_fields(self) -> None:
        publication = Publication(id=new_publication_id(), title="Example")
        record = bibliography_data([publication])[0]
        record["typo"] = "value"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bibliography.json"
            path.write_bytes(json_bytes({
                "metadata": {
                    "schema_version": 1,
                    "last_update": None,
                },
                "publications": [record],
            }))
            with self.assertRaisesRegex(StorageError, "unknown fields"):
                read_bibliography(path)


if __name__ == "__main__":
    unittest.main()
