from __future__ import annotations

from datetime import date
from pathlib import Path
import tempfile
import unittest

from bibreview.config import load_config
from bibreview.identity import new_publication_id
from bibreview.model import Author, Publication
from bibreview.project_render import (
    ProjectRenderError,
    apply_project_render,
    plan_project_render,
)
from bibreview.storage import BibliographyMetadata, write_bibliography, write_json


CONFIG = """\
schema_version: 1
project:
  name: Example Review
  slug: example-review
paths:
  bibliography: data/bibliography.json
  author_mappings: data/authors.json
  bibtex: bib
  site: site
site:
  enabled: true
  implementation: jekyll
  source: site
  jekyll:
    include_authorless_year_publications: false
    author_index_extra_html: |
      <p>Project note.</p>
      <hr />
    category_by_type:
      journal-article: articles
      proceedings-article: proceedings
      book-chapter: chapters
      book: books
      monograph: books
    event_category_rules:
      - pattern: 'Conference|Workshop'
        category: proceedings
    isbn_types:
      - book
      - book-chapter
      - monograph
"""


class ProjectRenderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config_path = self.root / "bibreview.yml"
        self.config_path.write_text(CONFIG, encoding="utf-8")
        self.config = load_config(self.config_path)

        publication = Publication(
            id=new_publication_id(),
            identifiers={"doi": "10.1/example"},
            type="journal-article",
            title="Port-Hamiltonian example",
            authors=(Author(given="Ada", family="Lovelace"),),
            abstract="An abstract.",
            container_title="Journal",
            publication_year="2026",
            volume="1",
            issue="2",
            pages="1--9",
            publisher="Publisher",
            keywords=("control", "energy"),
            created_date=date(2026, 9, 18),
            permalink="port-hamiltonian-example",
        )
        self.publication = publication
        self.config.paths.bibliography.parent.mkdir(parents=True, exist_ok=True)
        write_bibliography(
            self.config.paths.bibliography,
            (publication,),
            metadata=BibliographyMetadata(
                last_update=date(2026, 9, 11),
            ),
        )
        write_json(
            self.config.paths.author_mappings,
            {"ada-lovelace": ["Ada Lovelace"]},
        )
        self.config.paths.bibtex.mkdir(parents=True, exist_ok=True)
        (
            self.config.paths.bibtex / "port-hamiltonian-example.bib"
        ).write_text("@article{example}\n", encoding="utf-8")

    def snapshot(self) -> dict[str, bytes]:
        return {
            str(path.relative_to(self.root)): path.read_bytes()
            for path in self.root.rglob("*")
            if path.is_file()
        }

    def test_plan_and_apply_complete_jekyll_site(self):
        site = self.config.site.source
        self.assertIsNotNone(site)
        assert site is not None
        (site / "_posts").mkdir(parents=True)
        (site / "_posts/obsolete.md").write_text("obsolete", encoding="utf-8")
        (site / "manual.md").write_text("manual", encoding="utf-8")
        orphan = self.config.paths.bibtex / "unused.bib"
        orphan.write_text("@misc{unused}\n", encoding="utf-8")

        plan = plan_project_render(self.config)

        self.assertTrue(plan.changed)
        self.assertEqual(plan.persistence.expected_count, 6)
        self.assertEqual(plan.orphan_bibtex, (orphan,))
        self.assertIn(site / "_posts/obsolete.md", plan.persistence.deletes)

        apply_project_render(plan)

        self.assertTrue(
            (
                site
                / "_posts/2026-09-18-port-hamiltonian-example.md"
            ).is_file()
        )
        self.assertTrue((site / "authors/ada-lovelace.md").is_file())
        self.assertTrue((site / "authors/index.md").is_file())
        self.assertTrue((site / "years/2026.md").is_file())
        self.assertTrue((site / "years/index.md").is_file())
        self.assertEqual(
            (site / "_data/bibreview/metadata.json").read_text(
                encoding="utf-8"
            ),
            '{\n  "schema_version": 1,\n  "last_update": "2026-09-11"\n}\n',
        )
        self.assertFalse((site / "_posts/obsolete.md").exists())
        self.assertEqual((site / "manual.md").read_text(), "manual")
        self.assertTrue(orphan.exists())

        final = plan_project_render(self.config)
        self.assertFalse(final.changed)
        self.assertEqual(final.persistence.unchanged_count, 6)

    def test_planning_is_read_only(self):
        before = self.snapshot()
        plan = plan_project_render(self.config)
        self.assertTrue(plan.changed)
        self.assertEqual(before, self.snapshot())

    def test_missing_bibtex_is_rejected_before_site_mutation(self):
        (
            self.config.paths.bibtex / "port-hamiltonian-example.bib"
        ).unlink()
        before = self.snapshot()
        with self.assertRaisesRegex(ProjectRenderError, "missing:"):
            plan_project_render(self.config)
        self.assertEqual(before, self.snapshot())

    def test_disabled_site_is_rejected(self):
        self.config_path.write_text(
            CONFIG.replace("enabled: true", "enabled: false", 1),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ProjectRenderError, "disabled"):
            plan_project_render(load_config(self.config_path))


if __name__ == "__main__":
    unittest.main()
