from pathlib import Path
import tempfile
import unittest

from bibreview.config import load_config
from bibreview.contributor_hygiene_resolution import (
    contributor_hygiene_pending_cleanup_path,
    load_contributor_hygiene_state,
    record_contributor_hygiene_decision,
    save_contributor_hygiene_state,
)
from bibreview.identity import new_publication_id
from bibreview.model import Author, Publication
from bibreview.project import apply_project_merge, plan_project_merge
from bibreview.project_contributor_hygiene import (
    apply_project_contributor_hygiene_apply,
    plan_project_contributor_hygiene_apply,
    project_contributor_hygiene_review,
)
from bibreview.storage import read_bibliography, read_json, write_bibliography


CONFIG = """\
schema_version: 1
project:
  name: Example Review
  slug: example-review
site:
  enabled: false
"""


class ProjectContributorHygieneTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config_path = self.root / "bibreview.yml"
        self.config_path.write_text(CONFIG, encoding="utf-8")
        self.config = load_config(self.config_path)
        self.config.paths.bibliography.parent.mkdir(parents=True, exist_ok=True)

        self.institution = (
            "TIFR Centre for Applicable Mathematics, Bangalore"
        )
        self.publication = Publication(
            id=new_publication_id(),
            identifiers={"doi": "10.1/contaminated"},
            title="Contaminated contributors",
            authors=(
                Author(given="Debayan", family="Maity"),
                Author(literal=self.institution),
                Author(given="Takéo", family="Takahashi"),
            ),
            permalink="contaminated-contributors",
        )
        self.other = Publication(
            id=new_publication_id(),
            identifiers={"doi": "10.1/other"},
            title="Other publication",
            authors=(Author(literal="Ada Lovelace"),),
            permalink="other",
        )
        write_bibliography(
            self.config.paths.bibliography,
            (self.publication, self.other),
        )
        self.config.paths.author_mappings.write_text(
            """{
  "debayan-maity": ["Debayan Maity"],
  "tifr-centre": ["TIFR Centre for Applicable Mathematics, Bangalore"],
  "takeo-takahashi": ["Takéo Takahashi"],
  "ada-lovelace": ["Ada Lovelace"]
}
""",
            encoding="utf-8",
        )

    def _resolve(self, decision: str) -> None:
        review = project_contributor_hygiene_review(self.config)
        self.assertEqual(len(review.candidates), 1)
        state = load_contributor_hygiene_state(self.config, review)
        state = record_contributor_hygiene_decision(
            state,
            review.candidates[0],
            decision=decision,
        )
        save_contributor_hygiene_state(self.config, state)

    def _resolve_publication_candidate(
        self,
        *,
        publication_id: str,
        decision: str,
    ) -> None:
        review = project_contributor_hygiene_review(self.config)
        state = load_contributor_hygiene_state(self.config, review)
        target = next(
            item
            for item in review.candidates
            if item.publication_id == publication_id
        )
        state = record_contributor_hygiene_decision(
            state,
            target,
            decision=decision,
        )
        save_contributor_hygiene_state(self.config, state)

    def test_review_is_read_only_and_exposes_source_position(self):
        before = self.config.paths.bibliography.read_bytes()

        review = project_contributor_hygiene_review(self.config)

        self.assertEqual(self.config.paths.bibliography.read_bytes(), before)
        self.assertEqual(review.scanned_publications, 2)
        self.assertEqual(len(review.candidates), 1)
        candidate = review.candidates[0]
        self.assertEqual(candidate.role, "author")
        self.assertEqual(candidate.index, 1)
        self.assertEqual(candidate.name, self.institution)

    def test_keep_decision_produces_no_staging(self):
        self._resolve("keep")
        plan = plan_project_contributor_hygiene_apply(self.config)
        self.assertFalse(plan.changed)
        self.assertEqual(plan.changes, ())
        self.assertFalse(self.config.paths.collected.exists())

    def test_remove_stages_replacement_without_mutating_canonical(self):
        canonical_before = self.config.paths.bibliography.read_bytes()
        self._resolve("remove")

        plan = plan_project_contributor_hygiene_apply(self.config)
        self.assertTrue(plan.changed)
        apply_project_contributor_hygiene_apply(plan)

        self.assertEqual(
            self.config.paths.bibliography.read_bytes(),
            canonical_before,
        )
        staged = read_bibliography(self.config.paths.collected)
        self.assertEqual(len(staged), 1)
        self.assertEqual(staged[0].id, self.publication.id)
        self.assertEqual(staged[0].permalink, self.publication.permalink)
        self.assertEqual(
            [
                (author.given, author.family, author.literal)
                for author in staged[0].authors
            ],
            [
                ("Debayan", "Maity", None),
                ("Takéo", "Takahashi", None),
            ],
        )
        self.assertTrue(
            contributor_hygiene_pending_cleanup_path(self.config).exists()
        )

    def test_merge_prunes_only_removed_unused_mapping_variant(self):
        self._resolve("remove")
        apply_project_contributor_hygiene_apply(
            plan_project_contributor_hygiene_apply(self.config)
        )

        plan = plan_project_merge(self.config)
        apply_project_merge(plan)

        canonical = {
            item.doi: item
            for item in read_bibliography(self.config.paths.bibliography)
        }
        self.assertEqual(
            [author.literal for author in canonical["10.1/contaminated"].authors],
            [None, None],
        )
        mapping = read_json(self.config.paths.author_mappings, dict)
        self.assertNotIn("tifr-centre", mapping)
        self.assertIn("debayan-maity", mapping)
        self.assertIn("takeo-takahashi", mapping)
        self.assertIn("ada-lovelace", mapping)
        self.assertFalse(
            contributor_hygiene_pending_cleanup_path(self.config).exists()
        )

    def test_merge_keeps_removed_name_mapping_when_still_used_elsewhere(self):
        shared = Publication(
            id=new_publication_id(),
            identifiers={"doi": "10.1/shared"},
            title="Collective author",
            authors=(Author(literal=self.institution),),
            permalink="shared",
        )
        write_bibliography(
            self.config.paths.bibliography,
            (self.publication, self.other, shared),
        )
        self._resolve_publication_candidate(
            publication_id=self.publication.id,
            decision="remove",
        )
        self._resolve_publication_candidate(
            publication_id=shared.id,
            decision="keep",
        )
        apply_project_contributor_hygiene_apply(
            plan_project_contributor_hygiene_apply(self.config)
        )

        apply_project_merge(plan_project_merge(self.config))

        mapping = read_json(self.config.paths.author_mappings, dict)
        self.assertIn("tifr-centre", mapping)


if __name__ == "__main__":
    unittest.main()
