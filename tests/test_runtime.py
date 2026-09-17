from io import StringIO
from pathlib import Path
import tempfile
import unittest

from bibreview.config import load_config
from bibreview.providers.crossref import CrossRefProvider
from bibreview.reporting import Reporter
from bibreview.runtime import build_collection_services


CONFIG = """\
schema_version: 1
project:
  name: Example Review
  slug: example-review
providers:
  crossref:
    enabled: true
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


if __name__ == "__main__":
    unittest.main()
