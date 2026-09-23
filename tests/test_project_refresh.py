from __future__ import annotations

from datetime import date
from pathlib import Path
import tempfile
import unittest

from bibreview.config import load_config
from bibreview.identity import new_publication_id
from bibreview.model import Author, Publication
from bibreview.project import ProjectStateError
from bibreview.project_refresh import (
    apply_project_refresh,
    format_project_refresh_review,
    load_project_refresh_review,
    plan_project_refresh,
    refresh_review_path,
)
from bibreview.storage import read_bibliography, write_bibliography


CONFIG = """\
schema_version: 1
project:
  name: Example Review
  slug: example-review
refresh:
  types:
    - journal-article
  when_missing_any:
    - volume
    - issue
    - pages
site:
  enabled: false
"""


class FakeProvider:
    def __init__(self, records):
        self.records = records
        self.calls = []

    def work(self, doi):
        self.calls.append(doi)
        return self.records.get(doi)


def message(title="Provider title"):
    return {
        "type": "journal-article",
        "title": [title],
        "author": [{"given": "Ada", "family": "Lovelace"}],
        "container-title": ["Provider Journal"],
        "created": {"date-parts": [[2026, 9, 17]]},
        "published-print": {"date-parts": [[2026]]},
        "volume": "12",
        "issue": "3",
        "page": "10-20",
    }


class ProjectRefreshTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config_path = self.root / "bibreview.yml"
        self.config_path.write_text(CONFIG, encoding="utf-8")
        self.config = load_config(self.config_path)
        self.config.paths.bibliography.parent.mkdir(parents=True, exist_ok=True)

    def publication(
        self,
        doi,
        *,
        permalink="paper",
        volume="",
        issue="",
        pages="",
        title="Reviewed title",
    ):
        return Publication(
            id=new_publication_id(),
            identifiers={"doi": doi},
            type="journal-article",
            title=title,
            authors=(Author(literal="Reviewed Author"),),
            container_title="Reviewed Journal",
            publication_year="2025",
            volume=volume,
            issue=issue,
            pages=pages,
            created_date=date(2025, 1, 2),
            permalink=permalink,
        )

    def snapshot(self):
        return {
            str(path.relative_to(self.root)): path.read_bytes()
            for path in self.root.rglob("*")
            if path.is_file()
        }

    def test_refresh_persists_review_only_and_never_replaces_bibtex_or_stages(self):
        old = self.publication("10.1/stale", permalink="stable-paper")
        complete = self.publication(
            "10.1/complete",
            permalink="complete-paper",
            volume="1",
            issue="2",
            pages="1--2",
        )
        write_bibliography(self.config.paths.bibliography, [old, complete])
        write_bibliography(self.config.paths.collected, [])
        self.config.paths.known.write_text(
            "10.1/stale\n10.1/complete\n10.1/orphaned\n",
            encoding="utf-8",
        )
        self.config.paths.pending.write_text(
            "10.1/preexisting\n",
            encoding="utf-8",
        )
        self.config.paths.bibtex.mkdir(parents=True, exist_ok=True)
        old_bib = self.config.paths.bibtex / "stable-paper.bib"
        old_bib.write_text("old bibtex\n", encoding="utf-8")

        provider = FakeProvider({"10.1/stale": message()})
        before = self.snapshot()
        plan = plan_project_refresh(
            self.config,
            provider=provider,
            bibtex_lookup=lambda doi: "new bibtex\n",
        )

        self.assertEqual(before, self.snapshot())
        self.assertEqual(plan.review.stale_dois, ("10.1/stale",))
        self.assertEqual(
            {proposal.field for proposal in plan.review.proposals},
            {"volume", "issue", "pages"},
        )
        self.assertIn(
            "title",
            {item.field for item in plan.review.collateral},
        )
        self.assertEqual(plan.orphaned_known, ("10.1/orphaned",))
        self.assertTrue(plan.changed)

        apply_project_refresh(plan)

        self.assertEqual(read_bibliography(self.config.paths.bibliography)[0].id, old.id)
        self.assertEqual(read_bibliography(self.config.paths.collected), ())
        self.assertEqual(old_bib.read_text(encoding="utf-8"), "old bibtex\n")
        review = load_project_refresh_review(self.config)
        self.assertEqual(review.stale_dois, ("10.1/stale",))
        self.assertEqual(
            self.config.paths.known.read_text(encoding="utf-8"),
            "10.1/stale\n10.1/complete\n",
        )
        self.assertEqual(
            self.config.paths.pending.read_text(encoding="utf-8"),
            "10.1/preexisting\n10.1/orphaned\n",
        )

    def test_verbose_review_shows_collateral_current_and_provider_values(self):
        old = self.publication("10.1/stale")
        write_bibliography(self.config.paths.bibliography, [old])
        self.config.paths.bibtex.mkdir(parents=True, exist_ok=True)
        (self.config.paths.bibtex / "paper.bib").write_text(
            "old\n",
            encoding="utf-8",
        )
        plan = plan_project_refresh(
            self.config,
            provider=FakeProvider({"10.1/stale": message()}),
            bibtex_lookup=lambda doi: "new\n",
        )
        text = format_project_refresh_review(plan.review, verbose=True)
        self.assertIn("COLLATERAL (never auto-applied): title", text)
        self.assertIn("Reviewed title", text)
        self.assertIn("Provider title", text)
        self.assertIn("SAFE MISSING FIELD: volume", text)

    def test_nonempty_staging_blocks_refresh_before_network_access(self):
        write_bibliography(
            self.config.paths.collected,
            [self.publication("10.1/already-staged")],
        )
        provider = FakeProvider({})
        with self.assertRaisesRegex(
            ProjectStateError,
            "merge the existing batch",
        ):
            plan_project_refresh(
                self.config,
                provider=provider,
                bibtex_lookup=lambda doi: "new\n",
            )
        self.assertEqual(provider.calls, [])

    def test_unavailable_candidate_writes_review_but_does_not_touch_bibtex(self):
        old = self.publication("10.1/unavailable", permalink="paper")
        write_bibliography(self.config.paths.bibliography, [old])
        write_bibliography(self.config.paths.collected, [])
        self.config.paths.known.write_text(
            "10.1/unavailable\n",
            encoding="utf-8",
        )
        self.config.paths.bibtex.mkdir(parents=True, exist_ok=True)
        target = self.config.paths.bibtex / "paper.bib"
        target.write_text("old\n", encoding="utf-8")

        plan = plan_project_refresh(
            self.config,
            provider=FakeProvider({}),
            bibtex_lookup=lambda doi: "new\n",
        )
        self.assertEqual(plan.review.unavailable, ("10.1/unavailable",))
        apply_project_refresh(plan)

        self.assertTrue(refresh_review_path(self.config).exists())
        self.assertEqual(target.read_text(encoding="utf-8"), "old\n")
        self.assertEqual(read_bibliography(self.config.paths.collected), ())


if __name__ == "__main__":
    unittest.main()
