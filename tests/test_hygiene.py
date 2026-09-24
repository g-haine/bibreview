from __future__ import annotations

import unittest

from bibreview.hygiene import (
    format_abstract_hygiene_report,
    format_title_reference_hygiene_report,
    scan_abstract_hygiene,
    scan_title_reference_hygiene,
)
from bibreview.identity import new_publication_id
from bibreview.model import Author, Publication, Reference


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

    def test_embedded_graphic_requires_review(self) -> None:
        abstract = (
            'This study proposes a constructive stabilisation and '
            '<jats:inline-graphic xmlns:xlink="http://www.w3.org/1999/xlink" '
            'xlink:href="graphic/example-math-0002.png" '
            'xlink:title="urn:x-example:media:example-math-0002"/>'
            ' robust controller design method.'
        )
        finding = scan_abstract_hygiene((publication(abstract),)).findings[0]

        self.assertEqual(
            finding.families,
            ("embedded-graphic", "jats", "html-xml-markup"),
        )
        self.assertFalse(finding.deterministic_candidate)
        self.assertEqual(finding.normalization_hint, "embedded-graphic-review")

    def test_subscript_and_superscript_markup_requires_review(self) -> None:
        abstract = (
            "The equilibrium point is "
            "(u<inf>0</inf>,x<inf>0</inf>) and the supply rate is "
            "(y-y<inf>0</inf>)<sup>T</sup>(u-u<inf>0</inf>)."
        )
        finding = scan_abstract_hygiene((publication(abstract),)).findings[0]

        self.assertEqual(
            finding.families,
            ("script-markup", "html-xml-markup"),
        )
        self.assertFalse(finding.deterministic_candidate)
        self.assertEqual(finding.normalization_hint, "script-markup-review")
        self.assertIn("<inf>", finding.context)

    def test_tex_math_latex_for_every_inline_formula_is_deterministic(self) -> None:
        abstract = (
            'The operator <inline-formula>'
            '<tex-math notation="LaTeX">$g$</tex-math>'
            '</inline-formula> satisfies <inline-formula>'
            '<tex-math notation="LaTeX">$g^{2}=-1$</tex-math>'
            '</inline-formula>.'
        )
        finding = scan_abstract_hygiene((publication(abstract),)).findings[0]

        self.assertEqual(
            finding.families,
            ("inline-formula", "tex-math", "html-xml-markup"),
        )
        self.assertTrue(finding.deterministic_candidate)
        self.assertEqual(finding.normalization_hint, "embedded-tex-math")

    def test_partial_tex_math_inline_formula_coverage_requires_review(self) -> None:
        abstract = (
            '<inline-formula><tex-math notation="LaTeX">$g$</tex-math>'
            '</inline-formula>'
            '<inline-formula><italic>h</italic></inline-formula>'
        )
        finding = scan_abstract_hygiene((publication(abstract),)).findings[0]

        self.assertFalse(finding.deterministic_candidate)
        self.assertEqual(finding.normalization_hint, "review-required")

    def test_partial_mathml_tex_annotation_coverage_requires_review(self) -> None:
        abstract = (
            '<inline-formula><mml:math>'
            '<mml:annotation encoding="application/x-tex">V</mml:annotation>'
            '</mml:math></inline-formula>'
            '<inline-formula><mml:math><mml:mi>W</mml:mi></mml:math>'
            '</inline-formula>'
        )
        finding = scan_abstract_hygiene((publication(abstract),)).findings[0]

        self.assertFalse(finding.deterministic_candidate)
        self.assertEqual(finding.normalization_hint, "review-required")

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



class TitleReferenceHygieneTests(unittest.TestCase):
    def item(
        self,
        *,
        title: str = "Plain title",
        references: tuple[Reference, ...] = (),
        doi: str = "10.1/title",
    ) -> Publication:
        return Publication(
            id=new_publication_id(),
            identifiers={"doi": doi},
            title=title,
            authors=(Author(literal="Example Author"),),
            references=references,
        )

    def test_clean_title_and_citation_are_not_flagged(self) -> None:
        report = scan_title_reference_hygiene(
            (
                self.item(
                    references=(
                        Reference(
                            identifiers={"doi": "10.2/ref"},
                            citation="A. Author. Plain referenced work. Journal, 2024.",
                        ),
                    ),
                ),
            )
        )

        self.assertEqual(report.scanned_publications, 1)
        self.assertEqual(report.titles_present, 1)
        self.assertEqual(report.references_scanned, 1)
        self.assertEqual(report.citations_present, 1)
        self.assertEqual(report.title_findings, ())
        self.assertEqual(report.citation_findings, ())

    def test_small_caps_title_is_apparent_structural_candidate(self) -> None:
        report = scan_title_reference_hygiene(
            (self.item(title="A Port-<scp>H</scp>amiltonian approach"),)
        )
        finding = report.title_findings[0]

        self.assertEqual(
            finding.families,
            ("small-caps-markup", "html-xml-markup"),
        )
        self.assertTrue(finding.deterministic_candidate)
        self.assertEqual(finding.normalization_hint, "structural-unwrapping")
        self.assertEqual(finding.target, "title")

    def test_html_entity_in_reference_is_candidate_for_decoding(self) -> None:
        report = scan_title_reference_hygiene(
            (
                self.item(
                    references=(
                        Reference(
                            citation="Learning for Dynamics &amp; Control",
                        ),
                    ),
                ),
            )
        )
        finding = report.citation_findings[0]

        self.assertEqual(finding.families, ("html-entity",))
        self.assertTrue(finding.deterministic_candidate)
        self.assertEqual(finding.normalization_hint, "entity-decoding")
        self.assertTrue(finding.reference_key.startswith("sha256:"))

    def test_plain_tex_is_inventory_only_not_a_cleanup_candidate(self) -> None:
        report = scan_title_reference_hygiene(
            (self.item(title=r"Boundary control of \(H^1\) systems"),)
        )
        finding = report.title_findings[0]

        self.assertEqual(finding.families, ("tex-fragment",))
        self.assertFalse(finding.deterministic_candidate)
        self.assertEqual(finding.normalization_hint, "preserve-tex")

    def test_script_markup_requires_review(self) -> None:
        report = scan_title_reference_hygiene(
            (
                self.item(
                    references=(
                        Reference(citation="A model with H<sup>1</sup> regularity"),
                    ),
                ),
            )
        )
        finding = report.citation_findings[0]

        self.assertIn("script-markup", finding.families)
        self.assertFalse(finding.deterministic_candidate)
        self.assertEqual(finding.normalization_hint, "script-markup")

    def test_entity_decoding_is_reassessed_before_marking_title_safe(self) -> None:
        report = scan_title_reference_hygiene(
            (
                self.item(
                    title="Global finite-gain L&lt;inf&gt;2&lt;/inf&gt; stabilization"
                ),
            )
        )
        finding = report.title_findings[0]

        self.assertIn("html-entity", finding.families)
        self.assertFalse(finding.deterministic_candidate)
        self.assertEqual(finding.normalization_hint, "script-markup")

    def test_reference_identity_prefers_doi_and_disambiguates_duplicate_hashes(self) -> None:
        duplicate = "Same citation without DOI"
        report = scan_title_reference_hygiene(
            (
                self.item(
                    references=(
                        Reference(
                            identifiers={"doi": "10.2/ref"},
                            citation="DOI reference &amp; metadata",
                        ),
                        Reference(citation=duplicate + " &amp;"),
                        Reference(citation=duplicate + " &amp;"),
                    ),
                ),
            )
        )

        keys = tuple(item.reference_key for item in report.citation_findings)
        self.assertEqual(keys[0], "doi:10.2/ref")
        self.assertRegex(keys[1], r"^sha256:[0-9a-f]{16}#1$")
        self.assertRegex(keys[2], r"^sha256:[0-9a-f]{16}#2$")
        self.assertEqual(keys[1][:-2], keys[2][:-2])

    def test_report_separates_title_and_citation_family_counts(self) -> None:
        report = scan_title_reference_hygiene(
            (
                self.item(
                    title="<scp>H</scp> systems",
                    references=(Reference(citation="A &amp; B"),),
                ),
                self.item(title="Plain", doi="10.1/plain"),
            )
        )

        self.assertEqual(report.suspicious_titles, 1)
        self.assertEqual(report.suspicious_citations, 1)
        self.assertEqual(report.title_family_counts["small-caps-markup"], 1)
        self.assertEqual(report.citation_family_counts["html-entity"], 1)

        compact = format_title_reference_hygiene_report(report)
        self.assertIn("Titles with hygiene signals   : 1", compact)
        self.assertIn("Citations with hygiene signals: 1", compact)

        verbose = format_title_reference_hygiene_report(report, verbose=True)
        self.assertIn("Title findings:", verbose)
        self.assertIn("Reference citation findings:", verbose)
        self.assertIn("Reference: sha256:", verbose)


if __name__ == "__main__":
    unittest.main()
