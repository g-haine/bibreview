from contextlib import redirect_stderr, redirect_stdout
from datetime import date
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
from bibreview.project_refresh import load_project_refresh_review
from bibreview.storage import read_bibliography, write_bibliography


CONFIG = """\
schema_version: 1
project:
  name: Example Review
  slug: example-review
refresh:
  types:
    - journal-article
  when_missing_any:
    - volume
    - issue
    - pages
site:
  enabled: false
"""


class FakeProvider:
    def work(self, doi):
        return {
            "type": "journal-article",
            "title": ["Provider title"],
            "author": [{"given": "Ada", "family": "Lovelace"}],
            "container-title": ["Provider Journal"],
            "created": {"date-parts": [[2026, 9, 17]]},
            "published-print": {"date-parts": [[2026]]},
            "volume": "5",
            "issue": "2",
            "page": "10-12",
        }


class RefreshCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config_path = self.root / "bibreview.yml"
        self.config_path.write_text(CONFIG, encoding="utf-8")
        self.config = load_config(self.config_path)
        self.config.paths.bibliography.parent.mkdir(parents=True, exist_ok=True)
        self.publication = Publication(
            id=new_publication_id(),
            identifiers={"doi": "10.1/stale"},
            type="journal-article",
            title="Reviewed title",
            authors=(Author(literal="Reviewed Author"),),
            container_title="Reviewed Journal",
            publication_year="2025",
            created_date=date(2025, 1, 2),
            permalink="stable-paper",
        )
        write_bibliography(
            self.config.paths.bibliography,
            [self.publication],
        )
        write_bibliography(self.config.paths.collected, [])
        self.config.paths.known.write_text("10.1/stale\n", encoding="utf-8")
        self.config.paths.bibtex.mkdir(parents=True, exist_ok=True)
        self.bibtex = self.config.paths.bibtex / "stable-paper.bib"
        self.bibtex.write_text(
            "@article{stale,\n"
            "  title={{Reviewed title}},\n"
            "  author={Reviewed Author},\n"
            "  journal={Reviewed Journal},\n"
            "  year={2025}\n"
            "}\n",
            encoding="utf-8",
        )

    def snapshot(self):
        return {
            str(path.relative_to(self.root)): path.read_bytes()
            for path in self.root.rglob("*")
            if path.is_file()
        }

    def services(self):
        return SimpleNamespace(
            provider=FakeProvider(),
            enrichment_lookup=None,
            citation_lookup=None,
            bibtex_lookup=lambda doi: "remote changed bibtex\n",
        )

    def run_refresh_scan(self):
        stdout = StringIO()
        stderr = StringIO()
        with patch(
            "bibreview.cli.build_collection_services",
            return_value=self.services(),
        ), redirect_stdout(stdout), redirect_stderr(stderr):
            code = main([
                "--config",
                str(self.config_path),
                "refresh",
            ])
        self.assertEqual(code, 0, stderr.getvalue())
        return stdout.getvalue()

    def test_refresh_dry_run_then_scan_writes_review_only(self):
        before = self.snapshot()
        stdout = StringIO()
        stderr = StringIO()
        with patch(
            "bibreview.cli.build_collection_services",
            return_value=self.services(),
        ), redirect_stdout(stdout), redirect_stderr(stderr):
            code = main([
                "--config",
                str(self.config_path),
                "--dry-run",
                "refresh",
            ])
        self.assertEqual(code, 0, stderr.getvalue())
        self.assertIn("Dry run:", stdout.getvalue())
        self.assertIn("Safe proposals", stdout.getvalue())
        self.assertEqual(before, self.snapshot())

        output = self.run_refresh_scan()
        self.assertIn("Collateral provider differences", output)
        self.assertEqual(read_bibliography(self.config.paths.collected), ())
        self.assertIn("title={{Reviewed title}}", self.bibtex.read_text(encoding="utf-8"))

        review = load_project_refresh_review(self.config)
        self.assertEqual(
            {proposal.field for proposal in review.proposals},
            {"volume", "issue", "pages"},
        )
        self.assertIn(
            "title",
            {item.field for item in review.collateral},
        )

    def test_review_is_offline_and_verbose_shows_collateral(self):
        self.run_refresh_scan()
        stdout = StringIO()
        stderr = StringIO()
        with patch("bibreview.cli.build_collection_services") as services, redirect_stdout(
            stdout
        ), redirect_stderr(stderr):
            code = main([
                "--config",
                str(self.config_path),
                "-v",
                "refresh",
                "--review",
            ])
        self.assertEqual(code, 0, stderr.getvalue())
        services.assert_not_called()
        self.assertIn("COLLATERAL (never auto-applied): title", stdout.getvalue())
        self.assertIn("Provider title", stdout.getvalue())

    def test_resolve_then_apply_stages_only_accepted_missing_fields(self):
        self.run_refresh_scan()

        stdout = StringIO()
        stderr = StringIO()
        # Accept volume, reject issue, reject pages.
        with patch(
            "builtins.input",
            side_effect=["", "n", "n"],
        ), redirect_stdout(stdout), redirect_stderr(stderr):
            code = main([
                "--config",
                str(self.config_path),
                "refresh",
                "--resolve",
            ])
        self.assertEqual(code, 0, stderr.getvalue())
        self.assertIn("Refresh resolution", stdout.getvalue())
        self.assertEqual(read_bibliography(self.config.paths.collected), ())

        stdout = StringIO()
        stderr = StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main([
                "--config",
                str(self.config_path),
                "refresh",
                "--apply",
            ])
        self.assertEqual(code, 0, stderr.getvalue())
        staged = read_bibliography(self.config.paths.collected)
        self.assertEqual(staged[0].volume, "5")
        self.assertEqual(staged[0].issue, "")
        self.assertEqual(staged[0].pages, "")
        self.assertEqual(staged[0].title, "Reviewed title")
        self.assertEqual(staged[0].authors[0].literal, "Reviewed Author")

        bibtex = self.bibtex.read_text(encoding="utf-8")
        self.assertIn("volume={5}", bibtex)
        self.assertIn("title={{Reviewed title}}", bibtex)
        self.assertNotIn("Provider title", bibtex)

    def test_apply_before_resolution_is_refused(self):
        self.run_refresh_scan()
        stderr = StringIO()
        with redirect_stdout(StringIO()), redirect_stderr(stderr):
            code = main([
                "--config",
                str(self.config_path),
                "refresh",
                "--apply",
            ])
        self.assertEqual(code, 1)
        self.assertIn("must be complete", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
