from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
import json
import tempfile
import unittest

from bibreview.cli import main
from bibreview.relevance import RelevanceEvidence, relevance_evidence_data
from bibreview.storage import json_bytes


CONFIG = """\
schema_version: 1
project:
  name: Example Review
  slug: example-review
relevance:
  evidence: audit/relevance/evidence.json
site:
  enabled: false
"""


class RelevanceCorrectionCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config_path = self.root / "bibreview.yml"
        self.config_path.write_text(CONFIG, encoding="utf-8")
        self.rejected = self.root / "data" / "badID.txt"
        self.rejected.parent.mkdir(parents=True)
        self.rejected.write_text("doi:10.1/reject\n", encoding="utf-8")
        evidence = RelevanceEvidence(
            doi="10.1/reject",
            title="Coupled model",
            abstract="",
            keywords=(),
            work_type="journal-article",
            screening_outcome="rejected",
        )
        path = self.root / "audit" / "relevance" / "evidence.json"
        path.parent.mkdir(parents=True)
        path.write_bytes(json_bytes(relevance_evidence_data((evidence,))))

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

    def test_inspection_is_offline_and_read_only(self):
        before = self.snapshot()
        code, stdout, stderr = self.run_cli("correct", "10.1/reject")

        self.assertEqual(code, 0, stderr)
        self.assertIn("Terminal relevance: REJECT", stdout)
        self.assertIn("Current provenance: terminal", stdout)
        self.assertEqual(before, self.snapshot())

    def test_dry_run_keep_reports_machine_readable_plan_without_writing(self):
        before = self.snapshot()
        code, stdout, stderr = self.run_cli(
            "--dry-run",
            "correct",
            "10.1/reject",
            "--keep",
            "--json",
        )

        self.assertEqual(code, 0, stderr)
        payload = json.loads(stdout)
        self.assertTrue(payload["dry_run"])
        self.assertEqual(payload["decision"], "keep")
        self.assertEqual(payload["state"]["current_decision"], "reject")
        self.assertEqual(before, self.snapshot())

    def test_keep_applies_the_explicit_terminal_correction(self):
        code, stdout, stderr = self.run_cli("correct", "10.1/reject", "--keep")

        self.assertEqual(code, 0, stderr)
        self.assertIn("REJECT -> KEEP", stdout)
        self.assertIn("Target state: pending ordinary collection.", stdout)
        self.assertEqual(self.rejected.read_text(encoding="utf-8"), "")
        self.assertEqual(
            (self.root / "data" / "newID.txt").read_text(encoding="utf-8"),
            "doi:10.1/reject\n",
        )


if __name__ == "__main__":
    unittest.main()
