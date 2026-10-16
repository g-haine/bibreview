from pathlib import Path
import tempfile
import unittest

from bibreview.config import load_config
from bibreview.model import Author
from bibreview.project import ProjectStateError
from bibreview.repair import (
    ContributorRepairState,
    contributor_evidence_fingerprint,
    contributor_repair_state_from_data,
    lookup_contributor_repair,
    record_contributor_repair,
    repair_path,
)


CONFIG = """\
schema_version: 1
project:
  name: Example Review
  slug: example-review
site:
  enabled: false
"""


def message(title="Fluid-structure interaction"):
    return {
        "title": [title],
        "type": "journal-article",
        "author": [],
        "editor": [],
        "container-title": ["Journal"],
        "created": {"date-parts": [[2024, 3, 8]]},
        "published-print": {"date-parts": [[2025, 1, 1]]},
        "volume": "7",
        "issue": "1",
        "page": "1-9",
        "publisher": "Publisher",
    }


class ContributorRepairStateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        config_path = root / "bibreview.yml"
        config_path.write_text(CONFIG, encoding="utf-8")
        self.config = load_config(config_path)

    def test_default_repair_path_is_project_data_sidecar(self):
        self.assertEqual(
            repair_path(self.config),
            self.config.source.parent / "data" / "repairs.json",
        )

    def test_record_round_trip_and_lookup(self):
        work = message()
        state = record_contributor_repair(
            ContributorRepairState(),
            doi="10.1/TEST",
            message=work,
            reason="publication must contain at least one author or editor",
            authors=(Author(given="Ada", family="Lovelace"),),
        )

        loaded = contributor_repair_state_from_data(state.data())
        repair = lookup_contributor_repair(
            loaded,
            doi="10.1/test",
            message=work,
        )

        self.assertIsNotNone(repair)
        self.assertEqual(
            [(author.given, author.family) for author in repair.authors],
            [("Ada", "Lovelace")],
        )
        self.assertEqual(repair.editors, ())
        self.assertEqual(len(state.repairs), 1)
        self.assertEqual(state.repairs[0].doi, "10.1/test")
        self.assertEqual(
            state.data()["repairs"][0]["provenance"],
            "human-reviewed",
        )
        self.assertEqual(
            state.repairs[0].evidence_fingerprint,
            contributor_evidence_fingerprint("10.1/test", work),
        )

    def test_changed_provider_evidence_requires_reconfirmation(self):
        work = message()
        state = record_contributor_repair(
            ContributorRepairState(),
            doi="10.1/test",
            message=work,
            reason="publication must contain at least one author or editor",
            authors=(Author(given="Ada", family="Lovelace"),),
        )

        changed = message("Changed title")
        with self.assertRaisesRegex(
            ProjectStateError,
            "repair evidence is stale",
        ):
            lookup_contributor_repair(
                state,
                doi="10.1/test",
                message=changed,
            )


if __name__ == "__main__":
    unittest.main()
