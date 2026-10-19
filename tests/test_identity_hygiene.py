import unittest

from bibreview.identity_hygiene import (
    merge_author_identities,
    rename_author_identity,
    scan_author_identity_hygiene,
)


class AuthorIdentityHygieneTests(unittest.TestCase):
    def test_reports_encoding_duplicate_initial_full_candidate_and_weak_slug(self):
        mapping = {
            "michael-schafer": ["Michael Schäfer"],
            "michael-scha-fer": ["Michael Scha¨fer"],
            "w-g-price": ["W. G. Price"],
            "william-geraint-price": ["William Geraint Price"],
            "m-heck": ["M. Heck", "Marcus Heck"],
        }

        report = scan_author_identity_hygiene(mapping)

        kinds = {(item.kind, item.slugs) for item in report.findings}
        self.assertIn(
            (
                "encoding-normalization-duplicate",
                ("michael-scha-fer", "michael-schafer"),
            ),
            kinds,
        )
        self.assertIn(
            (
                "initial-full-name-candidate",
                ("w-g-price", "william-geraint-price"),
            ),
            kinds,
        )
        weak = next(
            item for item in report.findings
            if item.kind == "slug-can-be-strengthened"
            and item.slugs == ("m-heck",)
        )
        self.assertEqual(weak.proposed_slug, "marcus-heck")

    def test_reports_institution_like_authors_as_review_only_findings(self):
        report = scan_author_identity_hygiene(
            {
                "tifr-centre": [
                    "TIFR Centre for Applicable Mathematics, Bangalore"
                ],
                "numbered-department": [
                    "1 Department of Mathematics, Example University"
                ],
                "ada-lovelace": ["Ada Lovelace"],
            }
        )

        flagged = {
            item.slugs[0]
            for item in report.findings
            if item.kind == "institution-like-author"
        }
        self.assertEqual(
            flagged,
            {"tifr-centre", "numbered-department"},
        )
        self.assertEqual(report.institution_like, 2)

    def test_collective_author_signal_is_not_an_automatic_mutation(self):
        mapping = {
            "example-university": ["Example University"],
        }
        report = scan_author_identity_hygiene(mapping)
        self.assertEqual(report.institution_like, 1)
        self.assertEqual(mapping, {"example-university": ["Example University"]})


    def test_does_not_treat_two_different_full_given_names_as_duplicate(self):
        report = scan_author_identity_hygiene(
            {
                "keita-hara": ["Keita Hara"],
                "kensuke-hara": ["Kensuke Hara"],
            }
        )
        self.assertFalse(
            any(
                item.kind == "initial-full-name-candidate"
                for item in report.findings
            )
        )

    def test_explicit_merge_moves_variants_and_removes_source(self):
        merged = merge_author_identities(
            {
                "q-du": ["Q. Du"],
                "qixiu-du": ["Qixiu Du"],
            },
            source_slug="q-du",
            target_slug="qixiu-du",
        )
        self.assertNotIn("q-du", merged)
        self.assertEqual(merged["qixiu-du"], ["Qixiu Du", "Q. Du"])

    def test_explicit_rename_preserves_variants(self):
        renamed = rename_author_identity(
            {"m-heck": ["M. Heck", "Marcus Heck"]},
            source_slug="m-heck",
            target_slug="marcus-heck",
        )
        self.assertNotIn("m-heck", renamed)
        self.assertEqual(
            renamed["marcus-heck"],
            ["M. Heck", "Marcus Heck"],
        )


if __name__ == "__main__":
    unittest.main()
