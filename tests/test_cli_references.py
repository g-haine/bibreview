from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
from dataclasses import replace
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from bibreview.cli import main
from bibreview.config import load_config
from bibreview.identity import new_publication_id
from bibreview.model import Author, Publication, Reference
from bibreview import project_references
from bibreview.storage import read_bibliography, write_bibliography


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
                ("inserted", None, 3, None, "Inserted"),
                ("unchanged", 3, 4, "Legacy", "Legacy"),
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
                ("10.1/b", "10.1/b"),
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

    def test_review_explains_provider_only_expansion_without_reclassifying(self):
        current = (
            Reference(identifiers={"doi": "10.1/a"}, citation="A"),
            Reference(identifiers={"doi": "10.1/b"}, citation="B"),
        )
        proposed = (
            Reference(identifiers={"doi": "10.1/new"}, citation="New"),
            Reference(identifiers={"doi": "10.1/a"}, citation="A refreshed"),
            Reference(identifiers={"doi": "10.1/b"}, citation="B refreshed"),
        )
        item = SimpleNamespace(
            proposed_references=proposed,
            reason="reference-count-changed",
            changed_indices=(1, 2, 3),
        )

        explanation = project_references._review_reference_explanation(item, current)

        self.assertEqual(explanation, "explained-provider-expansion")

    def test_review_accepts_formatting_evidence_for_non_doi_structural_pair(self):
        current = (
            Reference(identifiers={"doi": "10.1/a"}, citation="A"),
            Reference(identifiers={}, citation="Khalil HK. Nonlinear Systems (2002)"),
        )
        proposed = (
            Reference(identifiers={"doi": "10.1/a"}, citation="A refreshed"),
            Reference(identifiers={}, citation="Khalil HK, Nonlinear Systems (2002)"),
            Reference(identifiers={"doi": "10.1/new"}, citation="New"),
        )
        item = SimpleNamespace(
            proposed_references=proposed,
            reason="reference-count-changed",
            changed_indices=(1, 2, 3),
        )

        explanation = project_references._review_reference_explanation(item, current)

        self.assertEqual(explanation, "explained-provider-expansion")

    def test_review_accepts_unique_metadata_enrichment_as_structural_anchor(self):
        current = (
            Reference(identifiers={"doi": "10.1/a"}, citation="A"),
            Reference(identifiers={}, citation="Example Book (2009)"),
        )
        proposed = (
            Reference(identifiers={"doi": "10.1/a"}, citation="A refreshed"),
            Reference(
                identifiers={},
                citation="Smith A (2009) Example Book. Publisher",
            ),
            Reference(identifiers={"doi": "10.1/new"}, citation="New"),
        )
        item = SimpleNamespace(
            proposed_references=proposed,
            reason="reference-count-changed",
            changed_indices=(1, 2, 3),
        )

        explanation = project_references._review_reference_explanation(item, current)

        self.assertEqual(explanation, "explained-provider-expansion")

    def test_review_accepts_compact_letter_digit_metadata_anchor(self):
        current = (
            Reference(identifiers={"doi": "10.1/a"}, citation="A"),
            Reference(
                citation="van der Schaft AJ, L2-Gain and Passivity Techniques (1996)"
            ),
        )
        proposed = (
            Reference(identifiers={"doi": "10.1/a"}, citation="A refreshed"),
            Reference(citation="New provider reference"),
            Reference(
                citation=(
                    "van der Schaft AJ, L 2-Gain and Passivity Techniques. "
                    "Springer, Berlin, 1996"
                )
            ),
        )
        item = SimpleNamespace(
            proposed_references=proposed,
            reason="reference-count-changed",
            changed_indices=(1, 2, 3),
        )

        explanation = project_references._review_reference_explanation(item, current)

        self.assertEqual(explanation, "explained-provider-expansion")

    def test_review_accepts_year_suffix_metadata_anchor(self):
        current = (
            Reference(identifiers={"doi": "10.1/a"}, citation="A"),
            Reference(citation="L Ljung, Modeling of dynamic systems (1994)"),
        )
        proposed = (
            Reference(identifiers={"doi": "10.1/a"}, citation="A refreshed"),
            Reference(citation="New provider reference"),
            Reference(
                citation=(
                    "Ljung L, Glad T (1994b) Modeling of dynamic systems. "
                    "Prentice Hall"
                )
            ),
        )
        item = SimpleNamespace(
            proposed_references=proposed,
            reason="reference-count-changed",
            changed_indices=(1, 2, 3),
        )

        explanation = project_references._review_reference_explanation(item, current)

        self.assertEqual(explanation, "explained-provider-expansion")

    def test_review_keeps_non_unique_metadata_enrichment_ambiguous(self):
        current = (
            Reference(identifiers={"doi": "10.1/a"}, citation="A"),
            Reference(identifiers={}, citation="Example Book (2009)"),
        )
        proposed = (
            Reference(identifiers={"doi": "10.1/a"}, citation="A refreshed"),
            Reference(
                identifiers={},
                citation="Smith A (2009) Example Book. Publisher",
            ),
            Reference(
                identifiers={},
                citation="Jones B (2009) Example Book. Other Publisher",
            ),
        )
        item = SimpleNamespace(
            proposed_references=proposed,
            reason="reference-count-changed",
            changed_indices=(1, 2, 3),
        )

        explanation = project_references._review_reference_explanation(item, current)

        self.assertEqual(explanation, "ambiguous-structural-drift")

    def test_review_accepts_identical_duplicate_doi_group_as_invariant_anchor(self):
        duplicate_current = Reference(
            identifiers={"doi": "10.1/dup"},
            citation="Historical duplicate citation",
        )
        duplicate_provider = Reference(
            identifiers={"doi": "10.1/dup"},
            citation="Provider duplicate citation",
        )
        current = (
            Reference(identifiers={"doi": "10.1/a"}, citation="A"),
            duplicate_current,
            duplicate_current,
            Reference(identifiers={"doi": "10.1/b"}, citation="B"),
        )
        proposed = (
            Reference(identifiers={"doi": "10.1/a"}, citation="A refreshed"),
            Reference(citation="Inserted before duplicate group"),
            duplicate_provider,
            duplicate_provider,
            Reference(citation="Inserted after duplicate group"),
            Reference(identifiers={"doi": "10.1/b"}, citation="B refreshed"),
        )
        item = SimpleNamespace(
            proposed_references=proposed,
            reason="reference-count-changed",
            changed_indices=(1, 2, 3, 4, 5, 6),
        )

        explanation = project_references._review_reference_explanation(item, current)

        self.assertEqual(explanation, "explained-provider-expansion")

    def test_review_keeps_duplicate_doi_structural_alignment_ambiguous(self):
        current = (
            Reference(identifiers={"doi": "10.1/dup"}, citation="Duplicate A"),
            Reference(identifiers={"doi": "10.1/dup"}, citation="Duplicate B"),
        )
        proposed = (
            Reference(citation="Inserted"),
            Reference(identifiers={"doi": "10.1/dup"}, citation="Duplicate A"),
            Reference(identifiers={"doi": "10.1/dup"}, citation="Duplicate B"),
        )
        item = SimpleNamespace(
            proposed_references=proposed,
            reason="reference-count-changed",
            changed_indices=(1, 2, 3),
        )

        explanation = project_references._review_reference_explanation(item, current)

        self.assertEqual(explanation, "ambiguous-structural-drift")

    def test_review_keeps_non_doi_structural_pair_ambiguous(self):
        current = (
            Reference(identifiers={"doi": "10.1/a"}, citation="A"),
            Reference(identifiers={}, citation="Legacy"),
        )
        proposed = (
            Reference(identifiers={"doi": "10.1/a"}, citation="A refreshed"),
            Reference(identifiers={}, citation="Different"),
            Reference(identifiers={"doi": "10.1/new"}, citation="New"),
        )
        item = SimpleNamespace(
            proposed_references=proposed,
            reason="reference-count-changed",
            changed_indices=(1, 2, 3),
        )

        explanation = project_references._review_reference_explanation(item, current)

        self.assertEqual(explanation, "ambiguous-structural-drift")

    def test_review_explains_unicode_doi_dash_normalization(self):
        current = (
            Reference(identifiers={"doi": "10.1007/s10444-004-7629-9"}, citation="Old"),
        )
        proposed = (
            Reference(identifiers={"doi": "10.1007/s10444‐004‐7629‐9"}, citation="New"),
        )
        item = SimpleNamespace(
            proposed_references=proposed,
            reason="reference-identifiers-changed:1",
            changed_indices=(1,),
        )

        explanation = project_references._review_reference_explanation(item, current)

        self.assertEqual(explanation, "identifier-typography-normalization")

    def test_review_same_doi_citation_drift_does_not_claim_format_equivalence(self):
        current = (
            Reference(identifiers={"doi": "10.1/a"}, citation="Historical citation"),
        )
        proposed = (
            Reference(identifiers={"doi": "10.1/a"}, citation="Potentially substantive drift"),
        )
        item = SimpleNamespace(
            proposed_references=proposed,
            reason="reference-citation-drift:1",
            changed_indices=(1,),
        )

        explanation = project_references._review_reference_explanation(item, current)

        self.assertEqual(explanation, "same-doi-citation-drift")

    def test_review_explains_punctuation_only_non_doi_citation_drift(self):
        current = (
            Reference(identifiers={}, citation="Khalil HK. Nonlinear Systems (2002)"),
        )
        proposed = (
            Reference(identifiers={}, citation="Khalil HK, Nonlinear Systems (2002)"),
        )
        item = SimpleNamespace(
            proposed_references=proposed,
            reason="reference-citation-drift:1",
            changed_indices=(1,),
        )

        explanation = project_references._review_reference_explanation(item, current)

        self.assertEqual(explanation, "citation-formatting-drift")

    def test_review_explains_duplicated_citation_wrapper_artifact(self):
        current = (
            Reference(
                identifiers={},
                citation=(
                    "A. Astolfi. Astolfi, A., Karagiannis, D., Ortega, R.: "
                    "Nonlinear and Adaptive Control with Applications. "
                    "Springer, Berlin (2007) (2007)"
                ),
            ),
        )
        proposed = (
            Reference(
                identifiers={},
                citation=(
                    "Astolfi, A., Karagiannis, D., Ortega, R.: "
                    "Nonlinear and Adaptive Control with Applications. "
                    "Springer, Berlin (2007)"
                ),
            ),
        )
        item = SimpleNamespace(
            proposed_references=proposed,
            reason="reference-citation-drift:1",
            changed_indices=(1,),
        )

        explanation = project_references._review_reference_explanation(item, current)

        self.assertEqual(explanation, "citation-wrapper-artifact")

    def test_review_explains_strict_non_doi_metadata_enrichment(self):
        current = (
            Reference(
                identifiers={},
                citation=(
                    "Modeling and control of complex physical systems; "
                    "the port-Hamiltonian approach (2009)"
                ),
            ),
        )
        proposed = (
            Reference(
                identifiers={},
                citation=(
                    "Duindam V, Macchelli A, Stramigioli S, Bruyninckx H "
                    "(eds) (2009) Modeling and control of complex physical "
                    "systems; the port-Hamiltonian approach. Springer, "
                    "Berlin/Heidelberg"
                ),
            ),
        )
        item = SimpleNamespace(
            proposed_references=proposed,
            reason="reference-citation-drift:1",
            changed_indices=(1,),
        )

        explanation = project_references._review_reference_explanation(item, current)

        self.assertEqual(explanation, "citation-metadata-enrichment")

    def test_review_keeps_non_doi_token_replacement_ambiguous(self):
        current = (
            Reference(identifiers={}, citation="Smith A. Example Book (2001)"),
        )
        proposed = (
            Reference(identifiers={}, citation="Jones B, Different Book (2002)"),
        )
        item = SimpleNamespace(
            proposed_references=proposed,
            reason="reference-citation-drift:1",
            changed_indices=(1,),
        )

        explanation = project_references._review_reference_explanation(item, current)

        self.assertEqual(explanation, "ambiguous-citation-drift")

    def test_safe_projection_adds_only_provider_insertions(self):
        current = (
            Reference(identifiers={"doi": "10.1/a"}, citation="Canonical A"),
            Reference(identifiers={"doi": "10.1/b"}, citation="Canonical B"),
        )
        proposed = (
            Reference(identifiers={"doi": "10.1/new"}, citation="New reference"),
            Reference(identifiers={"doi": "10.1/a"}, citation="Provider A"),
            Reference(identifiers={"doi": "10.1/b"}, citation="Provider B"),
        )
        item = SimpleNamespace(
            classification="review-required",
            proposed_references=proposed,
            reason="reference-count-changed",
            changed_indices=(1, 2, 3),
            provider_refusals=(),
        )

        projection = project_references.safe_reference_projection(item, current)

        self.assertEqual(projection.inserted_references, 1)
        self.assertEqual(projection.citation_updates, 0)
        self.assertEqual(
            projection.references,
            (
                proposed[0],
                current[0],
                current[1],
            ),
        )

    def test_safe_projection_preserves_doi_less_canonical_pair_during_expansion(self):
        current = (
            Reference(identifiers={"doi": "10.1/a"}, citation="Canonical A"),
            Reference(
                identifiers={},
                citation="Khalil HK. Nonlinear Systems (2002)",
            ),
        )
        proposed = (
            Reference(identifiers={"doi": "10.1/a"}, citation="Provider A"),
            Reference(
                identifiers={},
                citation="Khalil HK, Nonlinear Systems (2002)",
            ),
            Reference(identifiers={"doi": "10.1/new"}, citation="New reference"),
        )
        item = SimpleNamespace(
            classification="review-required",
            proposed_references=proposed,
            reason="reference-count-changed",
            changed_indices=(1, 2, 3),
            provider_refusals=(),
        )

        projection = project_references.safe_reference_projection(item, current)

        self.assertEqual(projection.inserted_references, 1)
        self.assertEqual(
            projection.references,
            (
                current[0],
                current[1],
                proposed[2],
            ),
        )

    def test_safe_projection_inserts_before_exact_doi_less_anchor(self):
        current = (
            Reference(identifiers={"doi": "10.1/a"}, citation="Canonical A"),
            Reference(citation="Legacy reference"),
        )
        proposed = (
            Reference(identifiers={"doi": "10.1/a"}, citation="Provider A"),
            Reference(citation="New provider reference"),
            Reference(citation="Legacy reference"),
        )
        item = SimpleNamespace(
            classification="review-required",
            proposed_references=proposed,
            reason="reference-count-changed",
            changed_indices=(1, 2, 3),
            provider_refusals=(),
        )

        projection = project_references.safe_reference_projection(item, current)

        self.assertEqual(projection.inserted_references, 1)
        self.assertEqual(
            projection.references,
            (
                current[0],
                proposed[1],
                current[1],
            ),
        )

    def test_safe_projection_skips_formatting_equivalent_doi_less_duplicate_insertion(self):
        current = (
            Reference(citation="Khalil HK. Nonlinear Systems (2002)"),
            Reference(identifiers={"doi": "10.1/a"}, citation="A"),
        )
        proposed = (
            Reference(citation="Khalil HK, Nonlinear Systems (2002)"),
            Reference(citation="Khalil HK, Nonlinear Systems (2002)"),
            Reference(identifiers={"doi": "10.1/a"}, citation="Provider A"),
        )
        item = SimpleNamespace(
            classification="review-required",
            proposed_references=proposed,
            reason="reference-count-changed",
            changed_indices=(1, 2, 3),
            provider_refusals=(),
        )

        projection = project_references.safe_reference_projection(item, current)

        self.assertFalse(projection.changed)
        self.assertEqual(projection.references, current)

    def test_safe_review_residual_distinguishes_automatic_and_human_cases(self):
        formatting_current = (
            Reference(citation="Khalil HK. Nonlinear Systems (2002)"),
        )
        formatting_proposed = (
            Reference(citation="Khalil HK, Nonlinear Systems (2002)"),
        )
        formatting_item = SimpleNamespace(
            classification="review-required",
            proposed_references=formatting_proposed,
            reason="reference-citation-drift:1",
            changed_indices=(1,),
            provider_refusals=(),
        )
        self.assertFalse(
            project_references.safe_reference_requires_human_review(
                formatting_item,
                formatting_current,
            )
        )

        substantive_current = (
            Reference(
                identifiers={"doi": "10.1/a"},
                citation="Historical citation",
            ),
        )
        substantive_proposed = (
            Reference(
                identifiers={"doi": "10.1/a"},
                citation="Substantively different provider citation",
            ),
        )
        substantive_item = SimpleNamespace(
            classification="review-required",
            proposed_references=substantive_proposed,
            reason="reference-citation-drift:1",
            changed_indices=(1,),
            provider_refusals=(),
        )
        self.assertFalse(
            project_references.safe_reference_requires_human_review(
                substantive_item,
                substantive_current,
            )
        )

    def test_safe_review_residual_resolves_expansion_with_canonical_citation_policy(self):
        current = (
            Reference(identifiers={"doi": "10.1/a"}, citation="Canonical A"),
        )
        proposed = (
            Reference(identifiers={"doi": "10.1/new"}, citation="New reference"),
            Reference(identifiers={"doi": "10.1/a"}, citation="Provider A differs"),
        )
        item = SimpleNamespace(
            classification="review-required",
            proposed_references=proposed,
            reason="reference-count-changed",
            changed_indices=(1, 2),
            provider_refusals=(),
        )

        projection = project_references.safe_reference_projection(item, current)

        self.assertEqual(projection.inserted_references, 1)
        self.assertFalse(
            project_references.safe_reference_requires_human_review(item, current)
        )

    def test_safe_review_residual_accepts_pure_safe_expansion(self):
        current = (
            Reference(
                identifiers={"doi": "10.1/a"},
                citation="Canonical A",
            ),
        )
        proposed = (
            Reference(identifiers={"doi": "10.1/new"}, citation="New reference"),
            Reference(
                identifiers={"doi": "10.1/a"},
                citation="Canonical A",
            ),
        )
        item = SimpleNamespace(
            classification="review-required",
            proposed_references=proposed,
            reason="reference-count-changed",
            changed_indices=(1, 2),
            provider_refusals=(),
        )

        self.assertFalse(
            project_references.safe_reference_requires_human_review(item, current)
        )

    def test_safe_review_residual_keeps_ambiguous_structure_human(self):
        current = (
            Reference(citation="Legacy citation"),
            Reference(identifiers={"doi": "10.1/a"}, citation="Canonical A"),
        )
        proposed = (
            Reference(citation="Different citation"),
            Reference(identifiers={"doi": "10.1/new"}, citation="New reference"),
            Reference(identifiers={"doi": "10.1/a"}, citation="Provider A"),
        )
        item = SimpleNamespace(
            classification="review-required",
            proposed_references=proposed,
            reason="reference-count-changed",
            changed_indices=(1, 2, 3),
            provider_refusals=(),
        )

        self.assertTrue(
            project_references.safe_reference_requires_human_review(item, current)
        )

    def test_safe_review_residual_resolves_provider_added_identifier_by_preserving_canon(self):
        current = (Reference(citation="Example citation"),)
        proposed = (
            Reference(
                identifiers={"doi": "10.1/new"},
                citation="Example citation",
            ),
        )
        item = SimpleNamespace(
            classification="review-required",
            proposed_references=proposed,
            reason="reference-identifiers-changed:1",
            changed_indices=(1,),
            provider_refusals=(),
        )

        self.assertFalse(
            project_references.safe_reference_requires_human_review(item, current)
        )

    def test_safe_review_residual_resolves_metadata_enrichment_by_preserving_canon(self):
        current = (Reference(citation="Example Book (2009)"),)
        proposed = (
            Reference(citation="Smith A (2009) Example Book. Publisher"),
        )
        item = SimpleNamespace(
            classification="review-required",
            proposed_references=proposed,
            reason="reference-citation-drift:1",
            changed_indices=(1,),
            provider_refusals=(),
        )

        self.assertFalse(
            project_references.safe_reference_requires_human_review(item, current)
        )

    def test_safe_review_residual_keeps_refused_insertion_human(self):
        current = (
            Reference(identifiers={"doi": "10.1/a"}, citation="Canonical A"),
        )
        proposed = (
            Reference(citation="<script>unsafe</script>"),
            Reference(identifiers={"doi": "10.1/a"}, citation="Provider A"),
        )
        item = SimpleNamespace(
            classification="review-required",
            proposed_references=proposed,
            reason="reference-count-changed",
            changed_indices=(1, 2),
            provider_refusals=((1, "script-markup"),),
        )

        self.assertTrue(
            project_references.safe_reference_requires_human_review(item, current)
        )

    def test_safe_projection_skips_refused_provider_insertion(self):
        current = (
            Reference(identifiers={"doi": "10.1/a"}, citation="Canonical A"),
        )
        proposed = (
            Reference(citation="<script>unsafe</script>"),
            Reference(identifiers={"doi": "10.1/a"}, citation="Provider A"),
        )
        item = SimpleNamespace(
            classification="review-required",
            proposed_references=proposed,
            reason="reference-count-changed",
            changed_indices=(1, 2),
            provider_refusals=((1, "script-markup"),),
        )

        projection = project_references.safe_reference_projection(item, current)

        self.assertFalse(projection.changed)
        self.assertEqual(projection.references, current)

    def test_safe_projection_keeps_substantive_same_doi_citation(self):
        current = (
            Reference(
                identifiers={"doi": "10.1/a"},
                citation="Historical citation",
            ),
        )
        proposed = (
            Reference(
                identifiers={"doi": "10.1/a"},
                citation="Substantively different provider citation",
            ),
        )
        item = SimpleNamespace(
            classification="review-required",
            proposed_references=proposed,
            reason="reference-citation-drift:1",
            changed_indices=(1,),
            provider_refusals=(),
        )

        projection = project_references.safe_reference_projection(item, current)

        self.assertFalse(projection.changed)
        self.assertEqual(projection.references, current)

    def test_safe_projection_applies_punctuation_but_not_case_only_drift(self):
        punctuation_current = (
            Reference(citation="Khalil HK. Nonlinear Systems (2002)"),
        )
        punctuation_proposed = (
            Reference(citation="Khalil HK, Nonlinear Systems (2002)"),
        )
        item = SimpleNamespace(
            classification="review-required",
            proposed_references=punctuation_proposed,
            reason="reference-citation-drift:1",
            changed_indices=(1,),
            provider_refusals=(),
        )

        punctuation = project_references.safe_reference_projection(
            item,
            punctuation_current,
        )

        self.assertEqual(punctuation.citation_updates, 1)
        self.assertEqual(
            punctuation.references[0].citation,
            punctuation_proposed[0].citation,
        )

        case_current = (Reference(citation="pH control (2002)"),)
        case_proposed = (Reference(citation="PH control (2002)"),)
        case_item = SimpleNamespace(
            classification="review-required",
            proposed_references=case_proposed,
            reason="reference-citation-drift:1",
            changed_indices=(1,),
            provider_refusals=(),
        )

        case_projection = project_references.safe_reference_projection(
            case_item,
            case_current,
        )

        self.assertFalse(case_projection.changed)
        self.assertEqual(case_projection.references, case_current)

    def test_safe_projection_does_not_auto_apply_metadata_enrichment(self):
        current = (
            Reference(citation="Example Book (2009)"),
        )
        proposed = (
            Reference(citation="Smith A (2009) Example Book. Publisher"),
        )
        item = SimpleNamespace(
            classification="review-required",
            proposed_references=proposed,
            reason="reference-citation-drift:1",
            changed_indices=(1,),
            provider_refusals=(),
        )

        projection = project_references.safe_reference_projection(item, current)

        self.assertFalse(projection.changed)
        self.assertEqual(projection.references, current)

    def test_safe_projection_normalizes_doi_typography_without_changing_citation(self):
        current = (
            Reference(
                identifiers={"doi": "10.1007/s10444‐004‐7629‐9"},
                citation="Canonical citation",
            ),
        )
        proposed = (
            Reference(
                identifiers={"doi": "10.1007/s10444-004-7629-9"},
                citation="Different provider citation",
            ),
        )
        item = SimpleNamespace(
            classification="review-required",
            proposed_references=proposed,
            reason="reference-identifiers-changed:1",
            changed_indices=(1,),
            provider_refusals=(),
        )

        projection = project_references.safe_reference_projection(item, current)

        self.assertEqual(projection.identifier_updates, 1)
        self.assertEqual(
            projection.references[0].identifiers["doi"],
            "10.1007/s10444-004-7629-9",
        )
        self.assertEqual(
            projection.references[0].citation,
            "Canonical citation",
        )

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

    def test_apply_safe_dry_run_then_stages_safe_update(self):
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
            "--dry-run",
            "references",
            "--apply-safe",
            "--json",
        )

        self.assertEqual(code, 0, stderr)
        payload = json.loads(stdout)
        self.assertTrue(payload["dry_run"])
        self.assertEqual(payload["publications_to_stage"], 1)
        self.assertEqual(payload["citation_updates"], 1)
        self.assertEqual(payload["review_required_publications"], 0)
        self.assertEqual(payload["human_reviews_remaining"], 0)
        self.assertEqual(payload["auto_resolved_reviews"], 0)
        self.assertEqual(payload["partially_staged_human_reviews"], 0)
        self.assertEqual(payload["unstaged_human_reviews"], 0)
        self.assertEqual(payload["policy_resolutions_recorded"], 1)
        self.assertEqual(payload["already_completed"], 0)
        self.assertFalse(self.config.paths.collected.exists())

        code, stdout, stderr = self.run_cli(
            "references",
            "--apply-safe",
            "--json",
        )

        self.assertEqual(code, 0, stderr)
        payload = json.loads(stdout)
        self.assertFalse(payload["dry_run"])
        staged = read_bibliography(self.config.paths.collected)
        self.assertEqual(len(staged), 1)
        self.assertEqual(
            staged[0].references[0].citation,
            "Systems & Control Letters",
        )
        canonical = read_bibliography(self.config.paths.bibliography)
        self.assertEqual(
            canonical[0].references[0].citation,
            "Systems &amp; Control Letters",
        )

    def test_apply_safe_expansion_inserts_only_new_provider_reference(self):
        self.publication = Publication(
            id=self.publication.id,
            identifiers=self.publication.identifiers,
            title=self.publication.title,
            authors=self.publication.authors,
            references=(
                Reference(
                    identifiers={"doi": "10.1/a"},
                    citation="Canonical A",
                ),
                Reference(
                    identifiers={"doi": "10.1/b"},
                    citation="Canonical B",
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
                        {"DOI": "10.1/new", "unstructured": "New reference"},
                        {"DOI": "10.1/a", "unstructured": "Provider A"},
                        {"DOI": "10.1/b", "unstructured": "Provider B"},
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
            "--apply-safe",
            "--json",
        )

        self.assertEqual(code, 0, stderr)
        payload = json.loads(stdout)
        self.assertEqual(payload["publications_to_stage"], 1)
        self.assertEqual(payload["references_inserted"], 1)
        staged = read_bibliography(self.config.paths.collected)
        self.assertEqual(
            [reference.citation for reference in staged[0].references],
            ["New reference", "Canonical A", "Canonical B"],
        )

    def test_apply_safe_refuses_stale_canonical_references(self):
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

        write_bibliography(
            self.config.paths.bibliography,
            (
                Publication(
                    id=self.publication.id,
                    identifiers=self.publication.identifiers,
                    title=self.publication.title,
                    authors=self.publication.authors,
                    references=(Reference(citation="Manual correction"),),
                ),
            ),
        )

        code, _, stderr = self.run_cli("references", "--apply-safe")

        self.assertEqual(code, 1)
        self.assertIn("stale reference review", stderr)
        self.assertFalse(self.config.paths.collected.exists())

    def test_apply_safe_refuses_nonempty_staging(self):
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
        write_bibliography(self.config.paths.collected, (self.publication,))

        code, _, stderr = self.run_cli("references", "--apply-safe")

        self.assertEqual(code, 1)
        self.assertIn("merge the existing batch", stderr)


    def _run_ambiguous_reference_refresh(self):
        self.publication = Publication(
            id=self.publication.id,
            identifiers=self.publication.identifiers,
            title=self.publication.title,
            authors=self.publication.authors,
            references=(
                Reference(citation="Legacy citation"),
                Reference(
                    identifiers={"doi": "10.1/a"},
                    citation="Canonical A",
                ),
            ),
        )
        write_bibliography(self.config.paths.bibliography, (self.publication,))
        provider = FakeBatchProvider(
            {
                "10.1000/parent": {
                    "reference": [
                        {"unstructured": "Different citation"},
                        {"DOI": "10.1/new", "unstructured": "New reference"},
                        {"DOI": "10.1/a", "unstructured": "Provider A"},
                    ]
                },
                "10.1/new": {},
                "10.1/a": {},
            }
        )
        with patch(
            "bibreview.cli.build_reference_services",
            return_value=SimpleNamespace(batch_provider=provider),
        ):
            code, _, stderr = self.run_cli("references")
        self.assertEqual(code, 0, stderr)

    def test_reference_resolve_keep_canonical_is_persisted_and_resumable(self):
        self._run_ambiguous_reference_refresh()
        resolutions = self.config.references.report.with_name("resolutions.json")

        with patch("builtins.input", side_effect=["k"]):
            code, stdout, stderr = self.run_cli("references", "--resolve")

        self.assertEqual(code, 0, stderr)
        self.assertIn("keep the current canonical reference list", stdout)
        self.assertIn("Keep canonical : 1", stdout)
        self.assertTrue(resolutions.exists())
        payload = json.loads(resolutions.read_text(encoding="utf-8"))
        self.assertEqual(payload["decisions"][0]["decision"], "keep-canonical")

        with patch("builtins.input") as prompt:
            code, stdout, stderr = self.run_cli("references", "--resolve")

        self.assertEqual(code, 0, stderr)
        prompt.assert_not_called()
        self.assertIn("No unresolved human reference decisions.", stdout)
        self.assertFalse(self.config.paths.collected.exists())

        code, stdout, stderr = self.run_cli(
            "--dry-run",
            "references",
            "--apply",
            "--json",
        )
        self.assertEqual(code, 0, stderr)
        result = json.loads(stdout)
        self.assertEqual(result["publications_to_stage"], 0)
        self.assertEqual(result["already_completed"], 1)

    def test_reference_resolve_use_provider_stages_only_after_apply(self):
        self._run_ambiguous_reference_refresh()

        with patch("builtins.input", side_effect=["p"]):
            code, _, stderr = self.run_cli("references", "--resolve")
        self.assertEqual(code, 0, stderr)
        self.assertFalse(self.config.paths.collected.exists())

        code, stdout, stderr = self.run_cli(
            "--dry-run",
            "references",
            "--apply",
            "--json",
        )
        self.assertEqual(code, 0, stderr)
        payload = json.loads(stdout)
        self.assertEqual(payload["publications_to_stage"], 1)
        self.assertFalse(self.config.paths.collected.exists())

        code, stdout, stderr = self.run_cli(
            "references",
            "--apply",
            "--json",
        )
        self.assertEqual(code, 0, stderr)
        payload = json.loads(stdout)
        self.assertEqual(payload["publications_to_stage"], 1)
        staged = read_bibliography(self.config.paths.collected)
        self.assertEqual(
            [reference.citation for reference in staged[0].references],
            ["Different citation", "New reference", "Provider A"],
        )
        canonical = read_bibliography(self.config.paths.bibliography)
        self.assertEqual(canonical[0].references, self.publication.references)

    def test_reference_resolve_custom_json_stages_exact_reviewed_list(self):
        self._run_ambiguous_reference_refresh()
        custom_path = self.root / "reviewed-references.json"
        custom_path.write_text(
            json.dumps(
                [
                    {
                        "identifiers": {"doi": "10.9/custom"},
                        "citation": "Reviewed custom reference",
                    }
                ]
            ),
            encoding="utf-8",
        )

        with patch("builtins.input", side_effect=[f"c {custom_path}"]):
            code, _, stderr = self.run_cli("references", "--resolve")
        self.assertEqual(code, 0, stderr)

        code, _, stderr = self.run_cli("references", "--apply")
        self.assertEqual(code, 0, stderr)
        staged = read_bibliography(self.config.paths.collected)
        self.assertEqual(
            staged[0].references,
            (
                Reference(
                    identifiers={"doi": "10.9/custom"},
                    citation="Reviewed custom reference",
                ),
            ),
        )

    def test_reference_resolve_deferred_blocks_reviewed_apply(self):
        self._run_ambiguous_reference_refresh()

        with patch("builtins.input", side_effect=["s"]):
            code, stdout, stderr = self.run_cli("references", "--resolve")
        self.assertEqual(code, 0, stderr)
        self.assertIn("Deferred       : 1", stdout)
        self.assertIn("Unresolved     : 1", stdout)

        code, _, stderr = self.run_cli("references", "--apply")
        self.assertEqual(code, 1)
        self.assertIn("reference decisions must be complete", stderr)
        self.assertFalse(self.config.paths.collected.exists())

    def test_reference_resolve_dry_run_does_not_persist_decision(self):
        self._run_ambiguous_reference_refresh()
        resolutions = self.config.references.report.with_name("resolutions.json")

        with patch("builtins.input", side_effect=["k"]):
            code, stdout, stderr = self.run_cli(
                "--dry-run",
                "references",
                "--resolve",
            )

        self.assertEqual(code, 0, stderr)
        self.assertIn("Dry run: Reference resolution", stdout)
        self.assertFalse(resolutions.exists())

    def test_reference_resolve_refuses_json_output(self):
        self._run_ambiguous_reference_refresh()

        code, _, stderr = self.run_cli(
            "references",
            "--resolve",
            "--json",
        )

        self.assertEqual(code, 1)
        self.assertIn("--json cannot be used", stderr)

    def test_reference_apply_refuses_stale_canonical_after_resolution(self):
        self._run_ambiguous_reference_refresh()

        with patch("builtins.input", side_effect=["p"]):
            code, _, stderr = self.run_cli("references", "--resolve")
        self.assertEqual(code, 0, stderr)

        write_bibliography(
            self.config.paths.bibliography,
            (
                replace(
                    self.publication,
                    references=(Reference(citation="Independent manual edit"),),
                ),
            ),
        )

        code, _, stderr = self.run_cli("references", "--apply")

        self.assertEqual(code, 1)
        self.assertIn("stale reference resolution", stderr)
        self.assertFalse(self.config.paths.collected.exists())

    def test_apply_safe_persists_policy_resolution_and_replays_after_merge(self):
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
            "--apply-safe",
            "--json",
        )
        self.assertEqual(code, 0, stderr)
        payload = json.loads(stdout)
        self.assertEqual(payload["policy_resolutions_recorded"], 1)
        self.assertEqual(payload["already_completed"], 0)

        resolutions = self.config.references.report.with_name("resolutions.json")
        self.assertTrue(resolutions.exists())
        ledger = json.loads(resolutions.read_text(encoding="utf-8"))
        self.assertEqual(
            ledger["decisions"][0]["decision"],
            "deterministic-policy",
        )

        code, _, stderr = self.run_cli("merge")
        self.assertEqual(code, 0, stderr)
        self.assertEqual(read_bibliography(self.config.paths.collected), ())

        code, stdout, stderr = self.run_cli(
            "--dry-run",
            "references",
            "--apply-safe",
            "--json",
        )
        self.assertEqual(code, 0, stderr)
        replay = json.loads(stdout)
        self.assertEqual(replay["publications_to_stage"], 0)
        self.assertEqual(replay["policy_resolutions_recorded"], 0)
        self.assertEqual(replay["already_completed"], 1)

    def test_reconcile_applied_adopts_explicit_historical_canonical_state(self):
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

        historical = replace(
            self.publication,
            references=(Reference(citation="Systems & Control Letters"),),
        )
        write_bibliography(self.config.paths.bibliography, (historical,))
        resolutions = self.config.references.report.with_name("resolutions.json")

        code, stdout, stderr = self.run_cli(
            "--dry-run",
            "references",
            "--reconcile-applied",
            "--json",
        )
        self.assertEqual(code, 0, stderr)
        preview = json.loads(stdout)
        self.assertEqual(preview["adopt_current_canonical"], 1)
        self.assertFalse(resolutions.exists())

        code, stdout, stderr = self.run_cli(
            "references",
            "--reconcile-applied",
            "--json",
        )
        self.assertEqual(code, 0, stderr)
        applied = json.loads(stdout)
        self.assertEqual(applied["adopt_current_canonical"], 1)
        ledger = json.loads(resolutions.read_text(encoding="utf-8"))
        self.assertEqual(
            ledger["decisions"][0]["decision"],
            "reconciled-current",
        )

        code, stdout, stderr = self.run_cli(
            "--dry-run",
            "references",
            "--apply-safe",
            "--json",
        )
        self.assertEqual(code, 0, stderr)
        replay = json.loads(stdout)
        self.assertEqual(replay["publications_to_stage"], 0)
        self.assertEqual(replay["already_completed"], 1)

    def test_reconciled_history_still_refuses_later_independent_edit(self):
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

        historical = replace(
            self.publication,
            references=(Reference(citation="Systems & Control Letters"),),
        )
        write_bibliography(self.config.paths.bibliography, (historical,))
        code, _, stderr = self.run_cli("references", "--reconcile-applied")
        self.assertEqual(code, 0, stderr)

        independent = replace(
            self.publication,
            references=(Reference(citation="Independent later edit"),),
        )
        write_bibliography(self.config.paths.bibliography, (independent,))

        code, _, stderr = self.run_cli("references", "--apply-safe")
        self.assertEqual(code, 1)
        self.assertIn("stale reference resolution", stderr)

    def test_reconcile_applied_refuses_nonempty_staging(self):
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

        write_bibliography(self.config.paths.collected, (self.publication,))
        code, _, stderr = self.run_cli("references", "--reconcile-applied")
        self.assertEqual(code, 1)
        self.assertIn("merge or clear staging", stderr)

    def test_apply_safe_leaves_genuine_human_case_completely_unstaged(self):
        self._run_ambiguous_reference_refresh()

        code, stdout, stderr = self.run_cli(
            "--dry-run",
            "references",
            "--apply-safe",
            "--json",
        )
        self.assertEqual(code, 0, stderr)
        payload = json.loads(stdout)
        self.assertEqual(payload["human_reviews_remaining"], 1)
        self.assertEqual(payload["partially_staged_human_reviews"], 0)
        self.assertEqual(payload["publications_to_stage"], 0)
        self.assertEqual(payload["policy_resolutions_recorded"], 0)
        self.assertFalse(self.config.paths.collected.exists())

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
