from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from bibreview.config import load_config
from bibreview.identity import new_publication_id
from bibreview.model import Author, Editor, Publication
from bibreview.project import ProjectStateError
from bibreview.project_contributor_correction import (
    apply_project_contributor_correction,
    plan_project_contributor_correction,
)
from bibreview.storage import read_bibliography, write_bibliography


CONFIG = """\
schema_version: 1
project:
  name: Example Review
  slug: example-review
paths:
  bibliography: data/bibliography.json
  collected: data/collected.json
  author_mappings: data/authors.json
  known: data/known.txt
  pending: data/pending.txt
  rejected: data/rejected.txt
  review: data/review.txt
  bibtex: bib
  archive: archive
  site: site
site:
  enabled: false
"""


class ContributorCorrectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config_path = self.root / "bibreview.yml"
        self.config_path.write_text(CONFIG, encoding="utf-8")
        self.config = load_config(self.config_path)
        self.publication = Publication(
            id=new_publication_id(),
            identifiers={"doi": "10.1000/example"},
            type="journal-article",
            title="Example",
            authors=(
                Author(literal="Ada Lovelace"),
                Author(literal="Y. Wang", source_fields={"orcid": "kept"}),
            ),
            editors=(Editor(literal="E. Editor"),),
            publication_year="2026",
            permalink="example",
        )
        write_bibliography(self.config.paths.bibliography, (self.publication,))
        self.config.paths.bibtex.mkdir(parents=True, exist_ok=True)
        self.bibtex = self.config.paths.bibtex / "example.bib"
        self.bibtex.write_text(
            "@article{example,\n"
            "  title={{Example}},\n"
            "  author={Ada Lovelace and Y. Wang},\n"
            "  editor={E. Editor},\n"
            "  year={2026}\n"
            "}\n",
            encoding="utf-8",
        )

    def test_author_correction_stages_and_synchronizes_bibtex(self):
        plan = plan_project_contributor_correction(
            self.config,
            doi="10.1000/example",
            role="author",
            source="Y. Wang",
            replacement="Yongxing Wang",
        )
        self.assertEqual(plan.position, 2)
        self.assertFalse(self.config.paths.collected.exists())
        self.assertFalse(plan.bibtex_backup.exists())

        apply_project_contributor_correction(plan)

        staged = read_bibliography(self.config.paths.collected)
        self.assertEqual(staged[0].id, self.publication.id)
        self.assertEqual(staged[0].permalink, self.publication.permalink)
        self.assertEqual(staged[0].authors[0].literal, "Ada Lovelace")
        self.assertEqual(staged[0].authors[1].literal, "Yongxing Wang")
        self.assertEqual(staged[0].authors[1].source_fields["orcid"], "kept")
        self.assertIn("author={Ada Lovelace and Yongxing Wang}", self.bibtex.read_text())
        self.assertTrue(plan.bibtex_backup.exists())

    def test_editor_correction_is_supported(self):
        plan = plan_project_contributor_correction(
            self.config,
            doi="10.1000/example",
            role="editor",
            source="E. Editor",
            replacement="Edith Editor",
        )
        apply_project_contributor_correction(plan)
        staged = read_bibliography(self.config.paths.collected)
        self.assertEqual(staged[0].editors[0].literal, "Edith Editor")
        self.assertIn("editor={Edith Editor}", self.bibtex.read_text())

    def test_ambiguous_occurrence_is_refused(self):
        duplicate = Publication(
            id=self.publication.id,
            identifiers=self.publication.identifiers,
            type=self.publication.type,
            title=self.publication.title,
            authors=(Author(literal="Y. Wang"), Author(literal="Y. Wang")),
            editors=self.publication.editors,
            publication_year=self.publication.publication_year,
            permalink=self.publication.permalink,
        )
        write_bibliography(self.config.paths.bibliography, (duplicate,))
        with self.assertRaisesRegex(ProjectStateError, "occurs 2 times"):
            plan_project_contributor_correction(
                self.config,
                doi="10.1000/example",
                role="author",
                source="Y. Wang",
                replacement="Yan Wang",
            )

    def test_nonempty_staging_is_refused(self):
        write_bibliography(self.config.paths.collected, (self.publication,))
        with self.assertRaisesRegex(ProjectStateError, "merge the existing batch"):
            plan_project_contributor_correction(
                self.config,
                doi="10.1000/example",
                role="author",
                source="Y. Wang",
                replacement="Yan Wang",
            )


if __name__ == "__main__":
    unittest.main()
