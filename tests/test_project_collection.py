from pathlib import Path
import tempfile
import unittest

from bibreview.config import load_config
from bibreview.identity import new_publication_id
from bibreview.model import Author, Publication
from bibreview.project import (
    ProjectStateError,
    apply_project_collection,
    plan_project_collection,
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


class FakeProvider:
    def __init__(self, records):
        self.records = records
        self.calls = []

    def work(self, doi):
        self.calls.append(doi)
        return self.records.get(doi)


def message(title):
    return {
        "type": "journal-article",
        "title": [title],
        "container-title": ["Journal"],
        "created": {"date-parts": [[2026, 9, 17]]},
        "published-print": {"date-parts": [[2026]]},
        "author": [{"given": "Ada", "family": "Lovelace"}],
    }


class ProjectCollectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config_path = self.root / "bibreview.yml"
        self.config_path.write_text(CONFIG, encoding="utf-8")
        self.config = load_config(self.config_path)
        self.config.paths.bibliography.parent.mkdir(parents=True, exist_ok=True)

    def publication(self, doi, title, permalink):
        return Publication(
            id=new_publication_id(),
            identifiers={"doi": doi},
            title=title,
            authors=(Author(literal="Example Author"),),
            permalink=permalink,
        )

    def snapshot(self):
        return {
            str(path.relative_to(self.root)): path.read_bytes()
            for path in self.root.rglob("*")
            if path.is_file()
        }

    def test_plan_is_read_only_and_apply_writes_staging_pending_and_bibtex(self):
        existing = self.publication("10.1/old", "Old", "old")
        write_bibliography(self.config.paths.bibliography, [existing])
        write_bibliography(self.config.paths.collected, [])
        self.config.paths.known.write_text("10.1/old\n", encoding="utf-8")
        self.config.paths.pending.write_text(
            "10.1/old\n10.1/new\n10.1/missing\n10.1/NEW\n",
            encoding="utf-8",
        )
        self.config.paths.bibtex.mkdir(parents=True, exist_ok=True)
        (self.config.paths.bibtex / "old.bib").write_text("old\n", encoding="utf-8")

        provider = FakeProvider({"10.1/new": message("A new publication")})
        before = self.snapshot()
        plan = plan_project_collection(
            self.config,
            provider=provider,
            bibtex_lookup=lambda doi: f"@article{{{doi}}}\n",
        )

        self.assertEqual(before, self.snapshot())
        self.assertTrue(plan.changed)
        self.assertEqual(plan.result.candidates, ("10.1/new", "10.1/missing"))
        self.assertEqual(plan.result.unavailable, ("10.1/missing",))
        self.assertEqual(len(plan.result.items), 1)
        self.assertEqual(provider.calls, ["10.1/new", "10.1/missing"])

        apply_project_collection(plan)

        staged = read_bibliography(self.config.paths.collected)
        self.assertEqual(len(staged), 1)
        self.assertEqual(staged[0].doi, "10.1/new")
        self.assertEqual(staged[0].title, "A new publication")
        self.assertEqual(
            self.config.paths.pending.read_text(encoding="utf-8"),
            "10.1/new\n10.1/missing\n",
        )
        self.assertEqual(
            (self.config.paths.bibtex / "a-new-publication.bib").read_text(encoding="utf-8"),
            "@article{10.1/new}\n",
        )
        self.assertEqual(len(read_bibliography(self.config.paths.bibliography)), 1)

    def test_nonempty_staging_must_be_merged_before_collection(self):
        write_bibliography(
            self.config.paths.collected,
            [self.publication("10.1/staged", "Staged", "staged")],
        )
        self.config.paths.pending.write_text("10.1/new\n", encoding="utf-8")
        provider = FakeProvider({"10.1/new": message("New")})
        before = self.snapshot()

        with self.assertRaisesRegex(ProjectStateError, "merge the existing batch"):
            plan_project_collection(self.config, provider=provider)

        self.assertEqual(provider.calls, [])
        self.assertEqual(before, self.snapshot())

    def test_known_pending_values_are_removed_without_network_access(self):
        existing = self.publication("10.1/old", "Old", "old")
        write_bibliography(self.config.paths.bibliography, [existing])
        write_bibliography(self.config.paths.collected, [])
        self.config.paths.pending.write_text("10.1/OLD\n", encoding="utf-8")
        provider = FakeProvider({})

        plan = plan_project_collection(self.config, provider=provider)
        self.assertEqual(plan.result.candidates, ())
        self.assertEqual(provider.calls, [])
        self.assertTrue(plan.changed)

        apply_project_collection(plan)
        self.assertEqual(self.config.paths.pending.read_bytes(), b"")
        self.assertEqual(read_bibliography(self.config.paths.collected), ())


if __name__ == "__main__":
    unittest.main()
