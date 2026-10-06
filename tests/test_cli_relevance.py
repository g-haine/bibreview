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
from bibreview.relevance import RelevanceEvidence, relevance_evidence_data
from bibreview.storage import json_bytes, write_bibliography


CONFIG = """\
schema_version: 1
project:
  name: Example Review
  slug: example-review
relevance:
  evidence: audit/relevance/evidence.json
  patterns:
    - 'partitioned[-\\s]+coupling'
  unmatched: manual-review
site:
  enabled: false
"""


class RelevanceCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config_path = self.root / "bibreview.yml"
        self.config_path.write_text(CONFIG, encoding="utf-8")
        self.config = load_config(self.config_path)

        publication = Publication(
            id=new_publication_id(),
            identifiers={"doi": "10.1/keep"},
            type="journal-article",
            title="Partitioned coupling method",
            authors=(Author(literal="Reviewed Author"),),
            publication_year="2026",
            permalink="partitioned-coupling-method",
        )
        write_bibliography(self.config.paths.bibliography, (publication,))
        evidence = RelevanceEvidence(
            doi="10.1/keep",
            title=publication.title,
            abstract="",
            keywords=(),
            work_type="journal-article",
            screening_outcome="queued",
        )
        self.config.relevance.evidence.parent.mkdir(parents=True, exist_ok=True)
        self.config.relevance.evidence.write_bytes(
            json_bytes(relevance_evidence_data((evidence,)))
        )

    def run_cli(self, *arguments):
        stdout = StringIO()
        stderr = StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(["--config", str(self.config_path), *arguments])
        return code, stdout.getvalue(), stderr.getvalue()

    def test_analyze_json_is_offline_and_read_only(self):
        before = {
            str(path.relative_to(self.root)): path.read_bytes()
            for path in self.root.rglob("*")
            if path.is_file()
        }

        code, stdout, stderr = self.run_cli(
            "relevance",
            "--analyze",
            "--json",
        )

        self.assertEqual(code, 0, stderr)
        self.assertEqual(stderr, "")
        payload = json.loads(stdout)
        self.assertEqual(payload["summary"]["evidence"], 1)
        self.assertEqual(payload["summary"]["labeled"], 1)
        self.assertEqual(payload["summary"]["keep"], 1)
        after = {
            str(path.relative_to(self.root)): path.read_bytes()
            for path in self.root.rglob("*")
            if path.is_file()
        }
        self.assertEqual(before, after)

    def test_relevance_requires_an_explicit_action(self):
        code, _, stderr = self.run_cli("relevance")
        self.assertEqual(code, 2)
        self.assertIn("required", stderr)


if __name__ == "__main__":
    unittest.main()
