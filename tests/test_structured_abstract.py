from __future__ import annotations

import unittest

from bibreview.structured_abstract import normalize_structured_abstract


class StructuredAbstractNormalizationTests(unittest.TestCase):
    def test_plain_text_and_tex_are_unchanged(self) -> None:
        for value in (
            "Plain abstract.",
            r"Let \(V \oplus V^*\) be a vector space.",
        ):
            with self.subTest(value=value):
                result = normalize_structured_abstract(value)
                self.assertTrue(result.deterministic)
                self.assertFalse(result.changed)
                self.assertEqual(result.reason, "already-clean")
                self.assertEqual(result.normalized, value)

    def test_structural_paragraphs_preserve_text_boundaries(self) -> None:
        result = normalize_structured_abstract(
            "<jats:p>First paragraph.</jats:p>"
            "<jats:p>Second paragraph.</jats:p>"
        )

        self.assertTrue(result.deterministic)
        self.assertEqual(result.normalized, "First paragraph. Second paragraph.")

    def test_typographic_wrappers_preserve_adjacent_text(self) -> None:
        result = normalize_structured_abstract(
            "port-<jats:styled-content style=\"fixed-case\">H</jats:styled-content>"
            "amiltonian with <jats:italic>ad hoc</jats:italic> notation and "
            "<jats:bold>one</jats:bold> result."
        )

        self.assertTrue(result.deterministic)
        self.assertEqual(
            result.normalized,
            "port-Hamiltonian with ad hoc notation and one result.",
        )

    def test_mathml_prefers_application_x_tex(self) -> None:
        result = normalize_structured_abstract(
            '<p>A space <inline-formula content-type="math/mathml">'
            '<mml:math xmlns:mml="http://www.w3.org/1998/Math/MathML">'
            '<mml:semantics><mml:mi>V</mml:mi>'
            '<mml:annotation encoding="application/x-tex">'
            r'V \oplus V^{\ast}'
            '</mml:annotation></mml:semantics></mml:math>'
            '</inline-formula> is considered.</p>'
        )

        self.assertTrue(result.deterministic)
        self.assertEqual(
            result.normalized,
            r"A space \(V \oplus V^{\ast}\) is considered.",
        )

    def test_multiple_inline_formulas_preserve_order(self) -> None:
        result = normalize_structured_abstract(
            '<inline-formula><mml:annotation encoding="application/x-tex">'
            'V</mml:annotation></inline-formula> then '
            '<inline-formula><mml:annotation encoding="application/x-tex">'
            'W</mml:annotation></inline-formula>'
        )

        self.assertTrue(result.deterministic)
        self.assertEqual(result.normalized, r"\(V\) then \(W\)")

    def test_tex_math_latex_is_canonicalized(self) -> None:
        result = normalize_structured_abstract(
            '<inline-formula><tex-math notation="LaTeX">$g$</tex-math>'
            '</inline-formula> and '
            '<inline-formula><tex-math notation="LaTeX">'
            r'\\( {H_\infty } \\)'
            '</tex-math></inline-formula>'
        )

        self.assertTrue(result.deterministic)
        self.assertEqual(
            result.normalized,
            r"\(g\) and \({H_\infty }\)",
        )

    def test_application_x_tex_has_priority_over_tex_math(self) -> None:
        result = normalize_structured_abstract(
            '<inline-formula>'
            '<tex-math notation="LaTeX">$fallback$</tex-math>'
            '<mml:annotation encoding="application/x-tex">preferred</mml:annotation>'
            '</inline-formula>'
        )

        self.assertTrue(result.deterministic)
        self.assertEqual(result.normalized, r"\(preferred\)")

    def test_presentation_comments_are_removed(self) -> None:
        result = normalize_structured_abstract(
            "<jats:p>Value<!-- presentation only --> preserved.</jats:p>"
        )

        self.assertTrue(result.deterministic)
        self.assertEqual(result.normalized, "Value preserved.")

    def test_embedded_graphic_is_refused_without_mutation(self) -> None:
        value = (
            'A controller <jats:inline-graphic '
            'xlink:href="graphic/math-0002.png"/> is proposed.'
        )
        result = normalize_structured_abstract(value)

        self.assertFalse(result.deterministic)
        self.assertFalse(result.changed)
        self.assertEqual(result.reason, "embedded-graphic")
        self.assertEqual(result.normalized, value)

    def test_script_markup_is_refused_without_mutation(self) -> None:
        value = "(u<inf>0</inf>,x<inf>0</inf>)<sup>T</sup>"
        result = normalize_structured_abstract(value)

        self.assertFalse(result.deterministic)
        self.assertEqual(result.reason, "script-markup")
        self.assertEqual(result.normalized, value)

    def test_escaped_markup_is_refused_without_mutation(self) -> None:
        value = "&lt;abstract&gt;&lt;p&gt;Text&lt;/p&gt;&lt;/abstract&gt;"
        result = normalize_structured_abstract(value)

        self.assertFalse(result.deterministic)
        self.assertEqual(result.reason, "escaped-markup")
        self.assertEqual(result.normalized, value)

    def test_formula_without_trusted_text_is_refused(self) -> None:
        value = (
            "<inline-formula><mml:math><mml:mi>V</mml:mi></mml:math>"
            "</inline-formula>"
        )
        result = normalize_structured_abstract(value)

        self.assertFalse(result.deterministic)
        self.assertEqual(result.reason, "formula-without-trusted-text")
        self.assertEqual(result.normalized, value)

    def test_partial_formula_coverage_is_refused(self) -> None:
        value = (
            '<inline-formula><tex-math notation="LaTeX">$g$</tex-math>'
            '</inline-formula>'
            '<inline-formula><italic>h</italic></inline-formula>'
        )
        result = normalize_structured_abstract(value)

        self.assertFalse(result.deterministic)
        self.assertEqual(result.reason, "formula-without-trusted-text")
        self.assertEqual(result.normalized, value)

    def test_unknown_balanced_markup_is_refused(self) -> None:
        value = "<custom-semantic>Text</custom-semantic>"
        result = normalize_structured_abstract(value)

        self.assertFalse(result.deterministic)
        self.assertEqual(result.reason, "unsupported-markup")
        self.assertEqual(result.normalized, value)

    def test_unbalanced_markup_is_refused(self) -> None:
        value = "<p>Broken <span>text</p>"
        result = normalize_structured_abstract(value)

        self.assertFalse(result.deterministic)
        self.assertEqual(result.reason, "unbalanced-markup")
        self.assertEqual(result.normalized, value)

    def test_normalization_is_idempotent(self) -> None:
        first = normalize_structured_abstract(
            "<jats:p>A <jats:italic>simple</jats:italic> abstract.</jats:p>"
        )
        second = normalize_structured_abstract(first.normalized)

        self.assertTrue(first.deterministic)
        self.assertTrue(first.changed)
        self.assertTrue(second.deterministic)
        self.assertFalse(second.changed)
        self.assertEqual(second.normalized, first.normalized)


if __name__ == "__main__":
    unittest.main()
