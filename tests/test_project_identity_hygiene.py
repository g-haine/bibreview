from pathlib import Path
import tempfile
import unittest

from bibreview.config import load_config
from bibreview.identity import new_publication_id
from bibreview.model import Author, Editor, Publication
from bibreview.project_identity_hygiene import (
    apply_project_author_identity_mutation,
    plan_project_author_identity_mutation,
    project_author_identity_hygiene,
    project_bibtex_contributor_hygiene,
)
from bibreview.storage import read_json, write_bibliography


CONFIG = """\
schema_version: 1
project:
  name: Example Review
  slug: example-review
site:
  enabled: false
"""


class ProjectIdentityHygieneTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config_path = self.root / "bibreview.yml"
        self.config_path.write_text(CONFIG, encoding="utf-8")
        self.config = load_config(self.config_path)
        self.config.paths.bibliography.parent.mkdir(parents=True, exist_ok=True)
        self.config.paths.bibtex.mkdir(parents=True, exist_ok=True)

    def test_bibtex_hygiene_detects_missing_author_field(self):
        publication = Publication(
            id=new_publication_id(),
            identifiers={"doi": "10.1/example"},
            title="Example",
            authors=(Author(given="Ada", family="Lovelace"),),
            permalink="example",
        )
        write_bibliography(self.config.paths.bibliography, (publication,))
        (self.config.paths.bibtex / "example.bib").write_text(
            """@article{example,
  title={{Example}},
  DOI={10.1/example}
}
""",
            encoding="utf-8",
        )

        report = project_bibtex_contributor_hygiene(self.config)

        self.assertEqual(report.missing_fields, 1)
        finding = report.findings[0]
        self.assertEqual(finding.field, "author")
        self.assertEqual(finding.canonical_value, "Ada Lovelace")

    def test_bibtex_hygiene_accepts_last_first_equivalent_name(self):
        publication = Publication(
            id=new_publication_id(),
            identifiers={"doi": "10.1/example"},
            title="Example",
            authors=(Author(given="Ada", family="Lovelace"),),
            editors=(Editor(given="Grace", family="Hopper"),),
            permalink="example",
        )
        write_bibliography(self.config.paths.bibliography, (publication,))
        (self.config.paths.bibtex / "example.bib").write_text(
            """@article{example,
  author={Lovelace, Ada},
  editor={Hopper, Grace},
  title={{Example}},
  DOI={10.1/example}
}
""",
            encoding="utf-8",
        )

        report = project_bibtex_contributor_hygiene(self.config)
        self.assertEqual(report.findings, ())

    def test_project_author_hygiene_and_explicit_rename_are_separate(self):
        self.config.paths.author_mappings.write_text(
            """{
  "m-heck": ["M. Heck", "Marcus Heck"]
}
""",
            encoding="utf-8",
        )

        report = project_author_identity_hygiene(self.config)
        self.assertEqual(report.weak_slugs, 1)

        plan = plan_project_author_identity_mutation(
            self.config,
            operation="rename",
            source_slug="m-heck",
            target_slug="marcus-heck",
        )
        self.assertTrue(plan.changed)
        self.assertIn(
            "m-heck",
            read_json(self.config.paths.author_mappings, dict),
        )

        apply_project_author_identity_mutation(plan)
        mapping = read_json(self.config.paths.author_mappings, dict)
        self.assertNotIn("m-heck", mapping)
        self.assertEqual(
            mapping["marcus-heck"],
            ["M. Heck", "Marcus Heck"],
        )


if __name__ == "__main__":
    unittest.main()
