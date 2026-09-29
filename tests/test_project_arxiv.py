from __future__ import annotations

from datetime import date, datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest

from bibreview.arxiv import ArxivEntry
from bibreview.config import load_config
from bibreview.project_arxiv import (
    ProjectArxivError,
    apply_project_arxiv,
    build_arxiv_provider,
    plan_project_arxiv,
)


CONFIG = """schema_version: 1
project:
  name: Example Review
  slug: example-review
  repository: https://example.org/review
  contact:
    name: Ada Lovelace
    email: ada@example.org
arxiv:
  enabled: true
  query: all:fluid AND all:structure
  max_results: 25
  sort_by: lastUpdatedDate
  sort_order: descending
  output: public/arxiv.json
site:
  enabled: false
"""


class FakeArxivProvider:
    def __init__(self, *, title: str = "Fluid-structure example"):
        self.title = title

    def fetch(self):
        return (
            ArxivEntry(
                title=self.title,
                summary="Summary",
                url="https://arxiv.org/abs/2609.12345v2",
                authors=("Ada Lovelace", "Emmy Noether"),
                updated=date(2026, 9, 18),
            ),
        )


class ProjectArxivTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config_path = self.root / "bibreview.yml"
        self.config_path.write_text(CONFIG, encoding="utf-8")
        self.config = load_config(self.config_path)
        self.generated_at = datetime(
            2026, 9, 18, 9, 30, tzinfo=timezone.utc
        )

    def test_build_provider_uses_project_identity(self):
        provider = build_arxiv_provider(self.config)
        request = provider.request()

        self.assertIn("Example Review", request.get_header("User-agent"))
        self.assertIn("BibReview", request.get_header("User-agent"))
        self.assertIn(
            "https://example.org/review",
            request.get_header("User-agent"),
        )
        self.assertEqual(request.get_header("From"), "ada@example.org")

    def test_plan_and_apply_preserve_stable_json_shape(self):
        plan = plan_project_arxiv(
            self.config,
            provider=FakeArxivProvider(),
            generated_at=self.generated_at,
        )

        self.assertTrue(plan.changed)
        self.assertEqual(plan.entry_count, 1)
        self.assertFalse(plan.output.exists())

        apply_project_arxiv(plan)

        payload = json.loads(plan.output.read_text(encoding="utf-8"))
        self.assertEqual(payload["generated_at"], "2026-09-18T09:30:00Z")
        self.assertEqual(
            payload["papers"],
            [
                {
                    "title": "Fluid-structure example",
                    "summary": "Summary",
                    "url": "https://arxiv.org/abs/2609.12345v2",
                    "authors": ["Ada Lovelace", "Emmy Noether"],
                    "updated": "2026-09-18",
                }
            ],
        )

        original = plan.output.read_bytes()
        later = self.generated_at.replace(minute=31)
        second = plan_project_arxiv(
            self.config,
            provider=FakeArxivProvider(),
            generated_at=later,
        )
        self.assertFalse(second.changed)
        self.assertEqual(second.content, original)

        apply_project_arxiv(second)
        self.assertEqual(plan.output.read_bytes(), original)

    def test_real_entry_change_refreshes_timestamp(self):
        first = plan_project_arxiv(
            self.config,
            provider=FakeArxivProvider(),
            generated_at=self.generated_at,
        )
        apply_project_arxiv(first)

        later = self.generated_at.replace(minute=31)
        second = plan_project_arxiv(
            self.config,
            provider=FakeArxivProvider(title="Updated fluid-structure example"),
            generated_at=later,
        )
        self.assertTrue(second.changed)

        apply_project_arxiv(second)
        payload = json.loads(second.output.read_text(encoding="utf-8"))
        self.assertEqual(payload["generated_at"], "2026-09-18T09:31:00Z")
        self.assertEqual(
            payload["papers"][0]["title"],
            "Updated fluid-structure example",
        )

    def test_malformed_existing_cache_is_replaced(self):
        output = self.config.arxiv.output
        assert output is not None
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"not-json")

        plan = plan_project_arxiv(
            self.config,
            provider=FakeArxivProvider(),
            generated_at=self.generated_at,
        )
        self.assertTrue(plan.changed)

        apply_project_arxiv(plan)
        payload = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(payload["generated_at"], "2026-09-18T09:30:00Z")
        self.assertEqual(len(payload["papers"]), 1)

    def test_planning_is_read_only(self):
        plan = plan_project_arxiv(
            self.config,
            provider=FakeArxivProvider(),
            generated_at=self.generated_at,
        )
        self.assertTrue(plan.changed)
        self.assertFalse(plan.output.exists())

    def test_disabled_module_is_rejected(self):
        self.config_path.write_text(
            CONFIG.replace("enabled: true", "enabled: false", 1),
            encoding="utf-8",
        )
        config = load_config(self.config_path)
        with self.assertRaisesRegex(ProjectArxivError, "disabled"):
            plan_project_arxiv(
                config,
                provider=FakeArxivProvider(),
                generated_at=self.generated_at,
            )


if __name__ == "__main__":
    unittest.main()
