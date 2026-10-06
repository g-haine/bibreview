from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

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


    def write_ambiguous_authors(self, *, include_second=True):
        publications = [
            Publication(
                id=new_publication_id(),
                identifiers={"doi": "10.1/hong"},
                title="Hong FSI paper",
                authors=(
                    Author(
                        given="Y.",
                        family="Hong",
                        source_fields={
                            "ORCID": "https://orcid.org/0000-0001-2345-6789",
                            "affiliation": [{"name": "Example University"}],
                        },
                    ),
                ),
            ),
            Publication(
                id=new_publication_id(),
                identifiers={"doi": "10.1/yin-hong"},
                title="Known Yin Hong paper",
                authors=(
                    Author(
                        given="Yin",
                        family="Hong",
                        source_fields={
                            "affiliation": [{"name": "Known Institute"}],
                        },
                    ),
                ),
            ),
        ]
        if include_second:
            publications.append(
                Publication(
                    id=new_publication_id(),
                    identifiers={"doi": "10.1/wang"},
                    title="Wang FSI paper",
                    authors=(Author(given="G.", family="Wang"),),
                )
            )
        write_bibliography(self.config.paths.bibliography, publications)
        mapping = {"yin-hong": ["Yin Hong"]}
        if include_second:
            mapping["gang-wang"] = ["Gang Wang"]
        write_json(self.config.paths.author_mappings, mapping)

    def test_manual_review_merges_existing_then_quits_and_resumes_with_new_identity(self):
        self.write_ambiguous_authors()
        with patch("builtins.input", side_effect=["1", "q"]):
            code, stdout, stderr = self.run_cli("authors", "--review")
        self.assertEqual(code, 0, stderr)
        self.assertEqual(stderr, "")
        self.assertIn("Mapped: Y. Hong -> yin-hong", stdout)
        self.assertIn("remaining manual review: 1", stdout)
        mapping = read_json(self.config.paths.author_mappings, dict)
        self.assertEqual(mapping["yin-hong"], ["Yin Hong", "Y. Hong"])
        self.assertNotIn("g-wang", mapping)

        with patch("builtins.input", side_effect=["n guang-wang"]):
            code, stdout, stderr = self.run_cli("authors", "--review")
        self.assertEqual(code, 0, stderr)
        self.assertEqual(stderr, "")
        self.assertIn("Created: G. Wang -> guang-wang", stdout)
        self.assertIn("remaining manual review: 0", stdout)
        mapping = read_json(self.config.paths.author_mappings, dict)
        self.assertEqual(mapping["guang-wang"], ["G. Wang"])

    def test_manual_review_can_defer_without_writing(self):
        self.write_ambiguous_authors(include_second=False)
        before = self.snapshot()
        with patch("builtins.input", side_effect=["s"]):
            code, stdout, stderr = self.run_cli("authors", "--review")
        self.assertEqual(code, 0, stderr)
        self.assertEqual(stderr, "")
        self.assertIn("deferred 1", stdout)
        self.assertIn("remaining manual review: 1", stdout)
        self.assertEqual(before, self.snapshot())

    def test_manual_review_rejects_new_identity_slug_collision(self):
        self.write_ambiguous_authors(include_second=False)
        before = self.snapshot()
        with patch("builtins.input", side_effect=["n yin-hong", "s"]):
            code, stdout, stderr = self.run_cli("authors", "--review")
        self.assertEqual(code, 0, stderr)
        self.assertEqual(stderr, "")
        self.assertIn("Invalid author decision", stdout)
        self.assertIn("already exists", stdout)
        self.assertEqual(before, self.snapshot())

    def test_manual_review_dry_run_prints_evidence_without_prompt_or_write(self):
        self.write_ambiguous_authors(include_second=False)
        before = self.snapshot()
        code, stdout, stderr = self.run_cli("--dry-run", "authors", "--review")
        self.assertEqual(code, 0, stderr)
        self.assertEqual(stderr, "")
        self.assertIn("Source name: Y. Hong", stdout)
        self.assertIn("DOI 10.1/hong: Hong FSI paper", stdout)
        self.assertIn("ORCID: https://orcid.org/0000-0001-2345-6789", stdout)
        self.assertIn("Affiliation: Example University", stdout)
        self.assertIn("Canonical publications:", stdout)
        self.assertIn("DOI 10.1/yin-hong: Known Yin Hong paper", stdout)
        self.assertIn("Affiliation: Known Institute", stdout)
        self.assertIn("no decisions were recorded", stdout)
        self.assertEqual(before, self.snapshot())

    def test_manual_review_is_incompatible_with_json(self):
        self.write_ambiguous_authors(include_second=False)
        code, stdout, stderr = self.run_cli("authors", "--review", "--json")
        self.assertEqual(code, 1)
        self.assertEqual(stdout, "")
        self.assertIn("--json cannot be used with interactive authors --review", stderr)


if __name__ == "__main__":
    unittest.main()
