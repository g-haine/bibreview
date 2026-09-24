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


class BatchProvider:
    def __init__(self, values, *, batch_size=500):
        self.values = dict(values)
        self.BATCH_SIZE = batch_size
        self.batch_calls = []
        self.calls = []
        self.batch_error = None

    def abstracts(self, dois):
        self.batch_calls.append(tuple(dois))
        if self.batch_error is not None:
            raise self.batch_error
        return {doi: self.values.get(doi, "") for doi in dois}

    def abstract(self, doi):
        self.calls.append(doi)
        return self.values.get(doi, "")


class AbstractFallbackTests(unittest.TestCase):
    def test_abstract_many_batches_capable_providers_and_keeps_mendeley_individual(self):
        semantic = BatchProvider(
            {
                "10.1/one": "semantic one",
                "10.1/two": "the longest semantic two",
            },
            batch_size=500,
        )
        openalex = BatchProvider(
            {
                "10.1/one": "the longest OpenAlex one",
                "10.1/two": "openalex two",
            },
            batch_size=100,
        )
        mendeley = SequenceProvider(
            "mendeley one",
            "mendeley two",
        )
        fallback = AbstractFallback(
            semantic_scholar=semantic,
            openalex=openalex,
            mendeley=mendeley,
            reporter=Reporter(-1),
        )

        result = fallback.abstract_many(("10.1/one", "10.1/two"))

        self.assertEqual(
            result,
            {
                "10.1/one": "the longest OpenAlex one",
                "10.1/two": "the longest semantic two",
            },
        )
        self.assertEqual(
            semantic.batch_calls,
            [("10.1/one", "10.1/two")],
        )
        self.assertEqual(
            openalex.batch_calls,
            [("10.1/one", "10.1/two")],
        )
        self.assertEqual(
            mendeley.calls,
            ["10.1/one", "10.1/two"],
        )

    def test_abstract_many_chunks_by_provider_batch_size(self):
        semantic = BatchProvider(
            {
                "10.1/one": "one",
                "10.1/two": "two",
                "10.1/three": "three",
            },
            batch_size=2,
        )
        fallback = AbstractFallback(
            semantic_scholar=semantic,
            reporter=Reporter(-1),
        )

        result = fallback.abstract_many(
            ("10.1/one", "10.1/two", "10.1/three")
        )

        self.assertEqual(
            semantic.batch_calls,
            [
                ("10.1/one", "10.1/two"),
                ("10.1/three",),
            ],
        )
        self.assertEqual(result["10.1/three"], "three")

    def test_persistent_batch_429_disables_only_that_optional_provider(self):
        semantic = BatchProvider({}, batch_size=500)
        semantic.batch_error = HttpError("limited", status_code=429)
        openalex = BatchProvider(
            {"10.1/one": "OpenAlex one", "10.1/two": "OpenAlex two"},
            batch_size=100,
        )
        stream = io.StringIO()
        fallback = AbstractFallback(
            semantic_scholar=semantic,
            openalex=openalex,
            reporter=Reporter(0, stream),
        )

        first = fallback.abstract_many(("10.1/one", "10.1/two"))
        second = fallback.abstract_many(("10.1/three",))

        self.assertEqual(
            first,
            {"10.1/one": "OpenAlex one", "10.1/two": "OpenAlex two"},
        )
        self.assertEqual(second, {"10.1/three": ""})
        self.assertEqual(
            semantic.batch_calls,
            [("10.1/one", "10.1/two")],
        )
        self.assertIn("Semantic Scholar HTTP 429", stream.getvalue())

    def test_failed_batch_falls_back_to_individual_for_that_chunk(self):
        class RecoveringBatchProvider(BatchProvider):
            def abstracts(self, dois):
                self.batch_calls.append(tuple(dois))
                raise HttpError("server", status_code=503)

        semantic = RecoveringBatchProvider(
            {"10.1/one": "one", "10.1/two": "two"},
            batch_size=500,
        )
        fallback = AbstractFallback(
            semantic_scholar=semantic,
            reporter=Reporter(-1),
        )

        result = fallback.abstract_many(("10.1/one", "10.1/two"))

        self.assertEqual(result, {"10.1/one": "one", "10.1/two": "two"})
        self.assertEqual(semantic.calls, ["10.1/one", "10.1/two"])

    def test_returns_longest_available_abstract(self):
        semantic = SequenceProvider("short abstract")
        mendeley = SequenceProvider("a much longer fallback abstract")
        openalex = SequenceProvider("the longest available OpenAlex fallback abstract")
        fallback = AbstractFallback(
            semantic_scholar=semantic,
            mendeley=mendeley,
            openalex=openalex,
            reporter=Reporter(-1),
        )
        self.assertEqual(
            fallback.abstract("10.1/test"),
            "the longest available OpenAlex fallback abstract",
        )

    def test_compares_provider_lengths_after_abstract_cleanup(self):
        fallback = AbstractFallback(
            semantic_scholar=SequenceProvider(
                "ABSTRACT: Short candidate"
            ),
            openalex=SequenceProvider(
                "A genuinely longer clean candidate"
            ),
            reporter=Reporter(-1),
        )
        self.assertEqual(
            fallback.abstract("10.1/test"),
            "A genuinely longer clean candidate",
        )

    def test_returns_cleaned_fallback_text(self):
        fallback = AbstractFallback(
            semantic_scholar=SequenceProvider(
                "  Résumé —  Useful text from provider.  "
            ),
            reporter=Reporter(-1),
        )
        self.assertEqual(
            fallback.abstract("10.1/test"),
            "Useful text from provider.",
        )

    def test_returns_empty_string_when_all_fallbacks_are_unavailable(self):
        fallback = AbstractFallback(
            semantic_scholar=SequenceProvider(""),
            mendeley=SequenceProvider("  "),
            reporter=Reporter(-1),
        )
        self.assertEqual(fallback.abstract("10.1/test"), "")

    def test_provider_placeholder_is_ignored_in_favor_of_real_abstract(self):
        fallback = AbstractFallback(
            semantic_scholar=SequenceProvider("NOT AVAILABLE"),
            openalex=SequenceProvider("A real fallback abstract."),
            reporter=Reporter(-1),
        )
        self.assertEqual(
            fallback.abstract("10.1/test"),
            "A real fallback abstract.",
        )

    def test_custom_unavailable_placeholder_is_collapsed_to_empty(self):
        fallback = AbstractFallback(
            unavailable_text="Not Available",
            reporter=Reporter(-1),
        )
        self.assertEqual(fallback.abstract("10.1/test"), "")


    def test_unsafe_structured_candidate_is_skipped_for_safe_fallback(self):
        stream = io.StringIO()
        fallback = AbstractFallback(
            semantic_scholar=SequenceProvider(
                'A controller <jats:inline-graphic '
                'xlink:href="graphic/math-0002.png"/> is proposed.'
            ),
            openalex=SequenceProvider("Safe OpenAlex abstract."),
            reporter=Reporter(0, stream),
        )

        self.assertEqual(
            fallback.abstract("10.1/test"),
            "Safe OpenAlex abstract.",
        )
        self.assertIn("Semantic Scholar abstract for 10.1/test", stream.getvalue())
        self.assertIn("embedded-graphic", stream.getvalue())


    def test_selection_retains_refused_evidence_with_safe_alternative(self):
        fallback = AbstractFallback(
            semantic_scholar=SequenceProvider(
                'A controller <jats:inline-graphic '
                'xlink:href="graphic/math-0002.png"/> is proposed.'
            ),
            openalex=SequenceProvider("Safe OpenAlex abstract."),
            reporter=Reporter(-1),
        )

        selection = fallback.select("10.1/test")

        self.assertEqual(selection.abstract, "Safe OpenAlex abstract.")
        self.assertEqual(selection.source, "openalex")
        self.assertEqual(len(selection.evidence), 1)
        self.assertEqual(selection.evidence[0].source, "semantic_scholar")
        self.assertEqual(selection.evidence[0].reason, "embedded-graphic")
        self.assertIn("math-0002.png", selection.evidence[0].value)


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

    def test_openalex_429_disables_only_that_provider_for_run(self):
        openalex = SequenceProvider(
            HttpError("limited", status_code=429),
            "should never be requested",
        )
        semantic = SequenceProvider("semantic first", "semantic second")
        stream = io.StringIO()
        fallback = AbstractFallback(
            semantic_scholar=semantic,
            openalex=openalex,
            reporter=Reporter(0, stream),
        )

        self.assertEqual(fallback.abstract("10.1/one"), "semantic first")
        self.assertEqual(fallback.abstract("10.1/two"), "semantic second")
        self.assertEqual(openalex.calls, ["10.1/one"])
        self.assertEqual(semantic.calls, ["10.1/one", "10.1/two"])
        self.assertIn("OpenAlex HTTP 429", stream.getvalue())

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
