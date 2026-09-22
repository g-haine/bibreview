from __future__ import annotations

import unittest

from bibreview.bibtex_edit import (
    BibtexEditError,
    bibtex_field_names,
    parse_bibtex_entry,
    update_bibtex_fields,
)


class BibtexEditTests(unittest.TestCase):
    def test_replaces_existing_fields_and_appends_missing_field(self):
        source = (
            "@article{example,\n"
            "  title={{Old {Nested} Title}},\n"
            "  author={Ada Lovelace},\n"
            "  year={2025}\n"
            "}\n"
        )

        updated = update_bibtex_fields(
            source,
            {
                "title": "{New Title}",
                "pages": "10--20",
            },
        )

        self.assertIn("title={{New Title}}", updated)
        self.assertIn("author={Ada Lovelace}", updated)
        self.assertIn("year={2025}", updated)
        self.assertIn("pages={10--20}", updated)
        self.assertEqual(
            bibtex_field_names(updated),
            frozenset({"title", "author", "year", "pages"}),
        )

    def test_parses_quoted_and_multiline_values(self):
        source = (
            "@inproceedings{example,\n"
            '  title="Quoted title",\n'
            "  abstract={First line\n"
            "    with {nested} braces},\n"
            "  year=2026\n"
            "}\n"
        )
        entry = parse_bibtex_entry(source)
        self.assertEqual(entry.entry_type, "inproceedings")
        self.assertEqual(
            {field.name for field in entry.fields},
            {"title", "abstract", "year"},
        )

    def test_rejects_duplicate_fields(self):
        source = (
            "@article{example,\n"
            "  title={One},\n"
            "  title={Two}\n"
            "}\n"
        )
        with self.assertRaisesRegex(BibtexEditError, "duplicate BibTeX field"):
            parse_bibtex_entry(source)

    def test_rejects_multiple_entries(self):
        source = "@article{one, title={One}}\n@article{two, title={Two}}\n"
        with self.assertRaisesRegex(BibtexEditError, "exactly one entry"):
            parse_bibtex_entry(source)


if __name__ == "__main__":
    unittest.main()
