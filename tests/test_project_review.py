from pathlib import Path
import tempfile
import unittest

from bibreview.config import load_config
from bibreview.project_init import (
    apply_project_init_plan,
    execute_project_init_batch,
    plan_project_init_batch,
    project_init_status,
)
from bibreview.project_review import (
    apply_project_relevance_review_decision,
    format_relevance_review_case,
    plan_project_relevance_review_decision,
    project_relevance_review_cases,
)
from bibreview.providers.base import Enrichment
from bibreview.storage import read_json


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
  reject_patterns:
    - 'soil'
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


def work(title, *, abstract="", keywords=()):
    return {
        "type": "journal-article",
        "title": [title],
        "_abstract": abstract,
        "_keywords": tuple(keywords),
    }


def enrich(_doi, message):
    return Enrichment(
        abstract=message.get("_abstract", ""),
        keywords=message.get("_keywords", ()),
        abstract_source="test" if message.get("_abstract") else "",
    )


class ProjectRelevanceReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config_path = self.root / "bibreview.yml"
        self.config_path.write_text(CONFIG, encoding="utf-8")
        self.config = load_config(self.config_path)
        self.config.paths.review.parent.mkdir(parents=True, exist_ok=True)

    def test_cases_refresh_human_evidence_and_pattern_diagnostics(self):
        self.config.paths.review.write_text("doi:10.1/review\n", encoding="utf-8")
        provider = FakeWorkProvider(
            {
                "10.1/review": work(
                    "Fluid-structure coupling",
                    abstract="A soil boundary example",
                    keywords=("fsi", "coupling"),
                )
            }
        )

        cases = project_relevance_review_cases(
            self.config,
            provider=provider,
            enrichment_lookup=enrich,
        )

        self.assertEqual(len(cases), 1)
        case = cases[0]
        self.assertEqual(case.doi, "10.1/review")
        self.assertEqual(case.title, "Fluid-structure coupling")
        self.assertEqual(case.work_type, "journal-article")
        self.assertEqual(case.abstract, "A soil boundary example")
        self.assertEqual(case.keywords, ("fsi", "coupling"))
        self.assertEqual(case.accept_matches, ("fluid[-\\s]+structure",))
        self.assertEqual(case.reject_matches, ("soil",))
        self.assertIsNone(case.init_batch)

        plain = format_relevance_review_case(case)
        self.assertIn("DOI: 10.1/review", plain)
        self.assertIn("Current accept-pattern matches:", plain)
        self.assertNotIn("\x1b]8;;", plain)

        linked = format_relevance_review_case(case, hyperlinks=True)
        self.assertIn(
            "\x1b]8;;https://doi.org/10.1/review\x1b\\10.1/review\x1b]8;;\x1b\\",
            linked,
        )

    def test_ordinary_keep_moves_review_to_pending(self):
        self.config.paths.pending.write_text("doi:10.1/existing\n", encoding="utf-8")
        self.config.paths.review.write_text("doi:10.1/review\n", encoding="utf-8")
        self.config.paths.rejected.write_text("doi:10.1/rejected\n", encoding="utf-8")

        plan = plan_project_relevance_review_decision(
            self.config,
            doi="10.1/review",
            decision="keep",
        )
        self.assertEqual(plan.remaining_review, 0)
        self.assertIsNone(plan.init_batch)
        apply_project_relevance_review_decision(plan)

        self.assertEqual(
            self.config.paths.pending.read_text(encoding="utf-8"),
            "doi:10.1/existing\ndoi:10.1/review\n",
        )
        self.assertEqual(self.config.paths.review.read_text(encoding="utf-8"), "")
        self.assertEqual(
            self.config.paths.rejected.read_text(encoding="utf-8"),
            "doi:10.1/rejected\n",
        )

    def test_ordinary_reject_moves_review_to_rejected(self):
        self.config.paths.review.write_text("doi:10.1/review\n", encoding="utf-8")

        plan = plan_project_relevance_review_decision(
            self.config,
            doi="10.1/review",
            decision="reject",
        )
        apply_project_relevance_review_decision(plan)

        self.assertEqual(self.config.paths.review.read_text(encoding="utf-8"), "")
        self.assertEqual(
            self.config.paths.rejected.read_text(encoding="utf-8"),
            "doi:10.1/review\n",
        )

    def initialize_manual_review(self, doi="10.1/init"):
        provider = FakeWorkProvider({doi: work("Neutral coupled model")})
        plan = plan_project_init_batch(
            self.config,
            candidates=(doi,),
            batch_size=1,
        )
        apply_project_init_plan(plan)
        self.assertIsNotNone(plan.batch)
        execute_project_init_batch(
            self.config,
            batch_id=plan.batch.id,
            provider=provider,
            enrichment_lookup=enrich,
        )
        self.assertEqual(
            self.config.paths.review.read_text(encoding="utf-8"),
            f"doi:{doi}\n",
        )

    def test_init_keep_reconciles_report_but_keeps_batch_active(self):
        self.initialize_manual_review()

        plan = plan_project_relevance_review_decision(
            self.config,
            doi="10.1/init",
            decision="keep",
        )
        self.assertEqual(plan.init_batch, "batch-0001")
        apply_project_relevance_review_decision(plan)

        report = read_json(self.config.initialization.report, dict)
        entry = next(item for item in report["entries"] if item["doi"] == "10.1/init")
        self.assertEqual(entry["outcome"], "queued")
        status = project_init_status(self.config)
        self.assertEqual(status.queued, 1)
        self.assertEqual(status.review, 0)
        self.assertEqual(status.current_batch, "batch-0001")
        self.assertEqual(status.batches_closed, 0)

    def test_init_reject_completes_candidate_and_closes_resolved_batch(self):
        self.initialize_manual_review()

        plan = plan_project_relevance_review_decision(
            self.config,
            doi="10.1/init",
            decision="reject",
        )
        apply_project_relevance_review_decision(plan)

        report = read_json(self.config.initialization.report, dict)
        entry = next(item for item in report["entries"] if item["doi"] == "10.1/init")
        self.assertEqual(entry["outcome"], "rejected")
        status = project_init_status(self.config)
        self.assertEqual(status.rejected, 1)
        self.assertEqual(status.review, 0)
        self.assertIsNone(status.current_batch)
        self.assertEqual(status.batches_closed, 1)


if __name__ == "__main__":
    unittest.main()
