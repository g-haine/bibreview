from __future__ import annotations

from datetime import date
from pathlib import Path
import tempfile
import unittest

from bibreview.backfill_resolution import (
    BackfillResolutionState,
    record_backfill_resolution,
)
from bibreview.config import load_config
from bibreview.identity import new_publication_id
from bibreview.model import Author, Publication
from bibreview.pipeline.backfill import BackfillCandidate
from bibreview.project import ProjectStateError
from bibreview.project_refresh import (
    RefreshReview,
    refresh_review_path,
)
from bibreview.project_refresh_apply import (
    apply_project_refresh_apply,
    plan_project_refresh_apply,
)
from bibreview.refresh_resolution import (
    refresh_resolution_candidates,
    save_project_refresh_resolutions,
)
from bibreview.storage import read_bibliography, write_bibliography, write_json


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


class ProjectRefreshApplyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config_path = self.root / "bibreview.yml"
        self.config_path.write_text(CONFIG, encoding="utf-8")
        self.config = load_config(self.config_path)
        self.publication = Publication(
            id=new_publication_id(),
            identifiers={"doi": "10.1000/refresh"},
            type="journal-article",
            title="Reviewed title",
            authors=(Author(literal="Reviewed Author"),),
            container_title="Reviewed Journal",
            publication_year="2025",
            created_date=date(2025, 1, 2),
            permalink="reviewed-title",
        )
        write_bibliography(
            self.config.paths.bibliography,
            (self.publication,),
        )
        self.config.paths.bibtex.mkdir(parents=True, exist_ok=True)
        self.bibtex = self.config.paths.bibtex / "reviewed-title.bib"
        self.bibtex.write_text(
            "@article{example,\n"
            "  title={{Reviewed title}},\n"
            "  author={Reviewed Author},\n"
            "  journal={Reviewed Journal},\n"
            "  year={2025}\n"
            "}\n",
            encoding="utf-8",
        )

    def persist_review(self, *proposals):
        review = RefreshReview(
            scanned_count=1,
            eligible_count=1,
            stale_dois=(self.publication.doi,),
            proposals=tuple(proposals),
            collateral=(),
            unavailable=(),
            reasons={self.publication.doi: "changed BibTeX"},
        )
        write_json(refresh_review_path(self.config), review.data())
        return review

    def resolved_state(self, review, decisions):
        state = BackfillResolutionState(
            review_fingerprint="0" * 64,
            total_proposals=len(review.proposals),
        )
        # Replace fingerprint with actual loader-compatible value through helper.
        from bibreview.project_refresh import refresh_review_fingerprint
        state = BackfillResolutionState(
            review_fingerprint=refresh_review_fingerprint(review),
            total_proposals=len(review.proposals),
        )
        candidates = refresh_resolution_candidates(review)
        for candidate, decision in zip(candidates, decisions, strict=True):
            state = record_backfill_resolution(
                state,
                candidate,
                decision=decision[0],
                resolved_value=decision[1],
            )
        save_project_refresh_resolutions(self.config, state)
        return state

    def test_apply_stages_only_accepted_missing_fields_and_edits_bibtex_targetedly(self):
        volume = BackfillCandidate(
            publication_id=self.publication.id,
            doi=self.publication.doi,
            title=self.publication.title,
            field="volume",
            proposed_value="12",
        )
        pages = BackfillCandidate(
            publication_id=self.publication.id,
            doi=self.publication.doi,
            title=self.publication.title,
            field="pages",
            proposed_value="10--20",
        )
        review = self.persist_review(volume, pages)
        self.resolved_state(
            review,
            (("accepted", None), ("rejected", None)),
        )

        plan = plan_project_refresh_apply(self.config)
        self.assertEqual(len(plan.changes), 1)
        self.assertEqual(plan.changes[0].field, "volume")
        self.assertEqual(plan.bibtex_files_affected, 1)

        old_bibtex = self.bibtex.read_bytes()
        apply_project_refresh_apply(plan)

        canonical = read_bibliography(self.config.paths.bibliography)
        self.assertEqual(canonical[0].volume, "")
        self.assertEqual(canonical[0].title, "Reviewed title")

        staged = read_bibliography(self.config.paths.collected)
        self.assertEqual(staged[0].volume, "12")
        self.assertEqual(staged[0].pages, "")
        self.assertEqual(staged[0].title, "Reviewed title")
        self.assertEqual(staged[0].authors[0].literal, "Reviewed Author")

        updated_bibtex = self.bibtex.read_text(encoding="utf-8")
        self.assertIn("volume={12}", updated_bibtex)
        self.assertIn("title={{Reviewed title}}", updated_bibtex)
        self.assertEqual(plan.bibtex_backups[0].read_bytes(), old_bibtex)

    def test_custom_value_is_applied_instead_of_provider_proposal(self):
        volume = BackfillCandidate(
            publication_id=self.publication.id,
            doi=self.publication.doi,
            title=self.publication.title,
            field="volume",
            proposed_value="12",
        )
        review = self.persist_review(volume)
        self.resolved_state(review, (("custom", "13"),))

        plan = plan_project_refresh_apply(self.config)
        apply_project_refresh_apply(plan)

        staged = read_bibliography(self.config.paths.collected)
        self.assertEqual(staged[0].volume, "13")

    def test_unresolved_or_deferred_decisions_block_apply(self):
        volume = BackfillCandidate(
            publication_id=self.publication.id,
            doi=self.publication.doi,
            title=self.publication.title,
            field="volume",
            proposed_value="12",
        )
        review = self.persist_review(volume)

        with self.assertRaisesRegex(ProjectStateError, "must be complete"):
            plan_project_refresh_apply(self.config)

        self.resolved_state(review, (("deferred", None),))
        with self.assertRaisesRegex(ProjectStateError, "must be complete"):
            plan_project_refresh_apply(self.config)

    def test_abstract_placeholder_can_be_replaced_by_reviewed_refresh(self):
        placeholder = Publication(
            id=self.publication.id,
            identifiers=self.publication.identifiers,
            type=self.publication.type,
            title=self.publication.title,
            authors=self.publication.authors,
            abstract="Not Available",
            container_title=self.publication.container_title,
            publication_year=self.publication.publication_year,
            created_date=self.publication.created_date,
            permalink=self.publication.permalink,
        )
        write_bibliography(self.config.paths.bibliography, (placeholder,))
        abstract = BackfillCandidate(
            publication_id=self.publication.id,
            doi=self.publication.doi,
            title=self.publication.title,
            field="abstract",
            proposed_value="Recovered abstract",
        )
        review = self.persist_review(abstract)
        self.resolved_state(review, (("accepted", None),))

        plan = plan_project_refresh_apply(self.config)
        apply_project_refresh_apply(plan)

        staged = read_bibliography(self.config.paths.collected)
        self.assertEqual(staged[0].abstract, "Recovered abstract")

    def test_nonempty_canonical_field_blocks_stale_proposal(self):
        volume = BackfillCandidate(
            publication_id=self.publication.id,
            doi=self.publication.doi,
            title=self.publication.title,
            field="volume",
            proposed_value="12",
        )
        review = self.persist_review(volume)
        self.resolved_state(review, (("accepted", None),))

        changed = Publication(
            id=self.publication.id,
            identifiers=self.publication.identifiers,
            type=self.publication.type,
            title=self.publication.title,
            authors=self.publication.authors,
            container_title=self.publication.container_title,
            publication_year=self.publication.publication_year,
            volume="11",
            created_date=self.publication.created_date,
            permalink=self.publication.permalink,
        )
        write_bibliography(self.config.paths.bibliography, (changed,))

        with self.assertRaisesRegex(ProjectStateError, "no longer missing"):
            plan_project_refresh_apply(self.config)

    def test_nonempty_staging_blocks_apply(self):
        volume = BackfillCandidate(
            publication_id=self.publication.id,
            doi=self.publication.doi,
            title=self.publication.title,
            field="volume",
            proposed_value="12",
        )
        review = self.persist_review(volume)
        self.resolved_state(review, (("accepted", None),))
        write_bibliography(
            self.config.paths.collected,
            (self.publication,),
        )

        with self.assertRaisesRegex(ProjectStateError, "merge the existing batch"):
            plan_project_refresh_apply(self.config)


if __name__ == "__main__":
    unittest.main()
