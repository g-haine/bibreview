import unittest

from bibreview.structured_title import (
    normalize_structured_citation,
    normalize_structured_title,
)


class StructuredTitleNormalizationTests(unittest.TestCase):
    def test_plain_text_is_unchanged(self) -> None:
        result = normalize_structured_title("Plain port-Hamiltonian title")

        self.assertTrue(result.deterministic)
        self.assertFalse(result.changed)
        self.assertEqual(result.normalized, "Plain port-Hamiltonian title")
        self.assertEqual(result.reason, "already-clean")

    def test_existing_tex_is_preserved_exactly(self) -> None:
        value = r"Fixed-Time $\mathcal {H}_{\infty }$ Control"
        result = normalize_structured_title(value)

        self.assertTrue(result.deterministic)
        self.assertFalse(result.changed)
        self.assertEqual(result.normalized, value)

    def test_plain_entity_is_decoded(self) -> None:
        result = normalize_structured_title(
            "Discrete-Time I&amp;I Adaptive Control"
        )

        self.assertTrue(result.deterministic)
        self.assertTrue(result.changed)
        self.assertEqual(
            result.normalized,
            "Discrete-Time I&I Adaptive Control",
        )
        self.assertEqual(result.reason, "entity-decoding")

    def test_nested_entity_decoding_rescans_revealed_script_markup(self) -> None:
        value = (
            "Interpolation-based &amp;#x210C;&lt;inf&gt;2&lt;/inf&gt; "
            "model reduction"
        )
        result = normalize_structured_title(value)

        self.assertFalse(result.deterministic)
        self.assertFalse(result.changed)
        self.assertEqual(result.normalized, value)
        self.assertEqual(result.reason, "script-markup")

    def test_double_escaped_subscript_markup_is_refused(self) -> None:
        value = (
            "Stabilization of the &amp;lt;i&amp;gt;L&amp;lt;/i&amp;gt;"
            "&amp;lt;sub&amp;gt;1&amp;lt;/sub&amp;gt; Lagrange Point"
        )
        result = normalize_structured_title(value)

        self.assertFalse(result.deterministic)
        self.assertEqual(result.reason, "script-markup")
        self.assertEqual(result.normalized, value)

    def test_small_caps_wrapper_is_losslessly_unwrapped(self) -> None:
        result = normalize_structured_title(
            "A Port‐<scp>H</scp>amiltonian Approach"
        )

        self.assertTrue(result.deterministic)
        self.assertTrue(result.changed)
        self.assertEqual(
            result.normalized,
            "A Port‐Hamiltonian Approach",
        )
        self.assertEqual(result.reason, "structural-unwrapping")

    def test_entity_then_small_caps_wrapper_is_normalized(self) -> None:
        result = normalize_structured_title(
            "parameterized <scp>discrete‐time</scp> systems via I&amp;I"
        )

        self.assertTrue(result.deterministic)
        self.assertEqual(
            result.normalized,
            "parameterized discrete‐time systems via I&I",
        )
        self.assertEqual(
            result.reason,
            "entity-decoding+structural-unwrapping",
        )

    def test_direct_subscript_markup_is_refused(self) -> None:
        value = "Stabilisation and ℋ<sub>∞</sub>control"
        result = normalize_structured_title(value)

        self.assertFalse(result.deterministic)
        self.assertFalse(result.changed)
        self.assertEqual(result.reason, "script-markup")
        self.assertEqual(result.normalized, value)

    def test_unbalanced_revealed_script_markup_is_refused_before_parsing(self) -> None:
        value = (
            "Global finite-gain L&lt;SUB align=right&gt;2 stabilisation"
        )
        result = normalize_structured_title(value)

        self.assertFalse(result.deterministic)
        self.assertEqual(result.reason, "unbalanced-markup")
        self.assertEqual(result.normalized, value)


class StructuredCitationNormalizationTests(unittest.TestCase):
    def test_html_entity_is_decoded(self) -> None:
        result = normalize_structured_citation(
            "Systems &amp; Control Letters 54, 911–917 (2005)"
        )

        self.assertTrue(result.deterministic)
        self.assertTrue(result.changed)
        self.assertEqual(
            result.normalized,
            "Systems & Control Letters 54, 911–917 (2005)",
        )

    def test_nested_html_entity_is_decoded_to_plain_text(self) -> None:
        result = normalize_structured_citation(
            "IET Control Theory &amp;amp; Appl 10, 1844–1858 (2016)"
        )

        self.assertTrue(result.deterministic)
        self.assertEqual(
            result.normalized,
            "IET Control Theory & Appl 10, 1844–1858 (2016)",
        )

    def test_presentation_markup_is_unwrapped(self) -> None:
        result = normalize_structured_citation(
            "A. Author, <em>Book title</em>, Publisher."
        )

        self.assertTrue(result.deterministic)
        self.assertEqual(
            result.normalized,
            "A. Author, Book title, Publisher.",
        )
        self.assertEqual(result.reason, "structural-unwrapping")

    def test_semantic_title_wrapper_is_unwrapped_in_citation(self) -> None:
        result = normalize_structured_citation(
            "A. Author. &lt;title&gt;Paper title&lt;/title&gt; Proceedings."
        )

        self.assertTrue(result.deterministic)
        self.assertEqual(
            result.normalized,
            "A. Author. Paper title Proceedings.",
        )
        self.assertEqual(
            result.reason,
            "entity-decoding+structural-unwrapping",
        )

    def test_escaped_inline_formula_extracts_explicit_tex(self) -> None:
        value = (
            "Liu, Y. Composite Robust &lt;inline-formula&gt;"
            "&lt;tex-math notation=\"LaTeX\"&gt;$H_\\infty$"
            "&lt;/tex-math&gt;&lt;/inline-formula&gt; Control"
        )
        result = normalize_structured_citation(value)

        self.assertTrue(result.deterministic)
        self.assertEqual(
            result.normalized,
            r"Liu, Y. Composite Robust \(H_\infty\) Control",
        )
        self.assertEqual(
            result.reason,
            "entity-decoding+embedded-tex",
        )

    def test_tex_wrapper_without_formula_is_trusted(self) -> None:
        value = (
            "An Iterative Method in the&lt;tex&gt;$s$&lt;/tex&gt;- "
            "and&lt;tex&gt;$z$&lt;/tex&gt;-Domains"
        )
        result = normalize_structured_citation(value)

        self.assertTrue(result.deterministic)
        self.assertEqual(
            result.normalized,
            r"An Iterative Method in the\(s\)- and\(z\)-Domains",
        )
        self.assertEqual(
            result.reason,
            "entity-decoding+embedded-tex",
        )

    def test_simple_mathml_subscript_is_converted_to_tex(self) -> None:
        value = (
            'Stabilization and <mml:math '
            'xmlns:mml="http://www.w3.org/1998/Math/MathML">'
            "<mml:msub><mml:mrow><mml:mi>H</mml:mi></mml:mrow>"
            "<mml:mrow><mml:mi>∞</mml:mi></mml:mrow></mml:msub>"
            "</mml:math> control"
        )
        result = normalize_structured_citation(value)

        self.assertTrue(result.deterministic)
        self.assertEqual(
            result.normalized,
            r"Stabilization and \(H_{\infty}\) control",
        )
        self.assertEqual(result.reason, "mathml-to-tex")

    def test_mathml_script_variant_is_preserved_semantically(self) -> None:
        value = (
            '<mml:math xmlns:mml="http://www.w3.org/1998/Math/MathML">'
            '<mml:msub><mml:mi mathvariant="script">H</mml:mi>'
            "<mml:mn>2</mml:mn></mml:msub></mml:math>"
        )
        result = normalize_structured_citation(value)

        self.assertTrue(result.deterministic)
        self.assertEqual(
            result.normalized,
            r"\(\mathcal{H}_{2}\)",
        )

    def test_mathml_bold_script_variant_is_preserved_semantically(self) -> None:
        value = (
            '<mml:math xmlns:mml="http://www.w3.org/1998/Math/MathML">'
            '<mml:msub><mml:mi mathvariant="bold-script">H</mml:mi>'
            "<mml:mi>∞</mml:mi></mml:msub></mml:math>"
        )
        result = normalize_structured_citation(value)

        self.assertTrue(result.deterministic)
        self.assertEqual(
            result.normalized,
            r"\(\boldsymbol{\mathcal{H}}_{\infty}\)",
        )

    def test_observed_mathml_row_expression_is_supported(self) -> None:
        value = (
            '<mml:math xmlns:mml="http://www.w3.org/1998/Math/MathML">'
            "<mml:mrow><mml:mi>n</mml:mi><mml:mo>+</mml:mo>"
            "<mml:mi>m</mml:mi></mml:mrow></mml:math>"
        )
        result = normalize_structured_citation(value)

        self.assertTrue(result.deterministic)
        self.assertEqual(result.normalized, r"\(n+m\)")

    def test_all_phraise_mathml_expression_shapes_are_supported(self) -> None:
        cases = (
            (
                "<mml:msub><mml:mrow><mml:mi>H</mml:mi></mml:mrow>"
                "<mml:mrow><mml:mi>∞</mml:mi></mml:mrow></mml:msub>",
                r"H_{\\infty}",
            ),
            (
                "<mml:msub><mml:mrow><mml:mi>L</mml:mi></mml:mrow>"
                "<mml:mrow><mml:mn>2</mml:mn></mml:mrow></mml:msub>",
                r"L_{2}",
            ),
            (
                "<mml:msub><mml:mi>H</mml:mi><mml:mi>∞</mml:mi></mml:msub>",
                r"H_{\\infty}",
            ),
            (
                "<mml:msub><mml:mrow><mml:mi>H</mml:mi></mml:mrow>"
                "<mml:mrow><mml:mn>2</mml:mn></mml:mrow></mml:msub>",
                r"H_{2}",
            ),
            (
                "<mml:mrow><mml:msub><mml:mrow><mml:mi>H</mml:mi></mml:mrow>"
                "<mml:mrow><mml:mi>∞</mml:mi></mml:mrow></mml:msub></mml:mrow>",
                r"H_{\\infty}",
            ),
            (
                "<mml:mrow><mml:msub><mml:mi>H</mml:mi>"
                "<mml:mi>∞</mml:mi></mml:msub></mml:mrow>",
                r"H_{\\infty}",
            ),
            (
                "<mml:msub><mml:mrow><mml:mi>ℒ</mml:mi></mml:mrow>"
                "<mml:mrow><mml:mn>2</mml:mn></mml:mrow></mml:msub>",
                r"\\mathcal{L}_{2}",
            ),
            ("<mml:mi>N</mml:mi>", "N"),
            (
                "<mml:msub><mml:mrow><mml:mi>H</mml:mi></mml:mrow>"
                "<mml:mrow><mml:mo>∞</mml:mo></mml:mrow></mml:msub>",
                r"H_{\\infty}",
            ),
            (
                "<mml:msub><mml:mrow><mml:mi>h</mml:mi></mml:mrow>"
                "<mml:mrow><mml:mn>2</mml:mn></mml:mrow></mml:msub>",
                r"h_{2}",
            ),
            ("<mml:mi>ϑ</mml:mi>", r"\\vartheta"),
            (
                "<mml:mrow><mml:mi>n</mml:mi><mml:mo>+</mml:mo>"
                "<mml:mi>m</mml:mi></mml:mrow>",
                "n+m",
            ),
            (
                "<mml:msup><mml:mi>L</mml:mi><mml:mo>∞</mml:mo></mml:msup>",
                r"L^{\\infty}",
            ),
            (
                '<mml:msub><mml:mrow><mml:mi mathvariant="italic">RH</mml:mi>'
                "</mml:mrow><mml:mrow><mml:mn>2</mml:mn></mml:mrow></mml:msub>",
                r"RH_{2}",
            ),
            (
                '<mml:msub><mml:mrow><mml:mi mathvariant="italic">RH</mml:mi>'
                "</mml:mrow><mml:mrow><mml:mo>∞</mml:mo></mml:mrow></mml:msub>",
                r"RH_{\\infty}",
            ),
        )

        for body, expected in cases:
            with self.subTest(expected=expected):
                value = (
                    '<mml:math xmlns:mml="http://www.w3.org/1998/Math/MathML">'
                    + body
                    + "</mml:math>"
                )
                result = normalize_structured_citation(value)
                self.assertTrue(result.deterministic)
                self.assertEqual(result.normalized, rf"\\({expected}\\)")

    def test_formula_tex_wrapper_is_supported(self) -> None:
        value = (
            'Interpolation-based <formula formulatype="inline">'
            '<tex Notation="TeX">\${\\cal H}_{2}$</tex></formula> model reduction'
        )
        result = normalize_structured_citation(value)

        self.assertTrue(result.deterministic)
        self.assertEqual(
            result.normalized,
            r"Interpolation-based \\({\\cal H}_{2}\\) model reduction",
        )
        self.assertEqual(result.reason, "embedded-tex")

    def test_unsupported_mathml_is_refused(self) -> None:
        value = (
            '<mml:math xmlns:mml="http://www.w3.org/1998/Math/MathML">'
            "<mml:mfrac><mml:mi>a</mml:mi><mml:mi>b</mml:mi></mml:mfrac>"
            "</mml:math>"
        )
        result = normalize_structured_citation(value)

        self.assertFalse(result.deterministic)
        self.assertEqual(result.reason, "unsupported-mathml")
        self.assertEqual(result.normalized, value)

    def test_unicode_replacement_is_never_guessed(self) -> None:
        value = "Est�vez Schwarz, D. Structural analysis"
        result = normalize_structured_citation(value)

        self.assertFalse(result.deterministic)
        self.assertFalse(result.changed)
        self.assertEqual(result.reason, "unicode-replacement")
        self.assertEqual(result.normalized, value)

    def test_unbalanced_markup_is_never_repaired(self) -> None:
        value = "A. Author, <em>Journal title (1999)"
        result = normalize_structured_citation(value)

        self.assertFalse(result.deterministic)
        self.assertEqual(result.reason, "unbalanced-markup")

    def test_provider_error_page_is_refused(self) -> None:
        value = (
            "<html><head><title>502 Bad Gateway</title></head>"
            "<body><center><h1>502 Bad Gateway</h1></center>"
            "<hr><center>cloudflare</center></body></html>"
        )
        result = normalize_structured_citation(value)

        self.assertFalse(result.deterministic)
        self.assertEqual(result.reason, "provider-error-page")
        self.assertEqual(result.normalized, value)

    def test_script_markup_is_refused(self) -> None:
        value = "<sub/>. IEEE Trans Smart Grid 15(1):67–76."
        result = normalize_structured_citation(value)

        self.assertFalse(result.deterministic)
        self.assertEqual(result.reason, "script-markup")

    def test_normalization_is_idempotent(self) -> None:
        first = normalize_structured_citation(
            "Systems &amp; Control Letters with "
            "<mml:math xmlns:mml=\"http://www.w3.org/1998/Math/MathML\">"
            "<mml:msub><mml:mi>H</mml:mi><mml:mi>∞</mml:mi></mml:msub>"
            "</mml:math>"
        )
        second = normalize_structured_citation(first.normalized)

        self.assertTrue(first.deterministic)
        self.assertTrue(first.changed)
        self.assertTrue(second.deterministic)
        self.assertFalse(second.changed)
        self.assertEqual(second.normalized, first.normalized)


if __name__ == "__main__":
    unittest.main()
