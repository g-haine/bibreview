from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from bibreview.campaign import campaign_from_data, campaign_progress
from bibreview.config import load_config
from bibreview.identity import new_publication_id
from bibreview.model import Author, Publication, Reference
from bibreview.project_references import (
    apply_project_references_plan,
    execute_project_references_batch,
    format_project_references_review,
    plan_project_references_batch,
    project_references_review,
    references_report_from_data,
)
from bibreview.providers.http import HttpError
from bibreview.reporting import Reporter
from bibreview.storage import read_json, write_bibliography


CONFIG = """\
schema_version: 1
project:
  name: Example Review
  slug: example-review
references:
  campaign: state/references-campaign.json
  report: state/references-report.json
  batch_size: 2
site:
  enabled: false
"""


class FakeBatchProvider:
    BATCH_SIZE = 2

    def __init__(self, messages=None, *, error=None):
        self.messages = dict(messages or {})
        self.error = error
        self.calls = []

    def works(self, dois):
        self.calls.append(tuple(dois))
        if self.error is not None:
            raise self.error
        return {
            doi: self.messages[doi]
            for doi in dois
            if doi in self.messages
        }


class ProjectReferencesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config_path = self.root / "bibreview.yml"
        self.config_path.write_text(CONFIG, encoding="utf-8")
        self.config = load_config(self.config_path)

        self.publications = (
            Publication(
                id=new_publication_id(),
                identifiers={"doi": "10.1000/one"},
                title="One",
                authors=(Author(literal="Author One"),),
                references=(
                    Reference(citation="Systems &amp; Control Letters"),
                ),
            ),
            Publication(
                id=new_publication_id(),
                identifiers={"doi": "10.1000/two"},
                title="Two",
                authors=(Author(literal="Author Two"),),
                references=(Reference(citation="Unchanged"),),
            ),
            Publication(
                id=new_publication_id(),
                identifiers={"doi": "10.1000/three"},
                title="Three",
                authors=(Author(literal="Author Three"),),
                references=(Reference(citation="Old"),),
            ),
        )
        self.config.paths.bibliography.parent.mkdir(parents=True, exist_ok=True)
        write_bibliography(
            self.config.paths.bibliography,
            self.publications,
        )

    def snapshot_non_reference_state(self):
        excluded = {
            self.config.references.campaign.resolve(),
            self.config.references.report.resolve(),
        }
        return {
            path.resolve(): path.read_bytes()
            for path in self.root.rglob("*")
            if path.is_file() and path.resolve() not in excluded
        }

    def test_start_is_read_only_until_apply(self):
        before = self.snapshot_non_reference_state()

        plan = plan_project_references_batch(self.config)

        self.assertEqual(before, self.snapshot_non_reference_state())
        self.assertEqual(plan.batch.id, "batch-0001")
        self.assertEqual(
            plan.batch.keys,
            tuple(publication.id for publication in self.publications[:2]),
        )
        self.assertEqual(
            set(plan.outputs),
            {
                self.config.references.campaign,
                self.config.references.report,
            },
        )

        apply_project_references_plan(plan)

        self.assertEqual(before, self.snapshot_non_reference_state())
        report = references_report_from_data(
            read_json(self.config.references.report, dict)
        )
        self.assertEqual(
            report.campaign_items,
            tuple(publication.id for publication in self.publications),
        )
        self.assertEqual(report.entries, ())

    def test_execute_batch_uses_parent_doi_batch_and_checkpoints_results(self):
        plan = plan_project_references_batch(self.config)
        apply_project_references_plan(plan)
        provider = FakeBatchProvider(
            {
                "10.1000/one": {
                    "reference": [
                        {"unstructured": "Systems &amp; Control Letters"}
                    ]
                },
                "10.1000/two": {
                    "reference": [{"unstructured": "Unchanged"}]
                },
            }
        )

        execution = execute_project_references_batch(
            self.config,
            batch_id=plan.batch.id,
            batch_provider=provider,
            reporter=Reporter(-1),
        )

        self.assertEqual(
            provider.calls,
            [("10.1000/one", "10.1000/two")],
        )
        self.assertEqual(execution.processed_count, 2)
        self.assertEqual(execution.completed_count, 2)
        self.assertEqual(execution.retryable_count, 0)
        self.assertEqual(execution.classifications["safe-update"], 1)
        self.assertEqual(execution.classifications["unchanged"], 1)

        report = references_report_from_data(
            read_json(self.config.references.report, dict)
        )
        by_doi = {entry.result.doi: entry.result for entry in report.entries}
        self.assertEqual(
            by_doi["10.1000/one"].classification,
            "safe-update",
        )
        self.assertEqual(
            by_doi["10.1000/one"].proposed_references[0].citation,
            "Systems & Control Letters",
        )
        self.assertEqual(
            by_doi["10.1000/two"].classification,
            "unchanged",
        )

        campaign = campaign_from_data(
            read_json(self.config.references.campaign, dict)
        )
        progress = campaign_progress(campaign)
        self.assertEqual(progress.completed, 2)
        self.assertEqual(progress.pending, 1)
        self.assertIsNone(progress.open_batch)

    def test_second_round_fetches_each_cited_doi_once_per_batch(self):
        child = "10.2000/child"
        publications = (
            Publication(
                id=new_publication_id(),
                identifiers={"doi": "10.1000/one"},
                title="One",
                authors=(Author(literal="Author One"),),
                references=(
                    Reference(
                        identifiers={"doi": child},
                        citation="Historical citation",
                    ),
                ),
            ),
            Publication(
                id=new_publication_id(),
                identifiers={"doi": "10.1000/two"},
                title="Two",
                authors=(Author(literal="Author Two"),),
                references=(
                    Reference(
                        identifiers={"doi": child},
                        citation="Historical citation",
                    ),
                ),
            ),
        )
        write_bibliography(self.config.paths.bibliography, publications)
        plan = plan_project_references_batch(self.config)
        apply_project_references_plan(plan)
        provider = FakeBatchProvider(
            {
                "10.1000/one": {
                    "reference": [
                        {"DOI": child, "unstructured": "Parent citation"}
                    ]
                },
                "10.1000/two": {
                    "reference": [
                        {"DOI": child, "unstructured": "Parent citation"}
                    ]
                },
                child: {
                    "type": "book",
                    "title": ["Referenced book"],
                    "author": [{"given": "A", "family": "Author"}],
                    "issued": {"date-parts": [[2024]]},
                    "publisher": "Publisher",
                    "DOI": child,
                },
            }
        )

        execution = execute_project_references_batch(
            self.config,
            batch_id=plan.batch.id,
            batch_provider=provider,
            reporter=Reporter(-1),
        )

        self.assertEqual(
            provider.calls,
            [
                ("10.1000/one", "10.1000/two"),
                (child,),
            ],
        )
        self.assertEqual(execution.cited_doi_count, 1)
        self.assertEqual(execution.formatted_citation_count, 1)
        self.assertEqual(execution.unavailable_citation_count, 0)

        report = references_report_from_data(
            read_json(self.config.references.report, dict)
        )
        self.assertEqual(len(report.entries), 2)
        for entry in report.entries:
            self.assertEqual(
                entry.result.proposed_references[0].identifiers["doi"],
                child,
            )
            self.assertIn(
                "Referenced book",
                entry.result.proposed_references[0].citation,
            )

    def test_provider_batch_failure_is_retryable_and_does_not_touch_canonical(self):
        canonical_before = self.config.paths.bibliography.read_bytes()
        plan = plan_project_references_batch(self.config)
        apply_project_references_plan(plan)
        provider = FakeBatchProvider(
            error=HttpError(
                "temporary",
                status_code=429,
            )
        )

        execution = execute_project_references_batch(
            self.config,
            batch_id=plan.batch.id,
            batch_provider=provider,
            reporter=Reporter(-1),
        )

        self.assertEqual(execution.retryable_count, 2)
        self.assertEqual(execution.classifications["unavailable"], 2)
        self.assertEqual(
            self.config.paths.bibliography.read_bytes(),
            canonical_before,
        )

        report = references_report_from_data(
            read_json(self.config.references.report, dict)
        )
        self.assertTrue(
            all(
                entry.result.reason == "provider-batch-error"
                for entry in report.entries
            )
        )

    def test_review_is_offline_and_lists_non_unchanged_results(self):
        plan = plan_project_references_batch(self.config)
        apply_project_references_plan(plan)
        provider = FakeBatchProvider(
            {
                "10.1000/one": {
                    "reference": [
                        {"unstructured": "Systems &amp; Control Letters"}
                    ]
                },
                "10.1000/two": {
                    "reference": [{"unstructured": "Updated"}]
                },
            }
        )
        execute_project_references_batch(
            self.config,
            batch_id=plan.batch.id,
            batch_provider=provider,
            reporter=Reporter(-1),
        )

        review = project_references_review(self.config)

        self.assertEqual(review.audited_publications, 2)
        self.assertEqual(review.safe_updates, 1)
        self.assertEqual(review.review_required, 1)
        self.assertEqual(len(review.items), 2)
        verbose = format_project_references_review(review, verbose=True)
        self.assertIn("10.1000/one", verbose)
        self.assertIn("safe-update", verbose)
        self.assertIn("10.1000/two", verbose)
        self.assertIn("review-required", verbose)

    def test_full_requeues_completed_items_without_skipping_pending_first_pass(self):
        first = plan_project_references_batch(self.config)
        apply_project_references_plan(first)
        provider = FakeBatchProvider(
            {
                "10.1000/one": {
                    "reference": [
                        {"unstructured": "Systems &amp; Control Letters"}
                    ]
                },
                "10.1000/two": {
                    "reference": [{"unstructured": "Unchanged"}]
                },
            }
        )
        execute_project_references_batch(
            self.config,
            batch_id=first.batch.id,
            batch_provider=provider,
            reporter=Reporter(-1),
        )

        full = plan_project_references_batch(
            self.config,
            batch_size=3,
            full=True,
        )

        # The generic campaign contract completes never-visited pending work
        # before retrying items requeued by --full.
        self.assertEqual(
            full.batch.keys,
            (self.publications[2].id,),
        )
        states = {item.key: item.state for item in full.campaign.items}
        self.assertEqual(states[self.publications[0].id], "retryable")
        self.assertEqual(states[self.publications[1].id], "retryable")
        self.assertEqual(states[self.publications[2].id], "active")

    def test_non_doi_publication_is_completed_as_unavailable_without_provider_call(self):
        item = Publication(
            id=new_publication_id(),
            identifiers={},
            title="No DOI",
            authors=(Author(literal="Author"),),
            references=(Reference(citation="Reference"),),
        )
        write_bibliography(self.config.paths.bibliography, (item,))
        plan = plan_project_references_batch(self.config)
        apply_project_references_plan(plan)
        provider = FakeBatchProvider()

        execution = execute_project_references_batch(
            self.config,
            batch_id=plan.batch.id,
            batch_provider=provider,
            reporter=Reporter(-1),
        )

        self.assertEqual(provider.calls, [])
        self.assertEqual(execution.completed_count, 1)
        report = references_report_from_data(
            read_json(self.config.references.report, dict)
        )
        self.assertEqual(
            report.entries[0].result.reason,
            "canonical-publication-without-doi",
        )


if __name__ == "__main__":
    unittest.main()
