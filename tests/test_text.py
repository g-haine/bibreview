import unittest

from bibreview.text import clean_metadata, safe_component, slugify


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


if __name__ == "__main__":
    unittest.main()
