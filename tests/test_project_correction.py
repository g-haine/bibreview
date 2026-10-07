from pathlib import Path
import tempfile
import unittest

from bibreview.config import load_config
from bibreview.identity import new_publication_id
from bibreview.model import Author, Publication
from bibreview.project import ProjectStateError
from bibreview.project_correction import (
    apply_project_relevance_correction,
    inspect_project_relevance_correction,
    plan_project_relevance_correction,
)
from bibreview.project_relevance import project_relevance_analysis
from bibreview.relevance import RelevanceEvidence, read_relevance_evidence, relevance_evidence_data
from bibreview.storage import json_bytes, read_bibliography, write_bibliography


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


def publication(doi, *, permalink="example"):
    return Publication(
        id=new_publication_id(),
        identifiers={"doi": doi},
        type="journal-article",
        title="Partitioned coupling method",
        authors=(Author(literal="Ada Lovelace"),),
        publication_year="2026",
        permalink=permalink,
    )


class ProjectRelevanceCorrectionTests(unittest.TestCase):
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

    def write_evidence(self, *entries):
        path = self.config.relevance.evidence
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(json_bytes(relevance_evidence_data(entries)))

    def evidence(self, doi, *, outcome="rejected", human=""):
        return RelevanceEvidence(
            doi=doi,
            title="Partitioned coupling method",
            abstract="",
            keywords=(),
            work_type="journal-article",
            screening_outcome=outcome,
            human_decision=human,
        )

    def test_correct_reject_to_keep_requeues_without_losing_evidence(self):
        self.config.paths.rejected.parent.mkdir(parents=True, exist_ok=True)
        self.config.paths.rejected.write_text("doi:10.1/reject\n", encoding="utf-8")
        self.write_evidence(self.evidence("10.1/reject"))
        before = self.snapshot()

        state = inspect_project_relevance_correction(self.config, doi="10.1/REJECT")
        self.assertEqual(state.current_decision, "reject")
        self.assertEqual(state.decision_provenance, "terminal")
        plan = plan_project_relevance_correction(
            self.config,
            doi="10.1/reject",
            decision="keep",
        )
        self.assertEqual(before, self.snapshot())
        self.assertTrue(plan.changed)
        self.assertEqual(plan.deletes, ())

        apply_project_relevance_correction(plan)
        self.assertEqual(self.config.paths.rejected.read_text(encoding="utf-8"), "")
        self.assertEqual(
            self.config.paths.pending.read_text(encoding="utf-8"),
            "doi:10.1/reject\n",
        )
        evidence = read_relevance_evidence(self.config.relevance.evidence)
        self.assertEqual(evidence[0].screening_outcome, "rejected")
        self.assertEqual(evidence[0].human_decision, "")
        self.assertEqual(evidence[0].correction_decision, "keep")

    def test_correct_canonical_keep_to_reject_archives_and_removes_content(self):
        item = publication("10.1/keep", permalink="partitioned-coupling")
        write_bibliography(self.config.paths.bibliography, (item,))
        self.config.paths.known.parent.mkdir(parents=True, exist_ok=True)
        self.config.paths.known.write_text("doi:10.1/keep\n", encoding="utf-8")
        self.config.paths.bibtex.mkdir(parents=True, exist_ok=True)
        bibtex = self.config.paths.bibtex / "partitioned-coupling.bib"
        bibtex.write_text("@article{example}\n", encoding="utf-8")
        self.write_evidence(self.evidence("10.1/keep", outcome="review", human="keep"))
        before = self.snapshot()

        plan = plan_project_relevance_correction(
            self.config,
            doi="10.1/keep",
            decision="reject",
        )
        self.assertEqual(before, self.snapshot())
        self.assertIsNotNone(plan.backup)
        self.assertEqual(plan.deletes, (bibtex,))

        apply_project_relevance_correction(plan)
        self.assertEqual(read_bibliography(self.config.paths.bibliography), ())
        self.assertEqual(self.config.paths.known.read_text(encoding="utf-8"), "")
        self.assertEqual(
            self.config.paths.rejected.read_text(encoding="utf-8"),
            "doi:10.1/keep\n",
        )
        self.assertFalse(bibtex.exists())
        self.assertTrue(plan.backup.exists())
        self.assertTrue(any(self.config.paths.archive.glob("bibtex-*.bib")))
        evidence = read_relevance_evidence(self.config.relevance.evidence)
        self.assertEqual(evidence[0].human_decision, "keep")
        self.assertEqual(evidence[0].correction_decision, "reject")
        analysis = project_relevance_analysis(self.config)
        self.assertEqual(analysis["summary"]["label_sources"], {"correction": 1})
        self.assertEqual(analysis["summary"]["human_labeled"], 1)

    def test_pending_staged_can_be_cancelled_to_reject(self):
        item = publication("10.1/pending")
        write_bibliography(self.config.paths.collected, (item,))
        self.config.paths.pending.parent.mkdir(parents=True, exist_ok=True)
        self.config.paths.pending.write_text("doi:10.1/pending\n", encoding="utf-8")
        self.write_evidence(self.evidence("10.1/pending", outcome="queued"))

        state = inspect_project_relevance_correction(self.config, doi="10.1/pending")
        self.assertEqual(state.surfaces, ("pending", "staged", "evidence"))
        plan = plan_project_relevance_correction(
            self.config,
            doi="10.1/pending",
            decision="reject",
        )
        self.assertTrue(plan.changed)
        apply_project_relevance_correction(plan)
        self.assertEqual(self.config.paths.pending.read_text(encoding="utf-8"), "")
        self.assertEqual(self.config.paths.rejected.read_text(encoding="utf-8"), "doi:10.1/pending\n")
        self.assertEqual(read_bibliography(self.config.paths.collected), ())

    def test_matching_pending_keep_is_a_read_only_no_op(self):
        self.config.paths.pending.parent.mkdir(parents=True, exist_ok=True)
        self.config.paths.pending.write_text("doi:10.1/pending\n", encoding="utf-8")
        self.write_evidence(self.evidence("10.1/pending", outcome="queued"))
        before = self.snapshot()

        plan = plan_project_relevance_correction(
            self.config,
            doi="10.1/pending",
            decision="keep",
        )

        self.assertFalse(plan.changed)
        apply_project_relevance_correction(plan)
        self.assertEqual(before, self.snapshot())

    def test_review_uses_human_decision_provenance(self):
        self.config.paths.review.parent.mkdir(parents=True, exist_ok=True)
        self.config.paths.review.write_text("doi:10.1/review\n", encoding="utf-8")
        self.write_evidence(self.evidence("10.1/review", outcome="review"))

        plan = plan_project_relevance_correction(
            self.config,
            doi="10.1/review",
            decision="keep",
        )
        apply_project_relevance_correction(plan)

        self.assertEqual(self.config.paths.review.read_text(encoding="utf-8"), "")
        self.assertEqual(self.config.paths.pending.read_text(encoding="utf-8"), "doi:10.1/review\n")
        evidence = read_relevance_evidence(self.config.relevance.evidence)
        self.assertEqual(evidence[0].human_decision, "keep")
        self.assertEqual(evidence[0].correction_decision, "")


if __name__ == "__main__":
    unittest.main()
