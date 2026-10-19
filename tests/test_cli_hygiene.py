from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

from bibreview.cli import main
from bibreview.config import load_config
from bibreview.hygiene_resolution import load_project_hygiene_resolutions
from bibreview.identity import new_publication_id
from bibreview.model import Author, Publication, Reference
from bibreview.project_hygiene import project_hygiene_migration_review
from bibreview.storage import read_bibliography, read_json, write_bibliography


CONFIG = """\
schema_version: 1
project:
  name: Example Review
  slug: example-review
site:
  enabled: false
"""


class HygieneCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config_path = self.root / "bibreview.yml"
        self.config_path.write_text(CONFIG, encoding="utf-8")
        self.config = load_config(self.config_path)
        self.config.paths.bibliography.parent.mkdir(parents=True, exist_ok=True)

        write_bibliography(
            self.config.paths.bibliography,
            (
                Publication(
                    id=new_publication_id(),
                    identifiers={"doi": "10.1/clean"},
                    title="Clean",
                    authors=(Author(literal="Ada Lovelace"),),
                    abstract=r"Clean \(V\) abstract.",
                ),
                Publication(
                    id=new_publication_id(),
                    identifiers={"doi": "10.1/markup"},
                    title="Markup",
                    authors=(Author(literal="Alan Turing"),),
                    abstract=(
                        '<p>Text <inline-formula>'
                        '<mml:math xmlns:mml="urn:test">'
                        '<mml:annotation encoding="application/x-tex">V</mml:annotation>'
                        '</mml:math></inline-formula>.</p>'
                    ),
                ),
            ),
        )

    def snapshot(self) -> dict[str, bytes]:
        return {
            str(path.relative_to(self.root)): path.read_bytes()
            for path in self.root.rglob("*")
            if path.is_file()
        }

    def run_cli(self, *extra: str) -> tuple[int, str, str]:
        stdout = StringIO()
        stderr = StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(["--config", str(self.config_path), *extra])
        return code, stdout.getvalue(), stderr.getvalue()

    def test_hygiene_is_read_only_and_summary_only_by_default(self) -> None:
        before = self.snapshot()
        code, stdout, stderr = self.run_cli("hygiene")

        self.assertEqual(code, 0, stderr)
        self.assertIn("Publications scanned     : 2", stdout)
        self.assertIn("Suspicious abstracts     : 1", stdout)
        self.assertNotIn("10.1/markup", stdout)
        self.assertEqual(before, self.snapshot())

    def test_verbose_hygiene_lists_findings_with_short_context(self) -> None:
        code, stdout, stderr = self.run_cli("-v", "hygiene")

        self.assertEqual(code, 0, stderr)
        self.assertIn("10.1/markup: Markup", stdout)
        self.assertIn("embedded-tex-annotation", stdout)
        self.assertIn("Context:", stdout)

    def test_hygiene_json_is_complete(self) -> None:
        code, stdout, stderr = self.run_cli("hygiene", "--json")

        self.assertEqual(code, 0, stderr)
        payload = json.loads(stdout)
        self.assertEqual(payload["scanned_publications"], 2)
        self.assertEqual(payload["suspicious_abstracts"], 1)
        self.assertEqual(payload["findings"][0]["doi"], "10.1/markup")
        self.assertEqual(
            payload["findings"][0]["normalization_hint"],
            "embedded-tex-annotation",
        )

    def test_title_reference_inventory_is_read_only(self) -> None:
        item = Publication(
            id=new_publication_id(),
            identifiers={"doi": "10.1/title"},
            title="A Port-<scp>H</scp>amiltonian approach",
            authors=(Author(literal="Ada Lovelace"),),
            references=(
                Reference(citation="Learning for Dynamics &amp; Control"),
            ),
        )
        write_bibliography(self.config.paths.bibliography, (item,))
        before = self.snapshot()

        code, stdout, stderr = self.run_cli("hygiene", "--titles")

        self.assertEqual(code, 0, stderr)
        self.assertIn("Publications scanned          : 1", stdout)
        self.assertIn("Titles with hygiene signals   : 1", stdout)
        self.assertIn("Citations with hygiene signals: 1", stdout)
        self.assertNotIn("10.1/title", stdout)
        self.assertEqual(before, self.snapshot())

    def test_verbose_title_reference_inventory_lists_stable_reference_identity(self) -> None:
        item = Publication(
            id=new_publication_id(),
            identifiers={"doi": "10.1/title"},
            title="<scp>H</scp> systems",
            authors=(Author(literal="Ada Lovelace"),),
            references=(
                Reference(
                    identifiers={"doi": "10.2/reference"},
                    citation="A &amp; B",
                ),
            ),
        )
        write_bibliography(self.config.paths.bibliography, (item,))

        code, stdout, stderr = self.run_cli("-v", "hygiene", "--titles")

        self.assertEqual(code, 0, stderr)
        self.assertIn("Title findings:", stdout)
        self.assertIn("10.1/title", stdout)
        self.assertIn("Reference: doi:10.2/reference", stdout)
        self.assertIn("html-entity", stdout)

    def test_title_reference_inventory_json_is_complete(self) -> None:
        item = Publication(
            id=new_publication_id(),
            identifiers={"doi": "10.1/title"},
            title="<scp>H</scp> systems",
            authors=(Author(literal="Ada Lovelace"),),
            references=(Reference(citation="A &amp; B"),),
        )
        write_bibliography(self.config.paths.bibliography, (item,))

        code, stdout, stderr = self.run_cli("hygiene", "--titles", "--json")

        self.assertEqual(code, 0, stderr)
        payload = json.loads(stdout)
        self.assertEqual(payload["titles_with_hygiene_signals"], 1)
        self.assertEqual(payload["citations_with_hygiene_signals"], 1)
        self.assertEqual(
            payload["title_findings"][0]["families"],
            ["small-caps-markup", "html-xml-markup"],
        )
        self.assertTrue(
            payload["citation_findings"][0]["reference_key"].startswith(
                "sha256:"
            )
        )

    def test_title_migration_review_is_distinct_from_inventory(self) -> None:
        item = Publication(
            id=new_publication_id(),
            identifiers={"doi": "10.1/title-migration"},
            title="A Port‐<scp>H</scp>amiltonian Approach",
            authors=(Author(literal="Ada Lovelace"),),
            abstract="Clean.",
            permalink="stable-title-url",
        )
        write_bibliography(self.config.paths.bibliography, (item,))
        before = self.snapshot()

        code, stdout, stderr = self.run_cli(
            "hygiene",
            "--titles",
            "--review",
        )

        self.assertEqual(code, 0, stderr)
        self.assertIn("Canonical title hygiene migration review", stdout)
        self.assertIn("Deterministic proposals  : 1", stdout)
        self.assertNotIn("Citations with hygiene signals", stdout)
        self.assertEqual(before, self.snapshot())

    def test_title_resolve_then_apply_preserves_permalink(self) -> None:
        item = Publication(
            id=new_publication_id(),
            identifiers={"doi": "10.1/title-migration"},
            title="A Port‐<scp>H</scp>amiltonian Approach",
            authors=(Author(literal="Ada Lovelace"),),
            abstract="Clean.",
            permalink="stable-title-url",
        )
        write_bibliography(self.config.paths.bibliography, (item,))
        canonical_before = self.config.paths.bibliography.read_bytes()

        with patch("builtins.input", side_effect=[""]):
            code, stdout, stderr = self.run_cli(
                "hygiene",
                "--titles",
                "--resolve",
            )

        self.assertEqual(code, 0, stderr)
        self.assertIn("Canonical title hygiene resolution", stdout)
        self.assertIn("Accepted   : 1", stdout)

        code, stdout, stderr = self.run_cli(
            "hygiene",
            "--titles",
            "--apply",
        )

        self.assertEqual(code, 0, stderr)
        self.assertIn("Canonical title hygiene application", stdout)
        self.assertIn("Changes to stage      : 1", stdout)
        self.assertEqual(
            self.config.paths.bibliography.read_bytes(),
            canonical_before,
        )
        staged = read_bibliography(self.config.paths.collected)
        self.assertEqual(len(staged), 1)
        self.assertEqual(staged[0].title, "A Port‐Hamiltonian Approach")
        self.assertEqual(staged[0].permalink, "stable-title-url")

    def test_reference_citation_review_reports_safe_and_human_cases(self) -> None:
        item = Publication(
            id=new_publication_id(),
            identifiers={"doi": "10.1/citation-review"},
            title="Citation review",
            authors=(Author(literal="Ada Lovelace"),),
            references=(
                Reference(citation="A &amp; B"),
                Reference(citation="H<sub>2</sub> control"),
                Reference(citation=r"Already \(H_2\)"),
            ),
        )
        write_bibliography(self.config.paths.bibliography, (item,))
        before = self.snapshot()

        code, stdout, stderr = self.run_cli(
            "hygiene",
            "--citations",
            "--review",
        )

        self.assertEqual(code, 0, stderr)
        self.assertIn(
            "Canonical reference-citation hygiene migration review",
            stdout,
        )
        self.assertIn("Citations with signals   : 3", stdout)
        self.assertIn("Preserved/no change      : 1", stdout)
        self.assertIn("Deterministic proposals  : 1", stdout)
        self.assertIn("Review required          : 1", stdout)
        self.assertEqual(before, self.snapshot())

    def test_reference_citation_apply_safe_stages_only_deterministic_changes(self) -> None:
        item = Publication(
            id=new_publication_id(),
            identifiers={"doi": "10.1/citation-safe"},
            title="Citation safe",
            authors=(Author(literal="Ada Lovelace"),),
            references=(
                Reference(citation="A &amp; B"),
                Reference(citation="H<sub>2</sub> control"),
            ),
        )
        write_bibliography(self.config.paths.bibliography, (item,))
        canonical_before = self.config.paths.bibliography.read_bytes()

        code, stdout, stderr = self.run_cli(
            "hygiene",
            "--citations",
            "--apply-safe",
        )

        self.assertEqual(code, 0, stderr)
        self.assertIn("Deterministic proposals: 1", stdout)
        self.assertIn("Review required        : 1", stdout)
        self.assertIn("Changes to stage       : 1", stdout)
        self.assertEqual(
            self.config.paths.bibliography.read_bytes(),
            canonical_before,
        )
        staged = read_bibliography(self.config.paths.collected)
        self.assertEqual(staged[0].references[0].citation, "A & B")
        self.assertEqual(
            staged[0].references[1].citation,
            "H<sub>2</sub> control",
        )

    def test_reference_citation_resolve_then_apply_custom_value(self) -> None:
        item = Publication(
            id=new_publication_id(),
            identifiers={"doi": "10.1/citation-custom"},
            title="Citation custom",
            authors=(Author(literal="Ada Lovelace"),),
            references=(
                Reference(
                    identifiers={"doi": "10.2/reference"},
                    citation="H<sub>2</sub> control",
                ),
            ),
        )
        write_bibliography(self.config.paths.bibliography, (item,))

        with patch(
            "builtins.input",
            side_effect=[r"f \(H_2\) control"],
        ):
            code, stdout, stderr = self.run_cli(
                "hygiene",
                "--citations",
                "--resolve",
            )

        self.assertEqual(code, 0, stderr)
        self.assertIn("Canonical reference-citation hygiene resolution", stdout)
        self.assertIn("Custom     : 1", stdout)

        review = project_hygiene_migration_review(
            self.config,
            field="reference-citation",
        )
        state = load_project_hygiene_resolutions(self.config, review)
        self.assertEqual(state.decisions[0].decision, "custom")
        self.assertTrue(
            state.decisions[0].key.startswith(
                f"{item.id}:reference-citation:doi:10.2/reference"
            )
        )

        code, stdout, stderr = self.run_cli(
            "hygiene",
            "--citations",
            "--apply",
        )
        self.assertEqual(code, 0, stderr)
        self.assertIn("Changes to stage      : 1", stdout)
        staged = read_bibliography(self.config.paths.collected)
        self.assertEqual(
            staged[0].references[0].citation,
            r"\(H_2\) control",
        )

    def test_hygiene_review_derives_proposals_without_writing_state(self) -> None:
        before = self.snapshot()

        code, stdout, stderr = self.run_cli("hygiene", "--review")

        self.assertEqual(code, 0, stderr)
        self.assertIn("Suspicious abstracts     : 1", stdout)
        self.assertIn("Deterministic proposals  : 1", stdout)
        self.assertIn("Review required          : 0", stdout)
        self.assertEqual(before, self.snapshot())

    def test_resolve_then_apply_stages_accepted_normalization(self) -> None:
        canonical_before = self.config.paths.bibliography.read_bytes()

        with patch("builtins.input", side_effect=[""]):
            code, stdout, stderr = self.run_cli("hygiene", "--resolve")

        self.assertEqual(code, 0, stderr)
        self.assertIn("Accepted   : 1", stdout)

        code, stdout, stderr = self.run_cli("hygiene", "--apply")

        self.assertEqual(code, 0, stderr)
        self.assertIn("Changes to stage      : 1", stdout)
        self.assertEqual(
            self.config.paths.bibliography.read_bytes(),
            canonical_before,
        )
        staged = read_bibliography(self.config.paths.collected)
        self.assertEqual(len(staged), 1)
        self.assertEqual(staged[0].doi, "10.1/markup")
        self.assertEqual(staged[0].abstract, r"Text \(V\).")

    def test_review_required_resolver_requires_custom_or_reject(self) -> None:
        unsafe = Publication(
            id=new_publication_id(),
            identifiers={"doi": "10.1/unsafe"},
            title="Unsafe",
            authors=(Author(literal="Grace Hopper"),),
            abstract=(
                'A controller <jats:inline-graphic '
                'xlink:href="graphic/math-0002.png"/> is proposed.'
            ),
        )
        write_bibliography(
            self.config.paths.bibliography,
            (unsafe,),
        )

        with patch(
            "builtins.input",
            side_effect=["", "f Reviewed safe abstract"],
        ):
            code, stdout, stderr = self.run_cli("hygiene", "--resolve")

        self.assertEqual(code, 0, stderr)
        self.assertIn("REVIEW REQUIRED", stdout)
        self.assertIn("No safe automatic normalized abstract is available", stdout)

        review = project_hygiene_migration_review(self.config)
        state = load_project_hygiene_resolutions(self.config, review)
        self.assertEqual(state.decisions[0].decision, "custom")
        self.assertEqual(
            state.decisions[0].resolved_value,
            "Reviewed safe abstract",
        )

    def test_contributor_hygiene_review_resolve_and_apply(self) -> None:
        contaminated = Publication(
            id=new_publication_id(),
            identifiers={"doi": "10.1/contaminated"},
            title="Contaminated contributors",
            authors=(
                Author(given="Debayan", family="Maity"),
                Author(literal="TIFR Centre for Applicable Mathematics"),
                Author(given="Takéo", family="Takahashi"),
            ),
            permalink="contaminated",
        )
        write_bibliography(
            self.config.paths.bibliography,
            (contaminated,),
        )

        code, stdout, stderr = self.run_cli(
            "hygiene",
            "--authors",
            "--review",
        )
        self.assertEqual(code, 0, stderr)
        self.assertIn("Review candidates     : 1", stdout)
        self.assertIn("unresolved 1", stdout)

        with patch("builtins.input", side_effect=["r"]):
            code, stdout, stderr = self.run_cli(
                "hygiene",
                "--authors",
                "--resolve",
            )
        self.assertEqual(code, 0, stderr)
        self.assertIn("Recorded: remove", stdout)

        before = self.config.paths.bibliography.read_bytes()
        code, stdout, stderr = self.run_cli(
            "--dry-run",
            "hygiene",
            "--authors",
            "--apply",
        )
        self.assertEqual(code, 0, stderr)
        self.assertIn("Dry run:", stdout)
        self.assertFalse(self.config.paths.collected.exists())
        self.assertEqual(
            self.config.paths.bibliography.read_bytes(),
            before,
        )

        code, stdout, stderr = self.run_cli(
            "hygiene",
            "--authors",
            "--apply",
        )
        self.assertEqual(code, 0, stderr)
        staged = read_bibliography(self.config.paths.collected)
        self.assertEqual(len(staged), 1)
        self.assertEqual(
            [(author.given, author.family) for author in staged[0].authors],
            [("Debayan", "Maity"), ("Takéo", "Takahashi")],
        )

    def test_author_hygiene_inventory_and_explicit_rename(self) -> None:
        self.config.paths.author_mappings.write_text(
            """{
  "m-heck": ["M. Heck", "Marcus Heck"]
}
""",
            encoding="utf-8",
        )
        before = self.config.paths.author_mappings.read_bytes()

        code, stdout, stderr = self.run_cli("hygiene", "--authors")
        self.assertEqual(code, 0, stderr)
        self.assertIn("Slugs to strengthen           : 1", stdout)
        self.assertEqual(
            self.config.paths.author_mappings.read_bytes(),
            before,
        )

        code, stdout, stderr = self.run_cli(
            "--dry-run",
            "hygiene",
            "--authors",
            "--rename-author",
            "m-heck",
            "marcus-heck",
        )
        self.assertEqual(code, 0, stderr)
        self.assertIn("Dry run: Rename author identity", stdout)
        self.assertIn(
            "m-heck",
            read_json(self.config.paths.author_mappings, dict),
        )

        code, stdout, stderr = self.run_cli(
            "hygiene",
            "--authors",
            "--rename-author",
            "m-heck",
            "marcus-heck",
        )
        self.assertEqual(code, 0, stderr)
        mapping = read_json(self.config.paths.author_mappings, dict)
        self.assertNotIn("m-heck", mapping)
        self.assertIn("marcus-heck", mapping)

    def test_author_merge_requires_authors_scope(self) -> None:
        code, stdout, stderr = self.run_cli(
            "hygiene",
            "--merge-author",
            "source",
            "target",
        )
        self.assertEqual(code, 1)
        self.assertEqual(stdout, "")
        self.assertIn("require --authors", stderr)

    def test_bibtex_hygiene_reports_missing_canonical_author(self) -> None:
        item = Publication(
            id=new_publication_id(),
            identifiers={"doi": "10.1/bibtex"},
            title="BibTeX check",
            authors=(Author(literal="Ada Lovelace"),),
            permalink="bibtex-check",
        )
        write_bibliography(self.config.paths.bibliography, (item,))
        self.config.paths.bibtex.mkdir(parents=True, exist_ok=True)
        (self.config.paths.bibtex / "bibtex-check.bib").write_text(
            """@article{example,
  title={{BibTeX check}},
  DOI={10.1/bibtex}
}
""",
            encoding="utf-8",
        )

        code, stdout, stderr = self.run_cli("hygiene", "--bibtex")
        self.assertEqual(code, 0, stderr)
        self.assertIn("Missing author/editor  : 1", stdout)

        code, stdout, stderr = self.run_cli(
            "hygiene",
            "--bibtex",
            "--json",
        )
        self.assertEqual(code, 0, stderr)
        payload = json.loads(stdout)
        self.assertEqual(payload["missing_contributor_fields"], 1)
        self.assertEqual(payload["findings"][0]["field"], "author")


    def test_bibtex_review_and_apply_safe_repair_tracked_file_only(self) -> None:
        item = Publication(
            id=new_publication_id(),
            identifiers={"doi": "10.1/bibtex-safe"},
            title="BibTeX safe repair",
            authors=(Author(literal="Eman Alhayki"),),
            permalink="bibtex-safe-repair",
        )
        write_bibliography(self.config.paths.bibliography, (item,))
        self.config.paths.bibtex.mkdir(parents=True, exist_ok=True)
        bibtex_path = self.config.paths.bibtex / "bibtex-safe-repair.bib"
        bibtex_path.write_text(
            """@article{example,
  author={ and Alhayki Eman},
  title={{BibTeX safe repair}},
  year={2024}
}
""",
            encoding="utf-8",
        )
        canonical_before = self.config.paths.bibliography.read_bytes()

        code, stdout, stderr = self.run_cli(
            "hygiene",
            "--bibtex",
            "--review",
        )
        self.assertEqual(code, 0, stderr)
        self.assertIn("Deterministic repairs  : 1", stdout)
        self.assertIn("Review required        : 0", stdout)

        code, stdout, stderr = self.run_cli(
            "--dry-run",
            "hygiene",
            "--bibtex",
            "--apply-safe",
        )
        self.assertEqual(code, 0, stderr)
        self.assertIn("Dry run:", stdout)
        self.assertIn("Repairs applied : 1", stdout)
        self.assertIn(" and Alhayki Eman", bibtex_path.read_text(encoding="utf-8"))

        code, stdout, stderr = self.run_cli(
            "hygiene",
            "--bibtex",
            "--apply-safe",
        )
        self.assertEqual(code, 0, stderr)
        self.assertIn("author={Alhayki Eman}", bibtex_path.read_text(encoding="utf-8"))
        self.assertEqual(
            self.config.paths.bibliography.read_bytes(),
            canonical_before,
        )

    def test_bibtex_invalid_resolve_accepts_explicit_replacement_file(self) -> None:
        item = Publication(
            id=new_publication_id(),
            identifiers={"doi": "10.1/bibtex-invalid"},
            title="BibTeX invalid repair",
            authors=(Author(literal="Ada Lovelace"),),
            permalink="bibtex-invalid-repair",
        )
        write_bibliography(self.config.paths.bibliography, (item,))
        self.config.paths.bibtex.mkdir(parents=True, exist_ok=True)
        bibtex_path = self.config.paths.bibtex / "bibtex-invalid-repair.bib"
        bibtex_path.write_text(
            "@article{broken,\n  author={Ada Lovelace}\n",
            encoding="utf-8",
        )
        replacement = self.root / "replacement.bib"
        replacement.write_text(
            """@article{fixed,
  author={Ada Lovelace},
  title={{BibTeX invalid repair}},
  year={2024}
}
""",
            encoding="utf-8",
        )

        with patch("builtins.input", side_effect=[str(replacement)]):
            code, stdout, stderr = self.run_cli(
                "hygiene",
                "--bibtex",
                "--resolve",
            )
        self.assertEqual(code, 0, stderr)
        self.assertIn("Recorded replacement", stdout)

        code, stdout, stderr = self.run_cli(
            "hygiene",
            "--bibtex",
            "--apply",
        )
        self.assertEqual(code, 0, stderr)
        self.assertIn("Repairs applied : 1", stdout)
        self.assertIn(
            "title={{BibTeX invalid repair}}",
            bibtex_path.read_text(encoding="utf-8"),
        )

    def test_missing_canonical_bibliography_fails_without_traceback(self) -> None:
        self.config.paths.bibliography.unlink()
        code, stdout, stderr = self.run_cli("hygiene")

        self.assertEqual(code, 1)
        self.assertEqual(stdout, "")
        self.assertIn("bibreview hygiene:", stderr)
        self.assertNotIn("Traceback", stderr)


if __name__ == "__main__":
    unittest.main()
