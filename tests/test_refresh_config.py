from pathlib import Path
import tempfile
import unittest

from bibreview.config import ConfigError, load_config


class RefreshConfigTests(unittest.TestCase):
    def load(self, refresh_block: str):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        path = Path(temporary.name) / "bibreview.yml"
        path.write_text(
            "schema_version: 1\n"
            "project:\n"
            "  name: Example Review\n"
            "  slug: example-review\n"
            + refresh_block
            + "site:\n"
            "  enabled: false\n",
            encoding="utf-8",
        )
        return load_config(path)

    def test_refresh_policy_is_disabled_by_default(self):
        config = self.load("")
        self.assertEqual(config.refresh.types, ())
        self.assertEqual(config.refresh.when_missing_any, ())

    def test_refresh_policy_loads_types_and_canonical_fields(self):
        config = self.load(
            "refresh:\n"
            "  types:\n"
            "    - journal-article\n"
            "  when_missing_any:\n"
            "    - volume\n"
            "    - issue\n"
            "    - pages\n"
        )
        self.assertEqual(config.refresh.types, ("journal-article",))
        self.assertEqual(config.refresh.when_missing_any, ("volume", "issue", "pages"))

    def test_unknown_refresh_field_is_rejected(self):
        with self.assertRaisesRegex(ConfigError, "unsupported publication field"):
            self.load(
                "refresh:\n"
                "  types:\n"
                "    - journal-article\n"
                "  when_missing_any:\n"
                "    - imaginary_field\n"
            )


if __name__ == "__main__":
    unittest.main()
