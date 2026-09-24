from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from bibreview.config import load_config
from bibreview.hygiene_resolution import (
    hygiene_resolution_candidates,
    hygiene_resolution_path,
    load_project_hygiene_resolutions,
    record_hygiene_resolution,
    save_project_hygiene_resolutions,
)
from bibreview.identity import new_publication_id
from bibreview.model import Author, Publication
from bibreview.project import ProjectStateError
from bibreview.project_hygiene import (
    format_project_hygiene_migration_review,
    project_hygiene_migration_review,
)
from bibreview.project_hygiene_apply import (
    apply_project_hygiene_apply,
    plan_project_hygiene_apply,
)
from bibreview.storage import read_bibliography, write_bibliography


CONFIG = """\
schema_version: 1
project:
  name: Example Review
  slug: example-review
site:
  enabled: false
"""


class ProjectHygieneMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config_path = self.root / "bibreview.yml"
        self.config_path.write_text(CONFIG, encoding="utf-8")
        self.config = load_config(self.config_path)
        self.config.paths.bibliography.parent.mkdir(parents=True, exist_ok=True)

        self.safe = Publication(
            id=new_publication_id(),
            identifiers={"doi": "10.1/safe"},
            title="Safe structured abstract",
            authors=(Author(literal="Ada Lovelace"),),
            abstract=(
                '<jats:p>A space <inline-formula>'
                '<mml:annotation encoding="application/x-tex">'
                r'V \oplus V^{\ast}'
                '</mml:annotation></inline-formula> is considered.</jats:p>'
            ),
            permalink="safe-structured",
        )
        self.unsafe = Publication(
            id=new_publication_id(),
            identifiers={"doi": "10.1/unsafe"},
            title="Unsafe structured abstract",
            authors=(Author(literal="Alan Turing"),),
            abstract=(
                'A controller <jats:inline-graphic '
                'xlink:href="graphic/math-0002.png"/> is proposed.'
            ),
            permalink="unsafe-structured",
        )
        self.clean = Publication(
            id=new_publication_id(),
            identifiers={"doi": "10.1/clean"},
            title="Clean abstract",
            authors=(Author(literal="Grace Hopper"),),
            abstract=r"Already clean \(V\).",
            permalink="clean",
        )
        write_bibliography(
            self.config.paths.bibliography,
            (self.safe, self.unsafe, self.clean),
        )

    def snapshot(self) -> dict[str, bytes]:
        return {
            str(path.relative_to(self.root)): path.read_bytes()
            for path in self.root.rglob("*")
            if path.is_file()
        }

    def review(self):
        return project_hygiene_migration_review(self.config)

    def test_review_is_read_only_and_separates_safe_from_review_required(self):
        before = self.snapshot()

        review = self.review()

        self.assertEqual(before, self.snapshot())
        self.assertEqual(review.scanned_publications, 3)
        self.assertEqual(review.suspicious_abstracts, 2)
        self.assertEqual(review.deterministic_proposals, 1)
        self.assertEqual(review.review_required, 1)

        safe, unsafe = review.proposals
        self.assertFalse(safe.review_required)
        self.assertEqual(
            safe.proposed_value,
            r"A space \(V \oplus V^{\ast}\) is considered.",
        )
        self.assertTrue(unsafe.review_required)
        self.assertEqual(unsafe.proposed_value, "")
        self.assertEqual(unsafe.reason, "embedded-graphic")

        compact = format_project_hygiene_migration_review(review)
        self.assertIn("Deterministic proposals  : 1", compact)
        self.assertNotIn("math-0002.png", compact)

        verbose = format_project_hygiene_migration_review(review, verbose=True)
        self.assertIn("10.1/safe", verbose)
        self.assertIn("10.1/unsafe", verbose)
        self.assertIn("math-0002.png", verbose)
        self.assertIn("REVIEW REQUIRED", verbose)

    def test_review_required_proposal_cannot_be_accepted_directly(self):
        review = self.review()
        state = load_project_hygiene_resolutions(self.config, review)
        candidates = hygiene_resolution_candidates(review)
        unsafe = next(
            item for item in candidates if item.proposal.review_required
        )

        with self.assertRaisesRegex(
            ProjectStateError,
            "cannot be accepted directly",
        ):
            record_hygiene_resolution(
                state,
                unsafe,
                decision="accepted",
            )

    def test_saved_resolutions_are_stale_after_canonical_abstract_changes(self):
        review = self.review()
        state = load_project_hygiene_resolutions(self.config, review)
        safe = next(
            item
            for item in hygiene_resolution_candidates(review)
            if not item.proposal.review_required
        )
        state = record_hygiene_resolution(
            state,
            safe,
            decision="accepted",
        )
        save_project_hygiene_resolutions(self.config, state)

        changed = tuple(
            publication
            if publication.id != self.safe.id
            else Publication(
                **{
                    **publication.__dict__,
                    "abstract": "<jats:p>Different canonical text.</jats:p>",
                }
            )
            for publication in read_bibliography(
                self.config.paths.bibliography
            )
        )
        write_bibliography(self.config.paths.bibliography, changed)
        current = self.review()

        with self.assertRaisesRegex(
            ProjectStateError,
            "do not match the current canonical migration review",
        ):
            load_project_hygiene_resolutions(self.config, current)

    def test_accepted_and_custom_decisions_stage_without_mutating_canonical(self):
        canonical_before = self.config.paths.bibliography.read_bytes()
        review = self.review()
        state = load_project_hygiene_resolutions(self.config, review)

        for candidate in hygiene_resolution_candidates(review):
            if candidate.proposal.review_required:
                state = record_hygiene_resolution(
                    state,
                    candidate,
                    decision="custom",
                    resolved_value=r"A reviewed custom \(u_0\) abstract.",
                )
            else:
                state = record_hygiene_resolution(
                    state,
                    candidate,
                    decision="accepted",
                )
        save_project_hygiene_resolutions(self.config, state)

        plan = plan_project_hygiene_apply(self.config)

        self.assertEqual(len(plan.changes), 2)
        self.assertEqual(
            self.config.paths.bibliography.read_bytes(),
            canonical_before,
        )
        self.assertFalse(self.config.paths.collected.exists())

        apply_project_hygiene_apply(plan)

        self.assertEqual(
            self.config.paths.bibliography.read_bytes(),
            canonical_before,
        )
        staged = {
            publication.doi: publication
            for publication in read_bibliography(self.config.paths.collected)
        }
        self.assertEqual(
            staged["10.1/safe"].abstract,
            r"A space \(V \oplus V^{\ast}\) is considered.",
        )
        self.assertEqual(
            staged["10.1/unsafe"].abstract,
            r"A reviewed custom \(u_0\) abstract.",
        )

    def test_apply_requires_complete_decisions(self):
        review = self.review()
        state = load_project_hygiene_resolutions(self.config, review)
        first = hygiene_resolution_candidates(review)[0]
        state = record_hygiene_resolution(
            state,
            first,
            decision="accepted",
        )
        save_project_hygiene_resolutions(self.config, state)

        with self.assertRaisesRegex(
            ProjectStateError,
            "decisions must be complete",
        ):
            plan_project_hygiene_apply(self.config)

    def test_apply_refuses_nonempty_staging(self):
        write_bibliography(
            self.config.paths.collected,
            (self.clean,),
        )

        with self.assertRaisesRegex(
            ProjectStateError,
            "merge the existing batch",
        ):
            plan_project_hygiene_apply(self.config)

    def test_all_rejected_decisions_produce_no_staging(self):
        review = self.review()
        state = load_project_hygiene_resolutions(self.config, review)
        for candidate in hygiene_resolution_candidates(review):
            state = record_hygiene_resolution(
                state,
                candidate,
                decision="rejected",
            )
        save_project_hygiene_resolutions(self.config, state)

        plan = plan_project_hygiene_apply(self.config)

        self.assertFalse(plan.changed)
        self.assertEqual(plan.changes, ())
        self.assertFalse(self.config.paths.collected.exists())
        self.assertTrue(hygiene_resolution_path(self.config).exists())


if __name__ == "__main__":
    unittest.main()
