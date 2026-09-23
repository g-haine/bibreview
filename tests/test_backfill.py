from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from bibreview.backfill_resolution import (
    backfill_resolution_candidates,
    load_project_backfill_resolutions,
    record_backfill_resolution,
    save_project_backfill_resolutions,
)
from bibreview.config import load_config
from bibreview.identity import new_publication_id
from bibreview.model import Author, Publication
from bibreview.pipeline.backfill import BackfillCandidate, backfill
from bibreview.project import ProjectStateError
from bibreview.project_backfill import (
    BackfillReview,
    apply_project_backfill_plan,
    backfill_review_path,
    plan_project_backfill,
)
from bibreview.project_backfill_apply import (
    apply_project_backfill_apply,
    plan_project_backfill_apply,
)
from bibreview.providers.base import Enrichment
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


class FakeProvider:
    def __init__(self, records):
        self.records = dict(records)
        self.calls = []

    def work(self, doi):
        self.calls.append(doi)
        return self.records.get(doi)


def message(title="Example title"):
    return {
        "type": "journal-article",
        "title": [title],
        "author": [{"given": "Ada", "family": "Lovelace"}],
        "container-title": ["Journal"],
        "created": {"date-parts": [[2026, 9, 23]]},
        "published-print": {"date-parts": [[2026]]},
        "reference": [],
    }


class BackfillPipelineTests(unittest.TestCase):
    def publication(self, *, abstract=""):
        return Publication(
            id=new_publication_id(),
            identifiers={"doi": "10.1000/backfill"},
            type="journal-article",
            title="Reviewed title",
            authors=(Author(literal="Reviewed Author"),),
            abstract=abstract,
            container_title="Reviewed Journal",
            publication_year="2026",
            permalink="reviewed-title",
        )

    def test_proposes_only_requested_empty_fields(self):
        publication = self.publication()
        provider = FakeProvider({publication.doi: message("Provider title")})

        result = backfill(
            [publication],
            provider=provider,
            fields=("title", "abstract"),
            enrichment_lookup=lambda doi, work: Enrichment(
                abstract="Useful reviewed candidate"
            ),
        )

        self.assertEqual(result.eligible_count, 1)
        self.assertEqual(len(result.candidates), 1)
        proposal = result.candidates[0]
        self.assertEqual(proposal.field, "abstract")
        self.assertEqual(proposal.proposed_value, "Useful reviewed candidate")
        self.assertEqual(provider.calls, [publication.doi])

    def test_abstract_backfill_does_not_require_provider_authors_editors_or_dates(self):
        publication = self.publication()
        provider = FakeProvider({
            publication.doi: {
                "type": "journal-article",
                "title": ["Partial provider record"],
            }
        })

        result = backfill(
            [publication],
            provider=provider,
            fields=("abstract",),
            enrichment_lookup=lambda doi, work: Enrichment(
                abstract="  ABSTRACT: Useful abstract from fallback.  "
            ),
        )

        self.assertEqual(result.eligible_count, 1)
        self.assertEqual(result.unavailable, ())
        self.assertEqual(result.no_value, ())
        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(result.candidates[0].field, "abstract")
        self.assertEqual(
            result.candidates[0].proposed_value,
            "Useful abstract from fallback.",
        )

    def test_nonempty_field_is_never_proposed_for_replacement(self):
        publication = self.publication(abstract="Canonical abstract")
        provider = FakeProvider({publication.doi: message()})

        result = backfill(
            [publication],
            provider=provider,
            fields=("abstract",),
            enrichment_lookup=lambda doi, work: Enrichment(
                abstract="Provider replacement"
            ),
        )

        self.assertEqual(result.eligible_count, 0)
        self.assertEqual(result.candidates, ())
        self.assertEqual(provider.calls, [])


class ProjectBackfillTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config_path = self.root / "bibreview.yml"
        self.config_path.write_text(CONFIG, encoding="utf-8")
        self.config = load_config(self.config_path)
        self.publication = Publication(
            id=new_publication_id(),
            identifiers={"doi": "10.1000/backfill"},
            type="journal-article",
            title="Reviewed title",
            authors=(Author(literal="Reviewed Author"),),
            container_title="Journal",
            publication_year="2026",
            permalink="reviewed-title",
        )
        write_bibliography(
            self.config.paths.bibliography,
            (self.publication,),
        )

    def review(self, *candidates):
        return BackfillReview(
            fields=("abstract", "event"),
            types=(),
            scanned_count=1,
            eligible_count=1,
            candidates=tuple(candidates),
            unavailable=(),
            no_value=(),
        )

    def persist_review(self, review):
        write_json(backfill_review_path(self.config), review.data())

    def test_proposal_generation_never_writes_collected_staging(self):
        provider = FakeProvider({self.publication.doi: message()})
        plan = plan_project_backfill(
            self.config,
            provider=provider,
            fields=("abstract",),
            enrichment_lookup=lambda doi, work: Enrichment(
                abstract="Candidate abstract"
            ),
        )

        self.assertEqual(set(plan.outputs), {backfill_review_path(self.config)})
        self.assertFalse(self.config.paths.collected.exists())

        apply_project_backfill_plan(plan)

        self.assertTrue(backfill_review_path(self.config).exists())
        self.assertFalse(self.config.paths.collected.exists())

    def test_resolution_is_resumable_and_fingerprinted(self):
        review = self.review(
            BackfillCandidate(
                publication_id=self.publication.id,
                doi=self.publication.doi,
                title=self.publication.title,
                field="abstract",
                proposed_value="Candidate abstract",
            )
        )
        self.persist_review(review)
        state = load_project_backfill_resolutions(self.config, review)
        candidate = backfill_resolution_candidates(review)[0]
        state = record_backfill_resolution(
            state,
            candidate,
            decision="accepted",
        )
        save_project_backfill_resolutions(self.config, state)

        resumed = load_project_backfill_resolutions(self.config, review)
        self.assertEqual(resumed.decisions[0].decision, "accepted")
        self.assertEqual(
            resumed.decisions[0].resolved_value,
            "Candidate abstract",
        )

        changed = self.review(
            BackfillCandidate(
                publication_id=self.publication.id,
                doi=self.publication.doi,
                title=self.publication.title,
                field="abstract",
                proposed_value="Different candidate",
            )
        )
        with self.assertRaisesRegex(ProjectStateError, "do not match"):
            load_project_backfill_resolutions(self.config, changed)

    def test_apply_stages_only_human_accepted_or_custom_values(self):
        abstract = BackfillCandidate(
            publication_id=self.publication.id,
            doi=self.publication.doi,
            title=self.publication.title,
            field="abstract",
            proposed_value="Candidate abstract",
        )
        event = BackfillCandidate(
            publication_id=self.publication.id,
            doi=self.publication.doi,
            title=self.publication.title,
            field="event",
            proposed_value="Candidate event",
        )
        review = self.review(abstract, event)
        self.persist_review(review)
        state = load_project_backfill_resolutions(self.config, review)
        candidates = backfill_resolution_candidates(review)
        state = record_backfill_resolution(
            state, candidates[0], decision="accepted"
        )
        state = record_backfill_resolution(
            state, candidates[1], decision="rejected"
        )
        save_project_backfill_resolutions(self.config, state)

        plan = plan_project_backfill_apply(self.config)
        self.assertEqual(len(plan.changes), 1)
        self.assertEqual(plan.changes[0].field, "abstract")
        self.assertFalse(self.config.paths.collected.exists())

        apply_project_backfill_apply(plan)

        staged = read_bibliography(self.config.paths.collected)
        canonical = read_bibliography(self.config.paths.bibliography)
        self.assertEqual(staged[0].abstract, "Candidate abstract")
        self.assertEqual(staged[0].event, "")
        self.assertEqual(canonical[0].abstract, "")
        self.assertEqual(canonical[0].event, "")

    def test_apply_requires_complete_human_decisions(self):
        proposal = BackfillCandidate(
            publication_id=self.publication.id,
            doi=self.publication.doi,
            title=self.publication.title,
            field="abstract",
            proposed_value="Candidate abstract",
        )
        review = self.review(proposal)
        self.persist_review(review)

        with self.assertRaisesRegex(ProjectStateError, "must be complete"):
            plan_project_backfill_apply(self.config)

    def test_apply_rejects_stale_canonical_field(self):
        proposal = BackfillCandidate(
            publication_id=self.publication.id,
            doi=self.publication.doi,
            title=self.publication.title,
            field="abstract",
            proposed_value="Candidate abstract",
        )
        review = self.review(proposal)
        self.persist_review(review)
        state = load_project_backfill_resolutions(self.config, review)
        state = record_backfill_resolution(
            state,
            backfill_resolution_candidates(review)[0],
            decision="accepted",
        )
        save_project_backfill_resolutions(self.config, state)

        changed = Publication(
            **{
                **self.publication.__dict__,
                "abstract": "Manually filled in meantime",
            }
        )
        write_bibliography(self.config.paths.bibliography, (changed,))

        with self.assertRaisesRegex(ProjectStateError, "stale proposal"):
            plan_project_backfill_apply(self.config)


if __name__ == "__main__":
    unittest.main()
