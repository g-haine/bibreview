from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from bibreview.storage import StorageError, atomic_write, json_bytes, read_json, write_json


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


if __name__ == "__main__":
    unittest.main()
