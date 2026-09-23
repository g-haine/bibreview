from io import StringIO
from pathlib import Path
import tempfile
import unittest

from bibreview.config import load_config
from bibreview.providers.audit import (
    CrossRefAuditSource,
    OpenAlexAuditSource,
    SemanticScholarAuditSource,
)
from bibreview.providers.crossref import CrossRefProvider
from bibreview.providers.openalex import OpenAlexProvider
from bibreview.providers.http import RateLimitedTransport
from bibreview.reporting import Reporter
from bibreview.runtime import (
    build_audit_services,
    build_collection_services,
    build_discovery_services,
)


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
    min_interval_seconds: 0.2
  openalex:
    enabled: true
    api_key_env: OPENALEX_KEY
    min_interval_seconds: 0.3
  elsevier:
    enabled: true
    api_key_env: ELSEVIER_KEY
    min_interval_seconds: 0.4
  springer:
    enabled: false
  ieee:
    enabled: true
    api_key_env: IEEE_KEY
    min_interval_seconds: 0.5
  semantic_scholar:
    enabled: true
    min_interval_seconds: 1.1
  mendeley:
    enabled: true
    client_id_env: MENDELEY_CLIENT_ID
    client_secret_env: MENDELEY_CLIENT_SECRET
    min_interval_seconds: 0.6
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
                "OPENALEX_KEY": "openalex-secret",
                "ELSEVIER_KEY": "elsevier-secret",
                "IEEE_KEY": "ieee-secret",
                "MENDELEY_CLIENT_ID": "mendeley-id",
                "MENDELEY_CLIENT_SECRET": "mendeley-secret",
            },
        )
        self.assertIsInstance(services.provider, CrossRefProvider)
        self.assertIsInstance(services.provider.transport, RateLimitedTransport)
        self.assertEqual(services.provider.transport.min_interval_seconds, 0.2)
        enrichment = services.enrichment_lookup.__self__
        self.assertEqual(
            enrichment.publisher.elsevier.transport.min_interval_seconds,
            0.4,
        )
        self.assertEqual(
            enrichment.publisher.ieee.transport.min_interval_seconds,
            0.5,
        )
        self.assertEqual(
            enrichment.fallback.openalex.transport.min_interval_seconds,
            0.3,
        )
        self.assertEqual(
            enrichment.fallback.semantic_scholar.transport.min_interval_seconds,
            1.1,
        )
        self.assertEqual(
            enrichment.fallback.mendeley.transport.min_interval_seconds,
            0.6,
        )
        self.assertEqual(
            enrichment.fallback.semantic_scholar.min_interval_seconds,
            0.0,
        )
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
                "MENDELEY_CLIENT_ID": "mendeley-id",
                "MENDELEY_CLIENT_SECRET": "mendeley-secret",
            },
        )
        self.assertIsInstance(services.discovery_provider, OpenAlexProvider)
        self.assertEqual(services.discovery_provider.api_key, "openalex-secret")
        self.assertEqual(
            services.discovery_provider.transport.min_interval_seconds,
            0.3,
        )
        self.assertIsInstance(services.provider, CrossRefProvider)
        self.assertEqual(services.provider.transport.min_interval_seconds, 0.2)
        self.assertTrue(callable(services.enrichment_lookup))
        self.assertEqual(stream.getvalue(), "")

    def test_audit_services_compose_only_core_evidence_providers(self):
        config = self.config(CONFIG.replace(
            "  semantic_scholar:\n    enabled: true\n",
            "  semantic_scholar:\n"
            "    enabled: true\n"
            "    api_key_env: SEMANTIC_KEY\n",
        ))
        stream = StringIO()
        services = build_audit_services(
            config,
            reporter=Reporter(stream=stream),
            environ={
                "OPENALEX_KEY": "openalex-secret",
                "SEMANTIC_KEY": "semantic-secret",
            },
        )

        self.assertEqual(
            tuple(type(source) for source in services.sources),
            (
                CrossRefAuditSource,
                OpenAlexAuditSource,
                SemanticScholarAuditSource,
            ),
        )
        self.assertEqual(
            services.sources[1].provider.api_key,
            "openalex-secret",
        )
        self.assertEqual(
            services.sources[2].provider.api_key,
            "semantic-secret",
        )
        self.assertEqual(
            services.sources[0].provider.transport.min_interval_seconds,
            0.2,
        )
        self.assertEqual(
            services.sources[1].provider.transport.min_interval_seconds,
            0.3,
        )
        self.assertEqual(
            services.sources[2].provider.transport.min_interval_seconds,
            1.1,
        )
        self.assertEqual(
            services.sources[2].provider.min_interval_seconds,
            0.0,
        )
        warnings = stream.getvalue()
        self.assertNotIn("Elsevier", warnings)
        self.assertNotIn("IEEE", warnings)
        self.assertNotIn("Mendeley", warnings)

    def test_provider_interval_defaults_to_zero_when_omitted(self):
        config = self.config(CONFIG.replace(
            "    min_interval_seconds: 1.1\n",
            "",
        ))
        services = build_audit_services(
            config,
            reporter=Reporter(stream=StringIO()),
            environ={},
        )
        semantic = next(
            source
            for source in services.sources
            if isinstance(source, SemanticScholarAuditSource)
        )
        self.assertEqual(semantic.provider.api_key, "")
        self.assertEqual(
            semantic.provider.transport.min_interval_seconds,
            0.0,
        )
        self.assertEqual(semantic.provider.min_interval_seconds, 0.0)

    def test_configured_environment_file_supplies_provider_secrets(self):
        config = self.config(CONFIG.replace(
            "project:\n",
            "environment:\n  file: .env\nproject:\n",
        ))
        (config.source.parent / ".env").write_text(
            "OPENALEX_KEY=file-openalex\n"
            "ELSEVIER_KEY=file-elsevier\n"
            "IEEE_KEY=file-ieee\n"
            "MENDELEY_CLIENT_ID=file-mendeley-id\n"
            "MENDELEY_CLIENT_SECRET=file-mendeley-secret\n",
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
            "MENDELEY_CLIENT_ID=file-mendeley-id\n"
            "MENDELEY_CLIENT_SECRET=file-mendeley-secret\n",
            encoding="utf-8",
        )
        services = build_discovery_services(
            config,
            reporter=Reporter(stream=StringIO()),
            environ={
                "OPENALEX_KEY": "shell-openalex",
                "ELSEVIER_KEY": "shell-elsevier",
                "IEEE_KEY": "shell-ieee",
                "MENDELEY_CLIENT_ID": "shell-mendeley-id",
                "MENDELEY_CLIENT_SECRET": "shell-mendeley-secret",
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
        self.assertIn("OpenAlex", warnings)
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
