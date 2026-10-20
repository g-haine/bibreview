from __future__ import annotations

from datetime import date
import unittest

import yaml

from bibreview.site import (
    HugoRenderOptions,
    RenderedArtifact,
    SiteAuthor,
    SiteAuthorPage,
    SiteModel,
    SitePublication,
    SitePublicationAuthor,
    SitePublicationEditor,
    SiteReference,
    SiteRenderError,
    SiteYearPage,
    render_hugo_site,
)


class HugoRendererTests(unittest.TestCase):
    def model(self) -> SiteModel:
        publication = SitePublication(
            id="pub-id",
            permalink="example-publication",
            created_date=date(2025, 5, 10),
            year="2025",
            type="journal-article",
            title="A $x$ title",
            authors=(
                SitePublicationAuthor(
                    slug="ada-lovelace",
                    name="Ada Lovelace",
                ),
            ),
            abstract="An $H$ abstract with literal {{< shortcode >}} text.",
            container_title="Journal of Examples",
            volume="12",
            issue="3",
            pages="10--20",
            publisher="Example Publisher",
            event="",
            keywords=("fluid-structure", "energy"),
            identifiers={"doi": "10.1234/example"},
            references=(
                SiteReference(
                    doi="10.1234/internal",
                    citation="Internal reference",
                    permalink="internal-work",
                ),
                SiteReference(
                    doi=None,
                    citation="Reference without DOI",
                ),
            ),
        )
        author = SiteAuthor(
            slug="ada-lovelace",
            name="Ada Lovelace",
            variants=("Ada Lovelace",),
        )
        return SiteModel(
            publications=(publication,),
            authors={
                "ada-lovelace": SiteAuthorPage(
                    author=author,
                    publication_ids=("pub-id",),
                )
            },
            years={
                "2025": SiteYearPage(
                    year="2025",
                    publication_ids=("pub-id",),
                )
            },
        )

    def artifacts(
        self,
        *,
        options: HugoRenderOptions | None = None,
    ) -> dict[str, str]:
        rendered = render_hugo_site(
            self.model(),
            {"pub-id": "@article{example}\n"},
            options=options,
        )
        self.assertTrue(all(isinstance(item, RenderedArtifact) for item in rendered))
        return {item.path: item.content for item in rendered}

    def test_renders_publication_author_and_year_content(self) -> None:
        artifacts = self.artifacts()
        self.assertEqual(
            set(artifacts),
            {
                "content/publications/example-publication.md",
                "content/authors/ada-lovelace.md",
                "content/authors/_index.md",
                "content/years/2025.md",
                "content/years/_index.md",
            },
        )

        publication = artifacts["content/publications/example-publication.md"]
        front_matter = publication.split("---", 2)[1]
        parsed = yaml.safe_load(front_matter)
        self.assertEqual(parsed["title"], "A $x$ title")
        self.assertEqual(parsed["year"], "2025")
        self.assertEqual(parsed["authors"], ["Ada Lovelace"])
        self.assertEqual(parsed["author_slugs"], ["ada-lovelace"])
        self.assertEqual(parsed["tags"], ["fluid-structure", "energy"])

        self.assertIn(
            "[Ada Lovelace](../../authors/ada-lovelace/)",
            publication,
        )
        self.assertIn("An $H$ abstract", publication)
        self.assertIn("&#123;&#123;&lt; shortcode >}}", publication)
        self.assertIn(
            "[10.1234/example](https://doi.org/10.1234/example)",
            publication,
        )
        self.assertIn("~~~bibtex\n@article{example}\n~~~", publication)
        self.assertIn(
            "[Download the bib file](../../bib/example-publication.bib)",
            publication,
        )
        self.assertIn(
            "[Internal reference](../internal-work/)",
            publication,
        )

        self.assertIn(
            "[A $x$ title](../../publications/example-publication/)",
            artifacts["content/authors/ada-lovelace.md"],
        )
        self.assertIn(
            "[2025](./2025/) (1)",
            artifacts["content/years/_index.md"],
        )

    def test_custom_section_and_asset_prefixes_are_used(self) -> None:
        artifacts = self.artifacts(
            options=HugoRenderOptions(
                publication_path_prefix="papers",
                author_path_prefix="people",
                year_path_prefix="dates",
                bibtex_asset_prefix="downloads/bib",
            )
        )
        self.assertIn("content/papers/example-publication.md", artifacts)
        self.assertIn("content/people/ada-lovelace.md", artifacts)
        self.assertIn("content/dates/2025.md", artifacts)
        publication = artifacts["content/papers/example-publication.md"]
        self.assertIn(
            "[Ada Lovelace](../../people/ada-lovelace/)",
            publication,
        )
        self.assertIn(
            "../../downloads/bib/example-publication.bib",
            publication,
        )

    def test_editor_only_publication_has_valid_empty_author_lists(self) -> None:
        item = self.model().publications[0]
        editor_only = SitePublication(
            id=item.id,
            permalink=item.permalink,
            created_date=item.created_date,
            year=item.year,
            type="book",
            title=item.title,
            authors=(),
            editors=(SitePublicationEditor(name="Peter Benner"),),
            abstract=item.abstract,
            container_title="",
            volume="",
            issue="",
            pages="",
            publisher=item.publisher,
            event="",
            keywords=(),
            identifiers={"isbn": "978-1-2345-6789-0"},
        )
        model = SiteModel(
            publications=(editor_only,),
            authors={},
            years={
                "2025": SiteYearPage(
                    year="2025",
                    publication_ids=("pub-id",),
                )
            },
        )
        rendered = render_hugo_site(
            model,
            {"pub-id": "@book{example}\n"},
        )
        publication = rendered[0].content
        parsed = yaml.safe_load(publication.split("---", 2)[1])
        self.assertEqual(parsed["authors"], [])
        self.assertEqual(parsed["author_slugs"], [])
        self.assertEqual(parsed["editors"], ["Peter Benner"])
        self.assertNotIn("## Authors", publication)
        self.assertIn("## Editors\nPeter Benner", publication)
        self.assertIn("- **ISBN:** 978-1-2345-6789-0", publication)

    def test_authorless_year_entries_can_be_excluded(self) -> None:
        item = self.model().publications[0]
        editor_only = SitePublication(
            id=item.id,
            permalink=item.permalink,
            created_date=item.created_date,
            year=item.year,
            type="book",
            title=item.title,
            authors=(),
            editors=(SitePublicationEditor(name="Peter Benner"),),
            abstract=item.abstract,
            container_title="",
            volume="",
            issue="",
            pages="",
            publisher=item.publisher,
            event="",
            keywords=(),
            identifiers={"isbn": "978-1-2345-6789-0"},
        )
        model = SiteModel(
            publications=(editor_only,),
            authors={},
            years={
                "2025": SiteYearPage(
                    year="2025",
                    publication_ids=("pub-id",),
                )
            },
        )
        rendered = render_hugo_site(
            model,
            {"pub-id": "@book{example}\n"},
            options=HugoRenderOptions(
                include_authorless_year_publications=False,
            ),
        )
        artifacts = {item.path: item.content for item in rendered}
        self.assertNotIn("content/years/2025.md", artifacts)
        self.assertNotIn("[2025]", artifacts["content/years/_index.md"])

    def test_missing_bibtex_is_rejected(self) -> None:
        with self.assertRaisesRegex(SiteRenderError, "missing BibTeX"):
            render_hugo_site(self.model(), {})

    def test_unsafe_prefix_is_rejected(self) -> None:
        with self.assertRaisesRegex(SiteRenderError, "safe relative path"):
            HugoRenderOptions(publication_path_prefix="../outside")


if __name__ == "__main__":
    unittest.main()
