from __future__ import annotations

import unittest

from bibreview.hygiene import (
    format_abstract_hygiene_report,
    scan_abstract_hygiene,
)
from bibreview.identity import new_publication_id
from bibreview.model import Author, Publication


def publication(abstract: str, *, doi: str = "10.1/example", title: str = "Example") -> Publication:
    return Publication(
        id=new_publication_id(),
        identifiers={"doi": doi},
        title=title,
        authors=(Author(literal="Example Author"),),
        abstract=abstract,
    )


class AbstractHygieneTests(unittest.TestCase):
    def test_clean_plain_text_and_tex_are_not_flagged(self) -> None:
        report = scan_abstract_hygiene(
            (
                publication("Plain abstract."),
                publication(r"Let \(V \oplus V^*\) be a vector space.", doi="10.1/tex"),
            )
        )
        self.assertEqual(report.scanned_publications, 2)
        self.assertEqual(report.abstracts_present, 2)
        self.assertEqual(report.findings, ())

    def test_mathml_with_tex_annotation_is_classified_as_deterministic_candidate(self) -> None:
        abstract = (
            '<p>A space <inline-formula content-type="math/mathml">'
            '<mml:math xmlns:mml="http://www.w3.org/1998/Math/MathML">'
            '<mml:annotation encoding="application/x-tex">'
            r'V \oplus V^{\ast}'
            '</mml:annotation></mml:math></inline-formula>.</p>'
        )
        report = scan_abstract_hygiene((publication(abstract),))
        finding = report.findings[0]

        self.assertEqual(
            finding.families,
            ("inline-formula", "mathml", "html-xml-markup"),
        )
        self.assertTrue(finding.deterministic_candidate)
        self.assertEqual(finding.normalization_hint, "embedded-tex-annotation")
        self.assertIn("<inline-formula", finding.context)

    def test_mathml_without_tex_annotation_requires_review(self) -> None:
        abstract = (
            '<inline-formula><mml:math xmlns:mml="urn:test">'
            '<mml:mi>V</mml:mi></mml:math></inline-formula>'
        )
        finding = scan_abstract_hygiene((publication(abstract),)).findings[0]
        self.assertFalse(finding.deterministic_candidate)
        self.assertEqual(finding.normalization_hint, "review-required")

    def test_structural_html_without_math_is_candidate_for_unwrapping(self) -> None:
        finding = scan_abstract_hygiene(
            (publication("<p>First paragraph.</p><p>Second paragraph.</p>"),)
        ).findings[0]
        self.assertEqual(finding.families, ("html-xml-markup",))
        self.assertTrue(finding.deterministic_candidate)
        self.assertEqual(finding.normalization_hint, "structural-unwrapping")

    def test_jats_and_comment_are_classified(self) -> None:
        finding = scan_abstract_hygiene(
            (publication("<jats:p>Text<!-- presentation --></jats:p>"),)
        ).findings[0]
        self.assertEqual(
            finding.families,
            ("jats", "xml-comment", "html-xml-markup"),
        )
        self.assertTrue(finding.deterministic_candidate)

    def test_escaped_markup_requires_review(self) -> None:
        finding = scan_abstract_hygiene(
            (publication("&lt;inline-formula&gt;V&lt;/inline-formula&gt;"),)
        ).findings[0]
        self.assertEqual(finding.families, ("escaped-markup",))
        self.assertFalse(finding.deterministic_candidate)

    def test_legacy_renderer_marker_is_detected_without_markup(self) -> None:
        finding = scan_abstract_hygiene(
            (publication(r"V^{\ast }[[:space:]]}"),)
        ).findings[0]
        self.assertEqual(finding.families, ("legacy-renderer-marker",))
        self.assertTrue(finding.deterministic_candidate)
        self.assertEqual(finding.normalization_hint, "legacy-marker-removal")

    def test_unbalanced_structured_tags_require_review(self) -> None:
        finding = scan_abstract_hygiene(
            (publication("<p>Broken <span>text</p>"),)
        ).findings[0]
        self.assertIn("unbalanced-structured-tags", finding.families)
        self.assertFalse(finding.deterministic_candidate)

    def test_summary_counts_families_and_verbose_format_lists_records(self) -> None:
        report = scan_abstract_hygiene(
            (
                publication("<p>Markup</p>", doi="10.1/html", title="HTML"),
                publication(
                    "<inline-formula><mml:math><mml:mi>V</mml:mi></mml:math></inline-formula>",
                    doi="10.1/math",
                    title="Math",
                ),
                publication("", doi="10.1/empty", title="Empty"),
            )
        )

        self.assertEqual(report.scanned_publications, 3)
        self.assertEqual(report.abstracts_present, 2)
        self.assertEqual(report.suspicious_abstracts, 2)
        self.assertEqual(report.deterministic_candidates, 1)
        self.assertEqual(report.review_required, 1)
        self.assertEqual(report.family_counts["html-xml-markup"], 2)

        compact = format_abstract_hygiene_report(report)
        self.assertIn("Suspicious abstracts     : 2", compact)
        self.assertNotIn("10.1/html", compact)

        verbose = format_abstract_hygiene_report(report, verbose=True)
        self.assertIn("10.1/html: HTML", verbose)
        self.assertIn("10.1/math: Math", verbose)


if __name__ == "__main__":
    unittest.main()
