from __future__ import annotations

from datetime import date
import unittest

from bibreview.model import Author, Editor, Publication, Reference
from bibreview.site.transform import SiteTransformError, build_site_model, site_model_data


class SiteTransformTests(unittest.TestCase):
    def publication(
        self,
        *,
        identifier: str,
        doi: str | None,
        permalink: str,
        created: date | None,
        year: str,
        title: str,
        authors: tuple[Author, ...] = (),
        editors: tuple[Editor, ...] | None = None,
        references: tuple[Reference, ...] = (),
    ) -> Publication:
        identifiers = {"doi": doi} if doi is not None else {}
        if editors is None:
            editors = () if authors else (Editor(literal="Example Editor"),)
        return Publication(
            id=identifier,
            identifiers=identifiers,
            type="journal-article",
            title=title,
            authors=authors,
            editors=editors,
            abstract=f"Abstract for {title}",
            container_title="Journal",
            publication_year=year,
            volume="1",
            issue="2",
            pages="3--4",
            publisher="Publisher",
            keywords=("fluid-structure",),
            created_date=created,
            permalink=permalink,
            references=references,
        )

    def test_builds_author_year_and_internal_reference_indexes(self) -> None:
        old = self.publication(
            identifier="00000000-0000-4000-8000-000000000001",
            doi="10.1000/old",
            permalink="old-work",
            created=date(2024, 1, 2),
            year="2024",
            title="Old work",
            authors=(Author(given="Ada", family="Lovelace"),),
            references=(Reference(identifiers={"doi": "10.1000/new"}, citation="New work"),),
        )
        new = self.publication(
            identifier="00000000-0000-4000-8000-000000000002",
            doi="10.1000/new",
            permalink="new-work",
            created=date(2025, 6, 1),
            year="2025",
            title="New work",
            authors=(Author(literal="A. Lovelace"),),
        )
        editor_only = self.publication(
            identifier="00000000-0000-4000-8000-000000000003",
            doi=None,
            permalink="editorial-overview",
            created=date(2025, 7, 1),
            year="2025",
            title="Editorial overview",
        )

        model = build_site_model(
            (old, new, editor_only),
            {
                "ada-lovelace": ["Ada Lovelace", "A. Lovelace"],
                "unused-person": ["Unused Person"],
            },
        )

        self.assertEqual([item.id for item in model.publications], [old.id, new.id, editor_only.id])
        self.assertEqual(
            [editor.name for editor in model.publications[2].editors],
            ["Example Editor"],
        )
        self.assertEqual(model.publications[0].references[0].permalink, "new-work")
        self.assertEqual(tuple(model.authors), ("ada-lovelace",))
        self.assertEqual(model.authors["ada-lovelace"].author.name, "Ada Lovelace")
        self.assertEqual(
            model.authors["ada-lovelace"].author.variants,
            ("Ada Lovelace", "A. Lovelace"),
        )
        self.assertEqual(
            model.authors["ada-lovelace"].publication_ids,
            (new.id, old.id),
        )
        self.assertEqual(tuple(model.years), ("2024", "2025"))
        self.assertEqual(model.years["2025"].publication_ids, (editor_only.id, new.id))

        payload = site_model_data(model)
        self.assertEqual(payload["publications"][0]["references"][0]["permalink"], "new-work")
        self.assertEqual(payload["authors"]["ada-lovelace"]["publication_ids"], [new.id, old.id])
        self.assertEqual(
            payload["publications"][2]["editors"],
            [{"name": "Example Editor"}],
        )

    def test_rejects_unmapped_publication_author(self) -> None:
        publication = self.publication(
            identifier="00000000-0000-4000-8000-000000000010",
            doi="10.1000/unknown-author",
            permalink="unknown-author",
            created=date(2025, 1, 1),
            year="2025",
            title="Unknown author",
            authors=(Author(given="Grace", family="Hopper"),),
        )
        with self.assertRaisesRegex(SiteTransformError, "unmapped author 'Grace Hopper'"):
            build_site_model((publication,), {})

    def test_rejects_duplicate_or_unsafe_permalinks(self) -> None:
        first = self.publication(
            identifier="00000000-0000-4000-8000-000000000020",
            doi="10.1000/first",
            permalink="same",
            created=date(2025, 1, 1),
            year="2025",
            title="First",
        )
        second = self.publication(
            identifier="00000000-0000-4000-8000-000000000021",
            doi="10.1000/second",
            permalink="same",
            created=date(2025, 1, 2),
            year="2025",
            title="Second",
        )
        with self.assertRaisesRegex(SiteTransformError, "duplicate publication permalink"):
            build_site_model((first, second), {})

        unsafe = self.publication(
            identifier="00000000-0000-4000-8000-000000000022",
            doi="10.1000/unsafe",
            permalink="../unsafe",
            created=date(2025, 1, 3),
            year="2025",
            title="Unsafe",
        )
        with self.assertRaisesRegex(SiteTransformError, "unsafe or reserved file name"):
            build_site_model((unsafe,), {})

    def test_requires_created_date_and_decimal_publication_year(self) -> None:
        missing_date = self.publication(
            identifier="00000000-0000-4000-8000-000000000030",
            doi="10.1000/missing-date",
            permalink="missing-date",
            created=None,
            year="2025",
            title="Missing date",
        )
        with self.assertRaisesRegex(SiteTransformError, "requires created_date"):
            build_site_model((missing_date,), {})

        bad_year = self.publication(
            identifier="00000000-0000-4000-8000-000000000031",
            doi="10.1000/bad-year",
            permalink="bad-year",
            created=date(2025, 1, 1),
            year="forthcoming",
            title="Bad year",
        )
        with self.assertRaisesRegex(SiteTransformError, "invalid publication year"):
            build_site_model((bad_year,), {})

    def test_external_reference_remains_unresolved(self) -> None:
        publication = self.publication(
            identifier="00000000-0000-4000-8000-000000000040",
            doi="10.1000/source",
            permalink="source",
            created=date(2025, 1, 1),
            year="2025",
            title="Source",
            references=(Reference(identifiers={"doi": "10.9999/external"}, citation="External"),),
        )
        model = build_site_model((publication,), {})
        self.assertEqual(model.publications[0].references[0].doi, "10.9999/external")
        self.assertIsNone(model.publications[0].references[0].permalink)


if __name__ == "__main__":
    unittest.main()
