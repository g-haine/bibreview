from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from bibreview.cli import main
from bibreview.config import load_config
from bibreview.identity import new_publication_id
from bibreview.model import Author, Publication
from bibreview.repair import (
    contributor_repair_state_from_data,
    repair_path,
)
from bibreview.storage import read_bibliography, read_json, write_bibliography


CONFIG = """\
schema_version: 1
project:
  name: Example Review
  slug: example-review
site:
  enabled: false
"""


class FakeProvider:
    def work(self, doi):
        if doi == "10.1/invalid":
            return {
                "type": "journal-article",
                "title": ["Incomplete publication"],
                "author": [],
                "editor": [],
                "container-title": ["Journal"],
                "created": {"date-parts": [[2026, 10, 16]]},
                "published-print": {"date-parts": [[2026]]},
            }
        if doi == "10.1/valid":
            return {
                "type": "journal-article",
                "title": ["Valid publication"],
                "author": [{"given": "Grace", "family": "Hopper"}],
                "editor": [],
                "container-title": ["Journal"],
                "created": {"date-parts": [[2026, 10, 16]]},
                "published-print": {"date-parts": [[2026]]},
            }
        return None


class RepairCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

        self.root = Path(self.temp.name)
        self.config_path = self.root / "bibreview.yml"
        self.config_path.write_text(CONFIG, encoding="utf-8")
        self.config = load_config(self.config_path)

        self.config.paths.pending.parent.mkdir(parents=True, exist_ok=True)
        self.config.paths.pending.write_text(
            "doi:10.1/invalid\n",
            encoding="utf-8",
        )

    def collection_services(self):
        return SimpleNamespace(
            provider=FakeProvider(),
            enrichment_lookup=None,
            citation_lookup=None,
            bibtex_lookup=None,
        )

    def test_repair_stages_only_the_repaired_pending_candidate(self):
        self.config.paths.pending.write_text(
            "doi:10.1/invalid\ndoi:10.1/valid\n",
            encoding="utf-8",
        )

        answers = iter([
            "a",
            "Ada",
            "Lovelace",
            "",
            "",
            "n",
            "y",
        ])

        stdout = StringIO()
        stderr = StringIO()
        with patch(
            "bibreview.cli.build_collection_services",
            return_value=self.collection_services(),
        ), patch(
            "builtins.input",
            side_effect=lambda prompt="": next(answers),
        ), redirect_stdout(stdout), redirect_stderr(stderr):
            code = main([
                "--config",
                str(self.config_path),
                "repair",
            ])

        self.assertEqual(code, 0, stderr.getvalue())

        staged = read_bibliography(self.config.paths.collected)
        self.assertEqual(
            [publication.doi for publication in staged],
            ["10.1/invalid"],
        )
        self.assertEqual(
            self.config.paths.pending.read_text(encoding="utf-8"),
            "doi:10.1/invalid\ndoi:10.1/valid\n",
        )


    def test_invalid_contributor_input_cannot_create_repair_state(self):
        answers = iter([
            "a",
            "",
            "",
            "",
            "",
            "s",
        ])

        stdout = StringIO()
        stderr = StringIO()

        with patch(
            "bibreview.cli.build_collection_services",
            return_value=self.collection_services(),
        ), patch(
            "builtins.input",
            side_effect=lambda prompt="": next(answers),
        ), redirect_stdout(stdout), redirect_stderr(stderr):
            code = main([
                "--config",
                str(self.config_path),
                "repair",
            ])

        self.assertEqual(code, 0, stderr.getvalue())
        self.assertIn("Invalid contributor", stdout.getvalue())
        self.assertIn("deferred", stdout.getvalue().lower())
        self.assertFalse(repair_path(self.config).exists())
        self.assertFalse(self.config.paths.collected.exists())


    def test_repair_defer_is_resumable(self):
        stdout = StringIO()
        stderr = StringIO()

        with patch(
            "bibreview.cli.build_collection_services",
            return_value=self.collection_services(),
        ), patch(
            "builtins.input",
            side_effect=["s"],
        ), redirect_stdout(stdout), redirect_stderr(stderr):
            code = main([
                "--config",
                str(self.config_path),
                "repair",
            ])

        self.assertEqual(code, 0, stderr.getvalue())
        self.assertFalse(repair_path(self.config).exists())
        self.assertFalse(self.config.paths.collected.exists())
        self.assertIn("deferred", stdout.getvalue().lower())

        answers = iter([
            "a",
            "Ada",
            "Lovelace",
            "",
            "",
            "n",
            "y",
        ])
        stdout = StringIO()
        stderr = StringIO()

        with patch(
            "bibreview.cli.build_collection_services",
            return_value=self.collection_services(),
        ), patch(
            "builtins.input",
            side_effect=lambda prompt="": next(answers),
        ), redirect_stdout(stdout), redirect_stderr(stderr):
            code = main([
                "--config",
                str(self.config_path),
                "repair",
            ])

        self.assertEqual(code, 0, stderr.getvalue())
        self.assertTrue(repair_path(self.config).exists())
        self.assertEqual(
            [publication.doi for publication in read_bibliography(
                self.config.paths.collected
            )],
            ["10.1/invalid"],
        )


    def test_repaired_publication_merges_through_ordinary_merge(self):
        answers = iter([
            "a",
            "Ada",
            "Lovelace",
            "",
            "",
            "n",
            "y",
        ])

        stdout = StringIO()
        stderr = StringIO()
        with patch(
            "bibreview.cli.build_collection_services",
            return_value=self.collection_services(),
        ), patch(
            "builtins.input",
            side_effect=lambda prompt="": next(answers),
        ), redirect_stdout(stdout), redirect_stderr(stderr):
            code = main([
                "--config",
                str(self.config_path),
                "repair",
            ])

        self.assertEqual(code, 0, stderr.getvalue())
        self.assertEqual(
            [publication.doi for publication in read_bibliography(
                self.config.paths.collected
            )],
            ["10.1/invalid"],
        )

        stdout = StringIO()
        stderr = StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main([
                "--config",
                str(self.config_path),
                "merge",
            ])

        self.assertEqual(code, 0, stderr.getvalue())

        canonical = read_bibliography(self.config.paths.bibliography)
        self.assertEqual(len(canonical), 1)
        self.assertEqual(canonical[0].doi, "10.1/invalid")
        self.assertEqual(
            [(author.given, author.family) for author in canonical[0].authors],
            [("Ada", "Lovelace")],
        )
        self.assertEqual(
            read_bibliography(self.config.paths.collected),
            (),
        )
        self.assertEqual(
            self.config.paths.pending.read_text(encoding="utf-8"),
            "",
        )


    def test_repair_can_record_contributor_source_fields(self):
        stdout = StringIO()
        stderr = StringIO()

        answers = iter([
            "a",
            "Ada",
            "Lovelace",
            "",
            '{"ORCID": "0000-0001-2345-6789"}',
            "n",
            "y",
        ])

        with patch(
            "bibreview.cli.build_collection_services",
            return_value=self.collection_services(),
        ), patch(
            "builtins.input",
            side_effect=lambda prompt="": next(answers),
        ), redirect_stdout(stdout), redirect_stderr(stderr):
            code = main([
                "--config",
                str(self.config_path),
                "repair",
            ])

        self.assertEqual(code, 0, stderr.getvalue())

        state = contributor_repair_state_from_data(
            read_json(repair_path(self.config), dict)
        )
        self.assertEqual(
            state.repairs[0].authors[0].source_fields,
            {"ORCID": "0000-0001-2345-6789"},
        )


    def test_repair_supports_multiple_authors_and_editors(self):
        stdout = StringIO()
        stderr = StringIO()

        answers = iter([
            "a",
            "Ada",
            "Lovelace",
            "",
            "",
            "a",
            "Grace",
            "Hopper",
            "",
            "",
            "e",
            "John",
            "Editor",
            "",
            "",
            "n",
            "y",
        ])

        with patch(
            "bibreview.cli.build_collection_services",
            return_value=self.collection_services(),
        ), patch(
            "builtins.input",
            side_effect=lambda prompt="": next(answers),
        ), redirect_stdout(stdout), redirect_stderr(stderr):
            code = main([
                "--config",
                str(self.config_path),
                "repair",
            ])

        self.assertEqual(code, 0, stderr.getvalue())

        state = contributor_repair_state_from_data(
            read_json(repair_path(self.config), dict)
        )
        record = state.repairs[0]

        self.assertEqual(
            [(author.given, author.family) for author in record.authors],
            [
                ("Ada", "Lovelace"),
                ("Grace", "Hopper"),
            ],
        )
        self.assertEqual(
            [(editor.given, editor.family) for editor in record.editors],
            [("John", "Editor")],
        )

        staged = read_bibliography(self.config.paths.collected)
        self.assertEqual(len(staged), 1)
        self.assertEqual(
            [(author.given, author.family) for author in staged[0].authors],
            [
                ("Ada", "Lovelace"),
                ("Grace", "Hopper"),
            ],
        )
        self.assertEqual(
            [(editor.given, editor.family) for editor in staged[0].editors],
            [("John", "Editor")],
        )


    def test_repair_dry_run_is_non_mutating(self):
        pending_before = self.config.paths.pending.read_bytes()

        stdout = StringIO()
        stderr = StringIO()

        with patch(
            "bibreview.cli.build_collection_services",
            return_value=self.collection_services(),
        ), patch(
            "builtins.input",
            side_effect=AssertionError(
                "dry-run repair must never enter interactive review"
            ),
        ), redirect_stdout(stdout), redirect_stderr(stderr):
            code = main([
                "--config",
                str(self.config_path),
                "--dry-run",
                "repair",
            ])

        self.assertEqual(code, 0, stderr.getvalue())
        self.assertIn("Dry run:", stdout.getvalue())
        self.assertIn("10.1/invalid", stdout.getvalue())
        self.assertIn("journal-article", stdout.getvalue())
        self.assertIn("Journal", stdout.getvalue())
        self.assertIn("2026", stdout.getvalue())
        self.assertFalse(repair_path(self.config).exists())
        self.assertFalse(self.config.paths.collected.exists())
        self.assertEqual(
            self.config.paths.pending.read_bytes(),
            pending_before,
        )


    def test_repair_refuses_before_review_when_staging_is_not_empty(self):
        staged = Publication(
            id=new_publication_id(),
            identifiers={"doi": "10.1/already-staged"},
            title="Already staged",
            authors=(Author(literal="Existing Author"),),
        )
        write_bibliography(
            self.config.paths.collected,
            [staged],
        )
        before = self.config.paths.collected.read_bytes()

        stdout = StringIO()
        stderr = StringIO()

        with patch(
            "bibreview.cli.build_collection_services",
            return_value=self.collection_services(),
        ), patch(
            "builtins.input",
            side_effect=AssertionError(
                "repair must refuse before interactive review"
            ),
        ), redirect_stdout(stdout), redirect_stderr(stderr):
            code = main([
                "--config",
                str(self.config_path),
                "repair",
            ])

        self.assertEqual(code, 1)
        self.assertIn(
            "merge the existing batch before",
            stderr.getvalue(),
        )
        self.assertEqual(
            self.config.paths.collected.read_bytes(),
            before,
        )
        self.assertFalse(repair_path(self.config).exists())


    def test_repair_records_explicit_author_for_invalid_pending_doi(self):
        stdout = StringIO()
        stderr = StringIO()

        answers = iter([
            "a",          # add author
            "Ada",        # given
            "Lovelace",   # family
            "",           # literal
            "",           # source fields
            "n",          # no additional contributor
            "y",          # confirm repair
        ])

        with patch(
            "bibreview.cli.build_collection_services",
            return_value=self.collection_services(),
        ), patch(
            "builtins.input",
            side_effect=lambda prompt="": next(answers),
        ), redirect_stdout(stdout), redirect_stderr(stderr):
            code = main([
                "--config",
                str(self.config_path),
                "repair",
            ])

        self.assertEqual(code, 0, stderr.getvalue())
        self.assertIn("10.1/invalid", stdout.getvalue())
        self.assertIn(
            "publication must contain at least one author or editor",
            stdout.getvalue(),
        )

        state = contributor_repair_state_from_data(
            read_json(repair_path(self.config), dict)
        )
        self.assertEqual(len(state.repairs), 1)

        record = state.repairs[0]
        self.assertEqual(record.doi, "10.1/invalid")
        self.assertEqual(
            [(author.given, author.family) for author in record.authors],
            [("Ada", "Lovelace")],
        )
        self.assertEqual(record.editors, ())

        staged = read_bibliography(self.config.paths.collected)
        self.assertEqual(len(staged), 1)
        self.assertEqual(staged[0].doi, "10.1/invalid")
        self.assertEqual(
            [(author.given, author.family) for author in staged[0].authors],
            [("Ada", "Lovelace")],
        )


if __name__ == "__main__":
    unittest.main()
