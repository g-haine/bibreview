from __future__ import annotations

from datetime import date
from pathlib import Path
import tempfile
import unittest

from bibreview.config import load_config
from bibreview.identity import new_publication_id
from bibreview.model import Publication
from bibreview.project import apply_project_merge, plan_project_merge, ProjectStateError
from bibreview.project_refresh import apply_project_refresh, plan_project_refresh
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


def message(title="Updated title"):
    return {
        "type": "journal-article",
        "title": [title],
        "container-title": ["Journal"],
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

    def publication(self, doi, *, permalink="paper", volume="", issue="", pages=""):
        return Publication(
            id=new_publication_id(),
            identifiers={"doi": doi},
            type="journal-article",
            title="Old title",
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

    def test_plan_is_read_only_and_merge_preserves_persisted_uuid(self):
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
        self.config.paths.pending.write_text("10.1/preexisting\n", encoding="utf-8")
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
        self.assertEqual(plan.result.candidates, ("10.1/stale",))
        self.assertEqual(plan.orphaned_known, ("10.1/orphaned",))
        self.assertEqual(len(plan.bibtex_backups), 1)
        self.assertTrue(plan.changed)

        apply_project_refresh(plan)
        self.assertEqual(read_bibliography(self.config.paths.bibliography)[0].id, old.id)
        staged = read_bibliography(self.config.paths.collected)
        self.assertEqual(len(staged), 1)
        self.assertEqual(staged[0].doi, "10.1/stale")
        self.assertEqual(staged[0].permalink, "stable-paper")
        self.assertNotEqual(staged[0].id, old.id)
        self.assertEqual(old_bib.read_text(encoding="utf-8"), "new bibtex\n")
        self.assertEqual(plan.bibtex_backups[0].read_text(encoding="utf-8"), "old bibtex\n")
        self.assertEqual(
            self.config.paths.known.read_text(encoding="utf-8"),
            "10.1/stale\n10.1/complete\n",
        )
        self.assertEqual(
            self.config.paths.pending.read_text(encoding="utf-8"),
            "10.1/preexisting\n10.1/orphaned\n",
        )

        merge = plan_project_merge(self.config)
        apply_project_merge(merge)
        refreshed = read_bibliography(self.config.paths.bibliography)
        refreshed_stale = next(publication for publication in refreshed if publication.doi == "10.1/stale")
        self.assertEqual(refreshed_stale.id, old.id)
        self.assertEqual(refreshed_stale.volume, "12")
        self.assertEqual(read_bibliography(self.config.paths.collected), ())

    def test_nonempty_staging_blocks_refresh_before_network_access(self):
        write_bibliography(
            self.config.paths.collected,
            [self.publication("10.1/already-staged")],
        )
        provider = FakeProvider({})
        with self.assertRaisesRegex(ProjectStateError, "merge the existing batch"):
            plan_project_refresh(
                self.config,
                provider=provider,
                bibtex_lookup=lambda doi: "new\n",
            )
        self.assertEqual(provider.calls, [])

    def test_unavailable_candidate_does_not_replace_existing_state(self):
        old = self.publication("10.1/unavailable", permalink="paper")
        write_bibliography(self.config.paths.bibliography, [old])
        write_bibliography(self.config.paths.collected, [])
        self.config.paths.known.write_text("10.1/unavailable\n", encoding="utf-8")
        self.config.paths.bibtex.mkdir(parents=True, exist_ok=True)
        target = self.config.paths.bibtex / "paper.bib"
        target.write_text("old\n", encoding="utf-8")
        before = self.snapshot()

        plan = plan_project_refresh(
            self.config,
            provider=FakeProvider({}),
            bibtex_lookup=lambda doi: "new\n",
        )
        self.assertEqual(plan.result.unavailable, ("10.1/unavailable",))
        self.assertFalse(plan.changed)
        apply_project_refresh(plan)
        self.assertEqual(before, self.snapshot())


if __name__ == "__main__":
    unittest.main()
