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
from bibreview.model import Publication
from bibreview.storage import read_bibliography, write_bibliography


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
        if doi != "10.1/new":
            return None
        return {
            "type": "journal-article",
            "title": ["New publication"],
            "container-title": ["Journal"],
            "created": {"date-parts": [[2026, 9, 17]]},
            "published-print": {"date-parts": [[2026]]},
        }


class CliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config_path = self.root / "bibreview.yml"
        self.config_path.write_text(CONFIG, encoding="utf-8")
        self.config = load_config(self.config_path)
        self.config.paths.bibliography.parent.mkdir(parents=True, exist_ok=True)

    def publication(self, doi: str, title: str) -> Publication:
        return Publication(
            id=new_publication_id(),
            identifiers={"doi": doi},
            title=title,
        )

    def snapshot(self):
        return {
            str(path.relative_to(self.root)): path.read_bytes()
            for path in self.root.rglob("*")
            if path.is_file()
        }

    def collection_services(self):
        return SimpleNamespace(
            provider=FakeProvider(),
            enrichment_lookup=None,
            citation_lookup=None,
            bibtex_lookup=lambda doi: "@article{new}\n",
        )

    def test_collect_dry_run_then_apply_uses_canonical_staging(self):
        write_bibliography(
            self.config.paths.bibliography,
            [self.publication("10.1/old", "Old publication")],
        )
        write_bibliography(self.config.paths.collected, [])
        self.config.paths.pending.write_text(
            "10.1/old\n10.1/new\n10.1/missing\n",
            encoding="utf-8",
        )
        before = self.snapshot()

        stdout = StringIO()
        stderr = StringIO()
        with patch(
            "bibreview.cli.build_collection_services",
            return_value=self.collection_services(),
        ), redirect_stdout(stdout), redirect_stderr(stderr):
            code = main([
                "--config", str(self.config_path),
                "--dry-run",
                "collect",
            ])
        self.assertEqual(code, 0, stderr.getvalue())
        self.assertIn("Dry run:", stdout.getvalue())
        self.assertIn("collected: 1", stdout.getvalue())
        self.assertEqual(before, self.snapshot())

        stdout = StringIO()
        stderr = StringIO()
        with patch(
            "bibreview.cli.build_collection_services",
            return_value=self.collection_services(),
        ), redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(["--config", str(self.config_path), "collect"])
        self.assertEqual(code, 0, stderr.getvalue())
        self.assertIn("collected: 1", stdout.getvalue())
        staged = read_bibliography(self.config.paths.collected)
        self.assertEqual(len(staged), 1)
        self.assertEqual(staged[0].doi, "10.1/new")
        self.assertEqual(
            self.config.paths.pending.read_text(encoding="utf-8"),
            "10.1/new\n10.1/missing\n",
        )
        self.assertEqual(
            (self.config.paths.bibtex / "new-publication.bib").read_text(encoding="utf-8"),
            "@article{new}\n",
        )

    def test_merge_dry_run_does_not_mutate_state(self):
        write_bibliography(
            self.config.paths.collected,
            [self.publication("10.1/new", "New publication")],
        )
        self.config.paths.pending.write_text("10.1/new\n", encoding="utf-8")
        before = self.snapshot()
        stdout = StringIO()
        stderr = StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main([
                "--config", str(self.config_path),
                "--dry-run",
                "merge",
            ])
        self.assertEqual(code, 0, stderr.getvalue())
        self.assertIn("Dry run:", stdout.getvalue())
        self.assertEqual(stderr.getvalue(), "")
        self.assertEqual(before, self.snapshot())

    def test_merge_applies_state_and_reports_backup(self):
        existing = self.publication("10.1/old", "Old")
        write_bibliography(self.config.paths.bibliography, [existing])
        write_bibliography(
            self.config.paths.collected,
            [self.publication("10.1/new", "New")],
        )
        self.config.paths.pending.write_text("10.1/new\n", encoding="utf-8")
        stdout = StringIO()
        stderr = StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(["--config", str(self.config_path), "merge"])
        self.assertEqual(code, 0, stderr.getvalue())
        self.assertIn("added: 1", stdout.getvalue())
        self.assertIn("Backup:", stdout.getvalue())
        self.assertEqual(stderr.getvalue(), "")
        self.assertEqual(len(read_bibliography(self.config.paths.bibliography)), 2)
        self.assertEqual(read_bibliography(self.config.paths.collected), ())

    def test_merge_rejects_invalid_state_without_traceback(self):
        write_bibliography(
            self.config.paths.collected,
            [self.publication("10.1/new", "New")],
        )
        self.config.paths.pending.write_text("not-a-doi\n", encoding="utf-8")
        stdout = StringIO()
        stderr = StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(["--config", str(self.config_path), "merge"])
        self.assertEqual(code, 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("bibreview merge:", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
