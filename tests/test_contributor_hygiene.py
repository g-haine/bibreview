import unittest

from bibreview.contributor_hygiene import (
    institution_like_contributor_name,
    provider_suspicious_contributors,
)


class ContributorHygieneSignalTests(unittest.TestCase):
    def test_detects_numbered_affiliation_and_institution_names(self):
        self.assertTrue(
            institution_like_contributor_name(
                "1 Department of Mathematics, Example University"
            )
        )
        self.assertTrue(
            institution_like_contributor_name(
                "TIFR Centre for Applicable Mathematics"
            )
        )
        self.assertFalse(institution_like_contributor_name("Debayan Maity"))

    def test_provider_signal_requires_mixed_person_and_institution_entries(self):
        mixed = {
            "author": [
                {"given": "Debayan", "family": "Maity"},
                {"name": "TIFR Centre for Applicable Mathematics"},
                {"given": "Takéo", "family": "Takahashi"},
            ]
        }
        self.assertEqual(
            provider_suspicious_contributors(mixed),
            (("author", 1, "TIFR Centre for Applicable Mathematics"),),
        )

        collective = {
            "author": [
                {"name": "Example University"},
                {"name": "National Research Centre"},
            ]
        }
        self.assertEqual(provider_suspicious_contributors(collective), ())


if __name__ == "__main__":
    unittest.main()
