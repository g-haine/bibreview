from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from bibreview.config import load_config
from bibreview.hygiene_resolution import (
    hygiene_resolution_candidates,
    load_project_hygiene_resolutions,
    record_hygiene_resolution,
    save_project_hygiene_resolutions,
)
from bibreview.identity import new_publication_id
from bibreview.model import Author, Publication
from bibreview.project import ProjectStateError
from bibreview.project_hygiene import (
    apply_project_hygiene_proposals,
    build_hygiene_review,
    load_project_hygiene_review,
    plan_project_hygiene_proposals,
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


def publication(
    abstract: str,
    *,
    doi: str,
    title: str,
) -> Publication:
    return Publication(
        id=new_publication_id(),
        identifiers={"doi": doi},
        title=title,
        authors=(Author(literal="Ada Lovelace"),),
        abstract=abstract,
        permalink=doi.replace("/", "-"),
    )


class HygieneMigrationTests(unittest.TestCase):
    def test_dirac_mathml_fixture_proposes_compact_tex_without_markup(self):
        current = (
            '<p>A Dirac structure on a vector space '
            '<inline-formula content-type="math/mathml">'
            '<mml:math xmlns:mml="http://www.w3.org/1998/Math/MathML">'
            '<mml:semantics><mml:mi>V</mml:mi>'
            '<!-- presentation glyph retained only inside formula -->'
            '<mml:annotation encoding="application/x-tex">V</mml:annotation>'
            '</mml:semantics></mml:math></inline-formula>'
            ' is a subspace of '
            '<inline-formula content-type="math/mathml">'
            '<mml:math xmlns:mml="http://www.w3.org/1998/Math/MathML">'
            '<mml:semantics><mml:mrow><mml:mi>V</mml:mi>'
            '<mml:mo>⊕</mml:mo><mml:msup><mml:mi>V</mml:mi>'
            '<mml:mo>∗</mml:mo></mml:msup></mml:mrow>'
            '<mml:annotation encoding="application/x-tex">'
            r'V \oplus V^{\ast}'
            '</mml:annotation></mml:semantics></mml:math>'
            '</inline-formula>.</p>'
        )
        review = build_hygiene_review(
            (
                publication(
                    current,
                    doi="10.1090/s0002-9947-1990-0998124-1",
                    title="Dirac manifolds",
                ),
            )
        )

        self.assertEqual(len(review.proposals), 1)
        proposal = review.proposals[0]
        self.assertFalse(proposal.review_required)
        self.assertIn(r"\(V\)", proposal.proposed_abstract)
        self.assertIn(
            r"\(V \oplus V^{\ast}\)",
            proposal.proposed_abstract,
        )
        self.assertNotIn("<", proposal.proposed_abstract)
        self.assertNotIn("<!--", proposal.proposed_abstract)

    def test_unsafe_historical_markup_is_review_required(self):
        values = (
            (
                'A controller <jats:inline-graphic '
                'xlink:href="graphic/math-0002.png"/> is proposed.',
                "embedded-graphic",
            ),
            (
                "(u<inf>0</inf>)<sup>T</sup>",
                "script-markup",
            ),
            (
                "<jats:p>&lt;abstract&gt;&lt;p&gt;Text&lt;/p&gt;"
                "&lt;/abstract&gt;</jats:p>",
                "escaped-markup",
            ),
        )
        publications = tuple(
            publication(
                abstract,
                doi=f"10.1/unsafe-{index}",
                title=f"Unsafe {index}",
            )
            for index, (abstract, _reason) in enumerate(values, 1)
        )

        review = build_hygiene_review(publications)

        self.assertEqual(review.review_required, 3)
        for proposal, (_abstract, reason) in zip(review.proposals, values):
            self.assertTrue(proposal.review_required)
            self.assertEqual(proposal.proposed_abstract, "")
            self.assertEqual(proposal.reason, reason)

    def test_clean_abstracts_do_not_create_migration_proposals(self):
        review = build_hygiene_review(
            (
                publication(
                    r"Clean abstract with \(V\).",
                    doi="10.1/clean",
                    title="Clean",
                ),
            )
        )
        self.assertEqual(review.proposals, ())


class ProjectHygieneMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config_path = self.root / "bibreview.yml"
        self.config_path.write_text(CONFIG, encoding="utf-8")
        self.config = load_config(self.config_path)
        self.config.paths.bibliography.parent.mkdir(parents=True, exist_ok=True)

        self.safe = publication(
            "<p>Historical safe abstract.</p>",
            doi="10.1/safe",
            title="Safe",
        )
        self.unsafe = publication(
            'Formula <jats:inline-graphic xlink:href="graphic/math.png"/>.',
            doi="10.1/unsafe",
            title="Unsafe",
        )
        write_bibliography(
            self.config.paths.bibliography,
            (self.safe, self.unsafe),
        )

    def persist_review(self):
        plan = plan_project_hygiene_proposals(self.config)
        apply_project_hygiene_proposals(plan)
        return load_project_hygiene_review(self.config)

    def test_review_round_trip_and_resolution_policy(self):
        review = self.persist_review()
        self.assertEqual(review.deterministic_proposals, 1)
        self.assertEqual(review.review_required, 1)

        state = load_project_hygiene_resolutions(self.config, review)
        candidates = hygiene_resolution_candidates(review)
        safe = next(item for item in candidates if not item.proposal.review_required)
        unsafe = next(item for item in candidates if item.proposal.review_required)

        state = record_hygiene_resolution(
            state,
            safe,
            decision="accepted",
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

        state = record_hygiene_resolution(
            state,
            unsafe,
            decision="custom",
            resolved_value=r"Formula \(H_\infty\).",
        )
        save_project_hygiene_resolutions(self.config, state)

        loaded = load_project_hygiene_resolutions(self.config, review)
        self.assertEqual(
            tuple(item.decision for item in loaded.decisions),
            ("accepted", "custom"),
        )

    def test_apply_stages_only_reviewed_clean_values_and_keeps_canon_unchanged(self):
        review = self.persist_review()
        state = load_project_hygiene_resolutions(self.config, review)
        for candidate in hygiene_resolution_candidates(review):
            if candidate.proposal.review_required:
                state = record_hygiene_resolution(
                    state,
                    candidate,
                    decision="custom",
                    resolved_value=r"Formula \(H_\infty\).",
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
        apply_project_hygiene_apply(plan)

        canonical = read_bibliography(self.config.paths.bibliography)
        staged = read_bibliography(self.config.paths.collected)
        self.assertEqual(canonical, (self.safe, self.unsafe))
        self.assertEqual(len(staged), 2)
        staged_by_doi = {item.doi: item for item in staged}
        self.assertEqual(
            staged_by_doi["10.1/safe"].abstract,
            "Historical safe abstract.",
        )
        self.assertEqual(
            staged_by_doi["10.1/unsafe"].abstract,
            r"Formula \(H_\infty\).",
        )

    def test_apply_rejects_stale_canonical_abstract(self):
        review = self.persist_review()
        state = load_project_hygiene_resolutions(self.config, review)
        for candidate in hygiene_resolution_candidates(review):
            state = record_hygiene_resolution(
                state,
                candidate,
                decision="rejected",
            )
        safe_candidate = next(
            item
            for item in hygiene_resolution_candidates(review)
            if not item.proposal.review_required
        )
        state = record_hygiene_resolution(
            state,
            safe_candidate,
            decision="accepted",
        )
        save_project_hygiene_resolutions(self.config, state)

        write_bibliography(
            self.config.paths.bibliography,
            (
                replace(self.safe, abstract="New human correction."),
                self.unsafe,
            ),
        )

        with self.assertRaisesRegex(
            ProjectStateError,
            "stale hygiene proposal",
        ):
            plan_project_hygiene_apply(self.config)

    def test_apply_rejects_custom_value_that_remains_contaminated(self):
        review = self.persist_review()
        state = load_project_hygiene_resolutions(self.config, review)
        for candidate in hygiene_resolution_candidates(review):
            if candidate.proposal.review_required:
                state = record_hygiene_resolution(
                    state,
                    candidate,
                    decision="custom",
                    resolved_value="<p>Still structured.</p>",
                )
            else:
                state = record_hygiene_resolution(
                    state,
                    candidate,
                    decision="rejected",
                )
        save_project_hygiene_resolutions(self.config, state)

        with self.assertRaisesRegex(
            ProjectStateError,
            "still contains suspicious hygiene markup",
        ):
            plan_project_hygiene_apply(self.config)

    def test_apply_requires_empty_staging(self):
        review = self.persist_review()
        state = load_project_hygiene_resolutions(self.config, review)
        for candidate in hygiene_resolution_candidates(review):
            state = record_hygiene_resolution(
                state,
                candidate,
                decision="rejected",
            )
        save_project_hygiene_resolutions(self.config, state)
        write_bibliography(self.config.paths.collected, (self.safe,))

        with self.assertRaisesRegex(
            ProjectStateError,
            "merge the existing batch",
        ):
            plan_project_hygiene_apply(self.config)


if __name__ == "__main__":
    unittest.main()
