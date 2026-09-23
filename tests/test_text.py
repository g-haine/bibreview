import unittest

from bibreview.text import clean_abstract, clean_metadata, is_missing_metadata_value, safe_component, slugify


class TextTests(unittest.TestCase):
    def test_slugify_transliterates_and_normalizes(self):
        self.assertEqual(slugify("Énergie & contrôle"), "energie-controle")

    def test_safe_component_rejects_reserved_and_unsafe_names(self):
        self.assertEqual(safe_component("valid-slug"), "valid-slug")
        for value in ("", ".", "..", "index", "../escape", "bad/name", "bad:name"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    safe_component(value)

    def test_clean_metadata_removes_controls(self):
        self.assertEqual(clean_metadata("  A\x01B  "), "AB")

    def test_abstract_missing_placeholder_is_case_and_space_insensitive(self):
        for value in (
            "",
            "Not Available",
            "not available",
            "NOT AVAILABLE",
            "  Not   Available  ",
        ):
            with self.subTest(value=value):
                self.assertTrue(
                    is_missing_metadata_value("abstract", value)
                )

    def test_not_available_is_not_special_for_other_fields(self):
        self.assertFalse(
            is_missing_metadata_value("publisher", "Not Available")
        )

    def test_clean_abstract_collapses_missing_placeholder(self):
        self.assertEqual(
            clean_abstract(" Abstract: NOT   AVAILABLE "),
            "",
        )

    def test_clean_abstract_removes_only_leading_labels(self):
        labels = (
            "Abstract",
            "ABSTRACT",
            "Summary",
            "Résumé",
            "RESUMEN",
            "Resumo",
            "Zusammenfassung",
            "Riassunto",
            "Samenvatting",
        )
        for label in labels:
            with self.subTest(label=label):
                self.assertEqual(
                    clean_metadata(
                        f"  {label}:   Useful abstract text.  ",
                        abstract=True,
                    ),
                    "Useful abstract text.",
                )

    def test_clean_abstract_normalizes_boundary_whitespace_and_preserves_body_words(self):
        self.assertEqual(
            clean_metadata(
                "  Abstract\nThis abstract studies abstract systems.  ",
                abstract=True,
            ),
            "This abstract studies abstract systems.",
        )


if __name__ == "__main__":
    unittest.main()
