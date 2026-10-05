from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from bibreview.cli import main
from bibreview.config import load_config
from bibreview.model import Author, Publication
from bibreview.identity import new_publication_id
from bibreview.providers.openalex import OpenAlexDiscoveryResult
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


class FakeDiscoveryProvider:
    def __init__(self, candidates):
        self.candidates = tuple(candidates)
        self.calls = []

    def discover(self, query, *, max_pages=20, search_field="title_and_abstract"):
        self.calls.append((query, max_pages, search_field))
        return self.candidates


class FakeDetailedDiscoveryProvider(FakeDiscoveryProvider):
    def __init__(self, candidates, *, total_matches, works_examined, truncated):
        super().__init__(candidates)
        self.total_matches = total_matches
        self.works_examined = works_examined
        self.truncated = truncated

    def discover_detailed(
        self,
        query,
        *,
        max_pages=20,
        search_field="title_and_abstract",
    ):
        self.calls.append((query, max_pages, search_field))
        return OpenAlexDiscoveryResult(
            candidates=self.candidates,
            total_matches=self.total_matches,
            pages_fetched=max_pages,
            works_examined=self.works_examined,
            truncated=self.truncated,
        )


class FakeWorkProvider:
    def __init__(self, records):
        self.records = dict(records)
        self.calls = []

    def work(self, doi):
        self.calls.append(doi)
        return self.records.get(doi)


def work(title):
    return {
        "type": "journal-article",
        "title": [title],
        "author": [{"given": "Ada", "family": "Lovelace"}],
        "container-title": ["Journal"],
        "created": {"date-parts": [[2026, 10, 1]]},
        "published-print": {"date-parts": [[2026]]},
        "reference": [],
    }


class InitCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config_path = self.root / "bibreview.yml"
        self.config_path.write_text(CONFIG, encoding="utf-8")
        self.config = load_config(self.config_path)

    def services(self):
        return SimpleNamespace(
            discovery_provider=FakeDiscoveryProvider(("10.1/a", "10.1/b")),
            provider=FakeWorkProvider({
                "10.1/a": work("Fluid-structure interaction model"),
                "10.1/b": work("Another coupled model"),
            }),
            enrichment_lookup=None,
        )

    def snapshot(self):
        return {
            str(path.relative_to(self.root)): path.read_bytes()
            for path in self.root.rglob("*")
            if path.is_file()
        }

    def test_dry_run_freezes_candidate_plan_without_writing_or_screening(self):
        services = self.services()
        stdout = StringIO()
        stderr = StringIO()

        with patch(
            "bibreview.cli.build_discovery_services",
            return_value=services,
        ), redirect_stdout(stdout), redirect_stderr(stderr):
            code = main([
                "--config",
                str(self.config_path),
                "--dry-run",
                "init",
                "--batch-size",
                "1",
                "--json",
            ])

        self.assertEqual(code, 0, stderr.getvalue())
        payload = json.loads(stdout.getvalue())
        self.assertTrue(payload["dry_run"])
        self.assertEqual(payload["batch_id"], "batch-0001")
        self.assertEqual(payload["keys"], ["10.1/a"])
        self.assertTrue(payload["needs_screening"])
        self.assertEqual(
            services.discovery_provider.calls,
            [("fluid-structure interaction", 3, "title_and_abstract")],
        )
        self.assertEqual(services.provider.calls, [])
        self.assertFalse(self.config.initialization.campaign.exists())
        self.assertFalse(self.config.initialization.report.exists())

    def test_dry_run_text_reports_only_candidates_that_need_screening(self):
        services = self.services()
        stdout = StringIO()
        stderr = StringIO()

        with patch(
            "bibreview.cli.build_discovery_services",
            return_value=services,
        ), redirect_stdout(stdout), redirect_stderr(stderr):
            code = main([
                "--config",
                str(self.config_path),
                "--dry-run",
                "init",
                "--batch-size",
                "1",
            ])

        self.assertEqual(code, 0, stderr.getvalue())
        self.assertIn(
            "Would screen 1 candidate(s) in batch-0001.",
            stdout.getvalue(),
        )
        self.assertNotIn("Would initialize", stdout.getvalue())

    def test_dry_run_text_reports_already_screened_open_batch(self):
        services = self.services()
        with patch(
            "bibreview.cli.build_discovery_services",
            return_value=services,
        ), redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            self.assertEqual(
                main([
                    "--config",
                    str(self.config_path),
                    "init",
                    "--batch-size",
                    "1",
                ]),
                0,
            )

        stdout = StringIO()
        stderr = StringIO()
        with patch(
            "bibreview.cli.build_discovery_services",
            side_effect=AssertionError("provider services must not be built"),
        ), redirect_stdout(stdout), redirect_stderr(stderr):
            code = main([
                "--config",
                str(self.config_path),
                "--dry-run",
                "init",
                "--batch-size",
                "1",
            ])

        self.assertEqual(code, 0, stderr.getvalue())
        self.assertIn(
            "Current initialization batch batch-0001 is already screened; "
            "no provider screening would run.",
            stdout.getvalue(),
        )
        self.assertNotIn("Would initialize", stdout.getvalue())

    def test_rescreen_current_dry_run_uses_providers_without_mutating_state(self):
        services = self.services()
        with patch(
            "bibreview.cli.build_discovery_services",
            return_value=services,
        ), redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            self.assertEqual(
                main([
                    "--config",
                    str(self.config_path),
                    "init",
                    "--batch-size",
                    "2",
                ]),
                0,
            )

        self.config_path.write_text(
            CONFIG.replace(
                "  unmatched: manual-review\n",
                "  reject_patterns:\n"
                "    - 'another[-\\s]+coupled'\n"
                "  unmatched: manual-review\n",
            ),
            encoding="utf-8",
        )
        before = self.snapshot()
        rescreen_services = self.services()
        stdout = StringIO()
        stderr = StringIO()

        with patch(
            "bibreview.cli.build_discovery_services",
            return_value=rescreen_services,
        ), redirect_stdout(stdout), redirect_stderr(stderr):
            code = main([
                "--config",
                str(self.config_path),
                "--dry-run",
                "init",
                "--rescreen-current",
                "--json",
            ])

        self.assertEqual(code, 0, stderr.getvalue())
        self.assertEqual(before, self.snapshot())
        self.assertEqual(
            rescreen_services.discovery_provider.calls,
            [],
        )
        self.assertEqual(
            rescreen_services.provider.calls,
            ["10.1/a", "10.1/b"],
        )
        payload = json.loads(stdout.getvalue())
        self.assertTrue(payload["dry_run"])
        self.assertEqual(payload["batch_id"], "batch-0001")
        self.assertEqual(payload["screened"], 2)
        self.assertEqual(payload["rejected"], 1)
        self.assertEqual(
            payload["changes"],
            [
                {
                    "doi": "10.1/b",
                    "previous": "review",
                    "proposed": "rejected",
                }
            ],
        )

    def test_dry_run_reports_openalex_discovery_diagnostics(self):
        services = SimpleNamespace(
            discovery_provider=FakeDetailedDiscoveryProvider(
                ("10.1/a", "10.1/b"),
                total_matches=12345,
                works_examined=600,
                truncated=True,
            ),
            provider=FakeWorkProvider({}),
            enrichment_lookup=None,
        )
        stdout = StringIO()
        stderr = StringIO()

        with patch(
            "bibreview.cli.build_discovery_services",
            return_value=services,
        ), redirect_stdout(stdout), redirect_stderr(stderr):
            code = main([
                "--config",
                str(self.config_path),
                "--dry-run",
                "init",
                "--batch-size",
                "1",
                "--json",
            ])

        self.assertEqual(code, 0, stderr.getvalue())
        payload = json.loads(stdout.getvalue())
        self.assertEqual(
            payload["discovery"],
            {
                "total_matches": 12345,
                "pages_fetched": 3,
                "works_examined": 600,
                "doi_candidates": 2,
                "truncated": True,
            },
        )
        self.assertEqual(payload["progress"]["total"], 2)

    def test_dry_run_excludes_configured_doi_substrings_from_campaign_universe(self):
        self.config_path.write_text(
            CONFIG.replace(
                "relevance:\n",
                "  exclude_doi_substrings:\n"
                "    - zenodo\n"
                "relevance:\n",
            ),
            encoding="utf-8",
        )
        services = SimpleNamespace(
            discovery_provider=FakeDiscoveryProvider((
                "10.5281/zenodo.12345",
                "10.1/a",
                "10.1/b",
            )),
            provider=FakeWorkProvider({}),
            enrichment_lookup=None,
        )
        stdout = StringIO()
        stderr = StringIO()

        with patch(
            "bibreview.cli.build_discovery_services",
            return_value=services,
        ), redirect_stdout(stdout), redirect_stderr(stderr):
            code = main([
                "--config",
                str(self.config_path),
                "--dry-run",
                "init",
                "--batch-size",
                "2",
                "--json",
            ])

        self.assertEqual(code, 0, stderr.getvalue())
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["keys"], ["10.1/a", "10.1/b"])
        self.assertEqual(payload["progress"]["total"], 2)
        self.assertFalse(self.config.initialization.campaign.exists())
        self.assertFalse(self.config.initialization.report.exists())

    def test_init_screens_one_batch_then_waits_without_rebuilding_services(self):
        services = self.services()
        stdout = StringIO()
        stderr = StringIO()
        with patch(
            "bibreview.cli.build_discovery_services",
            return_value=services,
        ), redirect_stdout(stdout), redirect_stderr(stderr):
            code = main([
                "--config",
                str(self.config_path),
                "init",
                "--batch-size",
                "1",
            ])

        self.assertEqual(code, 0, stderr.getvalue())
        self.assertEqual(services.provider.calls, ["10.1/a"])
        self.assertTrue(self.config.initialization.campaign.exists())
        self.assertEqual(
            self.config.paths.pending.read_text(encoding="utf-8"),
            "doi:10.1/a\n",
        )
        self.assertIn("Resolve the current batch", stdout.getvalue())

        stdout = StringIO()
        stderr = StringIO()
        with patch(
            "bibreview.cli.build_discovery_services",
            side_effect=AssertionError("provider services must not be built"),
        ), redirect_stdout(stdout), redirect_stderr(stderr):
            code = main([
                "--config",
                str(self.config_path),
                "init",
            ])

        self.assertEqual(code, 0, stderr.getvalue())
        self.assertIn("waiting for ordinary review/collect/merge", stdout.getvalue())

    def test_status_is_offline_json(self):
        services = self.services()
        with patch(
            "bibreview.cli.build_discovery_services",
            return_value=services,
        ), redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            self.assertEqual(
                main([
                    "--config",
                    str(self.config_path),
                    "init",
                    "--batch-size",
                    "1",
                ]),
                0,
            )

        stdout = StringIO()
        stderr = StringIO()
        with patch(
            "bibreview.cli.build_discovery_services",
            side_effect=AssertionError("status must be offline"),
        ), redirect_stdout(stdout), redirect_stderr(stderr):
            code = main([
                "--config",
                str(self.config_path),
                "init",
                "--status",
                "--json",
            ])

        self.assertEqual(code, 0, stderr.getvalue())
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["total"], 2)
        self.assertEqual(payload["queued"], 1)
        self.assertEqual(payload["current_batch"], "batch-0001")

    def test_merged_batch_allows_next_stable_batch(self):
        services = self.services()
        with patch(
            "bibreview.cli.build_discovery_services",
            return_value=services,
        ), redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            self.assertEqual(
                main([
                    "--config",
                    str(self.config_path),
                    "init",
                    "--batch-size",
                    "1",
                ]),
                0,
            )

        canonical = Publication(
            id=new_publication_id(),
            identifiers={"doi": "10.1/a"},
            type="journal-article",
            title="Reviewed A",
            authors=(Author(literal="Reviewed Author"),),
            publication_year="2026",
            permalink="reviewed-a",
        )
        write_bibliography(self.config.paths.bibliography, (canonical,))
        self.config.paths.pending.write_text("", encoding="utf-8")

        second_services = SimpleNamespace(
            discovery_provider=FakeDiscoveryProvider(()),
            provider=FakeWorkProvider({
                "10.1/b": work("Unrelated title"),
            }),
            enrichment_lookup=None,
        )
        stdout = StringIO()
        stderr = StringIO()
        with patch(
            "bibreview.cli.build_discovery_services",
            return_value=second_services,
        ), redirect_stdout(stdout), redirect_stderr(stderr):
            code = main([
                "--config",
                str(self.config_path),
                "init",
            ])

        self.assertEqual(code, 0, stderr.getvalue())
        self.assertEqual(second_services.discovery_provider.calls, [])
        self.assertEqual(second_services.provider.calls, ["10.1/b"])
        self.assertEqual(
            self.config.paths.review.read_text(encoding="utf-8"),
            "doi:10.1/b\n",
        )


if __name__ == "__main__":
    unittest.main()
