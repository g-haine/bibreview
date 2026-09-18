from __future__ import annotations

from datetime import date
import unittest

from bibreview.site import (
    JekyllIndexRenderOptions,
    RenderedArtifact,
    SiteAuthor,
    SiteAuthorPage,
    SiteModel,
    SitePublication,
    SitePublicationAuthor,
    SiteRenderError,
    SiteYearPage,
    render_jekyll_index_pages,
)


class JekyllIndexRendererTests(unittest.TestCase):
    def model(self) -> SiteModel:
        old = SitePublication(
            id="old-id",
            permalink="old-work",
            created_date=date(2024, 1, 2),
            year="2024",
            type="journal-article",
            title="Old & $x$ work",
            authors=(SitePublicationAuthor(slug="ada-lovelace", name="A. Lovelace"),),
            abstract="",
            container_title="Journal",
            volume="1",
            issue="",
            pages="1-2",
            publisher="Publisher",
            event="",
            keywords=(),
        )
        new = SitePublication(
            id="new-id",
            permalink="new-work",
            created_date=date(2025, 1, 2),
            year="2025",
            type="journal-article",
            title="New <work>",
            authors=(SitePublicationAuthor(slug="ada-lovelace", name="Ada Lovelace"),),
            abstract="",
            container_title="Journal",
            volume="2",
            issue="",
            pages="3-4",
            publisher="Publisher",
            event="",
            keywords=(),
        )
        author = SiteAuthor(
            slug="ada-lovelace",
            name="Ada Lovelace",
            variants=("Ada Lovelace", "A. Lovelace"),
        )
        return SiteModel(
            publications=(old, new),
            authors={
                "ada-lovelace": SiteAuthorPage(
                    author=author,
                    publication_ids=("new-id", "old-id"),
                )
            },
            years={
                "2024": SiteYearPage(year="2024", publication_ids=("old-id",)),
                "2025": SiteYearPage(year="2025", publication_ids=("new-id",)),
            },
        )

    def artifacts(self, **kwargs) -> dict[str, str]:
        rendered = render_jekyll_index_pages(self.model(), **kwargs)
        self.assertTrue(all(isinstance(item, RenderedArtifact) for item in rendered))
        return {item.path: item.content for item in rendered}

    def test_renders_author_and_year_pages_deterministically(self) -> None:
        rendered = render_jekyll_index_pages(self.model())
        self.assertEqual(
            [item.path for item in rendered],
            [
                "authors/ada-lovelace.md",
                "authors/index.md",
                "years/2024.md",
                "years/2025.md",
                "years/index.md",
            ],
        )
        artifacts = {item.path: item.content for item in rendered}
        self.assertEqual(
            artifacts["years/2024.md"],
            """---
title: Published in 2024
permalink: /years/2024
---

<h3 id="number-posts">There are ... items referenced.</h3>
<ul class="post-list">
<li><span class='post-meta'>2024 -- A. Lovelace</span><h3><a class='post-link' href="{{ site.baseurl }}/old-work">Old &amp; \\( x \\) work</a></h3></li>

</ul>
{% include count-posts.html %}
""",
        )
        self.assertIn(
            "Alternative author names: Ada Lovelace, A. Lovelace.",
            artifacts["authors/ada-lovelace.md"],
        )
        self.assertIn(
            "2025 -- Ada Lovelace",
            artifacts["authors/ada-lovelace.md"],
        )
        self.assertLess(
            artifacts["authors/ada-lovelace.md"].index("new-work"),
            artifacts["authors/ada-lovelace.md"].index("old-work"),
        )

    def test_same_date_tie_break_uses_rendered_row(self) -> None:
        model = self.model()
        first = model.publications[0]
        second = SitePublication(
            id="same-date-id",
            permalink="z-work",
            created_date=first.created_date,
            year=first.year,
            type=first.type,
            title="A title that would sort first by title",
            authors=first.authors,
            abstract="",
            container_title=first.container_title,
            volume="",
            issue="",
            pages="",
            publisher="",
            event="",
            keywords=(),
        )
        tied = SiteModel(
            publications=(first, second),
            authors={
                "ada-lovelace": SiteAuthorPage(
                    author=model.authors["ada-lovelace"].author,
                    publication_ids=("old-id", "same-date-id"),
                )
            },
            years={
                "2024": SiteYearPage(
                    year="2024",
                    publication_ids=("old-id", "same-date-id"),
                )
            },
        )
        artifacts = {
            item.path: item.content for item in render_jekyll_index_pages(tied)
        }
        content = artifacts["years/2024.md"]
        self.assertLess(content.index("z-work"), content.index("old-work"))

    def test_author_index_accepts_project_editorial_html(self) -> None:
        default = self.artifacts()["authors/index.md"]
        self.assertNotIn("Project-specific note", default)

        options = JekyllIndexRenderOptions(
            author_index_extra_html="<p>Project-specific note.</p>\n<hr />\n"
        )
        customized = self.artifacts(options=options)["authors/index.md"]
        self.assertIn("<h3>There are 1 authors referenced.</h3>", customized)
        self.assertIn("<p>Project-specific note.</p>\n<hr />", customized)
        self.assertIn(
            "<a href='{{ site.baseurl }}/authors/ada-lovelace'>Ada Lovelace</a>",
            customized,
        )

    def test_html_escaping_is_presentation_only(self) -> None:
        artifacts = self.artifacts()
        self.assertIn("New &lt;work&gt;", artifacts["years/2025.md"])
        self.assertEqual(self.model().publications[1].title, "New <work>")

    def test_authorless_year_publications_are_project_configurable(self) -> None:
        model = self.model()
        anonymous = SitePublication(
            id="anonymous-id",
            permalink="anonymous-work",
            created_date=date(2025, 2, 1),
            year="2025",
            type="journal-article",
            title="Anonymous work",
            authors=(),
            abstract="",
            container_title="Journal",
            volume="",
            issue="",
            pages="",
            publisher="",
            event="",
            keywords=(),
        )
        extended = SiteModel(
            publications=model.publications + (anonymous,),
            authors=model.authors,
            years={
                "2024": model.years["2024"],
                "2025": SiteYearPage(
                    year="2025",
                    publication_ids=("anonymous-id", "new-id"),
                ),
            },
        )
        default = {
            item.path: item.content for item in render_jekyll_index_pages(extended)
        }
        self.assertIn("anonymous-work", default["years/2025.md"])

        filtered = {
            item.path: item.content
            for item in render_jekyll_index_pages(
                extended,
                options=JekyllIndexRenderOptions(
                    include_authorless_year_publications=False,
                ),
            )
        }
        self.assertNotIn("anonymous-work", filtered["years/2025.md"])
        self.assertIn("new-work", filtered["years/2025.md"])

    def test_missing_publication_reference_is_rejected(self) -> None:
        model = self.model()
        bad = SiteModel(
            publications=model.publications,
            authors={
                "ada-lovelace": SiteAuthorPage(
                    author=model.authors["ada-lovelace"].author,
                    publication_ids=("missing-id",),
                )
            },
            years=model.years,
        )
        with self.assertRaisesRegex(SiteRenderError, "missing publication id"):
            render_jekyll_index_pages(bad)

    def test_reserved_author_index_slug_is_rejected(self) -> None:
        publication = self.model().publications[0]
        model = SiteModel(
            publications=(publication,),
            authors={
                "index": SiteAuthorPage(
                    author=SiteAuthor(
                        slug="index",
                        name="Index Person",
                        variants=("Index Person",),
                    ),
                    publication_ids=("old-id",),
                )
            },
            years={"2024": SiteYearPage(year="2024", publication_ids=("old-id",))},
        )
        with self.assertRaisesRegex(SiteRenderError, "conflicts with authors/index"):
            render_jekyll_index_pages(model)


if __name__ == "__main__":
    unittest.main()
