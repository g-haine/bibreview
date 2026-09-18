from __future__ import annotations

from pathlib import Path
import tomllib
import unittest

from bibreview import __version__


class VersionTests(unittest.TestCase):
    def test_package_version_matches_project_metadata(self) -> None:
        pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
        metadata = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        self.assertEqual(__version__, metadata["project"]["version"])


if __name__ == "__main__":
    unittest.main()
