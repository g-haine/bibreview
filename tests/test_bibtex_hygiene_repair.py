from pathlib import Path
import tempfile
import unittest

from bibreview.bibtex_hygiene_repair import (
    apply_project_bibtex_repair,
    load_bibtex_repair_state,
    plan_project_bibtex_repair,
    project_bibtex_repair_review,
    record_bibtex_replacement,
    save_bibtex_repair_state,
)
from bibreview.config import load_config
from bibreview.identity import new_publication_id
from bibreview.model import Author, Publication
from bibreview.storage import read_bibliography, write_bibliography


CONFIG = """\
schema_version: 1
project:
  name: Example Review
  slug: example-review
site:
  enabled: false
"""


class BibtexHygieneRepairTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config_path = self.root / "bibreview.yml"
        self.config_path.write_text(CONFIG, encoding="utf-8")
        self.config = load_config(self.config_path)
        self.config.paths.bibliography.parent.mkdir(parents=True, exist_ok=True)
        self.config.paths.bibtex.mkdir(parents=True, exist_ok=True)

    def _publication(self, slug: str, doi: str, author: str = "Ada Lovelace"):
        return Publication(
            id=new_publication_id(),
            identifiers={"doi": doi},
            title=slug,
            authors=(Author(literal=author),),
            permalink=slug,
        )

    def test_empty_and_and_missing_author_are_deterministic_and_preserve_canon(self):
        malformed = self._publication("malformed", "10.1/malformed", "Eman Alhayki")
        missing = self._publication("missing", "10.1/missing", "André Garon")
        write_bibliography(
            self.config.paths.bibliography,
            (malformed, missing),
        )
        malformed_path = self.config.paths.bibtex / "malformed.bib"
        missing_path = self.config.paths.bibtex / "missing.bib"
        malformed_path.write_text(
            """@article{malformed,
  author={ and Alhayki Eman},
  title={{Malformed}},
  year={2024}
}
""",
            encoding="utf-8",
        )
        missing_path.write_text(
            """@article{missing,
  title={{Missing}},
  year={2024}
}
""",
            encoding="utf-8",
        )
        canonical_before = self.config.paths.bibliography.read_bytes()

        review = project_bibtex_repair_review(self.config)

        self.assertEqual(len(review.deterministic), 2)
        self.assertEqual(len(review.review_required), 0)

        plan = plan_project_bibtex_repair(self.config, safe_only=True)
        self.assertEqual(len(plan.changed_paths), 2)
        self.assertEqual(
            self.config.paths.bibliography.read_bytes(),
            canonical_before,
        )
        apply_project_bibtex_repair(plan)

        self.assertIn(
            "author={Alhayki Eman}",
            malformed_path.read_text(encoding="utf-8"),
        )
        self.assertIn(
            "author={André Garon}",
            missing_path.read_text(encoding="utf-8"),
        )
        self.assertIn(
            "year={2024}",
            missing_path.read_text(encoding="utf-8"),
        )
        self.assertEqual(
            self.config.paths.bibliography.read_bytes(),
            canonical_before,
        )
        self.assertEqual(len(plan.backups), 2)
        self.assertTrue(all(path.exists() for path in plan.backups))

    def test_unterminated_bibtex_requires_reviewed_replacement(self):
        publication = self._publication("invalid", "10.1/invalid")
        write_bibliography(self.config.paths.bibliography, (publication,))
        path = self.config.paths.bibtex / "invalid.bib"
        path.write_text(
            """@article{invalid,
  author={Ada Lovelace},
  title={{Invalid}
""",
            encoding="utf-8",
        )

        review = project_bibtex_repair_review(self.config)
        self.assertEqual(len(review.deterministic), 0)
        self.assertEqual(len(review.review_required), 1)

        with self.assertRaisesRegex(ValueError, "still require review"):
            plan_project_bibtex_repair(self.config, safe_only=False)

        state = load_bibtex_repair_state(self.config)
        state = record_bibtex_replacement(
            state,
            review.review_required[0],
            """@article{invalid,
  author={Ada Lovelace},
  title={{Invalid}},
  year={2024}
}
""",
        )
        save_bibtex_repair_state(self.config, state)

        plan = plan_project_bibtex_repair(self.config, safe_only=False)
        apply_project_bibtex_repair(plan)
        repaired = path.read_text(encoding="utf-8")
        self.assertIn("title={{Invalid}}", repaired)
        self.assertIn("year={2024}", repaired)

    def test_multiple_entries_require_review_and_are_never_guessed(self):
        publication = self._publication("multiple", "10.1/multiple")
        write_bibliography(self.config.paths.bibliography, (publication,))
        path = self.config.paths.bibtex / "multiple.bib"
        path.write_text(
            """@article{one,
  author={Ada Lovelace},
  title={{One}}
}
@article{two,
  author={Ada Lovelace},
  title={{Two}}
}
""",
            encoding="utf-8",
        )

        review = project_bibtex_repair_review(self.config)
        self.assertEqual(len(review.review_required), 1)
        self.assertIn(
            "exactly one entry",
            review.review_required[0].reason,
        )

        safe_plan = plan_project_bibtex_repair(self.config, safe_only=True)
        self.assertFalse(safe_plan.changed)
        self.assertEqual(
            path.read_text(encoding="utf-8").count("@article"),
            2,
        )


if __name__ == "__main__":
    unittest.main()
