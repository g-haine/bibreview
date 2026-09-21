from pathlib import Path
import tempfile
import unittest

from bibreview.config import ConfigError, DEFAULT_DISCOVERY_TYPES, load_config


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
        self.assertEqual(config.paths.collected, root / "data/collected.json")
        self.assertEqual(config.paths.pending, root / "data/pending.txt")
        self.assertEqual(config.audit.campaign, root / "data/audit/campaign.json")
        self.assertEqual(config.audit.report, root / "data/audit/report.json")
        self.assertEqual(config.audit.batch_size, 50)
        self.assertEqual(config.discovery.query, "fluid-structure interaction")
        self.assertEqual(config.discovery.accepted_types, DEFAULT_DISCOVERY_TYPES)
        self.assertEqual(config.discovery.exclude_doi_substrings, ())

    def test_resolves_optional_environment_file_relative_to_config(self):
        root, path = self.write(BASE.replace(
            "project:\n",
            "environment:\n  file: secrets/.env\nproject:\n",
        ))
        config = load_config(path)
        self.assertEqual(config.environment.file, root / "secrets/.env")

    def test_environment_file_defaults_to_none(self):
        _, path = self.write()
        self.assertIsNone(load_config(path).environment.file)

    def test_loads_optional_arxiv_configuration(self):
        root, path = self.write(BASE.replace(
            "  slug: example-review\n",
            "  slug: example-review\n"
            "  repository: https://example.org/review\n"
            "  contact:\n"
            "    name: Ada Lovelace\n"
            "    email: ada@example.org\n"
            "arxiv:\n"
            "  enabled: true\n"
            "  query: all:fluid AND all:structure\n"
            "  max_results: 40\n"
            "  sort_by: submittedDate\n"
            "  sort_order: ascending\n"
            "  output: public/arxiv.json\n",
        ))
        config = load_config(path)
        self.assertTrue(config.arxiv.enabled)
        self.assertEqual(config.arxiv.query, "all:fluid AND all:structure")
        self.assertEqual(config.arxiv.max_results, 40)
        self.assertEqual(config.arxiv.sort_by, "submittedDate")
        self.assertEqual(config.arxiv.sort_order, "ascending")
        self.assertEqual(config.arxiv.output, root / "public/arxiv.json")
        self.assertEqual(config.project.contact_email, "ada@example.org")

    def test_arxiv_defaults_to_disabled(self):
        _, path = self.write()
        config = load_config(path)
        self.assertFalse(config.arxiv.enabled)
        self.assertIsNone(config.arxiv.output)

    def test_enabled_arxiv_requires_query_output_and_contact_email(self):
        cases = (
            (
                "missing query",
                "arxiv:\n  enabled: true\n  output: arxiv.json\n",
                "arxiv.query",
            ),
            (
                "missing output",
                "arxiv:\n  enabled: true\n  query: all:test\n",
                "arxiv.output",
            ),
            (
                "missing contact",
                "arxiv:\n  enabled: true\n  query: all:test\n  output: arxiv.json\n",
                "project.contact.email",
            ),
        )
        for label, arxiv, message in cases:
            with self.subTest(label=label):
                content = BASE.replace(
                    "paths:\n",
                    arxiv + "paths:\n",
                )
                if label != "missing contact":
                    content = content.replace(
                        "  slug: example-review\n",
                        "  slug: example-review\n"
                        "  contact:\n"
                        "    email: ada@example.org\n",
                    )
                _, path = self.write(content)
                with self.assertRaisesRegex(ConfigError, message):
                    load_config(path)

    def test_rejects_invalid_arxiv_sort_policy(self):
        content = BASE.replace(
            "  slug: example-review\n",
            "  slug: example-review\n"
            "  contact:\n"
            "    email: ada@example.org\n"
            "arxiv:\n"
            "  enabled: true\n"
            "  query: all:test\n"
            "  output: arxiv.json\n"
            "  sort_by: unknown\n",
        )
        _, path = self.write(content)
        with self.assertRaisesRegex(ConfigError, "arxiv.sort_by"):
            load_config(path)

    def test_loads_audit_configuration(self):
        root, path = self.write(BASE.replace(
            "relevance:\n",
            "audit:\n"
            "  campaign: state/audit.json\n"
            "  report: reports/audit.json\n"
            "  batch_size: 25\n"
            "relevance:\n",
        ))
        config = load_config(path)
        self.assertEqual(config.audit.campaign, root / "state/audit.json")
        self.assertEqual(config.audit.report, root / "reports/audit.json")
        self.assertEqual(config.audit.batch_size, 25)

    def test_rejects_identical_audit_paths_and_invalid_batch_size(self):
        same_path = BASE.replace(
            "relevance:\n",
            "audit:\n"
            "  campaign: data/audit.json\n"
            "  report: data/audit.json\n"
            "relevance:\n",
        )
        _, path = self.write(same_path)
        with self.assertRaisesRegex(ConfigError, "must be different paths"):
            load_config(path)

        invalid_size = BASE.replace(
            "relevance:\n",
            "audit:\n"
            "  batch_size: 0\n"
            "relevance:\n",
        )
        _, path = self.write(invalid_size)
        with self.assertRaisesRegex(ConfigError, "audit.batch_size"):
            load_config(path)

    def test_loads_provider_oauth_environment_fields(self):
        _, path = self.write(BASE.replace(
            "providers:\n",
            "providers:\n"
            "  mendeley:\n"
            "    enabled: true\n"
            "    client_id_env: MENDELEY_CLIENT_ID\n"
            "    client_secret_env: MENDELEY_CLIENT_SECRET\n",
        ))
        config = load_config(path)
        mendeley = config.providers["mendeley"]
        self.assertEqual(mendeley.client_id_env, "MENDELEY_CLIENT_ID")
        self.assertEqual(mendeley.client_secret_env, "MENDELEY_CLIENT_SECRET")

    def test_loads_configurable_discovery_type_and_doi_exclusions(self):
        _, path = self.write(BASE.replace(
            "  query: fluid-structure interaction\n",
            "  query: fluid-structure interaction\n"
            "  accepted_types:\n"
            "    - journal-article\n"
            "    - proceedings-article\n"
            "  exclude_doi_substrings:\n"
            "    - zenodo\n"
            "    - arxiv\n",
        ))
        config = load_config(path)
        self.assertEqual(
            config.discovery.accepted_types,
            ("journal-article", "proceedings-article"),
        )
        self.assertEqual(config.discovery.exclude_doi_substrings, ("zenodo", "arxiv"))

    def test_loads_jekyll_render_policy(self):
        _, path = self.write(BASE.replace(
            "site:\n  enabled: false\n",
            "site:\n"
            "  enabled: true\n"
            "  implementation: jekyll\n"
            "  jekyll:\n"
            "    include_authorless_year_publications: false\n"
            "    author_index_extra_html: |\n"
            "      <p>Project note.</p>\n"
            "      <hr />\n"
            "    category_by_type:\n"
            "      journal-article: articles\n"
            "      book: books\n"
            "    event_category_rules:\n"
            "      - pattern: 'Conference|Workshop'\n"
            "        category: proceedings\n"
            "    isbn_types:\n"
            "      - book\n"
            "      - book-chapter\n",
        ))
        config = load_config(path)
        self.assertFalse(
            config.site.jekyll.include_authorless_year_publications
        )
        self.assertEqual(
            config.site.jekyll.author_index_extra_html,
            "<p>Project note.</p>\n<hr />\n",
        )
        self.assertEqual(
            dict(config.site.jekyll.category_by_type),
            {"journal-article": "articles", "book": "books"},
        )
        self.assertEqual(
            config.site.jekyll.event_category_rules,
            (("Conference|Workshop", "proceedings"),),
        )
        self.assertEqual(
            config.site.jekyll.isbn_types,
            ("book", "book-chapter"),
        )

    def test_rejects_invalid_jekyll_event_category_rule(self):
        _, path = self.write(BASE.replace(
            "site:\n  enabled: false\n",
            "site:\n"
            "  enabled: true\n"
            "  jekyll:\n"
            "    event_category_rules:\n"
            "      - pattern: '['\n"
            "        category: proceedings\n",
        ))
        with self.assertRaisesRegex(ConfigError, "event_category_rules"):
            load_config(path)

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

    def test_rejects_invalid_discovery_string_lists(self):
        for field in ("accepted_types", "exclude_doi_substrings"):
            with self.subTest(field=field):
                _, path = self.write(BASE.replace(
                    "  query: fluid-structure interaction\n",
                    f"  query: fluid-structure interaction\n  {field}: not-a-list\n",
                ))
                with self.assertRaisesRegex(ConfigError, f"discovery.{field}"):
                    load_config(path)


if __name__ == "__main__":
    unittest.main()
