from io import StringIO
from pathlib import Path
import tempfile
import unittest

from bibreview.config import load_config
from bibreview.providers.crossref import CrossRefProvider
from bibreview.providers.openalex import OpenAlexProvider
from bibreview.reporting import Reporter
from bibreview.runtime import build_collection_services, build_discovery_services


CONFIG = """\
schema_version: 1
project:
  name: Example Review
  slug: example-review
discovery:
  provider: openalex
  query: example query
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
  ieee:
    enabled: true
    api_key_env: IEEE_KEY
  semantic_scholar:
    enabled: true
  mendeley:
    enabled: true
    token_env: MENDELEY_TOKEN
site:
  enabled: false
"""


class RuntimeTests(unittest.TestCase):
    def config(self, text=CONFIG):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        path = Path(temp.name) / "bibreview.yml"
        path.write_text(text, encoding="utf-8")
        return load_config(path)

    def test_collection_services_resolve_optional_secrets_at_composition_boundary(self):
        stream = StringIO()
        services = build_collection_services(
            self.config(),
            reporter=Reporter(stream=stream),
            environ={
                "ELSEVIER_KEY": "elsevier-secret",
                "IEEE_KEY": "ieee-secret",
                "MENDELEY_TOKEN": "mendeley-secret",
            },
        )
        self.assertIsInstance(services.provider, CrossRefProvider)
        self.assertTrue(callable(services.enrichment_lookup))
        self.assertTrue(callable(services.citation_lookup))
        self.assertTrue(callable(services.bibtex_lookup))
        self.assertEqual(stream.getvalue(), "")

    def test_discovery_services_compose_openalex_crossref_and_discovery_enrichment(self):
        stream = StringIO()
        services = build_discovery_services(
            self.config(),
            reporter=Reporter(stream=stream),
            environ={
                "OPENALEX_KEY": "openalex-secret",
                "ELSEVIER_KEY": "elsevier-secret",
                "IEEE_KEY": "ieee-secret",
                "MENDELEY_TOKEN": "mendeley-secret",
            },
        )
        self.assertIsInstance(services.discovery_provider, OpenAlexProvider)
        self.assertEqual(services.discovery_provider.api_key, "openalex-secret")
        self.assertIsInstance(services.provider, CrossRefProvider)
        self.assertTrue(callable(services.enrichment_lookup))
        self.assertEqual(stream.getvalue(), "")

    def test_configured_environment_file_supplies_provider_secrets(self):
        config = self.config(CONFIG.replace(
            "project:\n",
            "environment:\n  file: .env\nproject:\n",
        ))
        (config.source.parent / ".env").write_text(
            "OPENALEX_KEY=file-openalex\n"
            "ELSEVIER_KEY=file-elsevier\n"
            "IEEE_KEY=file-ieee\n"
            "MENDELEY_TOKEN=file-mendeley\n",
            encoding="utf-8",
        )
        stream = StringIO()
        services = build_discovery_services(
            config,
            reporter=Reporter(stream=stream),
            environ={},
        )
        self.assertEqual(services.discovery_provider.api_key, "file-openalex")
        self.assertEqual(stream.getvalue(), "")

    def test_explicit_environment_overrides_configured_environment_file(self):
        config = self.config(CONFIG.replace(
            "project:\n",
            "environment:\n  file: .env\nproject:\n",
        ))
        (config.source.parent / ".env").write_text(
            "OPENALEX_KEY=file-openalex\n"
            "ELSEVIER_KEY=file-elsevier\n"
            "IEEE_KEY=file-ieee\n"
            "MENDELEY_TOKEN=file-mendeley\n",
            encoding="utf-8",
        )
        services = build_discovery_services(
            config,
            reporter=Reporter(stream=StringIO()),
            environ={
                "OPENALEX_KEY": "shell-openalex",
                "ELSEVIER_KEY": "shell-elsevier",
                "IEEE_KEY": "shell-ieee",
                "MENDELEY_TOKEN": "shell-mendeley",
            },
        )
        self.assertEqual(services.discovery_provider.api_key, "shell-openalex")

    def test_missing_configured_environment_file_warns_and_continues(self):
        config = self.config(CONFIG.replace(
            "project:\n",
            "environment:\n  file: missing.env\nproject:\n",
        ))
        stream = StringIO()
        services = build_collection_services(
            config,
            reporter=Reporter(stream=stream),
            environ={},
        )
        self.assertIsInstance(services.provider, CrossRefProvider)
        self.assertIn("Configured environment file does not exist", stream.getvalue())

    def test_project_contact_email_is_forwarded_to_crossref(self):
        config = self.config(CONFIG.replace(
            "  slug: example-review\n",
            "  slug: example-review\n"
            "  contact:\n"
            "    email: maintainer@example.org\n",
        ))
        services = build_collection_services(
            config,
            reporter=Reporter(stream=StringIO()),
            environ={},
        )
        self.assertEqual(
            services.provider.mailto,
            "maintainer@example.org",
        )

    def test_missing_optional_secrets_warn_and_do_not_block_collection_wiring(self):
        stream = StringIO()
        services = build_collection_services(
            self.config(),
            reporter=Reporter(stream=stream),
            environ={},
        )
        self.assertIsInstance(services.provider, CrossRefProvider)
        warnings = stream.getvalue()
        self.assertIn("Elsevier", warnings)
        self.assertIn("IEEE", warnings)
        self.assertIn("Mendeley", warnings)

    def test_missing_openalex_key_warns_but_discovery_remains_available(self):
        stream = StringIO()
        services = build_discovery_services(
            self.config(),
            reporter=Reporter(stream=stream),
            environ={},
        )
        self.assertIsInstance(services.discovery_provider, OpenAlexProvider)
        self.assertEqual(services.discovery_provider.api_key, "")
        self.assertIn("OpenAlex API key variable OPENALEX_KEY is unset", stream.getvalue())

    def test_crossref_cannot_be_disabled_for_doi_collection(self):
        config = self.config("""\
schema_version: 1
project:
  name: Example Review
  slug: example-review
providers:
  crossref:
    enabled: false
site:
  enabled: false
""")
        with self.assertRaisesRegex(ValueError, "CrossRef must be enabled"):
            build_collection_services(config, environ={})

    def test_selected_openalex_provider_cannot_be_disabled(self):
        config = self.config("""\
schema_version: 1
project:
  name: Example Review
  slug: example-review
discovery:
  provider: openalex
  query: example
providers:
  crossref:
    enabled: true
  openalex:
    enabled: false
site:
  enabled: false
""")
        with self.assertRaisesRegex(ValueError, "OpenAlex must be enabled"):
            build_discovery_services(config, environ={})

    def test_unknown_discovery_provider_is_rejected_at_composition_boundary(self):
        config = self.config(CONFIG.replace("provider: openalex", "provider: unknown", 1))
        with self.assertRaisesRegex(ValueError, "unsupported discovery provider"):
            build_discovery_services(config, environ={})


if __name__ == "__main__":
    unittest.main()
