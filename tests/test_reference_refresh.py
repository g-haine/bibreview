import unittest
from types import MappingProxyType

from bibreview.identity import new_publication_id
from bibreview.model import Author, Publication, Reference
from bibreview.pipeline.references import (
    compare_reference_reconstruction,
    reconstruct_provider_references,
    reference_refresh_result_from_data,
    references_fingerprint,
)


def publication(*references: Reference) -> Publication:
    return Publication(
        id=new_publication_id(),
        identifiers={"doi": "10.1000/parent"},
        title="Parent work",
        authors=(Author(literal="Example Author"),),
        references=references,
    )


class ReferenceReconstructionTests(unittest.TestCase):
    def test_missing_reference_field_is_unavailable(self) -> None:
        reconstructed = reconstruct_provider_references({"DOI": "10.1000/parent"})

        self.assertFalse(reconstructed.available)
        self.assertEqual(reconstructed.reason, "provider-references-missing")
        self.assertEqual(reconstructed.references, ())

    def test_provider_reference_uses_parent_metadata_without_doi_citation_lookup(self) -> None:
        reconstructed = reconstruct_provider_references(
            {
                "reference": [
                    {
                        "DOI": "10.2000/CHILD",
                        "author": "Doe, J.",
                        "article-title": "Referenced work",
                        "journal-title": "Journal",
                        "year": "2024",
                    }
                ]
            }
        )

        self.assertTrue(reconstructed.available)
        item = reconstructed.candidates[0]
        self.assertEqual(dict(item.reference.identifiers), {"doi": "10.2000/child"})
        self.assertEqual(
            item.reference.citation,
            "Doe, J., Referenced work. Journal (2024)",
        )

    def test_provider_citation_is_normalized_with_t2(self) -> None:
        reconstructed = reconstruct_provider_references(
            {
                "reference": [
                    {
                        "unstructured": "Systems &amp; Control Letters",
                    }
                ]
            }
        )

        item = reconstructed.candidates[0]
        self.assertTrue(item.deterministic)
        self.assertEqual(item.reason, "entity-decoding")
        self.assertEqual(item.reference.citation, "Systems & Control Letters")

    def test_refused_provider_citation_is_preserved_verbatim(self) -> None:
        reconstructed = reconstruct_provider_references(
            {
                "reference": [
                    {
                        "unstructured": "A model with H<sup>1</sup> regularity",
                    }
                ]
            }
        )

        item = reconstructed.candidates[0]
        self.assertFalse(item.deterministic)
        self.assertEqual(item.reason, "script-markup")
        self.assertEqual(
            item.reference.citation,
            "A model with H<sup>1</sup> regularity",
        )

    def test_invalid_provider_reference_entry_makes_whole_reconstruction_unavailable(self) -> None:
        reconstructed = reconstruct_provider_references(
            {"reference": [{"unstructured": "Valid"}, "invalid"]}
        )

        self.assertFalse(reconstructed.available)
        self.assertEqual(reconstructed.reason, "provider-reference-2-invalid")


class ReferenceComparisonTests(unittest.TestCase):
    def test_exact_reference_list_is_unchanged(self) -> None:
        current = Reference(
            identifiers={"doi": "10.2000/child"},
            citation="Referenced work",
        )
        item = publication(current)
        reconstructed = reconstruct_provider_references(
            {
                "reference": [
                    {
                        "DOI": "10.2000/child",
                        "unstructured": "Referenced work",
                    }
                ]
            }
        )

        result = compare_reference_reconstruction(item, reconstructed)

        self.assertEqual(result.classification, "unchanged")
        self.assertFalse(result.actionable)
        self.assertEqual(result.changed_indices, ())
        self.assertEqual(
            result.current_fingerprint,
            result.proposed_fingerprint,
        )
        self.assertEqual(result.proposed_references, ())

    def test_exact_t2_citation_cleanup_is_safe_update(self) -> None:
        current = Reference(citation="Systems &amp; Control Letters")
        item = publication(current)
        reconstructed = reconstruct_provider_references(
            {
                "reference": [
                    {"unstructured": "Systems &amp; Control Letters"}
                ]
            }
        )

        result = compare_reference_reconstruction(item, reconstructed)

        self.assertEqual(result.classification, "safe-update")
        self.assertTrue(result.actionable)
        self.assertEqual(result.reason, "deterministic-citation-normalization")
        self.assertEqual(result.changed_indices, (1,))
        self.assertEqual(
            result.proposed_references[0].citation,
            "Systems & Control Letters",
        )

    def test_empty_provider_doi_citation_reuses_exact_canonical_position(self) -> None:
        current = Reference(
            identifiers={"doi": "10.2000/child"},
            citation="Rich canonical citation",
        )
        item = publication(current)
        reconstructed = reconstruct_provider_references(
            {"reference": [{"DOI": "10.2000/child"}]}
        )

        result = compare_reference_reconstruction(item, reconstructed)

        self.assertEqual(result.classification, "unchanged")
        self.assertEqual(result.changed_indices, ())
        self.assertEqual(
            result.current_fingerprint,
            result.proposed_fingerprint,
        )

    def test_empty_provider_doi_citation_can_safely_normalize_canonical_text(self) -> None:
        current = Reference(
            identifiers={"doi": "10.2000/child"},
            citation="Systems &amp; Control Letters",
        )
        item = publication(current)
        reconstructed = reconstruct_provider_references(
            {"reference": [{"DOI": "10.2000/child"}]}
        )

        result = compare_reference_reconstruction(item, reconstructed)

        self.assertEqual(result.classification, "safe-update")
        self.assertEqual(result.changed_indices, (1,))
        self.assertEqual(
            result.proposed_references[0].citation,
            "Systems & Control Letters",
        )

    def test_empty_provider_doi_citation_never_falls_back_across_identifier_drift(self) -> None:
        current = Reference(
            identifiers={"doi": "10.2000/old"},
            citation="Rich canonical citation",
        )
        item = publication(current)
        reconstructed = reconstruct_provider_references(
            {"reference": [{"DOI": "10.2000/new"}]}
        )

        result = compare_reference_reconstruction(item, reconstructed)

        self.assertEqual(result.classification, "review-required")
        self.assertEqual(result.reason, "reference-identifiers-changed:1")
        self.assertEqual(result.proposed_references[0].citation, "")

    def test_empty_non_doi_provider_citation_never_uses_positional_fallback(self) -> None:
        current = Reference(citation="Canonical without DOI")
        item = publication(current)
        reconstructed = reconstruct_provider_references(
            {"reference": [{}]}
        )

        result = compare_reference_reconstruction(item, reconstructed)

        self.assertEqual(result.classification, "review-required")
        self.assertEqual(result.reason, "reference-citation-drift:1")
        self.assertEqual(result.proposed_references[0].citation, "")

    def test_refused_canonical_doi_fallback_is_preserved_not_repaired(self) -> None:
        value = "A model with H<sup>1</sup> regularity"
        current = Reference(
            identifiers={"doi": "10.2000/child"},
            citation=value,
        )
        item = publication(current)
        reconstructed = reconstruct_provider_references(
            {"reference": [{"DOI": "10.2000/child"}]}
        )

        result = compare_reference_reconstruction(item, reconstructed)

        self.assertEqual(result.classification, "unchanged")
        self.assertEqual(result.changed_indices, ())
        self.assertEqual(
            result.provider_refusals,
            ((1, "canonical-doi-fallback:script-markup"),),
        )

    def test_provider_text_change_beyond_sanitizer_requires_review(self) -> None:
        item = publication(Reference(citation="Old citation"))
        reconstructed = reconstruct_provider_references(
            {"reference": [{"unstructured": "Updated citation"}]}
        )

        result = compare_reference_reconstruction(item, reconstructed)

        self.assertEqual(result.classification, "review-required")
        self.assertEqual(result.reason, "reference-citation-drift:1")

    def test_added_provider_doi_requires_review(self) -> None:
        item = publication(Reference(citation="Same citation"))
        reconstructed = reconstruct_provider_references(
            {
                "reference": [
                    {
                        "DOI": "10.2000/new-id",
                        "unstructured": "Same citation",
                    }
                ]
            }
        )

        result = compare_reference_reconstruction(item, reconstructed)

        self.assertEqual(result.classification, "review-required")
        self.assertEqual(result.reason, "reference-identifiers-changed:1")

    def test_changed_reference_count_requires_review(self) -> None:
        item = publication(Reference(citation="One"))
        reconstructed = reconstruct_provider_references(
            {
                "reference": [
                    {"unstructured": "One"},
                    {"unstructured": "Two"},
                ]
            }
        )

        result = compare_reference_reconstruction(item, reconstructed)

        self.assertEqual(result.classification, "review-required")
        self.assertEqual(result.reason, "reference-count-changed")
        self.assertEqual(result.changed_indices, (2,))

    def test_reordered_doi_references_require_review(self) -> None:
        item = publication(
            Reference(identifiers={"doi": "10.1/a"}, citation="A"),
            Reference(identifiers={"doi": "10.1/b"}, citation="B"),
        )
        reconstructed = reconstruct_provider_references(
            {
                "reference": [
                    {"DOI": "10.1/b", "unstructured": "B"},
                    {"DOI": "10.1/a", "unstructured": "A"},
                ]
            }
        )

        result = compare_reference_reconstruction(item, reconstructed)

        self.assertEqual(result.classification, "review-required")
        self.assertEqual(result.reason, "reference-identifiers-changed:1")

    def test_unicode_repair_from_provider_requires_human_review(self) -> None:
        item = publication(Reference(citation="Est�vez Schwarz"))
        reconstructed = reconstruct_provider_references(
            {"reference": [{"unstructured": "Estévez Schwarz"}]}
        )

        result = compare_reference_reconstruction(item, reconstructed)

        self.assertEqual(result.classification, "review-required")
        self.assertEqual(result.reason, "reference-citation-drift:1")

    def test_same_refused_citation_is_unchanged_not_auto_repaired(self) -> None:
        value = "A model with H<sup>1</sup> regularity"
        item = publication(Reference(citation=value))
        reconstructed = reconstruct_provider_references(
            {"reference": [{"unstructured": value}]}
        )

        result = compare_reference_reconstruction(item, reconstructed)

        self.assertEqual(result.classification, "unchanged")
        self.assertEqual(
            result.provider_refusals,
            ((1, "script-markup"),),
        )

    def test_missing_provider_references_is_unavailable(self) -> None:
        item = publication(Reference(citation="Existing"))
        reconstructed = reconstruct_provider_references({})

        result = compare_reference_reconstruction(item, reconstructed)

        self.assertEqual(result.classification, "unavailable")
        self.assertEqual(result.reason, "provider-references-missing")
        self.assertEqual(result.proposed_references, ())

    def test_result_roundtrip_is_lossless(self) -> None:
        item = publication(
            Reference(
                identifiers=MappingProxyType({"doi": "10.1/a"}),
                citation="A &amp; B",
            )
        )
        reconstructed = reconstruct_provider_references(
            {
                "reference": [
                    {
                        "DOI": "10.1/a",
                        "unstructured": "A &amp; B",
                    }
                ]
            }
        )
        result = compare_reference_reconstruction(item, reconstructed)

        loaded = reference_refresh_result_from_data(result.data())

        self.assertEqual(loaded, result)

    def test_reference_fingerprint_is_order_sensitive(self) -> None:
        a = Reference(citation="A")
        b = Reference(citation="B")

        self.assertNotEqual(
            references_fingerprint((a, b)),
            references_fingerprint((b, a)),
        )


if __name__ == "__main__":
    unittest.main()
