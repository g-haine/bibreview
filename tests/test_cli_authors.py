from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
import json
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


class AuthorCliTests(unittest.TestCase):
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
            [
                Publication(
                    id=new_publication_id(),
                    authors=(Author(given="Grace", family="Hopper"),),
                )
            ],
        )
        write_json(self.config.paths.author_mappings, {})

    def snapshot(self):
        return {
            str(path.relative_to(self.root)): path.read_bytes()
            for path in self.root.rglob("*")
            if path.is_file()
        }

    def run_cli(self, *arguments):
        stdout = StringIO()
        stderr = StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(["--config", str(self.config_path), *arguments])
        return code, stdout.getvalue(), stderr.getvalue()

    def test_authors_json_is_read_only_by_default(self):
        before = self.snapshot()
        code, stdout, stderr = self.run_cli("authors", "--json")
        self.assertEqual(code, 0, stderr)
        self.assertEqual(stderr, "")
        payload = json.loads(stdout)
        self.assertEqual(payload["unknown_names"], 1)
        self.assertEqual(payload["safe"], {"grace-hopper": ["Grace Hopper"]})
        self.assertEqual(before, self.snapshot())

    def test_apply_safe_dry_run_then_apply(self):
        before = self.snapshot()
        code, stdout, stderr = self.run_cli("--dry-run", "authors", "--apply-safe", "--json")
        self.assertEqual(code, 0, stderr)
        payload = json.loads(stdout)
        self.assertEqual(payload["applied"], 0)
        self.assertEqual(payload["would_apply"], 1)
        self.assertEqual(before, self.snapshot())

        code, stdout, stderr = self.run_cli("authors", "--apply-safe", "--json")
        self.assertEqual(code, 0, stderr)
        payload = json.loads(stdout)
        self.assertEqual(payload["applied"], 1)
        self.assertEqual(payload["unknown_names"], 0)
        self.assertEqual(
            read_json(self.config.paths.author_mappings, dict),
            {"grace-hopper": ["Grace Hopper"]},
        )


if __name__ == "__main__":
    unittest.main()
