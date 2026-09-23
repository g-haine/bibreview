from __future__ import annotations

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
from bibreview.storage import write_bibliography


CONFIG = """\
schema_version: 1
project:
  name: Example Review
  slug: example-review
site:
  enabled: false
"""


class HygieneCliTests(unittest.TestCase):
    def setUp(self) -> None:
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
                    identifiers={"doi": "10.1/clean"},
                    title="Clean",
                    authors=(Author(literal="Ada Lovelace"),),
                    abstract=r"Clean \(V\) abstract.",
                ),
                Publication(
                    id=new_publication_id(),
                    identifiers={"doi": "10.1/markup"},
                    title="Markup",
                    authors=(Author(literal="Alan Turing"),),
                    abstract=(
                        '<p>Text <inline-formula>'
                        '<mml:math xmlns:mml="urn:test">'
                        '<mml:annotation encoding="application/x-tex">V</mml:annotation>'
                        '</mml:math></inline-formula>.</p>'
                    ),
                ),
            ),
        )

    def snapshot(self) -> dict[str, bytes]:
        return {
            str(path.relative_to(self.root)): path.read_bytes()
            for path in self.root.rglob("*")
            if path.is_file()
        }

    def run(self, *extra: str) -> tuple[int, str, str]:
        stdout = StringIO()
        stderr = StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(["--config", str(self.config_path), *extra])
        return code, stdout.getvalue(), stderr.getvalue()

    def test_hygiene_is_read_only_and_summary_only_by_default(self) -> None:
        before = self.snapshot()
        code, stdout, stderr = self.run("hygiene")

        self.assertEqual(code, 0, stderr)
        self.assertIn("Publications scanned     : 2", stdout)
        self.assertIn("Suspicious abstracts     : 1", stdout)
        self.assertNotIn("10.1/markup", stdout)
        self.assertEqual(before, self.snapshot())

    def test_verbose_hygiene_lists_findings_with_short_context(self) -> None:
        code, stdout, stderr = self.run("-v", "hygiene")

        self.assertEqual(code, 0, stderr)
        self.assertIn("10.1/markup: Markup", stdout)
        self.assertIn("embedded-tex-annotation", stdout)
        self.assertIn("Context:", stdout)

    def test_hygiene_json_is_complete(self) -> None:
        code, stdout, stderr = self.run("hygiene", "--json")

        self.assertEqual(code, 0, stderr)
        payload = json.loads(stdout)
        self.assertEqual(payload["scanned_publications"], 2)
        self.assertEqual(payload["suspicious_abstracts"], 1)
        self.assertEqual(payload["findings"][0]["doi"], "10.1/markup")
        self.assertEqual(
            payload["findings"][0]["normalization_hint"],
            "embedded-tex-annotation",
        )

    def test_missing_canonical_bibliography_fails_without_traceback(self) -> None:
        self.config.paths.bibliography.unlink()
        code, stdout, stderr = self.run("hygiene")

        self.assertEqual(code, 1)
        self.assertEqual(stdout, "")
        self.assertIn("bibreview hygiene:", stderr)
        self.assertNotIn("Traceback", stderr)


if __name__ == "__main__":
    unittest.main()
