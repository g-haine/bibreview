from pathlib import Path
import tempfile
import unittest

from bibreview.config import load_config
from bibreview.identity import new_publication_id
from bibreview.model import Author, Publication
from bibreview.project_relevance import (
    apply_project_relevance_backfill,
    plan_project_relevance_backfill,
    project_relevance_analysis,
)
from bibreview.project_review import (
    apply_project_relevance_review_decision,
    plan_project_relevance_review_decision,
)
from bibreview.relevance import (
    RelevanceEvidence,
    read_relevance_evidence,
    relevance_evidence_data,
)
from bibreview.storage import json_bytes, write_bibliography


CONFIG = """\
schema_version: 1
project:
  name: Example Review
  slug: example-review
discovery:
  query: fluid structure interaction
  accepted_types:
    - journal-article
relevance:
  evidence: audit/relevance/evidence.json
  patterns:
    - 'partitioned[-\\s]+coupling'
  reject_patterns:
    - 'experimental[-\\s]+test'
  unmatched: manual-review
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


def publication(doi, title):
    return Publication(
        id=new_publication_id(),
        identifiers={"doi": doi},
        type="journal-article",
        title=title,
        authors=(Author(given="Ada", family="Lovelace"),),
        abstract="Canonical abstract",
        publication_year="2026",
        permalink=title.lower().replace(" ", "-"),
    )


class ProjectRelevanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config_path = self.root / "bibreview.yml"
        self.config_path.write_text(CONFIG, encoding="utf-8")
        self.config = load_config(self.config_path)

    def snapshot(self):
        return {
            str(path.relative_to(self.root)): path.read_bytes()
            for path in self.root.rglob("*")
            if path.is_file()
        }

    def write_evidence(self, entries):
        path = self.config.relevance.evidence
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(json_bytes(relevance_evidence_data(entries)))

    def test_analysis_joins_human_canonical_and_terminal_labels_offline(self):
        write_bibliography(
            self.config.paths.bibliography,
            (
                publication(
                    "10.1/auto-keep",
                    "Partitioned coupling for fluid structure interaction",
                ),
                publication(
                    "10.1/human-keep",
                    "Reviewed coupling method",
                ),
            ),
        )
        self.config.paths.rejected.parent.mkdir(parents=True, exist_ok=True)
        self.config.paths.rejected.write_text(
            "doi:10.1/human-reject\ndoi:10.1/auto-reject\n",
            encoding="utf-8",
        )
        self.write_evidence(
            (
                RelevanceEvidence(
                    doi="10.1/auto-keep",
                    title="Partitioned coupling for fluid structure interaction",
                    abstract="",
                    keywords=(),
                    work_type="journal-article",
                    screening_outcome="queued",
                ),
                RelevanceEvidence(
                    doi="10.1/human-keep",
                    title="Reviewed coupling method",
                    abstract="",
                    keywords=(),
                    work_type="journal-article",
                    screening_outcome="review",
                    human_decision="keep",
                ),
                RelevanceEvidence(
                    doi="10.1/human-reject",
                    title="Experimental test application",
                    abstract="",
                    keywords=(),
                    work_type="journal-article",
                    screening_outcome="review",
                    human_decision="reject",
                ),
                RelevanceEvidence(
                    doi="10.1/auto-reject",
                    title="Experimental test benchmark",
                    abstract="",
                    keywords=(),
                    work_type="journal-article",
                    screening_outcome="rejected",
                ),
            )
        )

        before = self.snapshot()
        analysis = project_relevance_analysis(self.config)

        self.assertEqual(before, self.snapshot())
        self.assertEqual(analysis["summary"]["labeled"], 4)
        self.assertEqual(analysis["summary"]["human_labeled"], 2)
        self.assertEqual(
            analysis["summary"]["label_sources"],
            {"canonical": 1, "human": 2, "terminal": 1},
        )

    def test_legacy_backfill_uses_canonical_metadata_before_provider(self):
        write_bibliography(
            self.config.paths.bibliography,
            (
                publication(
                    "10.1/keep",
                    "Partitioned coupling for fluid structure interaction",
                ),
            ),
        )
        self.config.paths.rejected.parent.mkdir(parents=True, exist_ok=True)
        self.config.paths.rejected.write_text(
            "doi:10.1/reject\n",
            encoding="utf-8",
        )
        provider = FakeWorkProvider(
            {
                "10.1/reject": {
                    "type": "journal-article",
                    "title": ["Experimental test application"],
                }
            }
        )
        before = self.snapshot()

        plan = plan_project_relevance_backfill(
            self.config,
            provider=provider,
        )

        self.assertEqual(before, self.snapshot())
        self.assertEqual(provider.calls, ["10.1/reject"])
        self.assertEqual(plan.canonical_added, 1)
        self.assertEqual(plan.provider_added, 1)
        self.assertEqual(plan.unavailable, ())

        apply_project_relevance_backfill(plan)
        evidence = read_relevance_evidence(self.config.relevance.evidence)
        by_doi = {entry.doi: entry for entry in evidence}
        self.assertEqual(by_doi["10.1/keep"].source, "canonical-backfill")
        self.assertEqual(by_doi["10.1/reject"].source, "backfill")
        self.assertEqual(by_doi["10.1/keep"].screening_outcome, "unknown")
        self.assertEqual(by_doi["10.1/reject"].screening_outcome, "unknown")

    def test_manual_review_decision_is_recorded_in_existing_evidence(self):
        self.config.paths.review.parent.mkdir(parents=True, exist_ok=True)
        self.config.paths.review.write_text(
            "doi:10.1/review\n",
            encoding="utf-8",
        )
        self.config.paths.pending.write_text("", encoding="utf-8")
        self.config.paths.rejected.write_text("", encoding="utf-8")
        self.write_evidence(
            (
                RelevanceEvidence(
                    doi="10.1/review",
                    title="Ambiguous fluid structure paper",
                    abstract="",
                    keywords=(),
                    work_type="journal-article",
                    screening_outcome="review",
                ),
            )
        )

        plan = plan_project_relevance_review_decision(
            self.config,
            doi="10.1/review",
            decision="keep",
        )
        apply_project_relevance_review_decision(plan)

        evidence = read_relevance_evidence(self.config.relevance.evidence)
        self.assertEqual(evidence[0].human_decision, "keep")
        self.assertEqual(
            self.config.paths.pending.read_text(encoding="utf-8"),
            "doi:10.1/review\n",
        )


if __name__ == "__main__":
    unittest.main()
