import tempfile
import unittest
from pathlib import Path

from bibreview.identifier_state import (
    CANONICAL_ID_KIND,
    DOI_KIND,
    IdentifierStateError,
    IdentifierToken,
    identifier_tokens_bytes,
    parse_identifier_token,
    read_identifier_tokens,
)
from bibreview.identity import STRONG_IDENTIFIER_NAMES


class IdentifierStateTests(unittest.TestCase):
    def test_typed_doi_is_canonicalized(self):
        token = parse_identifier_token(
            " DOI: HTTPS://DOI.ORG/10.1234/ABC.Def "
        )
        self.assertEqual(token, IdentifierToken("doi", "10.1234/abc.def"))
        self.assertEqual(str(token), "doi:10.1234/abc.def")

    def test_reserved_id_token_validates_and_canonicalizes_uuid(self):
        token = parse_identifier_token(
            "ID:550E8400-E29B-41D4-A716-446655440000"
        )
        self.assertEqual(token.kind, CANONICAL_ID_KIND)
        self.assertEqual(
            token.value,
            "550e8400-e29b-41d4-a716-446655440000",
        )

    def test_generic_value_may_contain_colons(self):
        token = parse_identifier_token("PMLR:331:example-id")
        self.assertEqual(token.kind, "pmlr")
        self.assertEqual(token.value, "331:example-id")
        self.assertEqual(str(token), "pmlr:331:example-id")

    def test_generic_kind_and_value_are_trimmed(self):
        token = parse_identifier_token(" PMID : 12345678 ")
        self.assertEqual(token, IdentifierToken("pmid", "12345678"))

    def test_rejects_untyped_value_by_default(self):
        with self.assertRaisesRegex(
            IdentifierStateError,
            "must use '<kind>:<value>' syntax",
        ):
            parse_identifier_token("10.1234/example")

    def test_accepts_legacy_bare_doi_when_enabled(self):
        token = parse_identifier_token(
            "10.1234/ABC",
            allow_legacy_doi=True,
        )
        self.assertEqual(token, IdentifierToken(DOI_KIND, "10.1234/abc"))

    def test_accepts_legacy_doi_url_when_enabled(self):
        token = parse_identifier_token(
            "https://doi.org/10.1234/ABC",
            allow_legacy_doi=True,
        )
        self.assertEqual(token, IdentifierToken(DOI_KIND, "10.1234/abc"))

    def test_never_guesses_untyped_non_doi(self):
        with self.assertRaisesRegex(
            IdentifierStateError,
            "must use '<kind>:<value>' syntax",
        ):
            parse_identifier_token(
                "9781234567890",
                allow_legacy_doi=True,
            )

    def test_context_can_restrict_allowed_kinds(self):
        token = parse_identifier_token(
            "doi:10.1234/example",
            allowed_kinds=STRONG_IDENTIFIER_NAMES,
        )
        self.assertEqual(token.kind, DOI_KIND)

        with self.assertRaisesRegex(
            IdentifierStateError,
            "identifier kind 'id' is not allowed; expected: doi",
        ):
            parse_identifier_token(
                "id:550e8400-e29b-41d4-a716-446655440000",
                allowed_kinds=STRONG_IDENTIFIER_NAMES,
            )

    def test_context_allowed_kinds_are_case_insensitive(self):
        token = parse_identifier_token(
            "DOI:10.1234/example",
            allowed_kinds={"DOI"},
        )
        self.assertEqual(token.kind, DOI_KIND)

    def test_rejects_empty_kind_and_value(self):
        with self.assertRaises(IdentifierStateError):
            parse_identifier_token(":value")
        with self.assertRaises(IdentifierStateError):
            parse_identifier_token("doi:")

    def test_rejects_invalid_kind_syntax(self):
        with self.assertRaisesRegex(
            IdentifierStateError,
            "identifier kind must start with a letter",
        ):
            parse_identifier_token("bad kind:value")

    def test_rejects_invalid_reserved_uuid(self):
        with self.assertRaisesRegex(
            IdentifierStateError,
            "invalid publication id",
        ):
            parse_identifier_token("id:not-a-uuid")

    def test_reader_ignores_comments_blanks_and_deduplicates(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ID.txt"
            path.write_text(
                "# registry\n"
                "\n"
                "DOI:10.1234/ABC\n"
                "doi:10.1234/abc\n"
                "id:550e8400-e29b-41d4-a716-446655440000\n",
                encoding="utf-8",
            )

            tokens = read_identifier_tokens(path)

        self.assertEqual(
            tokens,
            (
                IdentifierToken("doi", "10.1234/abc"),
                IdentifierToken(
                    "id",
                    "550e8400-e29b-41d4-a716-446655440000",
                ),
            ),
        )

    def test_reader_reports_path_and_line_number(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "newID.txt"
            path.write_text(
                "doi:10.1234/valid\n"
                "isbn:9781234567890\n",
                encoding="utf-8",
            )

            with self.assertRaises(IdentifierStateError) as caught:
                read_identifier_tokens(
                    path,
                    allowed_kinds=STRONG_IDENTIFIER_NAMES,
                )

        message = str(caught.exception)
        self.assertIn(str(path), message)
        self.assertIn("line 2", message)
        self.assertIn("identifier kind 'isbn' is not allowed", message)

    def test_missing_state_file_is_empty(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "missing.txt"
            self.assertEqual(read_identifier_tokens(path), ())

    def test_serializer_emits_canonical_one_token_per_line(self):
        content = identifier_tokens_bytes(
            (
                IdentifierToken("DOI", "10.1234/ABC"),
                IdentifierToken(
                    "ID",
                    "550E8400-E29B-41D4-A716-446655440000",
                ),
                IdentifierToken("PMLR", "331:example-id"),
            )
        )
        self.assertEqual(
            content,
            (
                b"doi:10.1234/abc\n"
                b"id:550e8400-e29b-41d4-a716-446655440000\n"
                b"pmlr:331:example-id\n"
            ),
        )

    def test_serializer_rejects_non_token_values(self):
        with self.assertRaisesRegex(
            IdentifierStateError,
            "must contain IdentifierToken objects",
        ):
            identifier_tokens_bytes(("doi:10.1234/example",))


if __name__ == "__main__":
    unittest.main()
