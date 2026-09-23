from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
import json
import tempfile
import unittest

from bibreview.cli import main


CONFIG = """\
schema_version: 1
environment:
  file: .env
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
    enabled: true
    api_key_env: OPENALEX_KEY
    min_interval_seconds: 0.75
  elsevier:
    enabled: true
    api_key_env: ELSEVIER_KEY
site:
  enabled: false
"""


class ProviderCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = self.root / "bibreview.yml"
        self.config.write_text(CONFIG, encoding="utf-8")
        (self.root / ".env").write_text(
            "OPENALEX_KEY=super-secret-openalex\n",
            encoding="utf-8",
        )

    def run_cli(self, *args):
        stdout = StringIO()
        stderr = StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(["--config", str(self.config), *args])
        return code, stdout.getvalue(), stderr.getvalue()

    def test_human_provider_report_is_static_and_secret_safe(self):
        code, stdout, stderr = self.run_cli("providers")
        self.assertEqual(code, 0, stderr)
        self.assertIn("openalex", stdout)
        self.assertIn("OPENALEX_KEY", stdout)
        self.assertIn("dotenv", stdout)
        self.assertIn("elsevier", stdout)
        self.assertIn("missing-credential", stdout)
        self.assertNotIn("super-secret-openalex", stdout)
        self.assertEqual(stderr, "")

    def test_json_provider_report_contains_no_secret_values(self):
        code, stdout, stderr = self.run_cli("providers", "--json")
        self.assertEqual(code, 0, stderr)
        payload = json.loads(stdout)
        by_name = {item["name"]: item for item in payload}
        self.assertEqual(by_name["openalex"]["credential_source"], "dotenv")
        self.assertEqual(by_name["openalex"]["credential_variable"], "OPENALEX_KEY")
        self.assertEqual(by_name["openalex"]["min_interval_seconds"], 0.75)
        self.assertNotIn("super-secret-openalex", stdout)
        self.assertEqual(stderr, "")


if __name__ == "__main__":
    unittest.main()
