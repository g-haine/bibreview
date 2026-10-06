from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
import json
import tempfile
import unittest
from unittest.mock import patch

from bibreview.cli import main
from bibreview.config import load_config
from bibreview.project_init import (
    apply_project_init_plan,
    execute_project_init_batch,
    plan_project_init_batch,
    project_init_status,
)
from bibreview.providers.base import Enrichment


CONFIG = """\
schema_version: 1
project:
  name: Example Review
  slug: example-review
discovery:
  provider: openalex
  query: fluid structure interaction
  accepted_types:
    - journal-article
relevance:
  patterns:
    - 'fluid[-\\s]+structure'
  unmatched: manual-review
initialization:
  campaign: state/init-campaign.json
  report: state/init-report.json
  batch_size: 2
site:
  enabled: false
"""


class FakeWorkProvider:
    def __init__(self, records):
        self.records = dict(records)
        self.calls = []

    def work(self, doi):
        self.calls.append(doi)
        return self.records.get(doi)


def work(title, *, abstract=""):
    return {
        "type": "journal-article",
        "title": [title],
        "_abstract": abstract,
    }


def enrich(_doi, message):
    return Enrichment(
        abstract=message.get("_abstract", ""),
        abstract_source="test" if message.get("_abstract") else "",
    )


class RelevanceReviewCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config_path = self.root / "bibreview.yml"
        self.config_path.write_text(CONFIG, encoding="utf-8")
        self.config = load_config(self.config_path)
        self.config.paths.review.parent.mkdir(parents=True, exist_ok=True)

    def services(self, *dois):
        records = {
            doi: work(f"Evidence for {doi}", abstract="Human review abstract")
            for doi in dois
        }
        return SimpleNamespace(
            provider=FakeWorkProvider(records),
            enrichment_lookup=enrich,
        )

    def snapshot(self):
        return {
            str(path.relative_to(self.root)): path.read_bytes()
            for path in self.root.rglob("*")
            if path.is_file()
        }

    def run_cli(self, services, *arguments, inputs=None):
        stdout = StringIO()
        stderr = StringIO()
        patches = [patch("bibreview.cli.build_discovery_services", return_value=services)]
        if inputs is not None:
            patches.append(patch("builtins.input", side_effect=inputs))

        with patches[0], redirect_stdout(stdout), redirect_stderr(stderr):
            if len(patches) == 2:
                with patches[1]:
                    code = main(["--config", str(self.config_path), *arguments])
            else:
                code = main(["--config", str(self.config_path), *arguments])
        return code, stdout.getvalue(), stderr.getvalue()

    def test_dry_run_refreshes_evidence_without_writing(self):
        self.config.paths.review.write_text("doi:10.1/review\n", encoding="utf-8")
        before = self.snapshot()
        services = self.services("10.1/review")

        code, stdout, stderr = self.run_cli(
            services,
            "--dry-run",
            "review",
        )

        self.assertEqual(code, 0, stderr)
        self.assertEqual(stderr, "")
        self.assertIn("Relevance review 1/1", stdout)
        self.assertIn("DOI: 10.1/review", stdout)
        self.assertIn("Evidence for 10.1/review", stdout)
        self.assertIn("no decisions were recorded", stdout)
        self.assertEqual(before, self.snapshot())

    def test_dry_run_json_is_machine_readable(self):
        self.config.paths.review.write_text("doi:10.1/review\n", encoding="utf-8")
        before = self.snapshot()

        code, stdout, stderr = self.run_cli(
            self.services("10.1/review"),
            "--dry-run",
            "review",
            "--json",
        )

        self.assertEqual(code, 0, stderr)
        payload = json.loads(stdout)
        self.assertTrue(payload["dry_run"])
        self.assertEqual(payload["cases"][0]["doi"], "10.1/review")
        self.assertEqual(before, self.snapshot())

    def test_keep_then_quit_and_resume_with_reject(self):
        self.config.paths.review.write_text(
            "doi:10.1/a\ndoi:10.1/b\n",
            encoding="utf-8",
        )

        code, stdout, stderr = self.run_cli(
            self.services("10.1/a", "10.1/b"),
            "review",
            inputs=["k", "q"],
        )
        self.assertEqual(code, 0, stderr)
        self.assertIn("KEEP: 10.1/a -> pending", stdout)
        self.assertIn("remaining manual review: 1", stdout)
        self.assertEqual(
            self.config.paths.pending.read_text(encoding="utf-8"),
            "doi:10.1/a\n",
        )
        self.assertEqual(
            self.config.paths.review.read_text(encoding="utf-8"),
            "doi:10.1/b\n",
        )

        code, stdout, stderr = self.run_cli(
            self.services("10.1/b"),
            "review",
            inputs=["r"],
        )
        self.assertEqual(code, 0, stderr)
        self.assertIn("REJECT: 10.1/b -> rejected", stdout)
        self.assertIn("remaining manual review: 0", stdout)
        self.assertEqual(self.config.paths.review.read_text(encoding="utf-8"), "")
        self.assertEqual(
            self.config.paths.rejected.read_text(encoding="utf-8"),
            "doi:10.1/b\n",
        )

    def test_defer_leaves_state_unchanged(self):
        self.config.paths.review.write_text("doi:10.1/review\n", encoding="utf-8")
        before = self.snapshot()

        code, stdout, stderr = self.run_cli(
            self.services("10.1/review"),
            "review",
            inputs=["s"],
        )

        self.assertEqual(code, 0, stderr)
        self.assertIn("deferred 1", stdout)
        self.assertEqual(before, self.snapshot())

    def test_keyboard_interrupt_preserves_completed_decisions_for_resume(self):
        self.config.paths.review.write_text(
            "doi:10.1/a\ndoi:10.1/b\n",
            encoding="utf-8",
        )

        code, stdout, stderr = self.run_cli(
            self.services("10.1/a", "10.1/b"),
            "review",
            inputs=["k", KeyboardInterrupt()],
        )

        self.assertEqual(code, 0, stderr)
        self.assertIn("previous decisions are preserved", stdout)
        self.assertEqual(
            self.config.paths.pending.read_text(encoding="utf-8"),
            "doi:10.1/a\n",
        )
        self.assertEqual(
            self.config.paths.review.read_text(encoding="utf-8"),
            "doi:10.1/b\n",
        )

    def initialize_manual_review(self):
        provider = FakeWorkProvider({"10.1/init": work("Neutral model")})
        plan = plan_project_init_batch(
            self.config,
            candidates=("10.1/init",),
            batch_size=1,
        )
        apply_project_init_plan(plan)
        execute_project_init_batch(
            self.config,
            batch_id=plan.batch.id,
            provider=provider,
            enrichment_lookup=enrich,
        )

    def test_init_review_displays_batch_context_and_reconciles_reject(self):
        self.initialize_manual_review()

        code, stdout, stderr = self.run_cli(
            self.services("10.1/init"),
            "review",
            inputs=["r"],
        )

        self.assertEqual(code, 0, stderr)
        self.assertIn("Initialization: batch-0001 (attempt 1)", stdout)
        self.assertIn("REJECT: 10.1/init -> rejected", stdout)
        status = project_init_status(self.config)
        self.assertEqual(status.review, 0)
        self.assertEqual(status.rejected, 1)
        self.assertIsNone(status.current_batch)


if __name__ == "__main__":
    unittest.main()
