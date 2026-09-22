from __future__ import annotations

from datetime import date
import unittest

from bibreview.model import Author, Editor, Publication
from bibreview.pipeline.audit import (
    AuditComparison,
    AuditError,
    AuditRecord,
    AuditResult,
    ProviderEvidence,
    audit_result_data,
    audit_result_from_data,
    audit_review_findings,
    compare_audit_record,
    publication_audit_record,
    reclassify_audit_result,
)


PUBLICATION_ID = "11111111-1111-4111-8111-111111111111"


class AuditComparisonTests(unittest.TestCase):
    def record(self, **fields):
        values = {
            "title": "Port-Hamiltonian systems",
            "container_title": "Journal of Examples",
            "pages": "10--20",
            "volume": "12",
        }
        values.update(fields)
        return AuditRecord(
            publication_id=PUBLICATION_ID,
            identifiers={"doi": "10.1000/example"},
            permalink="port-hamiltonian-systems",
            title="Port-Hamiltonian systems",
            fields=values,
        )

    def test_pairwise_classifications_cover_equal_formatting_and_missing(self):
        result = compare_audit_record(
            self.record(abstract=""),
            (
                ProviderEvidence(
                    provider="crossref",
                    identifiers={"doi": "https://doi.org/10.1000/EXAMPLE"},
                    fields={
                        "title": "port-Hamiltonian systems.",
                        "container_title": "Journal of Examples",
                        "pages": "10-20",
                        "volume": "",
                        "abstract": "Provider abstract",
                    },
                ),
            ),
        )
        by_field = {
            item.field: item.classification
            for item in result.comparisons
        }

        self.assertEqual(by_field["identifiers.doi"], "equal")
        self.assertEqual(by_field["title"], "formatting-only")
        self.assertEqual(by_field["container_title"], "equal")
        self.assertEqual(by_field["pages"], "formatting-only")
        self.assertEqual(by_field["volume"], "provider-missing")
        self.assertEqual(by_field["abstract"], "canonical-missing")

    def test_substantive_difference_does_not_decide_which_value_is_correct(self):
        result = compare_audit_record(
            self.record(publication_year="2020"),
            (
                ProviderEvidence(
                    provider="crossref",
                    fields={"publication_year": "2021"},
                ),
            ),
        )

        comparison = result.comparisons[0]
        self.assertEqual(comparison.classification, "substantive-difference")
        self.assertEqual(comparison.canonical_value, "2020")
        self.assertEqual(comparison.provider_value, "2021")

    def test_explicit_identifier_mismatch_is_identity_problem(self):
        result = compare_audit_record(
            self.record(),
            (
                ProviderEvidence(
                    provider="provider-a",
                    identifiers={"doi": "10.1000/other"},
                ),
            ),
        )

        comparison = result.comparisons[0]
        self.assertEqual(comparison.field, "identifiers.doi")
        self.assertEqual(comparison.classification, "identity-problem")

    def test_provider_only_identifier_is_canonical_missing_not_fuzzy_match(self):
        result = compare_audit_record(
            self.record(),
            (
                ProviderEvidence(
                    provider="provider-a",
                    identifiers={"isbn": "9780000000000"},
                ),
            ),
        )

        comparison = result.comparisons[0]
        self.assertEqual(comparison.field, "identifiers.isbn")
        self.assertEqual(comparison.classification, "canonical-missing")

    def test_provider_unavailable_and_error_are_separate_from_canonical_defects(self):
        result = compare_audit_record(
            self.record(),
            (
                ProviderEvidence(
                    provider="semantic-scholar",
                    status="unavailable",
                    detail="HTTP 429",
                ),
                ProviderEvidence(
                    provider="mendeley",
                    status="error",
                    detail="authentication failed",
                ),
            ),
        )

        self.assertEqual(result.comparisons, ())
        self.assertEqual(
            tuple(item.classification for item in result.provider_issues),
            ("unavailable", "error"),
        )
        self.assertEqual(
            dict(result.classification_counts()),
            {"error": 1, "unavailable": 1},
        )

    def test_provider_disagreement_is_reported_without_overwriting_pairwise_results(self):
        result = compare_audit_record(
            self.record(publication_year="2020"),
            (
                ProviderEvidence(
                    provider="crossref",
                    fields={"publication_year": "2020"},
                ),
                ProviderEvidence(
                    provider="openalex",
                    fields={"publication_year": "2021"},
                ),
                ProviderEvidence(
                    provider="semantic-scholar",
                    fields={"publication_year": "2021"},
                ),
            ),
        )

        self.assertEqual(
            tuple(item.classification for item in result.comparisons),
            ("equal", "substantive-difference", "substantive-difference"),
        )
        self.assertEqual(len(result.disagreements), 1)
        disagreement = result.disagreements[0]
        self.assertEqual(disagreement.field, "publication_year")
        self.assertEqual(disagreement.classification, "provider-disagreement")
        self.assertEqual(
            tuple(provider for provider, _ in disagreement.provider_values),
            ("crossref", "openalex", "semantic-scholar"),
        )

    def test_keyword_order_is_formatting_only_but_author_order_is_substantive(self):
        record = self.record(
            keywords=("control", "energy"),
            authors=("Ada Lovelace", "Alan Turing"),
        )
        result = compare_audit_record(
            record,
            (
                ProviderEvidence(
                    provider="provider-a",
                    fields={
                        "keywords": ("energy", "control"),
                        "authors": ("Alan Turing", "Ada Lovelace"),
                    },
                ),
            ),
        )
        by_field = {
            item.field: item.classification
            for item in result.comparisons
        }
        self.assertEqual(by_field["keywords"], "formatting-only")
        self.assertEqual(by_field["authors"], "substantive-difference")

    def test_contributor_format_variants_are_not_substantive(self):
        record = self.record(
            authors=(
                "Ai-Rong Wei",
                "Arjan van der Schaft",
                "José García",
            ),
        )
        result = compare_audit_record(
            record,
            (
                ProviderEvidence(
                    provider="provider-a",
                    fields={
                        "authors": (
                            "Airong Wei",
                            "A. van der Schaft",
                            "Jose Garcia",
                        ),
                    },
                ),
            ),
        )

        self.assertEqual(
            result.comparisons[0].classification,
            "formatting-only",
        )

    def test_contributor_reorder_remains_substantive(self):
        record = self.record(
            authors=("Ada Lovelace", "Alan Turing"),
        )
        result = compare_audit_record(
            record,
            (
                ProviderEvidence(
                    provider="provider-a",
                    fields={"authors": ("Alan Turing", "Ada Lovelace")},
                ),
            ),
        )

        self.assertEqual(
            result.comparisons[0].classification,
            "substantive-difference",
        )

    def test_abstract_prefix_and_near_identical_markup_are_formatting_only(self):
        canonical = (
            "We establish a structure-preserving formulation for the model. "
            "The resulting method conserves the relevant energy balance."
        )
        provider = (
            "ABSTRACT We establish a structure-preserving formulation for the model. "
            "The resulting method conserves the relevant energy balance!"
        )
        result = compare_audit_record(
            self.record(abstract=canonical),
            (
                ProviderEvidence(
                    provider="provider-a",
                    fields={"abstract": provider},
                ),
            ),
        )

        self.assertEqual(
            result.comparisons[0].classification,
            "formatting-only",
        )

    def test_truncated_abstract_remains_substantive(self):
        canonical = (
            "This first sentence introduces the model. "
            "This second sentence contains the principal result. "
            "This third sentence explains the numerical validation."
        )
        result = compare_audit_record(
            self.record(abstract=canonical),
            (
                ProviderEvidence(
                    provider="provider-a",
                    fields={"abstract": "This first sentence introduces the model."},
                ),
            ),
        )

        self.assertEqual(
            result.comparisons[0].classification,
            "substantive-difference",
        )

    def test_title_tex_and_unicode_math_are_formatting_only(self):
        result = compare_audit_record(
            self.record(title=r"Index $\\le 1$ and $\\theta$-methods"),
            (
                ProviderEvidence(
                    provider="provider-a",
                    fields={"title": "Index ≤ 1 and θ-methods"},
                ),
            ),
        )

        self.assertEqual(
            result.comparisons[0].classification,
            "formatting-only",
        )

    def test_review_findings_mask_non_actionable_pairwise_noise(self):
        result = compare_audit_record(
            self.record(pages="10--20", volume="12"),
            (
                ProviderEvidence(
                    provider="provider-a",
                    fields={"pages": "10-20", "volume": ""},
                ),
            ),
        )

        self.assertEqual(audit_review_findings(result), ())

    def test_editor_author_role_disagreement_is_informational(self):
        result = compare_audit_record(
            self.record(
                authors=(),
                editors=("Ada Lovelace", "Alan Turing"),
            ),
            (
                ProviderEvidence(
                    provider="crossref",
                    fields={"editors": ("Ada Lovelace", "Alan Turing")},
                ),
                ProviderEvidence(
                    provider="openalex",
                    fields={"authors": ("A. Lovelace", "A. Turing")},
                ),
            ),
        )

        findings = audit_review_findings(result)

        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].classification, "role-disagreement")
        self.assertEqual(findings[0].field, "contributors")
        self.assertFalse(findings[0].actionable)

    def test_year_and_container_difference_is_not_actionable_when_canon_is_confirmed(self):
        result = compare_audit_record(
            self.record(
                publication_year="2024",
                container_title="Journal of Examples",
            ),
            (
                ProviderEvidence(
                    provider="crossref",
                    fields={
                        "publication_year": "2024",
                        "container_title": "Journal of Examples",
                    },
                ),
                ProviderEvidence(
                    provider="openalex",
                    fields={
                        "publication_year": "2023",
                        "container_title": "Proceedings of Examples",
                    },
                ),
            ),
        )

        self.assertEqual(audit_review_findings(result), ())

    def test_persisted_result_can_be_reclassified_without_provider_evidence(self):
        result = AuditResult(
            publication_id=PUBLICATION_ID,
            identifiers={"doi": "10.1000/example"},
            permalink="example",
            title="Example",
            comparisons=(
                AuditComparison(
                    provider="crossref",
                    field="pages",
                    classification="substantive-difference",
                    canonical_value="10--20",
                    provider_value="10-20",
                ),
                AuditComparison(
                    provider="semantic-scholar",
                    field="authors",
                    classification="substantive-difference",
                    canonical_value=("Ai-Rong Wei",),
                    provider_value=("Airong Wei",),
                ),
            ),
            provider_issues=(),
            disagreements=(),
        )

        updated = reclassify_audit_result(result)

        self.assertEqual(
            tuple(item.classification for item in updated.comparisons),
            ("formatting-only", "formatting-only"),
        )
        self.assertEqual(updated.provider_issues, result.provider_issues)

    def test_unsupported_provider_field_is_rejected(self):
        with self.assertRaisesRegex(AuditError, "unsupported audit field"):
            compare_audit_record(
                self.record(),
                (
                    ProviderEvidence(
                        provider="provider-a",
                        fields={"unknown_field": "value"},
                    ),
                ),
            )

    def test_duplicate_provider_names_are_rejected(self):
        evidence = ProviderEvidence(
            provider="crossref",
            fields={"title": "Example"},
        )
        with self.assertRaisesRegex(AuditError, "unique"):
            compare_audit_record(self.record(), (evidence, evidence))

    def test_nonavailable_evidence_cannot_carry_stale_metadata(self):
        with self.assertRaisesRegex(AuditError, "must not contain metadata"):
            ProviderEvidence(
                provider="crossref",
                status="unavailable",
                fields={"title": "stale"},
            )

    def test_publication_projection_preserves_review_context_and_contributors(self):
        publication = Publication(
            id=PUBLICATION_ID,
            identifiers={"doi": "10.1000/example", "isbn": "9780000000000"},
            type="book",
            title="A Book",
            authors=(Author(given="Ada", family="Lovelace"),),
            editors=(Editor(literal="Example Editor"),),
            abstract="An abstract.",
            container_title="Series",
            publication_year="2024",
            volume="3",
            issue="",
            pages="1--10",
            publisher="Example Press",
            event="",
            keywords=("Energy", "Control"),
            created_date=date(2024, 2, 3),
            permalink="a-book",
        )

        record = publication_audit_record(publication)

        self.assertEqual(record.publication_id, PUBLICATION_ID)
        self.assertEqual(record.permalink, "a-book")
        self.assertEqual(record.title, "A Book")
        self.assertEqual(record.fields["authors"], ("Ada Lovelace",))
        self.assertEqual(record.fields["editors"], ("Example Editor",))
        self.assertEqual(record.fields["created_date"], "2024-02-03")
        self.assertEqual(record.fields["keywords"], ("Energy", "Control"))

    def test_machine_readable_result_round_trip_is_strict(self):
        result = compare_audit_record(
            self.record(publication_year="2020"),
            (
                ProviderEvidence(
                    provider="crossref",
                    fields={"publication_year": "2021"},
                ),
                ProviderEvidence(
                    provider="semantic-scholar",
                    status="unavailable",
                    detail="HTTP 429",
                ),
            ),
        )
        payload = audit_result_data(result)

        restored = audit_result_from_data(payload)

        self.assertEqual(restored, result)

        broken = dict(payload)
        broken["classification_counts"] = {"substantive-difference": 999}
        with self.assertRaisesRegex(AuditError, "classification_counts"):
            audit_result_from_data(broken)

    def test_machine_readable_output_keeps_provenance_and_context(self):
        result = compare_audit_record(
            self.record(publication_year="2020"),
            (
                ProviderEvidence(
                    provider="crossref",
                    fields={"publication_year": "2021"},
                ),
                ProviderEvidence(
                    provider="openalex",
                    fields={"publication_year": "2022"},
                ),
            ),
        )

        payload = audit_result_data(result)

        self.assertEqual(payload["publication_id"], PUBLICATION_ID)
        self.assertEqual(payload["identifiers"], {"doi": "10.1000/example"})
        self.assertEqual(payload["permalink"], "port-hamiltonian-systems")
        self.assertEqual(payload["title"], "Port-Hamiltonian systems")
        self.assertEqual(
            payload["comparisons"][0]["classification"],
            "substantive-difference",
        )
        self.assertEqual(
            payload["disagreements"][0]["classification"],
            "provider-disagreement",
        )
        self.assertEqual(
            payload["classification_counts"],
            {
                "provider-disagreement": 1,
                "substantive-difference": 2,
            },
        )


if __name__ == "__main__":
    unittest.main()
