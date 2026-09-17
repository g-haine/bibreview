import unittest

from bibreview.identity import new_publication_id
from bibreview.model import Author, Publication
from bibreview.pipeline.authors import (
    AuthorMappingError,
    apply_safe_author_mappings,
    author_mapping_plan_data,
    author_name,
    format_author_mapping_plan,
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


if __name__ == "__main__":
    unittest.main()
