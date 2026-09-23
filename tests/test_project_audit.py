from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from bibreview.config import load_config
from bibreview.identity import new_publication_id
from bibreview.model import Author, Publication
from bibreview.pipeline.audit import (
    AuditComparison,
    AuditResult,
    ProviderEvidence,
    compare_audit_record,
    publication_audit_record,
)
from bibreview.project import ProjectStateError
from bibreview.project_audit import (
    apply_project_audit_plan,
    audit_report_from_data,
    execute_project_audit_batch,
    format_project_audit_review,
    plan_project_audit_batch,
    plan_project_audit_checkpoint,
    plan_project_audit_close,
    plan_project_audit_reclassify,
    project_audit_review,
)
from bibreview.providers.http import HttpError
from bibreview.reporting import Reporter
from bibreview.storage import read_json, write_bibliography


CONFIG = """\
schema_version: 1
project:
  name: Example Review
  slug: example-review
audit:
  campaign: state/audit-campaign.json
  report: state/audit-report.json
  batch_size: 2
site:
  enabled: false
"""


class FakeAuditSource:
    name = "fake-provider"

    def __init__(self, outcomes):
        self.outcomes = dict(outcomes)
        self.calls = []

    def evidence(self, doi):
        self.calls.append(doi)
        outcome = self.outcomes[doi]
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class FakeBatchAuditSource:
    name = "fake-batch-provider"
    batch_size = 50

    def __init__(self, outcomes=None, *, error=None):
        self.outcomes = dict(outcomes or {})
        self.error = error
        self.batch_calls = []
        self.single_calls = []

    def evidence_many(self, dois):
        self.batch_calls.append(tuple(dois))
        if self.error is not None:
            raise self.error
        return {doi: self.outcomes[doi] for doi in dois}

    def evidence(self, doi):
        self.single_calls.append(doi)
        raise AssertionError("batch-capable source should not use evidence()")


class ProjectAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config_path = self.root / "bibreview.yml"
        self.config_path.write_text(CONFIG, encoding="utf-8")
        self.config = load_config(self.config_path)
        self.publications = tuple(
            Publication(
                id=new_publication_id(),
                identifiers={"doi": f"10.1000/item-{index}"},
                title=f"Publication {index}",
                authors=(Author(literal=f"Author {index}"),),
                publication_year=str(2020 + index),
                permalink=f"publication-{index}",
            )
            for index in range(1, 4)
        )
        self.config.paths.bibliography.parent.mkdir(parents=True, exist_ok=True)
        write_bibliography(
            self.config.paths.bibliography,
            self.publications,
        )
        self.config.paths.pending.parent.mkdir(parents=True, exist_ok=True)
        self.config.paths.pending.write_text(
            "10.1000/untouched\n",
            encoding="utf-8",
        )

    def snapshot_non_audit(self):
        excluded = {
            self.config.audit.campaign.resolve(),
            self.config.audit.report.resolve(),
        }
        return {
            path.resolve(): path.read_bytes()
            for path in self.root.rglob("*")
            if path.is_file() and path.resolve() not in excluded
        }

    def result_for(self, publication, provider_year=None, *, unavailable=False):
        record = publication_audit_record(publication)
        if unavailable:
            evidence = ProviderEvidence(
                provider="crossref",
                status="unavailable",
                detail="HTTP 429",
            )
        else:
            evidence = ProviderEvidence(
                provider="crossref",
                identifiers={"doi": publication.doi},
                fields={
                    "title": publication.title,
                    "publication_year": provider_year
                    or publication.publication_year,
                },
            )
        return compare_audit_record(record, (evidence,))

    def test_start_is_read_only_until_apply_and_writes_only_audit_state(self):
        before = self.snapshot_non_audit()

        plan = plan_project_audit_batch(self.config)

        self.assertEqual(before, self.snapshot_non_audit())
        self.assertEqual(plan.batch.id, "batch-0001")
        self.assertEqual(
            plan.batch.keys,
            tuple(publication.id for publication in self.publications[:2]),
        )
        self.assertEqual(
            set(plan.outputs),
            {self.config.audit.campaign, self.config.audit.report},
        )

        apply_project_audit_plan(plan)

        self.assertEqual(before, self.snapshot_non_audit())
        self.assertTrue(self.config.audit.campaign.exists())
        self.assertTrue(self.config.audit.report.exists())
        report = audit_report_from_data(
            read_json(self.config.audit.report, dict)
        )
        self.assertEqual(
            report.campaign_items,
            tuple(publication.id for publication in self.publications),
        )
        self.assertEqual(report.entries, ())

    def test_resume_returns_same_open_batch_without_new_writes(self):
        first = plan_project_audit_batch(self.config)
        apply_project_audit_plan(first)

        before = self.snapshot_non_audit()
        resumed = plan_project_audit_batch(self.config)

        self.assertEqual(resumed.batch, first.batch)
        self.assertFalse(resumed.changed)
        self.assertEqual(before, self.snapshot_non_audit())

    def test_completed_items_are_not_reaudited_when_new_publication_is_added(self):
        start = plan_project_audit_batch(self.config, batch_size=3)
        apply_project_audit_plan(start)
        for publication in self.publications:
            checkpoint = plan_project_audit_checkpoint(
                self.config,
                batch_id=start.batch.id,
                result=self.result_for(publication),
                state="completed",
            )
            apply_project_audit_plan(checkpoint)
        apply_project_audit_plan(
            plan_project_audit_close(self.config, batch_id=start.batch.id)
        )

        new_publication = Publication(
            id=new_publication_id(),
            identifiers={"doi": "10.1000/item-4"},
            title="Publication 4",
            authors=(Author(literal="Author 4"),),
            publication_year="2024",
            permalink="publication-4",
        )
        write_bibliography(
            self.config.paths.bibliography,
            self.publications + (new_publication,),
        )

        incremental = plan_project_audit_batch(self.config, batch_size=1)

        self.assertIsNotNone(incremental.batch)
        self.assertEqual(incremental.batch.keys, (new_publication.id,))
        states = {item.key: item.state for item in incremental.campaign.items}
        self.assertTrue(
            all(states[publication.id] == "completed" for publication in self.publications)
        )
        self.assertEqual(states[new_publication.id], "active")
        self.assertEqual(
            incremental.report.campaign_items,
            tuple(publication.id for publication in self.publications)
            + (new_publication.id,),
        )
        self.assertEqual(len(incremental.report.entries), 3)

    def test_full_requeues_current_completed_publications(self):
        start = plan_project_audit_batch(self.config, batch_size=3)
        apply_project_audit_plan(start)
        for publication in self.publications:
            checkpoint = plan_project_audit_checkpoint(
                self.config,
                batch_id=start.batch.id,
                result=self.result_for(publication),
                state="completed",
            )
            apply_project_audit_plan(checkpoint)
        apply_project_audit_plan(
            plan_project_audit_close(self.config, batch_id=start.batch.id)
        )

        full = plan_project_audit_batch(
            self.config,
            batch_size=2,
            full=True,
        )

        self.assertIsNotNone(full.batch)
        self.assertEqual(
            full.batch.keys,
            tuple(publication.id for publication in self.publications[:2]),
        )
        by_key = {item.key: item for item in full.campaign.items}
        self.assertEqual(by_key[self.publications[0].id].attempts, 2)
        self.assertEqual(by_key[self.publications[1].id].attempts, 2)
        self.assertEqual(by_key[self.publications[2].id].attempts, 1)
        self.assertEqual(by_key[self.publications[2].id].state, "retryable")
        self.assertEqual(len(full.report.entries), 3)

    def test_full_rejects_reset_while_batch_is_open(self):
        start = plan_project_audit_batch(self.config, batch_size=1)
        apply_project_audit_plan(start)

        with self.assertRaisesRegex(ProjectStateError, "batch is open"):
            plan_project_audit_batch(self.config, full=True)

    def test_checkpoint_persists_latest_result_without_touching_project_state(self):
        start = plan_project_audit_batch(self.config)
        apply_project_audit_plan(start)
        publication = self.publications[0]
        result = self.result_for(publication, provider_year="1999")
        before = self.snapshot_non_audit()

        checkpoint = plan_project_audit_checkpoint(
            self.config,
            batch_id=start.batch.id,
            result=result,
            state="completed",
        )

        self.assertEqual(before, self.snapshot_non_audit())
        self.assertEqual(
            set(checkpoint.outputs),
            {self.config.audit.campaign, self.config.audit.report},
        )
        apply_project_audit_plan(checkpoint)
        self.assertEqual(before, self.snapshot_non_audit())

        report = audit_report_from_data(
            read_json(self.config.audit.report, dict)
        )
        self.assertEqual(len(report.entries), 1)
        entry = report.entries[0]
        self.assertEqual(entry.publication_id, publication.id)
        self.assertEqual(entry.batch_id, "batch-0001")
        self.assertEqual(entry.attempt, 1)
        self.assertEqual(
            entry.result.comparisons[-1].classification,
            "substantive-difference",
        )

    def test_offline_reclassification_changes_only_report(self):
        start = plan_project_audit_batch(self.config, batch_size=1)
        apply_project_audit_plan(start)
        publication = self.publications[0]
        old_result = AuditResult(
            publication_id=publication.id,
            identifiers=publication.identifiers,
            permalink=publication.permalink,
            title=publication.title,
            comparisons=(
                AuditComparison(
                    provider="crossref",
                    field="pages",
                    classification="substantive-difference",
                    canonical_value="10--20",
                    provider_value="10-20",
                ),
            ),
            provider_issues=(),
            disagreements=(),
        )
        checkpoint = plan_project_audit_checkpoint(
            self.config,
            batch_id=start.batch.id,
            result=old_result,
            state="completed",
        )
        apply_project_audit_plan(checkpoint)
        apply_project_audit_plan(
            plan_project_audit_close(self.config, batch_id=start.batch.id)
        )
        campaign_before = self.config.audit.campaign.read_bytes()

        plan = plan_project_audit_reclassify(self.config)

        self.assertEqual(set(plan.outputs), {self.config.audit.report})
        self.assertEqual(self.config.audit.campaign.read_bytes(), campaign_before)
        self.assertEqual(plan.summary_metrics.publications, 1)
        self.assertEqual(plan.summary_metrics.changed_results, 1)
        self.assertEqual(plan.summary_metrics.changed_comparisons, 1)
        self.assertEqual(
            dict(plan.summary_metrics.before_counts),
            {"substantive-difference": 1},
        )
        self.assertEqual(
            dict(plan.summary_metrics.after_counts),
            {"formatting-only": 1},
        )
        self.assertEqual(plan.summary_metrics.review_findings, 0)

        apply_project_audit_plan(plan)

        self.assertEqual(self.config.audit.campaign.read_bytes(), campaign_before)
        report = audit_report_from_data(
            read_json(self.config.audit.report, dict)
        )
        self.assertEqual(
            report.entries[0].result.comparisons[0].classification,
            "formatting-only",
        )

    def test_review_groups_corroborating_actionable_findings(self):
        start = plan_project_audit_batch(self.config, batch_size=1)
        apply_project_audit_plan(start)
        publication = self.publications[0]
        result = AuditResult(
            publication_id=publication.id,
            identifiers=publication.identifiers,
            permalink=publication.permalink,
            title=publication.title,
            comparisons=(
                AuditComparison(
                    provider="crossref",
                    field="volume",
                    classification="canonical-missing",
                    canonical_value="",
                    provider_value="48",
                ),
                AuditComparison(
                    provider="openalex",
                    field="volume",
                    classification="canonical-missing",
                    canonical_value="",
                    provider_value="48",
                ),
            ),
            provider_issues=(),
            disagreements=(),
        )
        checkpoint = plan_project_audit_checkpoint(
            self.config,
            batch_id=start.batch.id,
            result=result,
            state="completed",
        )
        apply_project_audit_plan(checkpoint)
        apply_project_audit_plan(
            plan_project_audit_close(self.config, batch_id=start.batch.id)
        )
        report_before = self.config.audit.report.read_bytes()
        campaign_before = self.config.audit.campaign.read_bytes()

        review = project_audit_review(self.config)

        self.assertEqual(review.audited_publications, 1)
        self.assertEqual(review.flagged_publications, 1)
        self.assertEqual(review.actionable_findings, 1)
        self.assertEqual(review.informational_findings, 0)
        self.assertEqual(review.provider_issues, 0)
        finding = review.items[0].findings[0]
        self.assertEqual(finding.field, "volume")
        self.assertEqual(finding.classification, "canonical-missing")
        self.assertEqual(finding.providers, ("crossref", "openalex"))
        self.assertEqual(
            tuple(value for _, value in finding.provider_values),
            ("48", "48"),
        )
        rendered = format_project_audit_review(review)
        self.assertIn("canonical-missing / volume", rendered)
        self.assertIn("(crossref, openalex)", rendered)
        self.assertEqual(self.config.audit.report.read_bytes(), report_before)
        self.assertEqual(self.config.audit.campaign.read_bytes(), campaign_before)

    def test_cannot_close_batch_until_every_item_is_checkpointed(self):
        start = plan_project_audit_batch(self.config)
        apply_project_audit_plan(start)
        first = plan_project_audit_checkpoint(
            self.config,
            batch_id=start.batch.id,
            result=self.result_for(self.publications[0]),
            state="completed",
        )
        apply_project_audit_plan(first)

        with self.assertRaisesRegex(ProjectStateError, "cannot close with 1 active"):
            plan_project_audit_close(
                self.config,
                batch_id=start.batch.id,
            )

    def test_close_then_next_batch_appends_new_canonical_uuid(self):
        start = plan_project_audit_batch(self.config)
        apply_project_audit_plan(start)
        for publication in self.publications[:2]:
            checkpoint = plan_project_audit_checkpoint(
                self.config,
                batch_id=start.batch.id,
                result=self.result_for(publication),
                state="completed",
            )
            apply_project_audit_plan(checkpoint)

        close = plan_project_audit_close(
            self.config,
            batch_id=start.batch.id,
        )
        apply_project_audit_plan(close)

        changed = Publication(
            id=new_publication_id(),
            identifiers={"doi": "10.1000/new-after-start"},
            title="Added after audit start",
            authors=(Author(literal="Later Author"),),
            permalink="added-after-audit-start",
        )
        write_bibliography(
            self.config.paths.bibliography,
            self.publications + (changed,),
        )

        second = plan_project_audit_batch(self.config)

        self.assertEqual(second.batch.id, "batch-0002")
        self.assertEqual(
            second.batch.keys,
            (self.publications[2].id, changed.id),
        )
        self.assertIn(changed.id, second.report.campaign_items)

    def test_retry_replaces_old_report_entry_after_pending_first_pass(self):
        first = plan_project_audit_batch(self.config)
        apply_project_audit_plan(first)

        retryable = plan_project_audit_checkpoint(
            self.config,
            batch_id=first.batch.id,
            result=self.result_for(self.publications[0], unavailable=True),
            state="retryable",
        )
        apply_project_audit_plan(retryable)
        completed = plan_project_audit_checkpoint(
            self.config,
            batch_id=first.batch.id,
            result=self.result_for(self.publications[1]),
            state="completed",
        )
        apply_project_audit_plan(completed)
        apply_project_audit_plan(
            plan_project_audit_close(self.config, batch_id=first.batch.id)
        )

        second = plan_project_audit_batch(self.config)
        self.assertEqual(second.batch.keys, (self.publications[2].id,))
        apply_project_audit_plan(second)
        third_publication = plan_project_audit_checkpoint(
            self.config,
            batch_id=second.batch.id,
            result=self.result_for(self.publications[2]),
            state="completed",
        )
        apply_project_audit_plan(third_publication)
        apply_project_audit_plan(
            plan_project_audit_close(self.config, batch_id=second.batch.id)
        )

        retry = plan_project_audit_batch(self.config)
        self.assertEqual(retry.batch.id, "batch-0003")
        self.assertEqual(retry.batch.keys, (self.publications[0].id,))
        apply_project_audit_plan(retry)

        replacement = plan_project_audit_checkpoint(
            self.config,
            batch_id=retry.batch.id,
            result=self.result_for(self.publications[0]),
            state="completed",
        )
        apply_project_audit_plan(replacement)

        report = audit_report_from_data(
            read_json(self.config.audit.report, dict)
        )
        entries = {
            entry.publication_id: entry
            for entry in report.entries
        }
        updated = entries[self.publications[0].id]
        self.assertEqual(updated.batch_id, "batch-0003")
        self.assertEqual(updated.attempt, 2)
        self.assertEqual(updated.result.provider_issues, ())
        self.assertEqual(len(report.entries), 3)

    def test_batch_size_override_applies_only_to_newly_opened_batch(self):
        first = plan_project_audit_batch(self.config, batch_size=1)
        self.assertEqual(first.batch.keys, (self.publications[0].id,))
        self.assertEqual(first.campaign.default_batch_size, 2)
        apply_project_audit_plan(first)

        resumed = plan_project_audit_batch(self.config, batch_size=2)
        self.assertEqual(resumed.batch, first.batch)

        checkpoint = plan_project_audit_checkpoint(
            self.config,
            batch_id=first.batch.id,
            result=self.result_for(self.publications[0]),
            state="completed",
        )
        apply_project_audit_plan(checkpoint)
        apply_project_audit_plan(
            plan_project_audit_close(self.config, batch_id=first.batch.id)
        )

        second = plan_project_audit_batch(self.config)
        self.assertEqual(
            second.batch.keys,
            tuple(publication.id for publication in self.publications[1:]),
        )

    def test_schema_v1_campaign_resumes_and_migrates_without_report_loss(self):
        first = plan_project_audit_batch(self.config, batch_size=1)
        apply_project_audit_plan(first)
        checkpoint = plan_project_audit_checkpoint(
            self.config,
            batch_id=first.batch.id,
            result=self.result_for(self.publications[0]),
            state="completed",
        )
        apply_project_audit_plan(checkpoint)
        apply_project_audit_plan(
            plan_project_audit_close(self.config, batch_id=first.batch.id)
        )

        report_before = self.config.audit.report.read_bytes()
        legacy = read_json(self.config.audit.campaign, dict)
        legacy["schema_version"] = 1
        legacy["batch_size"] = legacy.pop("default_batch_size")
        self.config.audit.campaign.write_text(
            __import__("json").dumps(legacy, indent=2) + "\n",
            encoding="utf-8",
        )

        second = plan_project_audit_batch(self.config, batch_size=1)

        self.assertEqual(second.batch.id, "batch-0002")
        self.assertEqual(second.batch.keys, (self.publications[1].id,))
        self.assertEqual(second.campaign.default_batch_size, 2)
        self.assertEqual(
            next(
                item
                for item in second.campaign.items
                if item.key == self.publications[0].id
            ).state,
            "completed",
        )
        self.assertIn(self.config.audit.campaign, second.outputs)
        self.assertNotIn(self.config.audit.report, second.outputs)

        apply_project_audit_plan(second)

        migrated = read_json(self.config.audit.campaign, dict)
        self.assertEqual(migrated["schema_version"], 2)
        self.assertEqual(migrated["default_batch_size"], 2)
        self.assertNotIn("batch_size", migrated)
        self.assertEqual(self.config.audit.report.read_bytes(), report_before)

    def test_partial_audit_state_is_rejected(self):
        self.config.audit.campaign.parent.mkdir(parents=True, exist_ok=True)
        self.config.audit.campaign.write_text("{}\n", encoding="utf-8")

        with self.assertRaisesRegex(ProjectStateError, "both exist or both be absent"):
            plan_project_audit_batch(self.config)

    def test_execution_checkpoints_provider_failures_as_retryable(self):
        start = plan_project_audit_batch(self.config)
        apply_project_audit_plan(start)
        before = self.snapshot_non_audit()
        first, second = self.publications[:2]
        source = FakeAuditSource({
            first.doi: HttpError(
                "Cross-provider request: api.example.test: HTTP 429",
                status_code=429,
            ),
            second.doi: ProviderEvidence(
                provider="fake-provider",
                identifiers={"doi": second.doi},
                fields={
                    "title": second.title,
                    "publication_year": second.publication_year,
                },
            ),
        })

        execution = execute_project_audit_batch(
            self.config,
            batch_id=start.batch.id,
            sources=(source,),
            reporter=Reporter(-1),
        )

        self.assertEqual(execution.processed_count, 2)
        self.assertEqual(execution.completed_count, 1)
        self.assertEqual(execution.retryable_count, 1)
        self.assertEqual(execution.failed_count, 0)
        self.assertEqual(before, self.snapshot_non_audit())
        self.assertTrue(execution.campaign.batches[0].closed)

        entries = {
            entry.publication_id: entry
            for entry in execution.report.entries
        }
        first_result = entries[first.id].result
        self.assertEqual(
            first_result.provider_issues[0].classification,
            "unavailable",
        )
        self.assertIn("HTTP 429", first_result.provider_issues[0].detail)
        self.assertEqual(
            next(item for item in execution.campaign.items if item.key == first.id).state,
            "retryable",
        )

    def test_execution_batches_capable_sources_once_for_active_dois(self):
        start = plan_project_audit_batch(self.config)
        apply_project_audit_plan(start)
        first, second = self.publications[:2]
        source = FakeBatchAuditSource({
            first.doi: ProviderEvidence(
                provider="fake-batch-provider",
                identifiers={"doi": first.doi},
                fields={"title": first.title},
            ),
            second.doi: ProviderEvidence(
                provider="fake-batch-provider",
                identifiers={"doi": second.doi},
                fields={"title": second.title},
            ),
        })

        execution = execute_project_audit_batch(
            self.config,
            batch_id=start.batch.id,
            sources=(source,),
            reporter=Reporter(-1),
        )

        self.assertEqual(source.batch_calls, [(first.doi, second.doi)])
        self.assertEqual(source.single_calls, [])
        self.assertEqual(execution.completed_count, 2)
        self.assertEqual(execution.retryable_count, 0)

    def test_batch_provider_failure_marks_only_that_chunk_retryable(self):
        start = plan_project_audit_batch(self.config)
        apply_project_audit_plan(start)
        first, second = self.publications[:2]
        source = FakeBatchAuditSource(
            error=HttpError("provider: HTTP 429", status_code=429)
        )

        execution = execute_project_audit_batch(
            self.config,
            batch_id=start.batch.id,
            sources=(source,),
            reporter=Reporter(-1),
        )

        self.assertEqual(source.batch_calls, [(first.doi, second.doi)])
        self.assertEqual(execution.completed_count, 0)
        self.assertEqual(execution.retryable_count, 2)
        for entry in execution.report.entries:
            self.assertEqual(
                entry.result.provider_issues[0].classification,
                "unavailable",
            )
            self.assertIn("HTTP 429", entry.result.provider_issues[0].detail)

    def test_interruption_preserves_completed_checkpoint_and_open_batch(self):
        start = plan_project_audit_batch(self.config)
        apply_project_audit_plan(start)
        first, second = self.publications[:2]
        source = FakeAuditSource({
            first.doi: ProviderEvidence(
                provider="fake-provider",
                identifiers={"doi": first.doi},
                fields={"title": first.title},
            ),
            second.doi: KeyboardInterrupt(),
        })

        with self.assertRaises(KeyboardInterrupt):
            execute_project_audit_batch(
                self.config,
                batch_id=start.batch.id,
                sources=(source,),
                reporter=Reporter(-1),
            )

        resumed = plan_project_audit_batch(self.config)
        self.assertEqual(resumed.batch.id, start.batch.id)
        states = {item.key: item.state for item in resumed.campaign.items}
        self.assertEqual(states[first.id], "completed")
        self.assertEqual(states[second.id], "active")
        report = audit_report_from_data(
            read_json(self.config.audit.report, dict)
        )
        self.assertEqual(
            tuple(entry.publication_id for entry in report.entries),
            (first.id,),
        )

    def test_execution_summary_is_human_readable(self):
        start = plan_project_audit_batch(self.config, batch_size=2)
        apply_project_audit_plan(start)
        source = FakeAuditSource({
            self.publications[0].doi: ProviderEvidence(
                provider="fake-provider",
                identifiers={"doi": self.publications[0].doi},
                fields={"title": self.publications[0].title},
            ),
            self.publications[1].doi: HttpError(
                "provider: HTTP 429",
                status_code=429,
            ),
        })

        execution = execute_project_audit_batch(
            self.config,
            batch_id=start.batch.id,
            sources=(source,),
            reporter=Reporter(-1),
        )

        self.assertEqual(
            execution.summary(),
            "Audit batch batch-0001 complete\n"
            "  This batch : 2 processed (1 completed, 1 retryable, 0 failed)\n"
            "  Campaign   : 2 / 3 processed (1 completed, 1 retryable, 0 failed)\n"
            "  Remaining  : 1 pending\n"
            "  Batches    : 1 closed",
        )

    def test_report_corruption_is_rejected_before_checkpoint(self):
        start = plan_project_audit_batch(self.config)
        apply_project_audit_plan(start)
        payload = read_json(self.config.audit.report, dict)
        payload["campaign_items"] = payload["campaign_items"][1:]
        self.config.audit.report.write_text(
            __import__("json").dumps(payload, indent=2) + "\n",
            encoding="utf-8",
        )

        with self.assertRaisesRegex(ProjectStateError, "snapshots do not match"):
            plan_project_audit_checkpoint(
                self.config,
                batch_id=start.batch.id,
                result=self.result_for(self.publications[0]),
                state="completed",
            )


if __name__ == "__main__":
    unittest.main()
