import unittest

from bibreview.identity_hygiene import remove_author_variant
from bibreview.pipeline.authors import AuthorMappingError


class AuthorVariantRemovalTests(unittest.TestCase):
    def test_exact_variant_removal_preserves_identity(self):
        updated = remove_author_variant(
            {"yongxing-wang": ["Yongxing Wang", "Y. Wang"]},
            slug="yongxing-wang",
            variant="Y. Wang",
        )
        self.assertEqual(updated["yongxing-wang"], ["Yongxing Wang"])

    def test_last_variant_cannot_be_removed(self):
        with self.assertRaisesRegex(AuthorMappingError, "last name variant"):
            remove_author_variant(
                {"yan-wang": ["Yan Wang"]},
                slug="yan-wang",
                variant="Yan Wang",
            )


if __name__ == "__main__":
    unittest.main()
