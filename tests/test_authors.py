import unittest

from bibreview.identity import new_publication_id
from bibreview.model import Author, Publication
from bibreview.pipeline.authors import (
    AuthorMappingError,
    apply_safe_author_mappings,
    assign_author_mapping,
    author_mapping_plan_data,
    author_name,
    author_review_cases,
    format_author_mapping_plan,
    format_author_review_case,
    plan_author_mappings,
    validate_author_mappings,
)


class AuthorMappingTests(unittest.TestCase):
    def publication(self, *authors: Author) -> Publication:
        return Publication(id=new_publication_id(), authors=authors)

    def test_author_name_prefers_literal_and_never_invents_null_given_name(self):
        self.assertEqual(author_name(Author(literal="Collective Author")), "Collective Author")
        self.assertEqual(author_name(Author(given="Ada", family="Lovelace")), "Ada Lovelace")
        self.assertEqual(author_name(Author(family="Jun Qiu")), "Jun Qiu")

    def test_mapping_validation_rejects_name_owned_by_two_slugs(self):
        with self.assertRaisesRegex(AuthorMappingError, "ambiguous author name"):
            validate_author_mappings({"ada": ["Ada Lovelace"], "other": ["Ada Lovelace"]})

    def test_plan_separates_safe_and_ambiguous_proposals(self):
        publications = (
            self.publication(Author(given="Ada", family="Lovelace")),
            self.publication(Author(given="A.", family="Lovelace")),
            self.publication(Author(given="Grace", family="Hopper")),
            self.publication(Author(given="Grace", family="Hopper")),
            self.publication(Author(given="Éva", family="Test")),
            self.publication(Author(given="Eva", family="Test")),
        )
        plan = plan_author_mappings(
            publications,
            {"ada-lovelace": ["Ada Lovelace"]},
        )

        self.assertEqual(plan.known_names, 1)
        self.assertEqual(plan.unknown_names, 4)
        self.assertEqual(dict(plan.safe), {"grace-hopper": ("Grace Hopper",)})
        self.assertEqual(len(plan.review), 2)

        lovelace = next(item for item in plan.review if item.slug == "a-lovelace")
        self.assertIn("a known author has the same surname and first initial", lovelace.reasons)
        self.assertEqual(dict(lovelace.possible_matches), {"ada-lovelace": ("Ada Lovelace",)})

        collision = next(item for item in plan.review if item.slug == "eva-test")
        self.assertEqual(set(collision.names), {"Eva Test", "Éva Test"})
        self.assertIn("several unknown names produce the same slug", collision.reasons)

    def test_apply_safe_adds_only_safe_proposals(self):
        publications = (
            self.publication(Author(given="Ada", family="Lovelace")),
            self.publication(Author(given="A.", family="Lovelace")),
            self.publication(Author(given="Grace", family="Hopper")),
        )
        mapping = {"ada-lovelace": ["Ada Lovelace"]}
        plan = plan_author_mappings(publications, mapping)
        updated = apply_safe_author_mappings(mapping, plan)

        self.assertEqual(
            updated,
            {
                "ada-lovelace": ["Ada Lovelace"],
                "grace-hopper": ["Grace Hopper"],
            },
        )
        after = plan_author_mappings(publications, updated)
        self.assertEqual(after.unknown_names, 1)
        self.assertFalse(after.safe)
        self.assertEqual(after.review[0].slug, "a-lovelace")

    def test_json_and_human_reports_preserve_review_information(self):
        publications = (self.publication(Author(given="Grace", family="Hopper")),)
        plan = plan_author_mappings(publications, {})
        data = author_mapping_plan_data(plan)
        self.assertEqual(data["safe"], {"grace-hopper": ["Grace Hopper"]})
        report = format_author_mapping_plan(plan, applied=1, dry_run=True)
        self.assertIn("Would apply 1 safe author mapping(s).", report)
        self.assertIn("grace-hopper: Grace Hopper", report)


    def test_review_case_exposes_publication_orcid_and_affiliation_evidence(self):
        publication = Publication(
            id=new_publication_id(),
            identifiers={"doi": "10.1/example"},
            title="Example FSI paper",
            authors=(
                Author(
                    given="Y.",
                    family="Hong",
                    source_fields={
                        "ORCID": "https://orcid.org/0000-0001-2345-6789",
                        "affiliation": [{"name": "Example University"}],
                    },
                ),
            ),
        )
        cases = author_review_cases(
            (publication,),
            {"yin-hong": ["Yin Hong"]},
        )
        self.assertEqual(len(cases), 1)
        case = cases[0]
        self.assertEqual(case.name, "Y. Hong")
        self.assertEqual(case.slug, "y-hong")
        self.assertEqual(tuple(case.possible_matches), ("yin-hong",))
        self.assertEqual(case.occurrences[0].doi, "10.1/example")
        self.assertEqual(
            case.occurrences[0].orcid,
            "https://orcid.org/0000-0001-2345-6789",
        )
        self.assertEqual(case.occurrences[0].affiliations, ("Example University",))
        report = format_author_review_case(case, index=1, total=1)
        self.assertIn("Example FSI paper", report)
        self.assertIn("Example University", report)

    def test_explicit_manual_assignment_can_merge_or_create_but_rejects_conflicts(self):
        mapping = {"yin-hong": ["Yin Hong"]}

        merged = assign_author_mapping(
            mapping,
            name="Y. Hong",
            slug="yin-hong",
            create_new=False,
        )
        self.assertEqual(merged["yin-hong"], ["Yin Hong", "Y. Hong"])

        created = assign_author_mapping(
            mapping,
            name="Y. Hong",
            slug="yi-hong",
            create_new=True,
        )
        self.assertEqual(created["yi-hong"], ["Y. Hong"])

        with self.assertRaisesRegex(AuthorMappingError, "already exists"):
            assign_author_mapping(
                mapping,
                name="Y. Hong",
                slug="yin-hong",
                create_new=True,
            )
        with self.assertRaisesRegex(AuthorMappingError, "unknown author identity"):
            assign_author_mapping(
                mapping,
                name="Y. Hong",
                slug="missing-author",
                create_new=False,
            )
        with self.assertRaisesRegex(AuthorMappingError, "already assigned"):
            assign_author_mapping(
                mapping,
                name="Yin Hong",
                slug="other-hong",
                create_new=True,
            )


if __name__ == "__main__":
    unittest.main()
