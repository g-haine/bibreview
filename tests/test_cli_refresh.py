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
            "title": ["Updated publication"],
            "author": [{"given": "Ada", "family": "Lovelace"}],
            "container-title": ["Journal"],
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
        publication = Publication(
            id=new_publication_id(),
            identifiers={"doi": "10.1/stale"},
            type="journal-article",
            title="Old publication",
            authors=(Author(literal="Example Author"),),
            publication_year="2025",
            created_date=date(2025, 1, 2),
            permalink="stable-paper",
        )
        write_bibliography(self.config.paths.bibliography, [publication])
        write_bibliography(self.config.paths.collected, [])
        self.config.paths.known.write_text("10.1/stale\n", encoding="utf-8")
        self.config.paths.bibtex.mkdir(parents=True, exist_ok=True)
        (self.config.paths.bibtex / "stable-paper.bib").write_text("old\n", encoding="utf-8")

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
            bibtex_lookup=lambda doi: "new\n",
        )

    def test_refresh_dry_run_then_apply_uses_canonical_staging(self):
        before = self.snapshot()
        stdout = StringIO()
        stderr = StringIO()
        with patch("bibreview.cli.build_collection_services", return_value=self.services()), redirect_stdout(stdout), redirect_stderr(stderr):
            code = main([
                "--config", str(self.config_path),
                "--dry-run",
                "refresh",
            ])
        self.assertEqual(code, 0, stderr.getvalue())
        self.assertIn("Dry run:", stdout.getvalue())
        self.assertIn("refreshed: 1", stdout.getvalue())
        self.assertEqual(before, self.snapshot())

        stdout = StringIO()
        stderr = StringIO()
        with patch("bibreview.cli.build_collection_services", return_value=self.services()), redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(["--config", str(self.config_path), "refresh"])
        self.assertEqual(code, 0, stderr.getvalue())
        self.assertIn("refreshed: 1", stdout.getvalue())
        staged = read_bibliography(self.config.paths.collected)
        self.assertEqual(len(staged), 1)
        self.assertEqual(staged[0].doi, "10.1/stale")
        self.assertEqual(staged[0].permalink, "stable-paper")
        self.assertEqual(
            (self.config.paths.bibtex / "stable-paper.bib").read_text(encoding="utf-8"),
            "new\n",
        )


if __name__ == "__main__":
    unittest.main()
