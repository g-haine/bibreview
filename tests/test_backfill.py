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
    load_project_backfill_review,
    plan_project_backfill,
)
from bibreview.project_backfill_apply import (
    apply_project_backfill_apply,
    plan_project_backfill_apply,
)
from bibreview.providers.base import AbstractEvidence, Enrichment
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


class FakeBatchProvider(FakeProvider):
    BATCH_SIZE = 2

    def __init__(self, records):
        super().__init__(records)
        self.batch_calls = []
        self.batch_error = None

    def works(self, dois):
        self.batch_calls.append(tuple(dois))
        if self.batch_error is not None:
            raise self.batch_error
        return {
            doi: self.records[doi]
            for doi in dois
            if doi in self.records
        }


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

    def test_not_available_abstract_is_treated_as_missing(self):
        publication = self.publication(abstract="Not Available")
        provider = FakeProvider({publication.doi: message()})

        result = backfill(
            [publication],
            provider=provider,
            fields=("abstract",),
            enrichment_lookup=lambda doi, work: Enrichment(
                abstract="Recovered abstract"
            ),
        )

        self.assertEqual(result.eligible_count, 1)
        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(
            result.candidates[0].proposed_value,
            "Recovered abstract",
        )

    def test_provider_not_available_abstract_is_not_proposed(self):
        publication = self.publication()
        provider = FakeProvider({publication.doi: message()})

        result = backfill(
            [publication],
            provider=provider,
            fields=("abstract",),
            enrichment_lookup=lambda doi, work: Enrichment(
                abstract="NOT AVAILABLE"
            ),
        )

        self.assertEqual(result.eligible_count, 1)
        self.assertEqual(result.candidates, ())
        self.assertEqual(result.no_value, (publication.doi,))


    def test_unsafe_crossref_abstract_becomes_review_required_evidence(self):
        publication = self.publication()
        data = message()
        data["abstract"] = (
            'A controller <jats:inline-graphic '
            'xlink:href="graphic/math-0002.png"/> is proposed.'
        )
        provider = FakeProvider({publication.doi: data})

        result = backfill(
            [publication],
            provider=provider,
            fields=("abstract",),
        )

        self.assertEqual(result.no_value, ())
        self.assertEqual(len(result.candidates), 1)
        candidate = result.candidates[0]
        self.assertTrue(candidate.review_required)
        self.assertEqual(candidate.proposed_value, "")
        self.assertEqual(len(candidate.evidence), 1)
        self.assertEqual(candidate.evidence[0].source, "crossref")
        self.assertEqual(candidate.evidence[0].reason, "embedded-graphic")
        self.assertIn("math-0002.png", candidate.evidence[0].value)

    def test_safe_backfill_proposal_keeps_refused_alternative_evidence(self):
        publication = self.publication()
        data = message()
        data["abstract"] = "(u<inf>0</inf>)<sup>T</sup>"
        provider = FakeProvider({publication.doi: data})

        result = backfill(
            [publication],
            provider=provider,
            fields=("abstract",),
            enrichment_lookup=lambda doi, work: Enrichment(
                abstract="Safe provider abstract",
                abstract_source="openalex",
            ),
        )

        candidate = result.candidates[0]
        self.assertFalse(candidate.review_required)
        self.assertEqual(candidate.proposed_value, "Safe provider abstract")
        self.assertEqual(len(candidate.evidence), 1)
        self.assertEqual(candidate.evidence[0].source, "crossref")
        self.assertEqual(candidate.evidence[0].reason, "script-markup")


    def test_batched_backfill_preserves_input_order_and_avoids_individual_work_calls(self):
        publications = tuple(
            Publication(
                id=new_publication_id(),
                identifiers={"doi": f"10.1000/item-{index}"},
                type="journal-article",
                title=f"Reviewed {index}",
                authors=(Author(literal="Reviewed Author"),),
                abstract="",
                permalink=f"reviewed-{index}",
            )
            for index in range(3)
        )
        provider = FakeBatchProvider(
            {
                publication.doi: message(f"Provider {index}")
                for index, publication in enumerate(publications)
            }
        )
        enrichment_calls = []

        def enrich_many(messages):
            enrichment_calls.append(tuple(messages))
            return {
                doi: Enrichment(abstract=f"Abstract for {doi}")
                for doi in messages
            }

        result = backfill(
            publications,
            provider=provider,
            batch_provider=provider,
            fields=("abstract",),
            enrichment_lookup=lambda doi, work: Enrichment(
                abstract="individual fallback should not run"
            ),
            enrichment_many_lookup=enrich_many,
        )

        self.assertEqual(
            provider.batch_calls,
            [
                ("10.1000/item-0", "10.1000/item-1"),
                ("10.1000/item-2",),
            ],
        )
        self.assertEqual(provider.calls, [])
        self.assertEqual(
            enrichment_calls,
            [(
                "10.1000/item-0",
                "10.1000/item-1",
                "10.1000/item-2",
            )],
        )
        self.assertEqual(
            tuple(candidate.doi for candidate in result.candidates),
            tuple(publication.doi for publication in publications),
        )
        self.assertEqual(result.unavailable, ())
        self.assertEqual(result.no_value, ())

    def test_batch_missing_record_falls_back_only_for_that_doi(self):
        publications = tuple(
            Publication(
                id=new_publication_id(),
                identifiers={"doi": f"10.1000/item-{index}"},
                type="journal-article",
                title=f"Reviewed {index}",
                authors=(Author(literal="Reviewed Author"),),
                abstract="",
                permalink=f"reviewed-{index}",
            )
            for index in range(2)
        )
        provider = FakeBatchProvider(
            {publications[0].doi: message("Provider 0")}
        )

        result = backfill(
            publications,
            provider=provider,
            batch_provider=provider,
            fields=("abstract",),
            enrichment_many_lookup=lambda messages: {
                doi: Enrichment(abstract=f"Abstract for {doi}")
                for doi in messages
            },
        )

        self.assertEqual(
            provider.batch_calls,
            [("10.1000/item-0", "10.1000/item-1")],
        )
        self.assertEqual(provider.calls, ["10.1000/item-1"])
        self.assertEqual(
            tuple(candidate.doi for candidate in result.candidates),
            ("10.1000/item-0",),
        )
        self.assertEqual(result.unavailable, ("10.1000/item-1",))

    def test_failed_work_batch_falls_back_to_individual_lookups(self):
        publications = tuple(
            Publication(
                id=new_publication_id(),
                identifiers={"doi": f"10.1000/item-{index}"},
                type="journal-article",
                title=f"Reviewed {index}",
                authors=(Author(literal="Reviewed Author"),),
                abstract="",
                permalink=f"reviewed-{index}",
            )
            for index in range(2)
        )
        provider = FakeBatchProvider(
            {
                publication.doi: message(f"Provider {index}")
                for index, publication in enumerate(publications)
            }
        )
        provider.batch_error = ValueError("temporary batch failure")

        result = backfill(
            publications,
            provider=provider,
            batch_provider=provider,
            fields=("abstract",),
            enrichment_many_lookup=lambda messages: {
                doi: Enrichment(abstract=f"Abstract for {doi}")
                for doi in messages
            },
        )

        self.assertEqual(
            provider.calls,
            ["10.1000/item-0", "10.1000/item-1"],
        )
        self.assertEqual(len(result.candidates), 2)

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


    def test_review_required_evidence_round_trips_and_cannot_be_accepted(self):
        evidence = AbstractEvidence(
            source="crossref",
            value=(
                'A controller <jats:inline-graphic '
                'xlink:href="graphic/math-0002.png"/> is proposed.'
            ),
            reason="embedded-graphic",
        )
        review = self.review(
            BackfillCandidate(
                publication_id=self.publication.id,
                doi=self.publication.doi,
                title=self.publication.title,
                field="abstract",
                proposed_value="",
                review_required=True,
                evidence=(evidence,),
            )
        )
        self.persist_review(review)

        loaded = load_project_backfill_review(self.config)
        candidate = backfill_resolution_candidates(loaded)[0]
        self.assertTrue(candidate.proposal.review_required)
        self.assertEqual(candidate.proposal.evidence, (evidence,))

        state = load_project_backfill_resolutions(self.config, loaded)
        with self.assertRaisesRegex(
            ProjectStateError,
            "cannot be accepted directly",
        ):
            record_backfill_resolution(
                state,
                candidate,
                decision="accepted",
            )

    def test_review_required_custom_value_can_be_staged(self):
        evidence = AbstractEvidence(
            source="semantic_scholar",
            value="(u<inf>0</inf>)<sup>T</sup>",
            reason="script-markup",
        )
        review = self.review(
            BackfillCandidate(
                publication_id=self.publication.id,
                doi=self.publication.doi,
                title=self.publication.title,
                field="abstract",
                proposed_value="",
                review_required=True,
                evidence=(evidence,),
            )
        )
        self.persist_review(review)
        state = load_project_backfill_resolutions(self.config, review)
        candidate = backfill_resolution_candidates(review)[0]
        state = record_backfill_resolution(
            state,
            candidate,
            decision="custom",
            resolved_value=r"(u_0)^T",
        )
        save_project_backfill_resolutions(self.config, state)

        plan = plan_project_backfill_apply(self.config)
        self.assertEqual(len(plan.changes), 1)
        self.assertEqual(plan.changes[0].decision, "custom")
        self.assertEqual(plan.changes[0].value, r"(u_0)^T")

        apply_project_backfill_apply(plan)
        staged = read_bibliography(self.config.paths.collected)
        self.assertEqual(staged[0].abstract, r"(u_0)^T")


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

    def test_apply_can_replace_historical_not_available_placeholder(self):
        placeholder = Publication(
            **{
                **self.publication.__dict__,
                "abstract": "Not Available",
            }
        )
        write_bibliography(self.config.paths.bibliography, (placeholder,))
        proposal = BackfillCandidate(
            publication_id=self.publication.id,
            doi=self.publication.doi,
            title=self.publication.title,
            field="abstract",
            proposed_value="Recovered abstract",
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

        plan = plan_project_backfill_apply(self.config)
        apply_project_backfill_apply(plan)

        staged = read_bibliography(self.config.paths.collected)
        self.assertEqual(staged[0].abstract, "Recovered abstract")

    def test_apply_rejects_placeholder_as_custom_abstract(self):
        proposal = BackfillCandidate(
            publication_id=self.publication.id,
            doi=self.publication.doi,
            title=self.publication.title,
            field="abstract",
            proposed_value="Recovered abstract",
        )
        review = self.review(proposal)
        self.persist_review(review)
        state = load_project_backfill_resolutions(self.config, review)
        state = record_backfill_resolution(
            state,
            backfill_resolution_candidates(review)[0],
            decision="custom",
            resolved_value="  not   available ",
        )
        save_project_backfill_resolutions(self.config, state)

        with self.assertRaisesRegex(
            ProjectStateError,
            "no meaningful resolved value",
        ):
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
