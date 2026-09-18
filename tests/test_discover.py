from __future__ import annotations

import unittest

from bibreview.pipeline.discover import discover, is_relevant
from bibreview.providers.base import Enrichment


class FakeProvider:
    def __init__(self, works):
        self.works = works
        self.calls = []

    def work(self, doi):
        self.calls.append(doi)
        return self.works.get(doi)


class DiscoveryTests(unittest.TestCase):
    def test_relevance_normalizes_unicode_dash_punctuation(self):
        self.assertTrue(
            is_relevant(
                "Fluid–structure formulation",
                (r"fluid[-\s]+structure",),
            )
        )
        self.assertFalse(is_relevant("ordinary Hamiltonian system", (r"fluid-structure",)))

    def test_screens_candidates_with_configured_policy(self):
        provider = FakeProvider({
            "10.1/relevant-title": {
                "type": "journal-article",
                "title": ["Fluid-structure systems"],
            },
            "10.1/relevant-extra": {
                "type": "book-chapter",
                "title": ["Generic title"],
            },
            "10.1/review": {
                "type": "journal-article",
                "title": ["Unrelated title"],
            },
            "10.1/unsupported": {
                "type": "dataset",
                "title": ["Fluid-structure data"],
            },
        })

        def enrichment(doi, message):
            if doi == "10.1/relevant-extra":
                return Enrichment(abstract="A Dirac structure appears here")
            return Enrichment()

        result = discover(
            [
                "https://doi.org/10.1/RELEVANT-TITLE",
                "10.1/relevant-title",
                "10.1/relevant-extra",
                "10.1/review",
                "10.1/unsupported",
                "10.1/missing",
                "10.1/known",
                "10.1/rejected",
                "10.1/zenodo-record",
            ],
            provider=provider,
            known=("10.1/known",),
            rejected=("10.1/rejected",),
            patterns=(r"fluid[-\s]+structure", r"dirac structure"),
            unmatched="manual-review",
            excluded_doi_substrings=("zenodo",),
            enrichment_lookup=enrichment,
        )

        self.assertEqual(
            result.candidates,
            (
                "10.1/relevant-title",
                "10.1/relevant-extra",
                "10.1/review",
                "10.1/unsupported",
                "10.1/missing",
                "10.1/known",
                "10.1/rejected",
                "10.1/zenodo-record",
            ),
        )
        self.assertEqual(result.queued, ("10.1/relevant-title", "10.1/relevant-extra"))
        self.assertEqual(result.review, ("10.1/review",))
        self.assertEqual(result.rejected, ("10.1/unsupported", "10.1/missing"))
        self.assertEqual(
            result.skipped,
            ("10.1/known", "10.1/rejected", "10.1/zenodo-record"),
        )
        self.assertNotIn("10.1/known", provider.calls)
        self.assertNotIn("10.1/rejected", provider.calls)
        self.assertNotIn("10.1/zenodo-record", provider.calls)

    def test_unmatched_can_be_rejected_instead_of_reviewed(self):
        provider = FakeProvider({
            "10.1/no-match": {
                "type": "journal-article",
                "title": ["No configured expression"],
            }
        })
        result = discover(
            ["10.1/no-match"],
            provider=provider,
            patterns=(r"fluid[-\s]+structure",),
            unmatched="reject",
        )
        self.assertEqual(result.review, ())
        self.assertEqual(result.rejected, ("10.1/no-match",))

    def test_default_crossref_enrichment_includes_subjects_and_abstract(self):
        provider = FakeProvider({
            "10.1/subject": {
                "type": "journal-article",
                "title": ["Generic title"],
                "subject": ["Fluid-structure interaction"],
            },
            "10.1/abstract": {
                "type": "journal-article",
                "title": ["Generic title"],
                "abstract": "<jats:p>Dirac structure formulation</jats:p>",
            },
        })
        result = discover(
            ["10.1/subject", "10.1/abstract"],
            provider=provider,
            patterns=(r"fluid[-\s]+structure", r"dirac structure"),
        )
        self.assertEqual(result.queued, ("10.1/subject", "10.1/abstract"))

    def test_rejects_invalid_unmatched_policy(self):
        with self.assertRaisesRegex(ValueError, "unmatched policy"):
            discover([], provider=FakeProvider({}), unmatched="maybe")


if __name__ == "__main__":
    unittest.main()
