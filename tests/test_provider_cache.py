from pathlib import Path
import tempfile
import unittest

import requests

from bibreview.providers.cache import CachedTransport, ProviderResponseCache, _request_key
from bibreview.providers.http import HttpError
from bibreview.reporting import Reporter


def response(url: str, body: bytes, status: int = 200) -> requests.Response:
    item = requests.Response()
    item.status_code = status
    item.url = url
    item.encoding = "utf-8"
    item._content = body
    return item


class FakeTransport:
    def __init__(self, results):
        self.results = list(results)
        self.calls = []
        self.reporter = Reporter(-1)

    def request(self, url, **kwargs):
        self.calls.append(("request", url, kwargs))
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    def post_json(self, url, **kwargs):
        self.calls.append(("post_json", url, kwargs))
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    def post_form_json(self, url, **kwargs):
        self.calls.append(("post_form_json", url, kwargs))
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class ProviderResponseCacheTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.now = 1_000_000.0
        self.cache = ProviderResponseCache(
            ttl_hours=1.0,
            root=self.root,
            clock=lambda: self.now,
        )

    def test_get_hit_reuses_raw_response_without_touching_timestamp(self):
        url = "https://api.example.test/item"
        network = FakeTransport([response(url, b'{"value": 1}')])
        transport = CachedTransport(network, cache=self.cache)

        first = transport.json(url, params={"b": 2, "a": 1})
        key = _request_key("GET", url, {"params": {"b": 2, "a": 1}})
        path = self.cache.entry_path(key)
        before = path.stat().st_mtime

        self.now += 120.0
        second = transport.json(url, params={"a": 1, "b": 2})

        self.assertEqual(first, {"value": 1})
        self.assertEqual(second, {"value": 1})
        self.assertEqual(len(network.calls), 1)
        self.assertEqual(path.stat().st_mtime, before)

    def test_expired_entry_is_refetched_and_replaced(self):
        url = "https://api.example.test/item"
        network = FakeTransport([
            response(url, b'{"value": 1}'),
            response(url, b'{"value": 2}'),
        ])
        transport = CachedTransport(network, cache=self.cache)

        self.assertEqual(transport.json(url), {"value": 1})
        self.now += 3601.0
        self.assertEqual(transport.json(url), {"value": 2})
        self.assertEqual(len(network.calls), 2)

    def test_no_cache_neither_reads_nor_writes(self):
        url = "https://api.example.test/item"
        warm_network = FakeTransport([response(url, b'{"value": 1}')])
        CachedTransport(warm_network, cache=self.cache).json(url)

        bypass_network = FakeTransport([response(url, b'{"value": 2}')])
        bypass = CachedTransport(
            bypass_network,
            cache=self.cache,
            mode="no-cache",
        )
        self.assertEqual(bypass.json(url), {"value": 2})

        normal_network = FakeTransport([response(url, b'{"value": 3}')])
        normal = CachedTransport(normal_network, cache=self.cache)
        self.assertEqual(normal.json(url), {"value": 1})
        self.assertEqual(len(bypass_network.calls), 1)
        self.assertEqual(len(normal_network.calls), 0)

    def test_refresh_bypasses_read_and_replaces_on_success(self):
        url = "https://api.example.test/item"
        CachedTransport(
            FakeTransport([response(url, b'{"value": 1}')]),
            cache=self.cache,
        ).json(url)

        refresh = CachedTransport(
            FakeTransport([response(url, b'{"value": 2}')]),
            cache=self.cache,
            mode="refresh",
        )
        self.assertEqual(refresh.json(url), {"value": 2})

        network = FakeTransport([response(url, b'{"value": 3}')])
        self.assertEqual(
            CachedTransport(network, cache=self.cache).json(url),
            {"value": 2},
        )
        self.assertEqual(network.calls, [])

    def test_refresh_failure_preserves_previous_entry_without_using_it(self):
        url = "https://api.example.test/item"
        CachedTransport(
            FakeTransport([response(url, b'{"value": 1}')]),
            cache=self.cache,
        ).json(url)

        refresh = CachedTransport(
            FakeTransport([HttpError("failed", status_code=503)]),
            cache=self.cache,
            mode="refresh",
        )
        with self.assertRaises(HttpError):
            refresh.json(url)

        network = FakeTransport([response(url, b'{"value": 3}')])
        self.assertEqual(
            CachedTransport(network, cache=self.cache).json(url),
            {"value": 1},
        )
        self.assertEqual(network.calls, [])

    def test_404_absence_is_cached(self):
        url = "https://api.example.test/missing"
        network = FakeTransport([None])
        transport = CachedTransport(network, cache=self.cache)

        self.assertIsNone(transport.request(url))
        self.assertIsNone(transport.request(url))
        self.assertEqual(len(network.calls), 1)

    def test_accepted_auth_error_response_is_not_cached(self):
        url = "https://doi.org/10.1/test"
        network = FakeTransport([
            response("https://publisher.example/item", b"", status=403),
            response("https://publisher.example/item", b"", status=403),
        ])
        transport = CachedTransport(network, cache=self.cache)

        self.assertEqual(transport.request(url).status_code, 403)
        self.assertEqual(transport.request(url).status_code, 403)
        self.assertEqual(len(network.calls), 2)

    def test_json_post_is_cached_but_form_post_is_not(self):
        post_url = "https://api.example.test/batch"
        post_network = FakeTransport([{"items": [1]}])
        post = CachedTransport(post_network, cache=self.cache)
        self.assertEqual(
            post.post_json(post_url, json_body={"ids": ["A"]}),
            {"items": [1]},
        )
        self.assertEqual(
            post.post_json(post_url, json_body={"ids": ["A"]}),
            {"items": [1]},
        )
        self.assertEqual(len(post_network.calls), 1)

        token_url = "https://api.example.test/token"
        form_network = FakeTransport([{"token": "one"}, {"token": "two"}])
        form = CachedTransport(form_network, cache=self.cache)
        self.assertEqual(
            form.post_form_json(token_url, data={"grant_type": "client_credentials"}),
            {"token": "one"},
        )
        self.assertEqual(
            form.post_form_json(token_url, data={"grant_type": "client_credentials"}),
            {"token": "two"},
        )
        self.assertEqual(len(form_network.calls), 2)


if __name__ == "__main__":
    unittest.main()
