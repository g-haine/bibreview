from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from bibreview.audit_resolution import (
    AuditResolutionDecision,
    AuditResolutionState,
)
from bibreview.config import load_config
from bibreview.identity import new_publication_id
from bibreview.model import Author, Publication
from bibreview.pipeline.audit import AuditReviewFinding
from bibreview.project import ProjectStateError
from bibreview.project_audit import AuditPublicationReview, ProjectAuditReview
from bibreview.project_audit_apply import (
    apply_project_audit_apply,
    format_project_audit_apply_plan,
    plan_project_audit_apply,
)
from bibreview.storage import read_bibliography, write_bibliography


CONFIG = """\
schema_version: 1
project:
  name: Example Review
  slug: example-review
paths:
  bibliography: data/bibliography.json
  collected: data/collected.json
  author_mappings: data/authors.json
  known: data/known.txt
  pending: data/pending.txt
  rejected: data/rejected.txt
  review: data/review.txt
  bibtex: bib
  archive: archive
  site: site
audit:
  campaign: state/audit-campaign.json
  report: state/audit-report.json
  batch_size: 50
site:
  enabled: false
"""


class ProjectAuditApplyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config_path = self.root / "bibreview.yml"
        self.config_path.write_text(CONFIG, encoding="utf-8")
        self.config = load_config(self.config_path)
        self.publication = Publication(
            id=new_publication_id(),
            identifiers={"doi": "10.1000/example"},
            type="journal-article",
            title="Old title",
            authors=(Author(given="Ada", family="Lovelace"),),
            container_title="Journal",
            publication_year="2025",
            volume="1",
            issue="2",
            pages="10--19",
            publisher="Example Press",
            permalink="old-title",
        )
        write_bibliography(
            self.config.paths.bibliography,
            (self.publication,),
        )
        self.config.paths.bibtex.mkdir(parents=True, exist_ok=True)
        self.bibtex_path = self.config.paths.bibtex / "old-title.bib"
        self.bibtex_path.write_text(
            "@article{example,\n"
            "  title={{Old title}},\n"
            "  author={Ada Lovelace},\n"
            "  journal={Journal},\n"
            "  year={2025},\n"
            "  volume={1},\n"
            "  number={2},\n"
            "  pages={10--19},\n"
            "  publisher={Example Press}\n"
            "}\n",
            encoding="utf-8",
        )

    def finding(self, field, canonical, provider):
        return AuditReviewFinding(
            field=field,
            classification="substantive-difference",
            providers=("crossref", "openalex"),
            canonical_value=canonical,
            provider_values=(
                ("crossref", provider),
                ("openalex", provider),
            ),
            actionable=True,
            detail="corroborated by 2 independent providers",
        )

    def review(self, *findings):
        return ProjectAuditReview(
            audited_publications=1,
            flagged_publications=1,
            actionable_findings=len(findings),
            informational_findings=0,
            provider_issues=0,
            items=(
                AuditPublicationReview(
                    publication_id=self.publication.id,
                    identifiers=self.publication.identifiers,
                    permalink=self.publication.permalink,
                    title=self.publication.title,
                    findings=tuple(findings),
                ),
            ),
        )

    def decision(self, field, decision, value=None):
        return AuditResolutionDecision(
            key=f"{self.publication.id}:{field}",
            publication_id=self.publication.id,
            doi=self.publication.doi,
            title=self.publication.title,
            field=field,
            decision=decision,
            resolved_value=value,
        )

    def state(self, *decisions):
        return AuditResolutionState(
            review_fingerprint="test-fingerprint",
            total_actionable=len(decisions),
            decisions=tuple(decisions),
        )

    def patched_plan(self, review, state):
        with patch(
            "bibreview.project_audit_apply.project_audit_review",
            return_value=review,
        ), patch(
            "bibreview.project_audit_apply.load_project_audit_resolutions",
            return_value=state,
        ):
            return plan_project_audit_apply(self.config)

    def test_stages_accepted_and_custom_changes_updates_bibtex_and_backs_up(self):
        review = self.review(
            self.finding("title", "Old title", "New title"),
            self.finding("pages", "10--19", "10-20"),
            self.finding("volume", "1", "2"),
        )
        state = self.state(
            self.decision("title", "accepted", "New title"),
            self.decision("pages", "custom", "10--20"),
            self.decision("volume", "rejected"),
        )

        plan = self.patched_plan(review, state)

        self.assertEqual(len(plan.changes), 2)
        self.assertEqual(plan.affected_publication_ids, (self.publication.id,))
        self.assertEqual(plan.bibtex_files_affected, 1)
        self.assertIn(self.config.paths.collected, plan.outputs)
        self.assertIn(self.bibtex_path, plan.outputs)
        self.assertEqual(len(plan.bibtex_backups), 1)
        self.assertFalse(plan.bibtex_backups[0].exists())

        original_bibtex = self.bibtex_path.read_bytes()
        apply_project_audit_apply(plan)

        staged = read_bibliography(self.config.paths.collected)
        self.assertEqual(len(staged), 1)
        self.assertEqual(staged[0].title, "New title")
        self.assertEqual(staged[0].pages, "10--20")
        self.assertEqual(staged[0].volume, "1")
        updated_bibtex = self.bibtex_path.read_text(encoding="utf-8")
        self.assertIn("title={{New title}}", updated_bibtex)
        self.assertIn("pages={10--20}", updated_bibtex)
        self.assertIn("volume={1}", updated_bibtex)
        self.assertEqual(plan.bibtex_backups[0].read_bytes(), original_bibtex)

    def test_nonempty_collected_staging_blocks_apply(self):
        write_bibliography(
            self.config.paths.collected,
            (self.publication,),
        )
        with self.assertRaisesRegex(ProjectStateError, "merge the existing batch"):
            plan_project_audit_apply(self.config)

    def test_deferred_or_unresolved_resolution_blocks_apply(self):
        review = self.review(
            self.finding("title", "Old title", "New title"),
        )
        deferred = self.state(
            self.decision("title", "deferred"),
        )
        with patch(
            "bibreview.project_audit_apply.project_audit_review",
            return_value=review,
        ), patch(
            "bibreview.project_audit_apply.load_project_audit_resolutions",
            return_value=deferred,
        ):
            with self.assertRaisesRegex(ProjectStateError, "must be complete"):
                plan_project_audit_apply(self.config)

        unresolved = AuditResolutionState(
            review_fingerprint="test-fingerprint",
            total_actionable=1,
            decisions=(),
        )
        with patch(
            "bibreview.project_audit_apply.project_audit_review",
            return_value=review,
        ), patch(
            "bibreview.project_audit_apply.load_project_audit_resolutions",
            return_value=unresolved,
        ):
            with self.assertRaisesRegex(ProjectStateError, "must be complete"):
                plan_project_audit_apply(self.config)

    def test_stale_canonical_value_blocks_apply(self):
        review = self.review(
            self.finding("title", "Previously audited title", "New title"),
        )
        state = self.state(
            self.decision("title", "accepted", "New title"),
        )
        with self.assertRaisesRegex(ProjectStateError, "stale canonical value"):
            self.patched_plan(review, state)

    def test_missing_tracked_bibtex_blocks_relevant_change(self):
        self.bibtex_path.unlink()
        review = self.review(
            self.finding("title", "Old title", "New title"),
        )
        state = self.state(
            self.decision("title", "accepted", "New title"),
        )
        with self.assertRaisesRegex(ProjectStateError, "tracked BibTeX is required"):
            self.patched_plan(review, state)

    def test_json_only_event_change_does_not_require_bibtex_field(self):
        review = self.review(
            self.finding("event", "", "Example Conference"),
        )
        state = self.state(
            self.decision("event", "accepted", "Example Conference"),
        )

        plan = self.patched_plan(review, state)

        self.assertEqual(len(plan.changes), 1)
        self.assertIsNone(plan.changes[0].bibtex_field)
        self.assertEqual(plan.bibtex_files_affected, 0)
        self.assertNotIn(self.bibtex_path, plan.outputs)
        apply_project_audit_apply(plan)
        staged = read_bibliography(self.config.paths.collected)
        self.assertEqual(staged[0].event, "Example Conference")

    def test_contributor_correction_preserves_exact_names_without_inference(self):
        publication = Publication(
            id=self.publication.id,
            identifiers=self.publication.identifiers,
            type=self.publication.type,
            title=self.publication.title,
            authors=(
                Author(
                    given="Nguyen Thanh",
                    family="Sang",
                    source_fields={"ORCID": "0000-0001"},
                ),
                Author(
                    given="Mohd Azlan",
                    family="Hussain",
                    source_fields={"ORCID": "0000-0002"},
                ),
            ),
            publication_year="2020",
            permalink=self.publication.permalink,
        )
        self.publication = publication
        write_bibliography(
            self.config.paths.bibliography,
            (publication,),
        )
        self.bibtex_path.write_text(
            "@article{example,\n"
            "  title={{Old title}},\n"
            "  author={Nguyen Thanh Sang and Mohd Azlan Hussain},\n"
            "  year={2020}\n"
            "}\n",
            encoding="utf-8",
        )
        current = ("Nguyen Thanh Sang", "Mohd Azlan Hussain")
        resolved = ("Nguyen Thanh Sang", "Hussain Mohd Azlan")
        review = self.review(self.finding("authors", current, resolved))
        state = self.state(
            self.decision("authors", "custom", resolved),
        )

        plan = self.patched_plan(review, state)
        apply_project_audit_apply(plan)

        staged = read_bibliography(self.config.paths.collected)
        first, second = staged[0].authors
        self.assertEqual((first.given, first.family, first.literal), (
            "Nguyen Thanh",
            "Sang",
            None,
        ))
        self.assertEqual(dict(first.source_fields), {"ORCID": "0000-0001"})
        self.assertEqual((second.given, second.family, second.literal), (
            None,
            None,
            "Hussain Mohd Azlan",
        ))
        self.assertEqual(dict(second.source_fields), {"ORCID": "0000-0002"})
        self.assertIn(
            "author={Nguyen Thanh Sang and Hussain Mohd Azlan}",
            self.bibtex_path.read_text(encoding="utf-8"),
        )

    def test_equal_custom_resolution_is_reported_as_explicit_no_op(self):
        review = self.review(
            self.finding("title", "Old title", "New title"),
        )
        state = self.state(
            self.decision("title", "custom", "Old title"),
        )

        plan = self.patched_plan(review, state)

        self.assertFalse(plan.changed)
        self.assertEqual(plan.changes, ())
        self.assertEqual(len(plan.no_ops), 1)
        self.assertEqual(plan.no_ops[0].field, "title")
        self.assertEqual(plan.no_ops[0].decision, "custom")
        self.assertEqual(plan.no_ops[0].value, "Old title")
        self.assertEqual(plan.data()["no_op_resolutions"], 1)
        self.assertEqual(plan.data()["changes_to_stage"], 0)
        self.assertIn("No-op resolutions     : 1", plan.summary())
        verbose = format_project_audit_apply_plan(plan)
        self.assertIn("source  : custom (no-op)", verbose)
        self.assertIn("staged  : unchanged", verbose)
        self.assertIn("BibTeX  : unchanged", verbose)

    def test_equal_accepted_resolution_is_reported_as_explicit_no_op(self):
        review = self.review(
            self.finding("volume", "1", "1"),
        )
        state = self.state(
            self.decision("volume", "accepted", "1"),
        )

        plan = self.patched_plan(review, state)

        self.assertFalse(plan.changed)
        self.assertEqual(len(plan.no_ops), 1)
        self.assertEqual(plan.no_ops[0].decision, "accepted")
        self.assertEqual(plan.no_ops[0].value, "1")

    def test_rejected_only_review_is_a_noop(self):
        review = self.review(
            self.finding("volume", "1", "2"),
        )
        state = self.state(
            self.decision("volume", "rejected"),
        )

        plan = self.patched_plan(review, state)

        self.assertFalse(plan.changed)
        self.assertEqual(plan.changes, ())
        self.assertEqual(plan.no_ops, ())
        self.assertEqual(plan.affected_publication_ids, ())
        self.assertEqual(plan.bibtex_backups, ())


if __name__ == "__main__":
    unittest.main()
