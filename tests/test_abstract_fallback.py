from __future__ import annotations

import io
import unittest

from bibreview.providers.fallback import AbstractFallback
from bibreview.providers.http import HttpError
from bibreview.reporting import Reporter


class SequenceProvider:
    def __init__(self, *values):
        self.values = list(values)
        self.calls = []

    def abstract(self, doi):
        self.calls.append(doi)
        value = self.values.pop(0) if self.values else ""
        if isinstance(value, Exception):
            raise value
        return value


class AbstractFallbackTests(unittest.TestCase):
    def test_returns_longest_available_abstract(self):
        semantic = SequenceProvider("short abstract")
        mendeley = SequenceProvider("a much longer fallback abstract")
        fallback = AbstractFallback(
            semantic_scholar=semantic,
            mendeley=mendeley,
            reporter=Reporter(-1),
        )
        self.assertEqual(fallback.abstract("10.1/test"), "a much longer fallback abstract")

    def test_returns_explicit_unavailable_text_when_empty(self):
        fallback = AbstractFallback(
            semantic_scholar=SequenceProvider(""),
            mendeley=SequenceProvider("  "),
            reporter=Reporter(-1),
        )
        self.assertEqual(fallback.abstract("10.1/test"), "Not available")

    def test_semantic_scholar_429_disables_only_that_provider_for_run(self):
        semantic = SequenceProvider(
            HttpError("limited", status_code=429),
            "should never be requested",
        )
        mendeley = SequenceProvider("mendeley first", "mendeley second")
        stream = io.StringIO()
        fallback = AbstractFallback(
            semantic_scholar=semantic,
            mendeley=mendeley,
            reporter=Reporter(0, stream),
        )

        self.assertEqual(fallback.abstract("10.1/one"), "mendeley first")
        self.assertEqual(fallback.abstract("10.1/two"), "mendeley second")
        self.assertEqual(semantic.calls, ["10.1/one"])
        self.assertEqual(mendeley.calls, ["10.1/one", "10.1/two"])
        self.assertIn("Semantic Scholar HTTP 429", stream.getvalue())

    def test_mendeley_401_disables_only_that_provider_for_run(self):
        semantic = SequenceProvider("semantic first", "semantic second")
        mendeley = SequenceProvider(
            HttpError("unauthorized", status_code=401),
            "should never be requested",
        )
        stream = io.StringIO()
        fallback = AbstractFallback(
            semantic_scholar=semantic,
            mendeley=mendeley,
            reporter=Reporter(0, stream),
        )

        self.assertEqual(fallback.abstract("10.1/one"), "semantic first")
        self.assertEqual(fallback.abstract("10.1/two"), "semantic second")
        self.assertEqual(mendeley.calls, ["10.1/one"])
        self.assertEqual(semantic.calls, ["10.1/one", "10.1/two"])
        self.assertIn("Mendeley HTTP 401", stream.getvalue())
        self.assertNotIn("MENDELEY_API_KEY", stream.getvalue())

    def test_other_provider_errors_propagate(self):
        fallback = AbstractFallback(
            semantic_scholar=SequenceProvider(HttpError("server", status_code=500)),
            reporter=Reporter(-1),
        )
        with self.assertRaises(HttpError):
            fallback.abstract("10.1/test")


if __name__ == "__main__":
    unittest.main()
