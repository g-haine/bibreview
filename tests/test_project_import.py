from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
import tempfile
import unittest

import yaml

from bibreview.cli import main
from bibreview.config import load_config
from bibreview.identity import new_publication_id
from bibreview.model import Author, Publication
from bibreview.project_import import (
    ProjectImportError,
    apply_project_import,
    initialize_import_manifest,
    load_import_manifest,
    plan_project_import,
)
from bibreview.storage import read_bibliography, write_bibliography


CONFIG = """\
schema_version: 1
project:
  name: Example Review
  slug: example-review
site:
  enabled: false
"""


class ProjectImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config_path = self.root / "bibreview.yml"
        self.config_path.write_text(CONFIG, encoding="utf-8")
        self.config = load_config(self.config_path)
        self.config.paths.bibliography.parent.mkdir(parents=True, exist_ok=True)
        write_bibliography(self.config.paths.bibliography, ())
        write_bibliography(self.config.paths.collected, ())
        self.manifest_path = self.root / "publication.yml"

    def manifest_data(
        self,
        *,
        publication_id=None,
        identifiers=None,
        title="Manual publication",
        year="2026",
        permalink="",
        provenance_kind="manual",
        provenance_source="",
        bibtex="@article{manual,\n  title = {Manual publication}\n}\n",
    ):
        return {
            "schema_version": 1,
            "id": publication_id or new_publication_id(),
            "provenance": {
                "kind": provenance_kind,
                "source": provenance_source,
                "note": "reviewed import",
            },
            "citation": "Manual citation",
            "publication": {
                "identifiers": identifiers or {},
                "type": "journal-article",
                "title": title,
                "authors": [
                    {
                        "given": "Ada",
                        "family": "Lovelace",
                    }
                ],
                "editors": [],
                "abstract": "",
                "container_title": "Example Journal",
                "publication_year": year,
                "volume": "1",
                "issue": "2",
                "pages": "1--10",
                "publisher": "",
                "event": "",
                "keywords": ["port-Hamiltonian"],
                "created_date": None,
                "permalink": permalink,
                "references": [],
            },
            "bibtex": bibtex,
        }

    def write_manifest(self, **kwargs):
        data = self.manifest_data(**kwargs)
        self.manifest_path.write_text(
            yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
        return data

    def snapshot(self):
        return {
            str(path.relative_to(self.root)): path.read_bytes()
            for path in self.root.rglob("*")
            if path.is_file()
        }

    def test_init_allocates_and_persists_uuid_once(self):
        publication_id = initialize_import_manifest(self.manifest_path)
        self.assertTrue(self.manifest_path.exists())

        raw = yaml.safe_load(self.manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(raw["id"], publication_id)

        with self.assertRaisesRegex(ProjectImportError, "refusing to overwrite"):
            initialize_import_manifest(self.manifest_path)

        raw["publication"]["type"] = "journal-article"
        raw["publication"]["title"] = "Edited later"
        raw["publication"]["publication_year"] = "2026"
        raw["publication"]["authors"] = [{"literal": "Example Author"}]
        raw["bibtex"] = "@article{edited}\n"
        self.manifest_path.write_text(
            yaml.safe_dump(raw, sort_keys=False),
            encoding="utf-8",
        )
        self.assertEqual(load_import_manifest(self.manifest_path).publication.id, publication_id)

    def test_plan_is_read_only_and_apply_writes_only_staging_evidence_and_bibtex(self):
        raw = self.write_manifest(
            identifiers={"PMLR": "331:example"},
            permalink="",
            provenance_kind="official-import",
            provenance_source="https://example.test/paper",
        )
        before = self.snapshot()

        plan = plan_project_import(self.config, self.manifest_path)

        self.assertEqual(before, self.snapshot())
        self.assertEqual(plan.manifest.publication.id, raw["id"])
        self.assertEqual(plan.manifest.publication.identifiers, {"pmlr": "331:example"})
        self.assertEqual(plan.manifest.publication.permalink, "manual-publication")
        self.assertEqual(plan.warnings, ())

        apply_project_import(plan)

        staged = read_bibliography(self.config.paths.collected)
        self.assertEqual(len(staged), 1)
        self.assertEqual(staged[0].id, raw["id"])
        self.assertIsNone(staged[0].doi)
        self.assertEqual(staged[0].identifiers["pmlr"], "331:example")
        self.assertFalse(self.config.paths.known.exists())
        self.assertEqual(read_bibliography(self.config.paths.bibliography), ())

        self.assertEqual(
            (self.config.paths.bibtex / "manual-publication.bib").read_text(
                encoding="utf-8"
            ),
            "@article{manual,\n  title = {Manual publication}\n}\n",
        )
        evidence = self.config.paths.imports / f"{raw['id']}.yml"
        self.assertTrue(evidence.is_file())
        persisted = load_import_manifest(evidence)
        self.assertEqual(persisted.publication.id, raw["id"])
        self.assertEqual(persisted.publication.permalink, "manual-publication")
        self.assertEqual(persisted.provenance.kind, "official-import")

    def test_doi_is_refused_from_manual_import(self):
        self.write_manifest(identifiers={"doi": "10.1234/example"})
        before = self.snapshot()
        with self.assertRaisesRegex(
            ProjectImportError,
            "manual import cannot contain a DOI",
        ):
            plan_project_import(self.config, self.manifest_path)
        self.assertEqual(before, self.snapshot())

    def test_nonempty_staging_blocks_import(self):
        staged = Publication(
            id=new_publication_id(),
            identifiers={},
            type="journal-article",
            title="Already staged",
            authors=(Author(literal="Example"),),
            publication_year="2026",
            permalink="already-staged",
        )
        write_bibliography(self.config.paths.collected, (staged,))
        self.write_manifest()

        with self.assertRaisesRegex(ProjectImportError, "merge the existing batch"):
            plan_project_import(self.config, self.manifest_path)

    def test_auxiliary_identifier_and_title_year_matches_warn_without_merging(self):
        existing = Publication(
            id=new_publication_id(),
            identifiers={"pmlr": "331:example"},
            type="journal-article",
            title="Manual publication",
            authors=(Author(literal="Existing Author"),),
            publication_year="2026",
            permalink="existing-manual-publication",
        )
        write_bibliography(self.config.paths.bibliography, (existing,))
        self.write_manifest(identifiers={"pmlr": "331:example"})

        plan = plan_project_import(self.config, self.manifest_path)

        self.assertEqual(len(plan.warnings), 2)
        self.assertTrue(any("auxiliary identifier" in item for item in plan.warnings))
        self.assertTrue(any("title/year" in item for item in plan.warnings))
        self.assertNotEqual(plan.manifest.publication.id, existing.id)

    def test_duplicate_uuid_is_rejected(self):
        publication_id = new_publication_id()
        existing = Publication(
            id=publication_id,
            identifiers={},
            type="journal-article",
            title="Existing",
            authors=(Author(literal="Existing Author"),),
            publication_year="2026",
            permalink="existing",
        )
        write_bibliography(self.config.paths.bibliography, (existing,))
        self.write_manifest(publication_id=publication_id)

        with self.assertRaisesRegex(ProjectImportError, "already exists"):
            plan_project_import(self.config, self.manifest_path)

    def test_permalink_collision_is_rejected(self):
        existing = Publication(
            id=new_publication_id(),
            identifiers={},
            type="journal-article",
            title="Existing",
            authors=(Author(literal="Existing Author"),),
            publication_year="2026",
            permalink="manual-publication",
        )
        write_bibliography(self.config.paths.bibliography, (existing,))
        self.write_manifest(permalink="manual-publication")

        with self.assertRaisesRegex(ProjectImportError, "permalink"):
            plan_project_import(self.config, self.manifest_path)

    def test_official_or_provider_provenance_requires_source(self):
        self.write_manifest(provenance_kind="official-import")
        with self.assertRaisesRegex(ProjectImportError, "provenance.source is required"):
            load_import_manifest(self.manifest_path)

    def test_different_existing_bibtex_is_never_overwritten(self):
        self.write_manifest(permalink="manual-publication")
        self.config.paths.bibtex.mkdir(parents=True, exist_ok=True)
        target = self.config.paths.bibtex / "manual-publication.bib"
        target.write_text("@article{different}\n", encoding="utf-8")

        with self.assertRaisesRegex(ProjectImportError, "refusing to overwrite"):
            plan_project_import(self.config, self.manifest_path)

    def test_cli_init_dry_run_import_and_apply(self):
        stdout = StringIO()
        stderr = StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main([
                "--config", str(self.config_path),
                "import", "--init", str(self.manifest_path),
            ])
        self.assertEqual(code, 0, stderr.getvalue())
        self.assertIn("Initialized import manifest:", stdout.getvalue())

        raw = yaml.safe_load(self.manifest_path.read_text(encoding="utf-8"))
        publication_id = raw["id"]
        raw.update(self.manifest_data(publication_id=publication_id))
        self.manifest_path.write_text(
            yaml.safe_dump(raw, sort_keys=False),
            encoding="utf-8",
        )

        before = self.snapshot()
        stdout = StringIO()
        stderr = StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main([
                "--config", str(self.config_path),
                "--dry-run",
                "import", str(self.manifest_path),
            ])
        self.assertEqual(code, 0, stderr.getvalue())
        self.assertIn("Dry run:", stdout.getvalue())
        self.assertEqual(before, self.snapshot())

        stdout = StringIO()
        stderr = StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main([
                "--config", str(self.config_path),
                "import", str(self.manifest_path),
            ])
        self.assertEqual(code, 0, stderr.getvalue())
        self.assertIn(publication_id, stdout.getvalue())
        self.assertEqual(read_bibliography(self.config.paths.collected)[0].id, publication_id)

    def test_cli_rejects_dry_run_init(self):
        stdout = StringIO()
        stderr = StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main([
                "--config", str(self.config_path),
                "--dry-run",
                "import", "--init", str(self.manifest_path),
            ])
        self.assertEqual(code, 1)
        self.assertIn("cannot be used with --dry-run", stderr.getvalue())
        self.assertFalse(self.manifest_path.exists())


if __name__ == "__main__":
    unittest.main()
