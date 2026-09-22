from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from bibreview.audit_resolution import (
    actionable_resolution_candidates,
    audit_resolution_path,
    audit_resolution_state_from_data,
    audit_review_fingerprint,
    load_project_audit_resolutions,
    parse_custom_resolution_value,
    record_audit_resolution,
    resolution_counts,
    save_project_audit_resolutions,
    unresolved_resolution_candidates,
)
from bibreview.config import load_config
from bibreview.pipeline.audit import AuditReviewFinding
from bibreview.project import ProjectStateError
from bibreview.project_audit import AuditPublicationReview, ProjectAuditReview


CONFIG = """\
schema_version: 1
project:
  name: Example Review
  slug: example-review
audit:
  campaign: state/campaign.json
  report: state/report.json
site:
  enabled: false
"""


def finding(
    field: str,
    canonical,
    *provider_values,
) -> AuditReviewFinding:
    return AuditReviewFinding(
        field=field,
        classification="canonical-missing",
        providers=tuple(provider for provider, _ in provider_values),
        canonical_value=canonical,
        provider_values=tuple(provider_values),
        actionable=True,
        detail="corroborated",
    )


def review_with(*findings: AuditReviewFinding) -> ProjectAuditReview:
    item = AuditPublicationReview(
        publication_id="11111111-1111-5111-8111-111111111111",
        identifiers={"doi": "10.1000/example"},
        permalink="example",
        title="Example publication",
        findings=findings,
    )
    return ProjectAuditReview(
        audited_publications=1,
        flagged_publications=1,
        actionable_findings=len(findings),
        informational_findings=0,
        provider_issues=0,
        items=(item,),
    )


class AuditResolutionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config_path = self.root / "bibreview.yml"
        self.config_path.write_text(CONFIG, encoding="utf-8")
        self.config = load_config(self.config_path)

    def test_exact_provider_values_create_safe_default_proposal(self):
        review = review_with(
            finding(
                "volume",
                "",
                ("crossref", "48"),
                ("openalex", "48"),
            )
        )
        candidate = actionable_resolution_candidates(review)[0]
        self.assertEqual(candidate.proposed_value, "48")

        state = load_project_audit_resolutions(self.config, review)
        state = record_audit_resolution(
            state,
            candidate,
            decision="accepted",
        )
        self.assertEqual(state.decisions[0].decision, "accepted")
        self.assertEqual(state.decisions[0].resolved_value, "48")

    def test_page_proposal_uses_bibtex_double_hyphen(self):
        review = review_with(
            finding(
                "pages",
                "",
                ("crossref", "8793-8805"),
                ("openalex", "8793-8805"),
            )
        )
        candidate = actionable_resolution_candidates(review)[0]
        self.assertEqual(candidate.proposed_value, "8793--8805")

    def test_page_proposal_normalizes_unicode_and_mixed_provider_dashes(self):
        review = review_with(
            finding(
                "pages",
                "",
                ("crossref", "8793-8805"),
                ("openalex", "8793–8805"),
                ("semantic_scholar", "8793—8805"),
            )
        )
        candidate = actionable_resolution_candidates(review)[0]
        self.assertEqual(candidate.proposed_value, "8793--8805")

    def test_page_proposal_preserves_existing_bibtex_double_hyphen(self):
        review = review_with(
            finding(
                "pages",
                "",
                ("crossref", "8793--8805"),
                ("openalex", "8793--8805"),
            )
        )
        candidate = actionable_resolution_candidates(review)[0]
        self.assertEqual(candidate.proposed_value, "8793--8805")

    def test_equivalent_but_nonidentical_provider_values_require_custom_value(self):
        review = review_with(
            finding(
                "title",
                "Discrete Gradient theta-Methods",
                ("crossref", r"Discrete Gradient $$\theta $$-Methods"),
                ("openalex", r"Discrete Gradient $$\theta $$-Methods "),
                ("semantic_scholar", "Discrete Gradient θ-Methods"),
            )
        )
        candidate = actionable_resolution_candidates(review)[0]
        self.assertIsNone(candidate.proposed_value)
        with self.assertRaisesRegex(ProjectStateError, "custom resolution"):
            record_audit_resolution(
                load_project_audit_resolutions(self.config, review),
                candidate,
                decision="accepted",
            )

        state = record_audit_resolution(
            load_project_audit_resolutions(self.config, review),
            candidate,
            decision="custom",
            resolved_value="Discrete Gradient θ-Methods",
        )
        self.assertEqual(state.decisions[0].decision, "custom")
        self.assertEqual(
            state.decisions[0].resolved_value,
            "Discrete Gradient θ-Methods",
        )

    def test_rejected_deferred_and_unresolved_are_distinct(self):
        review = review_with(
            finding("volume", "", ("crossref", "48"), ("openalex", "48")),
            finding("issue", "", ("crossref", "8"), ("openalex", "8")),
            finding("pages", "", ("crossref", "1-9"), ("openalex", "1-9")),
        )
        candidates = actionable_resolution_candidates(review)
        state = load_project_audit_resolutions(self.config, review)
        state = record_audit_resolution(
            state,
            candidates[0],
            decision="rejected",
        )
        state = record_audit_resolution(
            state,
            candidates[1],
            decision="deferred",
        )

        counts = resolution_counts(state)
        self.assertEqual(counts["rejected"], 1)
        self.assertEqual(counts["deferred"], 1)
        self.assertEqual(counts["unresolved"], 1)
        self.assertEqual(
            tuple(item.finding.field for item in unresolved_resolution_candidates(review, state)),
            ("issue", "pages"),
        )

    def test_resolution_state_is_resumable_and_tied_to_exact_review(self):
        review = review_with(
            finding("volume", "", ("crossref", "48"), ("openalex", "48"))
        )
        candidate = actionable_resolution_candidates(review)[0]
        state = record_audit_resolution(
            load_project_audit_resolutions(self.config, review),
            candidate,
            decision="accepted",
        )
        save_project_audit_resolutions(self.config, state)

        self.assertEqual(
            audit_resolution_path(self.config),
            self.root / "state/resolutions.json",
        )
        resumed = load_project_audit_resolutions(self.config, review)
        self.assertEqual(resumed, state)

        changed_review = review_with(
            finding("volume", "", ("crossref", "49"), ("openalex", "49"))
        )
        with self.assertRaisesRegex(ProjectStateError, "do not match"):
            load_project_audit_resolutions(self.config, changed_review)

    def test_custom_tuple_values_require_json_string_array(self):
        review = review_with(
            finding(
                "authors",
                (),
                ("crossref", ("Ada Lovelace",)),
                ("openalex", ("Ada Lovelace",)),
            )
        )
        candidate = actionable_resolution_candidates(review)[0]
        self.assertEqual(
            parse_custom_resolution_value(
                '["Ada Lovelace", "Alan Turing"]',
                candidate,
            ),
            ("Ada Lovelace", "Alan Turing"),
        )
        self.assertEqual(
            parse_custom_resolution_value("Ada Lovelace", candidate),
            ("Ada Lovelace",),
        )

    def test_custom_tuple_values_accept_semicolon_separated_input(self):
        review = review_with(
            finding(
                "authors",
                (),
                ("crossref", ("Nguyen Thanh Sang", "Tan Chee Keong")),
                ("openalex", ("Nguyen Thanh Sang", "Tan Chee Keong")),
            )
        )
        candidate = actionable_resolution_candidates(review)[0]
        self.assertEqual(
            parse_custom_resolution_value(
                "Nguyen Thanh Sang; Tan Chee Keong; Hussain Mohd Azlan",
                candidate,
            ),
            (
                "Nguyen Thanh Sang",
                "Tan Chee Keong",
                "Hussain Mohd Azlan",
            ),
        )

    def test_custom_tuple_values_strip_whitespace_and_reject_empty_items(self):
        review = review_with(
            finding(
                "authors",
                (),
                ("crossref", ("Ada Lovelace",)),
                ("openalex", ("Ada Lovelace",)),
            )
        )
        candidate = actionable_resolution_candidates(review)[0]
        self.assertEqual(
            parse_custom_resolution_value(
                "  Ada Lovelace  ;  Alan Turing  ",
                candidate,
            ),
            ("Ada Lovelace", "Alan Turing"),
        )
        with self.assertRaisesRegex(ProjectStateError, "semicolon-separated"):
            parse_custom_resolution_value(
                "Ada Lovelace; ; Alan Turing",
                candidate,
            )

    def test_custom_tuple_json_input_remains_supported(self):
        review = review_with(
            finding(
                "authors",
                (),
                ("crossref", ("Ada Lovelace",)),
                ("openalex", ("Ada Lovelace",)),
            )
        )
        candidate = actionable_resolution_candidates(review)[0]
        self.assertEqual(
            parse_custom_resolution_value(
                '["Ada Lovelace", "Alan Turing"]',
                candidate,
            ),
            ("Ada Lovelace", "Alan Turing"),
        )

    def test_resolution_document_validation_rejects_duplicate_keys(self):
        review = review_with(
            finding("volume", "", ("crossref", "48"), ("openalex", "48"))
        )
        state = record_audit_resolution(
            load_project_audit_resolutions(self.config, review),
            actionable_resolution_candidates(review)[0],
            decision="accepted",
        )
        data = state.data()
        data["decisions"].append(dict(data["decisions"][0]))
        with self.assertRaisesRegex(ProjectStateError, "duplicate decision keys"):
            audit_resolution_state_from_data(data)

    def test_fingerprint_is_deterministic(self):
        review = review_with(
            finding("volume", "", ("crossref", "48"), ("openalex", "48"))
        )
        self.assertEqual(
            audit_review_fingerprint(review),
            audit_review_fingerprint(review),
        )


if __name__ == "__main__":
    unittest.main()
