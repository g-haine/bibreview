import unittest

from bibreview.text import clean_metadata, slugify


class TextTests(unittest.TestCase):
    def test_slugify_transliterates_and_normalizes(self):
        self.assertEqual(slugify("Énergie & contrôle"), "energie-controle")

    def test_clean_metadata_removes_controls(self):
        self.assertEqual(clean_metadata("  A\x01B  "), "AB")


if __name__ == "__main__":
    unittest.main()
