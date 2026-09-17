from pathlib import Path
import tempfile
import unittest

from bibreview.config import ConfigError, load_config


BASE = """\
schema_version: 1
project:
  name: Example Review
  slug: example-review
paths:
  bibliography: data/bibliography.json
discovery:
  provider: openalex
  query: fluid-structure interaction
relevance:
  patterns:
    - 'fluid[-\\s]+structure'
providers:
  crossref:
    enabled: true
site:
  enabled: false
"""


class ConfigTests(unittest.TestCase):
    def write(self, content=BASE):
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        path = root / "bibreview.yml"
        path.write_text(content, encoding="utf-8")
        self.addCleanup(temp.cleanup)
        return root, path

    def test_loads_and_resolves_paths_relative_to_config(self):
        root, path = self.write()
        config = load_config(path)
        self.assertEqual(config.project.slug, "example-review")
        self.assertEqual(config.paths.bibliography, root / "data/bibliography.json")
        self.assertEqual(config.paths.pending, root / "data/pending.txt")
        self.assertEqual(config.discovery.query, "fluid-structure interaction")

    def test_rejects_unknown_schema_version(self):
        _, path = self.write(BASE.replace("schema_version: 1", "schema_version: 2"))
        with self.assertRaisesRegex(ConfigError, "unsupported schema_version"):
            load_config(path)

    def test_rejects_project_slug_with_uppercase(self):
        _, path = self.write(BASE.replace("example-review", "ExampleReview"))
        with self.assertRaisesRegex(ConfigError, "project.slug"):
            load_config(path)

    def test_rejects_invalid_relevance_regex(self):
        _, path = self.write(BASE.replace("'fluid[-\\s]+structure'", "'[broken'"))
        with self.assertRaisesRegex(ConfigError, "relevance.patterns"):
            load_config(path)


if __name__ == "__main__":
    unittest.main()
