from pathlib import Path
import tempfile
import unittest

from bibreview.config import load_config
from bibreview.identity import new_publication_id
from bibreview.model import Author, Publication
from bibreview.project import apply_project_author_mappings, plan_project_author_mappings
from bibreview.storage import read_json, write_bibliography, write_json


CONFIG = """\
schema_version: 1
project:
  name: Example Review
  slug: example-review
site:
  enabled: false
"""


class ProjectAuthorMappingTests(unittest.TestCase):
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

    def test_plan_is_read_only_and_apply_writes_only_safe_proposals(self):
        publications = [
            Publication(
                id=new_publication_id(),
                authors=(Author(given="Ada", family="Lovelace"),),
            ),
            Publication(
                id=new_publication_id(),
                authors=(Author(given="A.", family="Lovelace"),),
            ),
            Publication(
                id=new_publication_id(),
                authors=(Author(given="Grace", family="Hopper"),),
            ),
        ]
        write_bibliography(self.config.paths.bibliography, publications)
        write_json(self.config.paths.author_mappings, {"ada-lovelace": ["Ada Lovelace"]})

        before = self.snapshot()
        inspect = plan_project_author_mappings(self.config)
        self.assertEqual(before, self.snapshot())
        self.assertFalse(inspect.changed)
        self.assertEqual(inspect.before.unknown_names, 2)

        plan = plan_project_author_mappings(self.config, apply_safe=True)
        self.assertEqual(before, self.snapshot())
        self.assertTrue(plan.changed)
        self.assertEqual(plan.applied_count, 1)
        self.assertEqual(plan.before.unknown_names, 2)
        self.assertEqual(plan.after.unknown_names, 1)
        self.assertIn("grace-hopper", plan.before.safe)
        self.assertEqual(plan.after.review[0].slug, "a-lovelace")

        apply_project_author_mappings(plan)
        mapping = read_json(self.config.paths.author_mappings, dict)
        self.assertEqual(
            mapping,
            {
                "ada-lovelace": ["Ada Lovelace"],
                "grace-hopper": ["Grace Hopper"],
            },
        )

    def test_missing_mapping_file_can_be_bootstrapped(self):
        write_bibliography(
            self.config.paths.bibliography,
            [
                Publication(
                    id=new_publication_id(),
                    authors=(Author(literal="Example Consortium"),),
                )
            ],
        )
        plan = plan_project_author_mappings(self.config, apply_safe=True)
        self.assertEqual(plan.applied_count, 1)
        self.assertTrue(plan.changed)
        apply_project_author_mappings(plan)
        self.assertEqual(
            read_json(self.config.paths.author_mappings, dict),
            {"example-consortium": ["Example Consortium"]},
        )


if __name__ == "__main__":
    unittest.main()
