"""Offline tests for shared HTTP retries and sanitized diagnostics."""

from __future__ import annotations

import io
import unittest
from unittest.mock import Mock

import requests

from bibreview.providers.http import HttpError, HttpTransport
from bibreview.reporting import Reporter


def response(status: int, url: str, content: bytes = b"{}") -> requests.Response:
    result = requests.Response()
    result.status_code = status
    result.url = url
    result._content = content
    return result


class HttpTransportTests(unittest.TestCase):
    def test_404_is_absent(self) -> None:
        session = Mock()
        session.get.return_value = response(404, "https://api.example.test/item")
        transport = HttpTransport(session)
        self.assertIsNone(transport.request("https://api.example.test/item"))

    def test_http_error_reports_hosts_without_url_secrets(self) -> None:
        session = Mock()
        session.get.return_value = response(
            403, "https://publisher.test/paper?token=secret-key"
        )
        transport = HttpTransport(session)
        with self.assertRaises(HttpError) as caught:
            transport.request(
                "https://doi.org/10.1/test?api_key=secret-key",
                context="Metadata lookup",
            )
        message = str(caught.exception)
        self.assertIn("Metadata lookup", message)
        self.assertIn("doi.org -> publisher.test: HTTP 403", message)
        self.assertNotIn("secret-key", message)
        self.assertNotIn("/paper", message)

    def test_transport_causes_are_sanitized_and_distinct(self) -> None:
        cases = [
            (requests.Timeout, "timed out"),
            (requests.exceptions.SSLError, "TLS certificate"),
            (requests.ConnectionError, "connection failed"),
            (requests.TooManyRedirects, "too many redirects"),
        ]
        for failure, expected in cases:
            with self.subTest(failure=failure.__name__):
                session = Mock()
                session.get.side_effect = failure("secret-key")
                transport = HttpTransport(session)
                with self.assertRaises(HttpError) as caught:
                    transport.request("https://api.example.test?key=secret-key")
                self.assertIn(expected, str(caught.exception))
                self.assertNotIn("secret-key", str(caught.exception))

    def test_invalid_json_reports_host_only(self) -> None:
        session = Mock()
        item = response(200, "https://api.example.test/item?token=secret")
        item._content = b"not-json"
        session.get.return_value = item
        transport = HttpTransport(session)
        with self.assertRaisesRegex(HttpError, r"api\.example\.test: invalid JSON") as caught:
            transport.json("https://api.example.test/item?token=secret")
        self.assertNotIn("secret", str(caught.exception))

    def test_default_transport_uses_established_retry_policy(self) -> None:
        transport = HttpTransport()
        retry = transport.session.get_adapter("https://").max_retries
        self.assertEqual(retry.total, 3)
        self.assertEqual(set(retry.status_forcelist), {429, 500, 502, 503, 504})
        self.assertEqual(set(retry.allowed_methods), {"GET"})
        self.assertFalse(retry.raise_on_status)

    def test_debug_output_is_sanitized(self) -> None:
        session = Mock()
        session.get.return_value = response(
            200, "https://publisher.test/paper?token=secret"
        )
        stream = io.StringIO()
        transport = HttpTransport(session, reporter=Reporter(2, stream))
        transport.request(
            "https://doi.org/10.1/test?api_key=secret",
            context="Publisher lookup",
        )
        output = stream.getvalue()
        self.assertIn("GET doi.org", output)
        self.assertIn("HTTP 200 from doi.org -> publisher.test", output)
        self.assertNotIn("secret", output)

    def test_timeout_and_redirect_arguments_are_explicit(self) -> None:
        session = Mock()
        session.get.return_value = response(200, "https://api.example.test/item")
        transport = HttpTransport(session)
        transport.request("https://api.example.test/item")
        self.assertEqual(session.get.call_args.kwargs["timeout"], (5, 30))
        self.assertTrue(session.get.call_args.kwargs["allow_redirects"])


if __name__ == "__main__":
    unittest.main()
