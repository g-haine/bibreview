from pathlib import Path
import tempfile
import unittest

from bibreview.config import load_config
from bibreview.identity import new_publication_id
from bibreview.model import Publication
from bibreview.project import apply_project_merge, plan_project_merge
from bibreview.storage import read_bibliography, write_bibliography


CONFIG = """\
schema_version: 1
project:
  name: Example Review
  slug: example-review
site:
  enabled: false
"""


class ProjectMergeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config_path = self.root / "bibreview.yml"
        self.config_path.write_text(CONFIG, encoding="utf-8")
        self.config = load_config(self.config_path)
        self.config.paths.bibliography.parent.mkdir(parents=True, exist_ok=True)

    def publication(self, doi: str | None, title: str) -> Publication:
        identifiers = {"doi": doi} if doi else {}
        return Publication(id=new_publication_id(), identifiers=identifiers, title=title)

    def snapshot(self):
        return {
            str(path.relative_to(self.root)): path.read_bytes()
            for path in self.root.rglob("*")
            if path.is_file()
        }

    def test_plan_is_read_only_and_apply_updates_project_state(self):
        existing = self.publication("10.1/old", "Old title")
        refreshed = self.publication("10.1/old", "Corrected title")
        added = self.publication("10.1/new", "New publication")
        rejected = self.publication("10.1/rejected", "Rejected publication")
        doi_less = self.publication(None, "DOI-less publication")

        write_bibliography(self.config.paths.bibliography, [existing])
        original_bibliography = self.config.paths.bibliography.read_bytes()
        write_bibliography(
            self.config.paths.collected,
            [refreshed, added, rejected, doi_less],
        )
        self.config.paths.known.write_text("10.1/old\n", encoding="utf-8")
        self.config.paths.pending.write_text(
            "10.1/new\n10.1/rejected\n10.1/waiting\n",
            encoding="utf-8",
        )
        self.config.paths.rejected.write_text("10.1/rejected\n", encoding="utf-8")
        self.config.paths.review.write_text(
            "10.1/new\n10.1/rejected\n10.1/review-later\n",
            encoding="utf-8",
        )

        before = self.snapshot()
        plan = plan_project_merge(self.config)
        self.assertEqual(before, self.snapshot())
        self.assertTrue(plan.changed)
        self.assertEqual(plan.incoming_count, 4)
        self.assertEqual(plan.rejected_count, 1)
        self.assertEqual(len(plan.result.publications), 3)
        self.assertEqual(plan.result.publications[0].id, existing.id)
        self.assertEqual(plan.result.publications[0].title, "Corrected title")
        self.assertEqual(len(plan.result.added_ids), 2)
        self.assertEqual(plan.result.updated_ids, (existing.id,))
        self.assertIsNotNone(plan.backup)

        apply_project_merge(plan)

        merged = read_bibliography(self.config.paths.bibliography)
        self.assertEqual([publication.title for publication in merged], [
            "Corrected title", "New publication", "DOI-less publication"
        ])
        self.assertEqual(read_bibliography(self.config.paths.collected), ())
        self.assertEqual(
            self.config.paths.known.read_text(encoding="utf-8"),
            "10.1/old\n10.1/new\n",
        )
        self.assertEqual(
            self.config.paths.pending.read_text(encoding="utf-8"),
            "10.1/waiting\n",
        )
        self.assertEqual(
            self.config.paths.review.read_text(encoding="utf-8"),
            "10.1/review-later\n",
        )
        self.assertEqual(plan.backup.read_bytes(), original_bibliography)

    def test_missing_or_empty_collected_state_is_a_noop(self):
        plan = plan_project_merge(self.config)
        self.assertFalse(plan.changed)
        self.assertEqual(plan.incoming_count, 0)
        self.assertIsNone(plan.backup)

        write_bibliography(self.config.paths.collected, [])
        before = self.snapshot()
        plan = plan_project_merge(self.config)
        self.assertFalse(plan.changed)
        apply_project_merge(plan)
        self.assertEqual(before, self.snapshot())

    def test_invalid_doi_state_fails_before_any_write(self):
        write_bibliography(
            self.config.paths.collected,
            [self.publication("10.1/new", "New publication")],
        )
        self.config.paths.pending.parent.mkdir(parents=True, exist_ok=True)
        self.config.paths.pending.write_text("not-a-doi\n", encoding="utf-8")
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "line 1"):
            plan_project_merge(self.config)
        self.assertEqual(before, self.snapshot())


if __name__ == "__main__":
    unittest.main()
