from contextlib import redirect_stderr, redirect_stdout
from datetime import date
from io import StringIO
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from bibreview.arxiv import ArxivError, TemporaryArxivError
from bibreview.cli import main
from bibreview.config import load_config
from bibreview.identity import new_publication_id
from bibreview.model import Author, Publication
from bibreview.storage import read_bibliography, write_bibliography, write_json


CONFIG = """\
schema_version: 1
project:
  name: Example Review
  slug: example-review
discovery:
  provider: openalex
  query: port-Hamiltonian
relevance:
  patterns:
    - 'port[-\\s]+hamiltonian'
site:
  enabled: false
"""


class FakeProvider:
    def work(self, doi):
        if doi != "10.1/new":
            return None
        return {
            "type": "journal-article",
            "title": ["New publication"],
            "container-title": ["Journal"],
            "created": {"date-parts": [[2026, 9, 17]]},
            "published-print": {"date-parts": [[2026]]},
        }


class FakeDiscoveryProvider:
    def discover(self, query, *, max_pages=20):
        return ("10.1/relevant", "10.1/review", "10.1/unsupported")


class FakeDiscoveryWorkProvider:
    def work(self, doi):
        if doi == "10.1/relevant":
            return {"type": "journal-article", "title": ["Port-Hamiltonian model"]}
        if doi == "10.1/review":
            return {"type": "journal-article", "title": ["Generic model"]}
        if doi == "10.1/unsupported":
            return {"type": "dataset", "title": ["Port-Hamiltonian data"]}
        return None


class CliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config_path = self.root / "bibreview.yml"
        self.config_path.write_text(CONFIG, encoding="utf-8")
        self.config = load_config(self.config_path)
        self.config.paths.bibliography.parent.mkdir(parents=True, exist_ok=True)

    def publication(self, doi: str, title: str) -> Publication:
        return Publication(
            id=new_publication_id(),
            identifiers={"doi": doi},
            title=title,
        )

    def snapshot(self):
        return {
            str(path.relative_to(self.root)): path.read_bytes()
            for path in self.root.rglob("*")
            if path.is_file()
        }

    def collection_services(self):
        return SimpleNamespace(
            provider=FakeProvider(),
            enrichment_lookup=None,
            citation_lookup=None,
            bibtex_lookup=lambda doi: "@article{new}\n",
        )

    def discovery_services(self):
        return SimpleNamespace(
            discovery_provider=FakeDiscoveryProvider(),
            provider=FakeDiscoveryWorkProvider(),
            enrichment_lookup=None,
        )

    def test_discover_dry_run_then_apply_updates_only_queue_state(self):
        before = self.snapshot()
        stdout = StringIO()
        stderr = StringIO()
        with patch(
            "bibreview.cli.build_discovery_services",
            return_value=self.discovery_services(),
        ), redirect_stdout(stdout), redirect_stderr(stderr):
            code = main([
                "--config", str(self.config_path),
                "--dry-run",
                "discover",
            ])
        self.assertEqual(code, 0, stderr.getvalue())
        self.assertIn("Dry run:", stdout.getvalue())
        self.assertIn("queued: 1", stdout.getvalue())
        self.assertIn("review: 1", stdout.getvalue())
        self.assertIn("rejected: 1", stdout.getvalue())
        self.assertEqual(before, self.snapshot())

        stdout = StringIO()
        stderr = StringIO()
        with patch(
            "bibreview.cli.build_discovery_services",
            return_value=self.discovery_services(),
        ), redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(["--config", str(self.config_path), "discover"])
        self.assertEqual(code, 0, stderr.getvalue())
        self.assertIn("queued: 1", stdout.getvalue())
        self.assertEqual(self.config.paths.pending.read_text(encoding="utf-8"), "10.1/relevant\n")
        self.assertEqual(self.config.paths.review.read_text(encoding="utf-8"), "10.1/review\n")
        self.assertEqual(self.config.paths.rejected.read_text(encoding="utf-8"), "10.1/unsupported\n")

    def test_discover_errors_are_reported_without_traceback(self):
        services = SimpleNamespace(
            discovery_provider=FakeDiscoveryProvider(),
            provider=FakeDiscoveryWorkProvider(),
            enrichment_lookup=lambda doi, message: "not enrichment",
        )
        stdout = StringIO()
        stderr = StringIO()
        with patch(
            "bibreview.cli.build_discovery_services",
            return_value=services,
        ), redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(["--config", str(self.config_path), "discover"])
        self.assertEqual(code, 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("bibreview discover:", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())

    def test_collect_dry_run_then_apply_uses_canonical_staging(self):
        write_bibliography(
            self.config.paths.bibliography,
            [self.publication("10.1/old", "Old publication")],
        )
        write_bibliography(self.config.paths.collected, [])
        self.config.paths.pending.write_text(
            "10.1/old\n10.1/new\n10.1/missing\n",
            encoding="utf-8",
        )
        before = self.snapshot()

        stdout = StringIO()
        stderr = StringIO()
        with patch(
            "bibreview.cli.build_collection_services",
            return_value=self.collection_services(),
        ), redirect_stdout(stdout), redirect_stderr(stderr):
            code = main([
                "--config", str(self.config_path),
                "--dry-run",
                "collect",
            ])
        self.assertEqual(code, 0, stderr.getvalue())
        self.assertIn("Dry run:", stdout.getvalue())
        self.assertIn("collected: 1", stdout.getvalue())
        self.assertEqual(before, self.snapshot())

        stdout = StringIO()
        stderr = StringIO()
        with patch(
            "bibreview.cli.build_collection_services",
            return_value=self.collection_services(),
        ), redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(["--config", str(self.config_path), "collect"])
        self.assertEqual(code, 0, stderr.getvalue())
        self.assertIn("collected: 1", stdout.getvalue())
        staged = read_bibliography(self.config.paths.collected)
        self.assertEqual(len(staged), 1)
        self.assertEqual(staged[0].doi, "10.1/new")
        self.assertEqual(
            self.config.paths.pending.read_text(encoding="utf-8"),
            "10.1/new\n10.1/missing\n",
        )
        self.assertEqual(
            (self.config.paths.bibtex / "new-publication.bib").read_text(encoding="utf-8"),
            "@article{new}\n",
        )

    def test_render_dry_run_apply_and_noop(self):
        self.config_path.write_text(
            CONFIG.replace(
                "site:\n  enabled: false\n",
                "site:\n"
                "  enabled: true\n"
                "  implementation: jekyll\n"
                "  source: site\n"
                "  jekyll:\n"
                "    category_by_type:\n"
                "      journal-article: articles\n",
            ),
            encoding="utf-8",
        )
        config = load_config(self.config_path)
        publication = Publication(
            id=new_publication_id(),
            identifiers={"doi": "10.1/render"},
            type="journal-article",
            title="Rendered publication",
            authors=(Author(given="Ada", family="Lovelace"),),
            abstract="Abstract",
            container_title="Journal",
            publication_year="2026",
            volume="1",
            issue="2",
            pages="1--9",
            publisher="Publisher",
            created_date=date(2026, 9, 18),
            permalink="rendered-publication",
        )
        write_bibliography(config.paths.bibliography, (publication,))
        write_json(
            config.paths.author_mappings,
            {"ada-lovelace": ["Ada Lovelace"]},
        )
        config.paths.bibtex.mkdir(parents=True, exist_ok=True)
        (config.paths.bibtex / "rendered-publication.bib").write_text(
            "@article{render}\n",
            encoding="utf-8",
        )

        before = self.snapshot()
        stdout = StringIO()
        stderr = StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main([
                "--config", str(self.config_path),
                "--dry-run",
                "render",
            ])
        self.assertEqual(code, 0, stderr.getvalue())
        self.assertIn("Dry run:", stdout.getvalue())
        self.assertIn("expected: 6", stdout.getvalue())
        self.assertEqual(before, self.snapshot())

        stdout = StringIO()
        stderr = StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(["--config", str(self.config_path), "render"])
        self.assertEqual(code, 0, stderr.getvalue())
        self.assertTrue(
            (
                config.site.source
                / "_posts/2026-09-18-rendered-publication.md"
            ).is_file()
        )

        stdout = StringIO()
        stderr = StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(["--config", str(self.config_path), "render"])
        self.assertEqual(code, 0, stderr.getvalue())
        self.assertIn(
            "Rendered site artifacts already up to date.",
            stdout.getvalue(),
        )

    def test_arxiv_dry_run_and_apply(self):
        self.config_path.write_text(
            CONFIG.replace(
                "  slug: example-review\n",
                "  slug: example-review\n"
                "  contact:\n"
                "    email: ada@example.org\n"
                "arxiv:\n"
                "  enabled: true\n"
                "  query: all:test\n"
                "  output: arxiv.json\n",
            ),
            encoding="utf-8",
        )
        plan = SimpleNamespace(
            changed=True,
            summary=lambda: "arXiv entries: 1; cache changed: yes",
        )

        stdout = StringIO()
        stderr = StringIO()
        with patch(
            "bibreview.cli.plan_project_arxiv",
            return_value=plan,
        ), patch(
            "bibreview.cli.apply_project_arxiv",
        ) as apply, redirect_stdout(stdout), redirect_stderr(stderr):
            code = main([
                "--config", str(self.config_path),
                "--dry-run",
                "arxiv",
            ])
        self.assertEqual(code, 0, stderr.getvalue())
        self.assertIn("Dry run: arXiv entries: 1", stdout.getvalue())
        apply.assert_not_called()

        stdout = StringIO()
        stderr = StringIO()
        with patch(
            "bibreview.cli.plan_project_arxiv",
            return_value=plan,
        ), patch(
            "bibreview.cli.apply_project_arxiv",
        ) as apply, redirect_stdout(stdout), redirect_stderr(stderr):
            code = main([
                "--config", str(self.config_path),
                "arxiv",
            ])
        self.assertEqual(code, 0, stderr.getvalue())
        self.assertIn("arXiv entries: 1", stdout.getvalue())
        apply.assert_called_once_with(plan)

    def test_arxiv_transient_failure_warns_and_keeps_success_exit(self):
        self.config_path.write_text(
            CONFIG.replace(
                "  slug: example-review\n",
                "  slug: example-review\n"
                "  contact:\n"
                "    email: ada@example.org\n"
                "arxiv:\n"
                "  enabled: true\n"
                "  query: all:test\n"
                "  output: arxiv.json\n",
            ),
            encoding="utf-8",
        )
        stdout = StringIO()
        stderr = StringIO()
        with patch(
            "bibreview.cli.plan_project_arxiv",
            side_effect=TemporaryArxivError("temporary failure"),
        ), redirect_stdout(stdout), redirect_stderr(stderr):
            code = main([
                "--config", str(self.config_path),
                "arxiv",
            ])
        self.assertEqual(code, 0)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("warning", stderr.getvalue())
        self.assertIn("keeping the existing arXiv cache unchanged", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())

    def test_arxiv_permanent_failure_is_reported(self):
        self.config_path.write_text(
            CONFIG.replace(
                "  slug: example-review\n",
                "  slug: example-review\n"
                "  contact:\n"
                "    email: ada@example.org\n"
                "arxiv:\n"
                "  enabled: true\n"
                "  query: all:test\n"
                "  output: arxiv.json\n",
            ),
            encoding="utf-8",
        )
        stdout = StringIO()
        stderr = StringIO()
        with patch(
            "bibreview.cli.plan_project_arxiv",
            side_effect=ArxivError("bad feed"),
        ), redirect_stdout(stdout), redirect_stderr(stderr):
            code = main([
                "--config", str(self.config_path),
                "arxiv",
            ])
        self.assertEqual(code, 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("bibreview arxiv: bad feed", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())

    def test_merge_dry_run_does_not_mutate_state(self):
        write_bibliography(
            self.config.paths.collected,
            [self.publication("10.1/new", "New publication")],
        )
        self.config.paths.pending.write_text("10.1/new\n", encoding="utf-8")
        before = self.snapshot()
        stdout = StringIO()
        stderr = StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main([
                "--config", str(self.config_path),
                "--dry-run",
                "merge",
            ])
        self.assertEqual(code, 0, stderr.getvalue())
        self.assertIn("Dry run:", stdout.getvalue())
        self.assertEqual(stderr.getvalue(), "")
        self.assertEqual(before, self.snapshot())

    def test_merge_applies_state_and_reports_backup(self):
        existing = self.publication("10.1/old", "Old")
        write_bibliography(self.config.paths.bibliography, [existing])
        write_bibliography(
            self.config.paths.collected,
            [self.publication("10.1/new", "New")],
        )
        self.config.paths.pending.write_text("10.1/new\n", encoding="utf-8")
        stdout = StringIO()
        stderr = StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(["--config", str(self.config_path), "merge"])
        self.assertEqual(code, 0, stderr.getvalue())
        self.assertIn("added: 1", stdout.getvalue())
        self.assertIn("Backup:", stdout.getvalue())
        self.assertEqual(stderr.getvalue(), "")
        self.assertEqual(len(read_bibliography(self.config.paths.bibliography)), 2)
        self.assertEqual(read_bibliography(self.config.paths.collected), ())

    def test_merge_rejects_invalid_state_without_traceback(self):
        write_bibliography(
            self.config.paths.collected,
            [self.publication("10.1/new", "New")],
        )
        self.config.paths.pending.write_text("not-a-doi\n", encoding="utf-8")
        stdout = StringIO()
        stderr = StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(["--config", str(self.config_path), "merge"])
        self.assertEqual(code, 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("bibreview merge:", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
