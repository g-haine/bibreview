from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
import tempfile
import unittest

from bibreview.cli import main
from bibreview.config import load_config
from bibreview.identity import new_publication_id
from bibreview.model import Author, Publication
from bibreview.storage import read_json, write_bibliography, write_json


CONFIG = """\
schema_version: 1
project:
  name: Example Review
  slug: example-review
site:
  enabled: false
"""


class AuthorIdentityHygieneCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config_path = self.root / "bibreview.yml"
        self.config_path.write_text(CONFIG, encoding="utf-8")
        self.config = load_config(self.config_path)
        self.config.paths.bibliography.parent.mkdir(parents=True, exist_ok=True)
        write_bibliography(
            self.config.paths.bibliography,
            (
                Publication(
                    id=new_publication_id(),
                    identifiers={"doi": "10.1/example"},
                    title="Example",
                    authors=(Author(literal="Yongxing Wang"),),
                ),
            ),
        )

    def run_cli(self, *extra):
        stdout = StringIO()
        stderr = StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(["--config", str(self.config_path), *extra])
        return code, stdout.getvalue(), stderr.getvalue()

    def test_remove_author_variant_supports_dry_run_and_apply(self):
        write_json(
            self.config.paths.author_mappings,
            {"yongxing-wang": ["Yongxing Wang", "Y. Wang"]},
        )

        code, stdout, stderr = self.run_cli(
            "--dry-run",
            "hygiene",
            "--authors",
            "--remove-author-variant",
            "yongxing-wang",
            "Y. Wang",
        )
        self.assertEqual(code, 0, stderr)
        self.assertIn("Dry run: Remove author variant", stdout)
        self.assertIn(
            "Y. Wang",
            read_json(self.config.paths.author_mappings, dict)["yongxing-wang"],
        )

        code, stdout, stderr = self.run_cli(
            "hygiene",
            "--authors",
            "--remove-author-variant",
            "yongxing-wang",
            "Y. Wang",
        )
        self.assertEqual(code, 0, stderr)
        self.assertEqual(
            read_json(self.config.paths.author_mappings, dict)["yongxing-wang"],
            ["Yongxing Wang"],
        )

    def test_mark_distinct_removes_candidate_from_ordinary_report(self):
        write_json(
            self.config.paths.author_mappings,
            {
                "a-zhang": ["A Zhang"],
                "alvin-zhang": ["Alvin Zhang"],
            },
        )

        code, stdout, stderr = self.run_cli(
            "hygiene",
            "--authors",
            "--mark-distinct",
            "a-zhang",
            "alvin-zhang",
        )
        self.assertEqual(code, 0, stderr)
        self.assertIn("reviewed distinct", stdout)

        code, stdout, stderr = self.run_cli(
            "hygiene",
            "--authors",
        )
        self.assertEqual(code, 0, stderr)
        self.assertIn("Initial/full-name candidates  : 0", stdout)
        self.assertIn("Reviewed distinct pairs       : 1", stdout)

    def test_author_identity_mutations_require_authors_scope(self):
        write_json(
            self.config.paths.author_mappings,
            {"yongxing-wang": ["Yongxing Wang", "Y. Wang"]},
        )
        code, stdout, stderr = self.run_cli(
            "hygiene",
            "--remove-author-variant",
            "yongxing-wang",
            "Y. Wang",
        )
        self.assertEqual(code, 1)
        self.assertEqual(stdout, "")
        self.assertIn("require --authors", stderr)


if __name__ == "__main__":
    unittest.main()
