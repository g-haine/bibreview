from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from bibreview.config import load_config
from bibreview.identity import new_publication_id
from bibreview.model import Author, Publication
from bibreview.project import ProjectStateError
from bibreview.project_init import (
    apply_project_init_plan,
    apply_project_init_rescreen,
    execute_project_init_batch,
    plan_project_init_batch,
    plan_project_init_rescreen,
    project_init_status,
    validate_project_init_start,
)
from bibreview.storage import write_bibliography


CONFIG = """\
schema_version: 1
project:
  name: Example Review
  slug: example-review
discovery:
  provider: openalex
  query: fluid-structure interaction
  accepted_types:
    - journal-article
relevance:
  patterns:
    - 'fluid[-\\s]+structure'
  unmatched: manual-review
initialization:
  campaign: state/init-campaign.json
  report: state/init-report.json
  batch_size: 2
site:
  enabled: false
"""


class FakeWorkProvider:
    def __init__(self, records):
        self.records = dict(records)
        self.calls = []
        self.errors = {}

    def work(self, doi):
        self.calls.append(doi)
        error = self.errors.get(doi)
        if error is not None:
            raise error
        return self.records.get(doi)


def work(title, *, work_type="journal-article"):
    return {
        "type": work_type,
        "title": [title],
        "author": [{"given": "Ada", "family": "Lovelace"}],
        "container-title": ["Journal"],
        "created": {"date-parts": [[2026, 10, 1]]},
        "published-print": {"date-parts": [[2026]]},
        "reference": [],
    }


def publication(doi, title):
    return Publication(
        id=new_publication_id(),
        identifiers={"doi": doi},
        type="journal-article",
        title=title,
        authors=(Author(literal="Reviewed Author"),),
        publication_year="2026",
        permalink=title.lower().replace(" ", "-"),
    )


class ProjectInitTests(unittest.TestCase):
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

    def test_new_campaign_is_read_only_until_applied_and_has_stable_batch(self):
        before = self.snapshot()

        plan = plan_project_init_batch(
            self.config,
            candidates=("10.1/a", "10.1/b", "10.1/c", "10.1/a"),
        )

        self.assertEqual(before, self.snapshot())
        self.assertEqual(plan.batch.id, "batch-0001")
        self.assertEqual(plan.batch.keys, ("10.1/a", "10.1/b"))
        self.assertTrue(plan.needs_screening)
        self.assertEqual(
            tuple(item.key for item in plan.campaign.items),
            ("10.1/a", "10.1/b", "10.1/c"),
        )

        apply_project_init_plan(plan)

        self.assertTrue(self.config.initialization.campaign.exists())
        self.assertTrue(self.config.initialization.report.exists())

    def test_new_campaign_filters_configured_excluded_doi_substrings_before_freeze(self):
        self.config_path.write_text(
            CONFIG.replace(
                "relevance:\n",
                "  exclude_doi_substrings:\n"
                "    - zenodo\n"
                "    - arxiv\n"
                "relevance:\n",
            ),
            encoding="utf-8",
        )
        config = load_config(self.config_path)

        plan = plan_project_init_batch(
            config,
            candidates=(
                "10.5281/zenodo.12345",
                "10.1000/KEEP",
                "10.48550/arxiv.2601.12345",
                "10.1000/keep",
            ),
        )

        self.assertEqual(
            tuple(item.key for item in plan.campaign.items),
            ("10.1000/keep",),
        )
        self.assertEqual(plan.batch.keys, ("10.1000/keep",))

    def test_batch_waits_for_review_collect_merge_before_next_batch(self):
        plan = plan_project_init_batch(
            self.config,
            candidates=("10.1/a", "10.1/b", "10.1/c"),
        )
        apply_project_init_plan(plan)
        provider = FakeWorkProvider({
            "10.1/a": work("Fluid-structure interaction model"),
            "10.1/b": work("Another coupled model"),
            "10.1/c": work("Fluid-structure interaction continuation"),
        })

        execution = execute_project_init_batch(
            self.config,
            batch_id=plan.batch.id,
            provider=provider,
        )

        self.assertEqual(provider.calls, ["10.1/a", "10.1/b"])
        self.assertEqual(execution.queued, 1)
        self.assertEqual(execution.review, 1)
        self.assertEqual(execution.status.current_batch, "batch-0001")
        self.assertEqual(execution.status.queued, 1)
        self.assertEqual(execution.status.review, 1)
        self.assertEqual(execution.status.unscreened, 1)
        self.assertEqual(
            self.config.paths.pending.read_text(encoding="utf-8"),
            "doi:10.1/a\n",
        )
        self.assertEqual(
            self.config.paths.review.read_text(encoding="utf-8"),
            "doi:10.1/b\n",
        )

        waiting = plan_project_init_batch(self.config)
        self.assertEqual(waiting.batch.id, "batch-0001")
        self.assertFalse(waiting.needs_screening)

        write_bibliography(
            self.config.paths.bibliography,
            (
                publication("10.1/a", "Reviewed A"),
                publication("10.1/b", "Reviewed B"),
            ),
        )
        self.config.paths.pending.write_text("", encoding="utf-8")
        self.config.paths.review.write_text("", encoding="utf-8")

        next_plan = plan_project_init_batch(self.config)
        self.assertEqual(next_plan.batch.id, "batch-0002")
        self.assertEqual(next_plan.batch.keys, ("10.1/c",))
        self.assertTrue(next_plan.needs_screening)
        apply_project_init_plan(next_plan)

        second = execute_project_init_batch(
            self.config,
            batch_id=next_plan.batch.id,
            provider=FakeWorkProvider({
                "10.1/c": work("Dataset", work_type="dataset"),
            }),
        )
        self.assertEqual(second.rejected, 1)
        self.assertIsNone(second.status.current_batch)
        self.assertEqual(second.status.merged, 2)
        self.assertEqual(second.status.rejected, 1)
        self.assertTrue(second.status.complete)
        self.assertTrue(second.status.successful)

    def test_provider_failure_is_retryable_and_pending_candidates_go_first(self):
        first = plan_project_init_batch(
            self.config,
            candidates=("10.1/a", "10.1/b"),
            batch_size=1,
        )
        apply_project_init_plan(first)
        provider = FakeWorkProvider({})
        provider.errors["10.1/a"] = OSError("temporary outage")

        execution = execute_project_init_batch(
            self.config,
            batch_id=first.batch.id,
            provider=provider,
        )

        self.assertEqual(execution.retryable, 1)
        self.assertIsNone(execution.status.current_batch)
        self.assertEqual(execution.status.retryable, 1)
        self.assertEqual(
            self.config.paths.rejected.read_text(encoding="utf-8"),
            "",
        )

        next_plan = plan_project_init_batch(self.config)
        self.assertEqual(next_plan.batch.keys, ("10.1/b",))

    def test_start_refuses_existing_canonical_staging_or_queue_state(self):
        write_bibliography(
            self.config.paths.bibliography,
            (publication("10.1/existing", "Existing"),),
        )
        with self.assertRaisesRegex(ProjectStateError, "empty canonical"):
            validate_project_init_start(self.config)

        self.config.paths.bibliography.unlink()
        write_bibliography(
            self.config.paths.collected,
            (publication("10.1/staged", "Staged"),),
        )
        with self.assertRaisesRegex(ProjectStateError, "empty collected"):
            validate_project_init_start(self.config)

        self.config.paths.collected.unlink()
        self.config.paths.pending.parent.mkdir(parents=True, exist_ok=True)
        self.config.paths.pending.write_text("doi:10.1/pending\n", encoding="utf-8")
        with self.assertRaisesRegex(ProjectStateError, "empty pending"):
            validate_project_init_start(self.config)

    def test_status_treats_collected_pending_candidate_as_staged(self):
        plan = plan_project_init_batch(
            self.config,
            candidates=("10.1/a",),
        )
        apply_project_init_plan(plan)
        execute_project_init_batch(
            self.config,
            batch_id=plan.batch.id,
            provider=FakeWorkProvider({
                "10.1/a": work("Fluid-structure interaction"),
            }),
        )

        write_bibliography(
            self.config.paths.collected,
            (publication("10.1/a", "Staged A"),),
        )

        status = project_init_status(self.config)

        self.assertEqual(status.queued, 0)
        self.assertEqual(status.staged, 1)
        self.assertEqual(status.merged, 0)
        self.assertEqual(status.current_batch, "batch-0001")
        self.assertFalse(status.complete)

        waiting = plan_project_init_batch(self.config)
        self.assertEqual(waiting.batch.id, "batch-0001")
        self.assertFalse(waiting.needs_screening)

    def test_status_still_rejects_incompatible_review_staging_overlap(self):
        plan = plan_project_init_batch(
            self.config,
            candidates=("10.1/a",),
        )
        apply_project_init_plan(plan)
        execute_project_init_batch(
            self.config,
            batch_id=plan.batch.id,
            provider=FakeWorkProvider({
                "10.1/a": work("Another coupled model"),
            }),
        )
        write_bibliography(
            self.config.paths.collected,
            (publication("10.1/a", "Staged A"),),
        )

        with self.assertRaisesRegex(
            ProjectStateError,
            "both review and staged project state",
        ):
            project_init_status(self.config)

    def test_current_batch_rescreen_is_read_only_until_applied(self):
        plan = plan_project_init_batch(
            self.config,
            candidates=("10.1/a", "10.1/b", "10.1/c", "10.1/d"),
            batch_size=4,
        )
        apply_project_init_plan(plan)
        execute_project_init_batch(
            self.config,
            batch_id=plan.batch.id,
            provider=FakeWorkProvider({
                "10.1/a": work("Fluid-structure interaction model"),
                "10.1/b": work("Coupled numerical model"),
                "10.1/c": work("Experimental benchmark study"),
                "10.1/d": work("Experimental fluid-structure interaction"),
            }),
        )

        self.config_path.write_text(
            CONFIG.replace(
                "  patterns:\n    - 'fluid[-\\s]+structure'\n",
                "  patterns:\n"
                "    - 'fluid[-\\s]+structure'\n"
                "    - 'coupled[-\\s]+numerical'\n"
                "  reject_patterns:\n"
                "    - 'experimental'\n",
            ),
            encoding="utf-8",
        )
        config = load_config(self.config_path)
        before = self.snapshot()
        provider = FakeWorkProvider({
            "10.1/a": work("Fluid-structure interaction model"),
            "10.1/b": work("Coupled numerical model"),
            "10.1/c": work("Experimental benchmark study"),
            "10.1/d": work("Experimental fluid-structure interaction"),
        })

        rescreen = plan_project_init_rescreen(
            config,
            provider=provider,
        )

        self.assertEqual(before, self.snapshot())
        self.assertEqual(provider.calls, ["10.1/a", "10.1/b", "10.1/c", "10.1/d"])
        self.assertEqual(rescreen.screened, 4)
        self.assertEqual(rescreen.queued, 2)
        self.assertEqual(rescreen.review, 1)
        self.assertEqual(rescreen.rejected, 1)
        self.assertEqual(rescreen.unchanged, 1)
        self.assertEqual(rescreen.retryable, 0)
        self.assertEqual(rescreen.preserved, 0)
        self.assertEqual(
            [(item.doi, item.previous, item.proposed) for item in rescreen.changes],
            [
                ("10.1/b", "review", "queued"),
                ("10.1/c", "review", "rejected"),
                ("10.1/d", "queued", "review"),
            ],
        )

        apply_project_init_rescreen(rescreen)

        self.assertEqual(
            config.paths.pending.read_text(encoding="utf-8"),
            "doi:10.1/a\ndoi:10.1/b\n",
        )
        self.assertEqual(
            config.paths.review.read_text(encoding="utf-8"),
            "doi:10.1/d\n",
        )
        self.assertEqual(
            config.paths.rejected.read_text(encoding="utf-8"),
            "doi:10.1/c\n",
        )
        status = project_init_status(config)
        self.assertEqual(status.queued, 2)
        self.assertEqual(status.review, 1)
        self.assertEqual(status.rejected, 1)
        self.assertEqual(status.current_batch, "batch-0001")

    def test_current_batch_rescreen_preserves_explicit_human_queue_move(self):
        plan = plan_project_init_batch(
            self.config,
            candidates=("10.1/a",),
        )
        apply_project_init_plan(plan)
        execute_project_init_batch(
            self.config,
            batch_id=plan.batch.id,
            provider=FakeWorkProvider({
                "10.1/a": work("Another coupled model"),
            }),
        )

        self.config.paths.review.write_text("", encoding="utf-8")
        self.config.paths.pending.write_text("doi:10.1/a\n", encoding="utf-8")
        provider = FakeWorkProvider({
            "10.1/a": work("Experimental fluid-structure interaction"),
        })

        rescreen = plan_project_init_rescreen(
            self.config,
            provider=provider,
        )

        self.assertEqual(provider.calls, [])
        self.assertEqual(rescreen.screened, 0)
        self.assertEqual(rescreen.preserved, 1)
        self.assertEqual(rescreen.changes, ())
        self.assertFalse(rescreen.changed)

    def test_status_is_derived_from_persisted_state(self):
        plan = plan_project_init_batch(
            self.config,
            candidates=("10.1/a",),
        )
        apply_project_init_plan(plan)
        execute_project_init_batch(
            self.config,
            batch_id=plan.batch.id,
            provider=FakeWorkProvider({
                "10.1/a": work("Fluid-structure interaction"),
            }),
        )

        status = project_init_status(self.config)

        self.assertEqual(status.total, 1)
        self.assertEqual(status.queued, 1)
        self.assertEqual(status.review, 0)
        self.assertEqual(status.staged, 0)
        self.assertEqual(status.merged, 0)
        self.assertFalse(status.complete)


if __name__ == "__main__":
    unittest.main()
