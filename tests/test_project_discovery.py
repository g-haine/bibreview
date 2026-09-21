from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from bibreview.config import load_config
from bibreview.identity import new_publication_id
from bibreview.model import Author, Publication
from bibreview.project import (
    ProjectStateError,
    apply_project_discovery,
    plan_project_discovery,
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
  max_pages: 3
  accepted_types:
    - journal-article
  exclude_doi_substrings:
    - zenodo
relevance:
  patterns:
    - 'fluid[-\\s]+structure'
  unmatched: manual-review
site:
  enabled: false
"""


class FakeDiscoveryProvider:
    def __init__(self, candidates):
        self.candidates = tuple(candidates)
        self.calls = []

    def discover(self, query, *, max_pages=20):
        self.calls.append((query, max_pages))
        return self.candidates


class FakeWorkProvider:
    def __init__(self, works):
        self.works = works
        self.calls = []

    def work(self, doi):
        self.calls.append(doi)
        return self.works.get(doi)


class ProjectDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config_path = self.root / "bibreview.yml"
        self.config_path.write_text(CONFIG, encoding="utf-8")
        self.config = load_config(self.config_path)
        self.config.paths.bibliography.parent.mkdir(parents=True, exist_ok=True)

    def snapshot(self):
        return {
            str(path.relative_to(self.root)): path.read_bytes()
            for path in self.root.rglob("*")
            if path.is_file()
        }

    def test_plan_is_read_only_and_apply_updates_only_discovery_queues(self):
        existing = Publication(
            id=new_publication_id(),
            identifiers={"doi": "10.1/in-bibliography"},
            title="Existing",
            authors=(Author(literal="Example Author"),),
        )
        write_bibliography(self.config.paths.bibliography, [existing])
        self.config.paths.known.write_text("10.1/known\n", encoding="utf-8")
        self.config.paths.pending.write_text("10.1/already-pending\n", encoding="utf-8")
        self.config.paths.rejected.write_text("10.1/already-rejected\n", encoding="utf-8")
        self.config.paths.review.write_text("10.1/already-review\n", encoding="utf-8")

        discovery = FakeDiscoveryProvider([
            "10.1/known",
            "10.1/in-bibliography",
            "10.1/already-pending",
            "10.1/already-review",
            "10.1/already-rejected",
            "10.1/zenodo-record",
            "10.1/relevant",
            "10.1/review",
            "10.1/unsupported",
        ])
        works = FakeWorkProvider({
            "10.1/relevant": {
                "type": "journal-article",
                "title": ["Fluid–structure interaction model"],
            },
            "10.1/review": {
                "type": "journal-article",
                "title": ["Another coupled model"],
            },
            "10.1/unsupported": {
                "type": "dataset",
                "title": ["Fluid-structure interaction dataset"],
            },
        })
        before = self.snapshot()

        plan = plan_project_discovery(
            self.config,
            discovery_provider=discovery,
            provider=works,
        )

        self.assertEqual(before, self.snapshot())
        self.assertEqual(discovery.calls, [("fluid-structure interaction", 3)])
        self.assertEqual(
            works.calls,
            ["10.1/relevant", "10.1/review", "10.1/unsupported"],
        )
        self.assertEqual(plan.result.queued, ("10.1/relevant",))
        self.assertEqual(plan.result.review, ("10.1/review",))
        self.assertEqual(plan.result.rejected, ("10.1/unsupported",))
        self.assertTrue(plan.changed)

        apply_project_discovery(plan)

        self.assertEqual(
            self.config.paths.pending.read_text(encoding="utf-8"),
            "10.1/already-pending\n10.1/relevant\n",
        )
        self.assertEqual(
            self.config.paths.review.read_text(encoding="utf-8"),
            "10.1/already-review\n10.1/review\n",
        )
        self.assertEqual(
            self.config.paths.rejected.read_text(encoding="utf-8"),
            "10.1/already-rejected\n10.1/unsupported\n",
        )
        self.assertEqual(
            self.config.paths.bibliography.read_bytes(),
            before["data/bibliography.json"],
        )

    def test_no_candidates_is_a_noop_when_queue_files_do_not_exist(self):
        discovery = FakeDiscoveryProvider([])
        works = FakeWorkProvider({})
        before = self.snapshot()
        plan = plan_project_discovery(
            self.config,
            discovery_provider=discovery,
            provider=works,
        )
        self.assertFalse(plan.changed)
        apply_project_discovery(plan)
        self.assertEqual(before, self.snapshot())

    def test_empty_query_is_rejected_before_provider_access(self):
        empty_path = self.root / "empty.yml"
        empty_path.write_text(CONFIG.replace("query: fluid-structure interaction", "query: ''"), encoding="utf-8")
        config = load_config(empty_path)
        discovery = FakeDiscoveryProvider(["10.1/new"])
        with self.assertRaisesRegex(ProjectStateError, "discovery.query"):
            plan_project_discovery(
                config,
                discovery_provider=discovery,
                provider=FakeWorkProvider({}),
            )
        self.assertEqual(discovery.calls, [])


if __name__ == "__main__":
    unittest.main()
