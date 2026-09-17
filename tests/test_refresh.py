from __future__ import annotations

from datetime import date
import unittest

from bibreview.identity import new_publication_id
from bibreview.model import Publication
from bibreview.pipeline.refresh import refresh


class FakeProvider:
    def __init__(self, records):
        self.records = records
        self.calls = []

    def work(self, doi):
        self.calls.append(doi)
        return self.records.get(doi)


def publication(doi, *, volume="", issue="", pages="", permalink="paper"):
    return Publication(
        id=new_publication_id(),
        identifiers={"doi": doi},
        type="journal-article",
        title="Old title",
        publication_year="2025",
        volume=volume,
        issue=issue,
        pages=pages,
        created_date=date(2025, 1, 2),
        permalink=permalink,
    )


def message(title="Updated title"):
    return {
        "type": "journal-article",
        "title": [title],
        "container-title": ["Journal"],
        "created": {"date-parts": [[2026, 9, 17]]},
        "published-print": {"date-parts": [[2026]]},
        "volume": "12",
        "issue": "3",
        "page": "10-20",
    }


class RefreshTests(unittest.TestCase):
    def test_unchanged_bibtex_does_not_recollect(self):
        item = publication("10.1/same")
        provider = FakeProvider({"10.1/same": message()})
        result = refresh(
            [item],
            provider=provider,
            stored_bibtex_lookup=lambda publication: "@article{same}\n",
            bibtex_lookup=lambda doi: "@article{same}\n",
            types=("journal-article",),
            when_missing_any=("volume", "issue", "pages"),
        )
        self.assertEqual(result.eligible_count, 1)
        self.assertEqual(result.candidates, ())
        self.assertEqual(result.items, ())
        self.assertEqual(provider.calls, [])

    def test_missing_and_changed_bibtex_are_recollected_with_existing_permalink(self):
        missing = publication("10.1/missing", permalink="stable-one")
        changed = publication("10.1/changed", issue="1", permalink="stable-two")
        provider = FakeProvider({
            "10.1/missing": message("One"),
            "10.1/changed": message("Two"),
        })
        stored = {
            "10.1/missing": None,
            "10.1/changed": "old bibtex\n",
        }
        current = {
            "10.1/missing": "new one\n",
            "10.1/changed": "new two\n",
        }
        result = refresh(
            [missing, changed],
            provider=provider,
            stored_bibtex_lookup=lambda publication: stored[publication.doi],
            bibtex_lookup=lambda doi: current[doi],
            types=("journal-article",),
            when_missing_any=("volume", "issue", "pages"),
        )
        self.assertEqual(result.candidates, ("10.1/missing", "10.1/changed"))
        self.assertEqual(tuple(item.reason for item in result.items), ("missing BibTeX", "changed BibTeX"))
        self.assertEqual(tuple(item.publication.permalink for item in result.items), ("stable-one", "stable-two"))
        self.assertEqual(tuple(item.publication.volume for item in result.items), ("12", "12"))
        self.assertEqual(provider.calls, ["10.1/missing", "10.1/changed"])

    def test_unavailable_refresh_candidate_keeps_retry_information(self):
        item = publication("10.1/unavailable")
        provider = FakeProvider({})
        result = refresh(
            [item],
            provider=provider,
            stored_bibtex_lookup=lambda publication: None,
            bibtex_lookup=lambda doi: "current\n",
            types=("journal-article",),
            when_missing_any=("volume",),
        )
        self.assertEqual(result.candidates, ("10.1/unavailable",))
        self.assertEqual(result.unavailable, ("10.1/unavailable",))
        self.assertEqual(result.items, ())

    def test_disabled_policy_performs_no_provider_or_bibtex_access(self):
        item = publication("10.1/disabled")
        provider = FakeProvider({"10.1/disabled": message()})
        result = refresh(
            [item],
            provider=provider,
            stored_bibtex_lookup=lambda publication: self.fail("stored BibTeX should not be read"),
            bibtex_lookup=lambda doi: self.fail("remote BibTeX should not be read"),
            types=(),
            when_missing_any=(),
        )
        self.assertEqual(result.scanned_count, 1)
        self.assertEqual(result.eligible_count, 0)
        self.assertEqual(provider.calls, [])


if __name__ == "__main__":
    unittest.main()
