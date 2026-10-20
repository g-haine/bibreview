from pathlib import Path
import tempfile
import unittest

from bibreview.author_distinct import load_reviewed_distinct_state
from bibreview.config import load_config
from bibreview.identity import new_publication_id
from bibreview.model import Author, Publication
from bibreview.project_identity_hygiene import (
    apply_project_author_distinct,
    apply_project_author_identity_mutation,
    apply_project_author_variant_removal,
    plan_project_author_distinct,
    plan_project_author_identity_mutation,
    plan_project_author_variant_removal,
    project_author_identity_hygiene,
)
from bibreview.storage import read_json, write_bibliography, write_json


CONFIG = """\
schema_version: 1
project:
  name: Example Review
  slug: example-review
site:
  enabled: false
"""


class ProjectAuthorIdentityDecisionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config_path = self.root / "bibreview.yml"
        self.config_path.write_text(CONFIG, encoding="utf-8")
        self.config = load_config(self.config_path)
        self.config.paths.bibliography.parent.mkdir(parents=True, exist_ok=True)

    def write_mapping(self, value):
        write_json(self.config.paths.author_mappings, value)

    def write_publication(self, name="Ada Lovelace"):
        write_bibliography(
            self.config.paths.bibliography,
            (
                Publication(
                    id=new_publication_id(),
                    identifiers={"doi": "10.1/example"},
                    title="Example",
                    authors=(Author(literal=name),),
                ),
            ),
        )

    def test_unused_variant_can_be_removed_but_used_variant_is_refused(self):
        self.write_mapping(
            {"yongxing-wang": ["Yongxing Wang", "Y. Wang"]}
        )
        self.write_publication("Yongxing Wang")

        plan = plan_project_author_variant_removal(
            self.config,
            slug="yongxing-wang",
            variant="Y. Wang",
        )
        self.assertIn(
            "Y. Wang",
            read_json(self.config.paths.author_mappings, dict)["yongxing-wang"],
        )
        apply_project_author_variant_removal(plan)
        self.assertEqual(
            read_json(self.config.paths.author_mappings, dict)["yongxing-wang"],
            ["Yongxing Wang"],
        )

        self.write_mapping(
            {"yongxing-wang": ["Yongxing Wang", "Y. Wang"]}
        )
        self.write_publication("Y. Wang")
        with self.assertRaisesRegex(ValueError, "still used by 1 canonical"):
            plan_project_author_variant_removal(
                self.config,
                slug="yongxing-wang",
                variant="Y. Wang",
            )

    def test_reviewed_distinct_pair_is_suppressed_and_stale_evidence_reappears(self):
        self.write_mapping(
            {
                "a-zhang": ["A Zhang"],
                "alvin-zhang": ["Alvin Zhang"],
            }
        )
        self.write_publication()

        before = project_author_identity_hygiene(self.config)
        self.assertEqual(before.initial_full_candidates, 1)

        plan = plan_project_author_distinct(
            self.config,
            left_slug="alvin-zhang",
            right_slug="a-zhang",
        )
        apply_project_author_distinct(plan)

        reviewed = project_author_identity_hygiene(self.config)
        self.assertEqual(reviewed.initial_full_candidates, 0)
        self.assertEqual(reviewed.reviewed_distinct, 1)
        self.assertEqual(reviewed.stale_reviewed_distinct, 0)

        self.write_mapping(
            {
                "a-zhang": ["A Zhang", "A. Zhang"],
                "alvin-zhang": ["Alvin Zhang"],
            }
        )
        stale = project_author_identity_hygiene(self.config)
        self.assertEqual(stale.initial_full_candidates, 1)
        self.assertEqual(stale.reviewed_distinct, 0)
        self.assertEqual(stale.stale_reviewed_distinct, 1)

    def test_rename_reconciles_and_merge_drops_affected_distinct_decisions(self):
        self.write_mapping(
            {
                "a-zhang": ["A Zhang"],
                "alvin-zhang": ["Alvin Zhang"],
            }
        )
        self.write_publication()
        apply_project_author_distinct(
            plan_project_author_distinct(
                self.config,
                left_slug="a-zhang",
                right_slug="alvin-zhang",
            )
        )

        rename = plan_project_author_identity_mutation(
            self.config,
            operation="rename",
            source_slug="a-zhang",
            target_slug="anonymous-zhang",
        )
        apply_project_author_identity_mutation(rename)
        renamed = project_author_identity_hygiene(self.config)
        self.assertEqual(renamed.initial_full_candidates, 0)
        self.assertEqual(renamed.reviewed_distinct, 1)

        merge = plan_project_author_identity_mutation(
            self.config,
            operation="merge",
            source_slug="anonymous-zhang",
            target_slug="alvin-zhang",
        )
        apply_project_author_identity_mutation(merge)
        self.assertEqual(
            load_reviewed_distinct_state(self.config).decisions,
            (),
        )


if __name__ == "__main__":
    unittest.main()
