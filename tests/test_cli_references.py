from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from bibreview.cli import main
from bibreview.config import load_config
from bibreview.identity import new_publication_id
from bibreview.model import Author, Publication, Reference
from bibreview.storage import write_bibliography


CONFIG = """\
schema_version: 1
project:
  name: Example Review
  slug: example-review
references:
  campaign: state/references-campaign.json
  report: state/references-report.json
  batch_size: 1
site:
  enabled: false
"""


class FakeBatchProvider:
    BATCH_SIZE = 25

    def __init__(self, messages):
        self.messages = dict(messages)
        self.calls = []

    def works(self, dois):
        self.calls.append(tuple(dois))
        return {
            doi: self.messages[doi]
            for doi in dois
            if doi in self.messages
        }


class ReferencesCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config_path = self.root / "bibreview.yml"
        self.config_path.write_text(CONFIG, encoding="utf-8")
        self.config = load_config(self.config_path)
        self.publication = Publication(
            id=new_publication_id(),
            identifiers={"doi": "10.1000/parent"},
            title="Parent",
            authors=(Author(literal="Example Author"),),
            references=(
                Reference(citation="Systems &amp; Control Letters"),
            ),
        )
        self.config.paths.bibliography.parent.mkdir(parents=True, exist_ok=True)
        write_bibliography(
            self.config.paths.bibliography,
            (self.publication,),
        )

    def run_cli(self, *args):
        stdout = StringIO()
        stderr = StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(["--config", str(self.config_path), *args])
        return code, stdout.getvalue(), stderr.getvalue()

    def snapshot(self):
        return {
            str(path.relative_to(self.root)): path.read_bytes()
            for path in self.root.rglob("*")
            if path.is_file()
        }

    def test_dry_run_does_not_create_campaign_or_call_provider(self):
        before = self.snapshot()

        with patch("bibreview.cli.build_reference_services") as services:
            code, stdout, stderr = self.run_cli(
                "--dry-run",
                "references",
                "--json",
            )

        self.assertEqual(code, 0, stderr)
        services.assert_not_called()
        payload = json.loads(stdout)
        self.assertTrue(payload["dry_run"])
        self.assertEqual(payload["batch_id"], "batch-0001")
        self.assertEqual(payload["keys"], [self.publication.id])
        self.assertEqual(before, self.snapshot())

    def test_reference_batch_uses_provider_and_writes_only_reference_state(self):
        provider = FakeBatchProvider(
            {
                "10.1000/parent": {
                    "reference": [
                        {"unstructured": "Systems &amp; Control Letters"}
                    ]
                }
            }
        )
        before_canonical = self.config.paths.bibliography.read_bytes()

        with patch(
            "bibreview.cli.build_reference_services",
            return_value=SimpleNamespace(batch_provider=provider),
        ):
            code, stdout, stderr = self.run_cli("references", "--json")

        self.assertEqual(code, 0, stderr)
        payload = json.loads(stdout)
        self.assertEqual(payload["processed"], 1)
        self.assertEqual(payload["classifications"]["safe-update"], 1)
        self.assertEqual(provider.calls, [("10.1000/parent",)])
        self.assertTrue(self.config.references.campaign.exists())
        self.assertTrue(self.config.references.report.exists())
        self.assertEqual(
            self.config.paths.bibliography.read_bytes(),
            before_canonical,
        )
        self.assertFalse(self.config.paths.collected.exists())

    def test_review_is_offline(self):
        provider = FakeBatchProvider(
            {
                "10.1000/parent": {
                    "reference": [
                        {"unstructured": "Systems &amp; Control Letters"}
                    ]
                }
            }
        )
        with patch(
            "bibreview.cli.build_reference_services",
            return_value=SimpleNamespace(batch_provider=provider),
        ):
            code, _, stderr = self.run_cli("references")
        self.assertEqual(code, 0, stderr)

        with patch("bibreview.cli.build_reference_services") as services:
            code, stdout, stderr = self.run_cli(
                "-v",
                "references",
                "--review",
            )

        self.assertEqual(code, 0, stderr)
        services.assert_not_called()
        self.assertIn("Reference refresh review", stdout)
        self.assertIn("Safe updates         : 1", stdout)
        self.assertIn("10.1000/parent", stdout)
        self.assertIn("safe-update", stdout)

    def test_double_verbose_review_shows_current_and_proposed_references(self):
        provider = FakeBatchProvider(
            {
                "10.1000/parent": {
                    "reference": [
                        {"unstructured": "Systems &amp; Control Letters"}
                    ]
                }
            }
        )
        with patch(
            "bibreview.cli.build_reference_services",
            return_value=SimpleNamespace(batch_provider=provider),
        ):
            code, _, stderr = self.run_cli("references")
        self.assertEqual(code, 0, stderr)

        with patch("bibreview.cli.build_reference_services") as services:
            code, stdout, stderr = self.run_cli(
                "-vv",
                "references",
                "--review",
            )

        self.assertEqual(code, 0, stderr)
        services.assert_not_called()
        self.assertIn("Reference diff — changed", stdout)
        self.assertIn("Current DOI : (none)", stdout)
        self.assertIn("Proposed DOI: (none)", stdout)
        self.assertIn(
            "Current     : Systems &amp; Control Letters",
            stdout,
        )
        self.assertIn(
            "Proposed    : Systems & Control Letters",
            stdout,
        )

    def test_double_verbose_review_shows_reference_doi_identifiers(self):
        self.publication = Publication(
            id=self.publication.id,
            identifiers={"doi": "10.1000/parent"},
            title="Parent",
            authors=(Author(literal="Example Author"),),
            references=(
                Reference(
                    identifiers={"doi": "10.1000/ref"},
                    citation="Old citation",
                ),
            ),
        )
        write_bibliography(
            self.config.paths.bibliography,
            (self.publication,),
        )
        provider = FakeBatchProvider(
            {
                "10.1000/parent": {
                    "reference": [
                        {
                            "DOI": "10.1000/ref",
                            "unstructured": "New citation",
                        }
                    ]
                },
                "10.1000/ref": {
                    "title": ["Reference title"],
                    "author": [{"family": "Example"}],
                    "published": {"date-parts": [[2020]]},
                    "type": "journal-article",
                },
            }
        )
        with patch(
            "bibreview.cli.build_reference_services",
            return_value=SimpleNamespace(batch_provider=provider),
        ):
            code, _, stderr = self.run_cli("references")
        self.assertEqual(code, 0, stderr)

        code, stdout, stderr = self.run_cli(
            "-vv",
            "references",
            "--review",
        )

        self.assertEqual(code, 0, stderr)
        self.assertIn("Current DOI : 10.1000/ref", stdout)
        self.assertIn("Proposed DOI: 10.1000/ref", stdout)

    def test_double_verbose_structural_review_aligns_by_doi_without_reordering_provider(self):
        self.publication = Publication(
            id=self.publication.id,
            identifiers={"doi": "10.1000/parent"},
            title="Parent",
            authors=(Author(literal="Example Author"),),
            references=(
                Reference(identifiers={"doi": "10.1/a"}, citation="A"),
                Reference(identifiers={"doi": "10.1/b"}, citation="B"),
            ),
        )
        write_bibliography(self.config.paths.bibliography, (self.publication,))
        provider = FakeBatchProvider(
            {
                "10.1000/parent": {
                    "reference": [
                        {"DOI": "10.1/new", "unstructured": "Inserted"},
                        {"DOI": "10.1/a", "unstructured": "A"},
                        {"DOI": "10.1/b", "unstructured": "B"},
                    ]
                },
                "10.1/new": {},
                "10.1/a": {},
                "10.1/b": {},
            }
        )
        with patch(
            "bibreview.cli.build_reference_services",
            return_value=SimpleNamespace(batch_provider=provider),
        ):
            code, _, stderr = self.run_cli("references")
        self.assertEqual(code, 0, stderr)

        with patch("bibreview.cli.build_reference_services") as services:
            code, stdout, stderr = self.run_cli(
                "-vv",
                "references",
                "--review",
            )

        self.assertEqual(code, 0, stderr)
        services.assert_not_called()
        self.assertIn("Reason        : reference-count-changed", stdout)
        self.assertIn("Reference diff — inserted", stdout)
        self.assertIn("Provider pos: 1", stdout)
        self.assertIn("Proposed DOI: 10.1/new", stdout)
        self.assertNotIn(
            "Current DOI : 10.1/a\n    Proposed DOI: 10.1/new",
            stdout,
        )

        payload = json.loads(self.config.references.report.read_text())
        proposed = payload["results"][0]["result"]["proposed_references"]
        self.assertEqual(
            [item["identifiers"].get("doi") for item in proposed],
            ["10.1/new", "10.1/a", "10.1/b"],
        )

    def test_double_verbose_structural_review_keeps_later_doi_anchors_after_insertion(self):
        current = (
            Reference(identifiers={"doi": "10.1/a"}, citation="A"),
            Reference(identifiers={"doi": "10.1/b"}, citation="B"),
            Reference(citation="Legacy"),
        )
        proposed = (
            Reference(identifiers={"doi": "10.1/a"}, citation="A refreshed"),
            Reference(identifiers={"doi": "10.1/b"}, citation="B refreshed"),
            Reference(citation="Inserted"),
            Reference(citation="Legacy"),
        )
        rows = project_references._review_reference_alignment(current, proposed)

        self.assertEqual(
            [
                (
                    row.kind,
                    row.current_index,
                    row.provider_index,
                    row.current.citation if row.current is not None else None,
                    row.proposed.citation if row.proposed is not None else None,
                )
                for row in rows
            ],
            [
                ("changed", 1, 1, "A", "A refreshed"),
                ("changed", 2, 2, "B", "B refreshed"),
                ("changed", 3, 3, "Legacy", "Inserted"),
                ("inserted", None, 4, None, "Legacy"),
            ],
        )

    def test_review_alignment_uses_longest_order_preserving_doi_anchors(self):
        current = (
            Reference(identifiers={"doi": "10.1/a"}, citation="A"),
            Reference(identifiers={"doi": "10.1/b"}, citation="B"),
            Reference(identifiers={"doi": "10.1/c"}, citation="C"),
            Reference(identifiers={"doi": "10.1/d"}, citation="D"),
        )
        proposed = (
            Reference(identifiers={"doi": "10.1/b"}, citation="B refreshed"),
            Reference(identifiers={"doi": "10.1/a"}, citation="A moved"),
            Reference(identifiers={"doi": "10.1/c"}, citation="C refreshed"),
            Reference(identifiers={"doi": "10.1/d"}, citation="D refreshed"),
        )
        rows = project_references._review_reference_alignment(current, proposed)

        matched_dois = [
            (
                row.current.identifiers.get("doi"),
                row.proposed.identifiers.get("doi"),
            )
            for row in rows
            if row.current is not None
            and row.proposed is not None
            and row.current.identifiers.get("doi")
            == row.proposed.identifiers.get("doi")
        ]
        self.assertEqual(
            matched_dois,
            [
                ("10.1/a", "10.1/a"),
                ("10.1/c", "10.1/c"),
                ("10.1/d", "10.1/d"),
            ],
        )

    def test_double_verbose_structural_review_marks_removed_doi(self):
        self.publication = Publication(
            id=self.publication.id,
            identifiers={"doi": "10.1000/parent"},
            title="Parent",
            authors=(Author(literal="Example Author"),),
            references=(
                Reference(identifiers={"doi": "10.1/a"}, citation="A"),
                Reference(identifiers={"doi": "10.1/b"}, citation="B"),
                Reference(identifiers={"doi": "10.1/c"}, citation="C"),
            ),
        )
        write_bibliography(self.config.paths.bibliography, (self.publication,))
        provider = FakeBatchProvider(
            {
                "10.1000/parent": {
                    "reference": [
                        {"DOI": "10.1/a", "unstructured": "A"},
                        {"DOI": "10.1/c", "unstructured": "C"},
                    ]
                },
                "10.1/a": {},
                "10.1/c": {},
            }
        )
        with patch(
            "bibreview.cli.build_reference_services",
            return_value=SimpleNamespace(batch_provider=provider),
        ):
            code, _, stderr = self.run_cli("references")
        self.assertEqual(code, 0, stderr)

        code, stdout, stderr = self.run_cli("-vv", "references", "--review")

        self.assertEqual(code, 0, stderr)
        self.assertIn("Reference diff — removed", stdout)
        self.assertIn("Current pos : 2", stdout)
        self.assertIn("Current DOI : 10.1/b", stdout)
        self.assertIn("Provider pos: -", stdout)

    def test_single_verbose_review_omits_reference_diff(self):
        provider = FakeBatchProvider(
            {
                "10.1000/parent": {
                    "reference": [
                        {"unstructured": "Systems &amp; Control Letters"}
                    ]
                }
            }
        )
        with patch(
            "bibreview.cli.build_reference_services",
            return_value=SimpleNamespace(batch_provider=provider),
        ):
            code, _, stderr = self.run_cli("references")
        self.assertEqual(code, 0, stderr)

        code, stdout, stderr = self.run_cli(
            "-v",
            "references",
            "--review",
        )

        self.assertEqual(code, 0, stderr)
        self.assertIn("Changed       : 1", stdout)
        self.assertNotIn("Reference 1", stdout)
        self.assertNotIn("Current     :", stdout)
        self.assertNotIn("Proposed    :", stdout)

    def test_review_json_contains_persisted_proposal(self):
        provider = FakeBatchProvider(
            {
                "10.1000/parent": {
                    "reference": [
                        {"unstructured": "Systems &amp; Control Letters"}
                    ]
                }
            }
        )
        with patch(
            "bibreview.cli.build_reference_services",
            return_value=SimpleNamespace(batch_provider=provider),
        ):
            code, _, stderr = self.run_cli("references")
        self.assertEqual(code, 0, stderr)

        code, stdout, stderr = self.run_cli(
            "references",
            "--review",
            "--json",
        )

        self.assertEqual(code, 0, stderr)
        payload = json.loads(stdout)
        self.assertEqual(payload["safe_updates"], 1)
        self.assertEqual(
            payload["items"][0]["proposed_references"][0]["citation"],
            "Systems & Control Letters",
        )


if __name__ == "__main__":
    unittest.main()
