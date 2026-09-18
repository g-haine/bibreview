from __future__ import annotations

from datetime import datetime, timezone
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlparse
import unittest

from bibreview.arxiv import ArxivError, ArxivProvider, TemporaryArxivError


ATOM = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>https://arxiv.org/abs/2609.12345v2</id>
    <updated>2026-09-18T09:00:00Z</updated>
    <title> Fluid-structure example </title>
    <summary> A concise summary. </summary>
    <author><name>Ada Lovelace</name></author>
    <author><name>Emmy Noether</name></author>
  </entry>
</feed>
"""

EMPTY_ATOM = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"></feed>
"""


class FakeResponse:
    def __init__(self, content: bytes):
        self.content = content

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return self.content


class SequenceOpener:
    def __init__(self, *results):
        self.results = list(results)
        self.calls = []

    def __call__(self, request, timeout=30):
        self.calls.append((request, timeout))
        result = self.results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


class ArxivProviderTests(unittest.TestCase):
    def provider(self, *, opener, sleeper=lambda _: None):
        return ArxivProvider(
            query="all:fluid AND all:structure",
            max_results=25,
            sort_by="lastUpdatedDate",
            sort_order="descending",
            user_agent="Example via BibReview/0.1 (contact: ada@example.org)",
            contact_email="ada@example.org",
            opener=opener,
            sleeper=sleeper,
            now=lambda: datetime(2026, 9, 18, 8, 0, tzinfo=timezone.utc),
        )

    def test_request_contains_configured_query_policy_and_identity(self):
        opener = SequenceOpener(FakeResponse(ATOM))
        provider = self.provider(opener=opener)

        request = provider.request()
        query = parse_qs(urlparse(request.full_url).query)

        self.assertEqual(query["search_query"], ["all:fluid AND all:structure"])
        self.assertEqual(query["start"], ["0"])
        self.assertEqual(query["max_results"], ["25"])
        self.assertEqual(query["sortBy"], ["lastUpdatedDate"])
        self.assertEqual(query["sortOrder"], ["descending"])
        self.assertEqual(request.get_header("From"), "ada@example.org")
        self.assertIn("BibReview", request.get_header("User-agent"))
        self.assertEqual(
            request.get_header("Accept"),
            "application/atom+xml",
        )

    def test_fetch_parses_display_oriented_entries(self):
        provider = self.provider(
            opener=SequenceOpener(FakeResponse(ATOM)),
        )

        entries = provider.fetch()

        self.assertEqual(len(entries), 1)
        entry = entries[0]
        self.assertEqual(entry.title, "Fluid-structure example")
        self.assertEqual(entry.summary, "A concise summary.")
        self.assertEqual(entry.url, "https://arxiv.org/abs/2609.12345v2")
        self.assertEqual(entry.authors, ("Ada Lovelace", "Emmy Noether"))
        self.assertEqual(entry.updated.isoformat(), "2026-09-18")

    def test_retry_after_is_honored_before_success(self):
        error = HTTPError(
            "https://export.arxiv.org/api/query",
            429,
            "Too Many Requests",
            {"Retry-After": "2"},
            None,
        )
        opener = SequenceOpener(error, FakeResponse(ATOM))
        delays = []
        provider = self.provider(opener=opener, sleeper=delays.append)

        entries = provider.fetch()

        self.assertEqual(len(entries), 1)
        self.assertEqual(delays, [2])
        self.assertEqual(len(opener.calls), 2)

    def test_429_without_retry_after_does_not_retry(self):
        error = HTTPError(
            "https://export.arxiv.org/api/query",
            429,
            "Too Many Requests",
            {},
            None,
        )
        opener = SequenceOpener(error)
        delays = []
        provider = self.provider(opener=opener, sleeper=delays.append)

        with self.assertRaisesRegex(
            TemporaryArxivError,
            "without Retry-After",
        ):
            provider.fetch()

        self.assertEqual(delays, [])
        self.assertEqual(len(opener.calls), 1)

    def test_retryable_server_errors_use_bounded_schedule(self):
        errors = [
            HTTPError(
                "https://export.arxiv.org/api/query",
                503,
                "Unavailable",
                {},
                None,
            )
            for _ in range(4)
        ]
        opener = SequenceOpener(*errors)
        delays = []
        provider = self.provider(opener=opener, sleeper=delays.append)

        with self.assertRaisesRegex(
            TemporaryArxivError,
            "after 4 attempts",
        ):
            provider.fetch()

        self.assertEqual(delays, [60, 180, 600])

    def test_empty_feed_is_rejected(self):
        provider = self.provider(
            opener=SequenceOpener(FakeResponse(EMPTY_ATOM)),
        )
        with self.assertRaisesRegex(ArxivError, "no entries"):
            provider.fetch()


if __name__ == "__main__":
    unittest.main()
