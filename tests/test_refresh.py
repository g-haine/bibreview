from __future__ import annotations

from datetime import date
import unittest

from bibreview.identity import new_publication_id
from bibreview.model import Author, Publication
from bibreview.pipeline.refresh import refresh


class FakeProvider:
    def __init__(self, records):
        self.records = records
        self.calls = []

    def work(self, doi):
        self.calls.append(doi)
        return self.records.get(doi)


def publication(
    doi,
    *,
    volume="",
    issue="",
    pages="",
    title="Reviewed title",
    permalink="paper",
):
    return Publication(
        id=new_publication_id(),
        identifiers={"doi": doi},
        type="journal-article",
        title=title,
        authors=(Author(literal="Reviewed Author"),),
        container_title="Reviewed Journal",
        publication_year="2025",
        volume=volume,
        issue=issue,
        pages=pages,
        created_date=date(2025, 1, 2),
        permalink=permalink,
    )


def message(title="Provider title"):
    return {
        "type": "journal-article",
        "title": [title],
        "author": [{"given": "Ada", "family": "Lovelace"}],
        "container-title": ["Provider Journal"],
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

    def test_changed_bibtex_creates_only_safe_missing_field_proposals(self):
        item = publication("10.1/changed")
        provider = FakeProvider({"10.1/changed": message()})

        result = refresh(
            [item],
            provider=provider,
            stored_bibtex_lookup=lambda publication: "old bibtex\n",
            bibtex_lookup=lambda doi: "new bibtex\n",
            types=("journal-article",),
            when_missing_any=("volume", "issue", "pages"),
        )

        self.assertEqual(result.candidates, ("10.1/changed",))
        self.assertEqual(
            tuple((proposal.field, proposal.proposed_value) for proposal in result.proposals),
            (
                ("volume", "12"),
                ("issue", "3"),
                ("pages", "10-20"),
            ),
        )
        collateral = {item.field: item for item in result.collateral}
        self.assertEqual(collateral["title"].current_value, "Reviewed title")
        self.assertEqual(collateral["title"].proposed_value, "Provider title")
        self.assertEqual(
            collateral["authors"].current_value,
            ("Reviewed Author",),
        )
        self.assertEqual(
            collateral["authors"].proposed_value,
            ("Ada Lovelace",),
        )
        self.assertEqual(
            collateral["container_title"].current_value,
            "Reviewed Journal",
        )
        self.assertEqual(provider.calls, ["10.1/changed"])

    def test_existing_nonempty_configured_field_is_never_a_safe_proposal(self):
        item = publication(
            "10.1/existing",
            volume="1",
            issue="",
            pages="",
        )
        provider = FakeProvider({"10.1/existing": message()})

        result = refresh(
            [item],
            provider=provider,
            stored_bibtex_lookup=lambda publication: "old\n",
            bibtex_lookup=lambda doi: "new\n",
            types=("journal-article",),
            when_missing_any=("volume", "issue", "pages"),
        )

        self.assertNotIn("volume", {proposal.field for proposal in result.proposals})
        collateral = {item.field: item for item in result.collateral}
        self.assertEqual(collateral["volume"].current_value, "1")
        self.assertEqual(collateral["volume"].proposed_value, "12")

    def test_formatting_only_differences_are_not_reported_as_collateral(self):
        item = publication(
            "10.1/formatting",
            pages="10--20",
            issue="",
        )
        provider_message = message(title="Reviewed title")
        provider_message["author"] = [{"name": "Reviewed Author"}]
        provider_message["container-title"] = ["Reviewed Journal"]
        provider_message["published-print"] = {"date-parts": [[2025]]}
        provider_message["page"] = "10-20"

        result = refresh(
            [item],
            provider=FakeProvider({"10.1/formatting": provider_message}),
            stored_bibtex_lookup=lambda publication: "old\n",
            bibtex_lookup=lambda doi: "new\n",
            types=("journal-article",),
            when_missing_any=("issue",),
        )

        self.assertNotIn("pages", {item.field for item in result.collateral})

    def test_empty_current_bibtex_keeps_existing_state(self):
        item = publication("10.1/empty-current")
        provider = FakeProvider({"10.1/empty-current": message()})
        result = refresh(
            [item],
            provider=provider,
            stored_bibtex_lookup=lambda publication: "@article{manual}\n",
            bibtex_lookup=lambda doi: "",
            types=("journal-article",),
            when_missing_any=("volume",),
        )
        self.assertEqual(result.candidates, ())
        self.assertEqual(result.items, ())
        self.assertEqual(provider.calls, [])

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
            stored_bibtex_lookup=lambda publication: self.fail(
                "stored BibTeX should not be read"
            ),
            bibtex_lookup=lambda doi: self.fail(
                "remote BibTeX should not be read"
            ),
            types=(),
            when_missing_any=(),
        )
        self.assertEqual(result.scanned_count, 1)
        self.assertEqual(result.eligible_count, 0)
        self.assertEqual(provider.calls, [])


if __name__ == "__main__":
    unittest.main()
