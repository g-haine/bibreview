from __future__ import annotations

from io import StringIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from bibreview.config import load_config
from bibreview.provider_diagnostics import (
    diagnose_providers,
    format_provider_diagnostics,
)
from bibreview.providers.http import HttpError
from bibreview.reporting import Reporter


CONFIG = """\
schema_version: 1
environment:
  file: .env
project:
  name: Example Review
  slug: example-review
  contact:
    email: maintainer@example.org
discovery:
  provider: openalex
  query: example
providers:
  crossref:
    enabled: true
  openalex:
    enabled: true
    api_key_env: OPENALEX_KEY
  elsevier:
    enabled: true
    api_key_env: ELSEVIER_KEY
  springer:
    enabled: false
    api_key_env: SPRINGER_KEY
  ieee:
    enabled: true
    api_key_env: IEEE_KEY
  semantic_scholar:
    enabled: true
    api_key_env: SEMANTIC_KEY
  mendeley:
    enabled: true
    client_id_env: MENDELEY_CLIENT_ID
    client_secret_env: MENDELEY_CLIENT_SECRET
site:
  enabled: false
"""


class ProviderDiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / "bibreview.yml"
        self.path.write_text(CONFIG, encoding="utf-8")
        (self.root / ".env").write_text(
            "OPENALEX_KEY=file-openalex-secret\n"
            "SEMANTIC_KEY=file-semantic-secret\n"
            "MENDELEY_CLIENT_ID=file-mendeley-id\n"
            "MENDELEY_CLIENT_SECRET=file-mendeley-secret\n",
            encoding="utf-8",
        )
        self.config = load_config(self.path)

    def test_reports_credential_sources_without_values(self):
        diagnostics = diagnose_providers(
            self.config,
            environ={"ELSEVIER_KEY": "shell-elsevier-secret"},
            reporter=Reporter(-1, StringIO()),
        )
        by_name = {item.name: item for item in diagnostics}

        self.assertEqual(by_name["crossref"].credential_source, "not-required")
        self.assertEqual(by_name["openalex"].credential_source, "dotenv")
        self.assertEqual(by_name["elsevier"].credential_source, "environment")
        self.assertEqual(by_name["springer"].status, "disabled")
        self.assertEqual(by_name["ieee"].status, "missing-credential")
        self.assertEqual(by_name["semantic_scholar"].credential_source, "dotenv")
        self.assertEqual(
            by_name["mendeley"].credential_source,
            "client_id_env=dotenv, client_secret_env=dotenv",
        )

        report = format_provider_diagnostics(diagnostics)
        for secret in (
            "file-openalex-secret",
            "file-semantic-secret",
            "file-mendeley-id",
            "file-mendeley-secret",
            "shell-elsevier-secret",
        ):
            self.assertNotIn(secret, report)
        self.assertIn("OPENALEX_KEY", report)
        self.assertIn("missing-credential", report)

    def test_process_environment_overrides_dotenv_source(self):
        diagnostics = diagnose_providers(
            self.config,
            environ={
                "OPENALEX_KEY": "shell-openalex-secret",
                "ELSEVIER_KEY": "shell-elsevier-secret",
            },
            reporter=Reporter(-1, StringIO()),
        )
        openalex = next(item for item in diagnostics if item.name == "openalex")
        self.assertEqual(openalex.credential_source, "environment")

    def test_live_checks_classify_provider_failures(self):
        config_text = CONFIG.replace(
            "    enabled: false\n    api_key_env: SPRINGER_KEY",
            "    enabled: true\n    api_key_env: SPRINGER_KEY",
        )
        self.path.write_text(config_text, encoding="utf-8")
        config = load_config(self.path)
        transport = Mock()

        def json_probe(url, **kwargs):
            context = kwargs.get("context", "")
            if context.startswith("OpenAlex"):
                raise HttpError("limited", status_code=429)
            if context.startswith("Elsevier"):
                raise HttpError("unauthorized", status_code=401)
            if context.startswith("Springer"):
                raise HttpError("forbidden", status_code=403)
            if context.startswith("IEEE"):
                raise HttpError("server", status_code=503)
            return {}

        transport.json.side_effect = json_probe
        transport.post_form_json.side_effect = HttpError(
            "unauthorized",
            status_code=401,
        )
        diagnostics = diagnose_providers(
            config,
            check=True,
            environ={
                "OPENALEX_KEY": "openalex-secret",
                "ELSEVIER_KEY": "elsevier-secret",
                "SPRINGER_KEY": "springer-secret",
                "IEEE_KEY": "ieee-secret",
                "SEMANTIC_KEY": "semantic-secret",
                "MENDELEY_CLIENT_ID": "mendeley-id",
                "MENDELEY_CLIENT_SECRET": "mendeley-secret",
            },
            reporter=Reporter(-1, StringIO()),
            transport=transport,
        )
        status = {item.name: item.status for item in diagnostics}

        self.assertEqual(status["crossref"], "available")
        self.assertEqual(status["openalex"], "rate-limited")
        self.assertEqual(status["elsevier"], "authentication-failed")
        self.assertEqual(status["springer"], "access-denied")
        self.assertEqual(status["ieee"], "unavailable")
        self.assertEqual(status["semantic_scholar"], "available")
        self.assertEqual(status["mendeley"], "authentication-failed")

        mendeley = next(item for item in diagnostics if item.name == "mendeley")
        self.assertIn("application ID/secret", mendeley.detail)
        report = format_provider_diagnostics(diagnostics)
        self.assertNotIn("mendeley-id", report)
        self.assertNotIn("mendeley-secret", report)

    def test_required_credential_configuration_is_reported_without_network(self):
        self.path.write_text(
            CONFIG.replace(
                "    api_key_env: IEEE_KEY\n",
                "",
            ),
            encoding="utf-8",
        )
        config = load_config(self.path)
        transport = Mock()
        diagnostics = diagnose_providers(
            config,
            check=True,
            environ={"ELSEVIER_KEY": "elsevier-secret"},
            reporter=Reporter(-1, StringIO()),
            transport=transport,
        )
        ieee = next(item for item in diagnostics if item.name == "ieee")
        self.assertEqual(ieee.status, "configuration-error")
        self.assertFalse(ieee.checked)


if __name__ == "__main__":
    unittest.main()
