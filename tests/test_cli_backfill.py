from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from bibreview.backfill_resolution import (
    backfill_resolution_path,
    load_project_backfill_resolutions,
)
from bibreview.cli import main
from bibreview.config import load_config
from bibreview.identity import new_publication_id
from bibreview.model import Author, Publication
from bibreview.pipeline.backfill import BackfillCandidate
from bibreview.project_backfill import BackfillReview, backfill_review_path
from bibreview.providers.base import AbstractEvidence
from bibreview.storage import read_bibliography, write_bibliography, write_json


CONFIG = """\
schema_version: 1
project:
  name: Example Review
  slug: example-review
paths:
  bibliography: data/bibliography.json
  collected: data/collected.json
  author_mappings: data/authors.json
  known: data/known.txt
  pending: data/pending.txt
  rejected: data/rejected.txt
  review: data/review.txt
  bibtex: bib
  archive: archive
  site: site
audit:
  campaign: state/audit-campaign.json
  report: state/audit-report.json
  batch_size: 50
site:
  enabled: false
"""


class BackfillCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config_path = self.root / "bibreview.yml"
        self.config_path.write_text(CONFIG, encoding="utf-8")
        self.config = load_config(self.config_path)
        self.publication = Publication(
            id=new_publication_id(),
            identifiers={"doi": "10.1000/backfill"},
            type="journal-article",
            title="Reviewed title",
            authors=(Author(literal="Reviewed Author"),),
            publication_year="2026",
            permalink="reviewed-title",
        )
        write_bibliography(
            self.config.paths.bibliography,
            (self.publication,),
        )
        self.review = BackfillReview(
            fields=("abstract",),
            types=(),
            scanned_count=1,
            eligible_count=1,
            candidates=(
                BackfillCandidate(
                    publication_id=self.publication.id,
                    doi=self.publication.doi,
                    title=self.publication.title,
                    field="abstract",
                    proposed_value="Candidate abstract",
                ),
            ),
            unavailable=(),
            no_value=(),
        )
        write_json(backfill_review_path(self.config), self.review.data())

    def test_resolve_accepts_proposal_and_persists_decision(self):
        stdout = StringIO()
        stderr = StringIO()
        with patch("builtins.input", side_effect=[""]), redirect_stdout(
            stdout
        ), redirect_stderr(stderr):
            code = main([
                "--config",
                str(self.config_path),
                "backfill",
                "--resolve",
            ])

        self.assertEqual(code, 0, stderr.getvalue())
        self.assertIn("Candidate abstract", stdout.getvalue())
        self.assertTrue(backfill_resolution_path(self.config).exists())
        state = load_project_backfill_resolutions(self.config, self.review)
        self.assertEqual(state.decisions[0].decision, "accepted")


    def test_review_required_evidence_requires_custom_or_reject(self):
        review = BackfillReview(
            fields=("abstract",),
            types=(),
            scanned_count=1,
            eligible_count=1,
            candidates=(
                BackfillCandidate(
                    publication_id=self.publication.id,
                    doi=self.publication.doi,
                    title=self.publication.title,
                    field="abstract",
                    proposed_value="",
                    review_required=True,
                    evidence=(
                        AbstractEvidence(
                            source="crossref",
                            value=(
                                'A controller <jats:inline-graphic '
                                'xlink:href="graphic/math-0002.png"/> is proposed.'
                            ),
                            reason="embedded-graphic",
                        ),
                    ),
                ),
            ),
            unavailable=(),
            no_value=(),
        )
        write_json(backfill_review_path(self.config), review.data())

        stdout = StringIO()
        stderr = StringIO()
        with patch(
            "builtins.input",
            side_effect=["", "f Reviewed safe abstract"],
        ), redirect_stdout(stdout), redirect_stderr(stderr):
            code = main([
                "--config",
                str(self.config_path),
                "backfill",
                "--resolve",
            ])

        self.assertEqual(code, 0, stderr.getvalue())
        output = stdout.getvalue()
        self.assertIn("REVIEW REQUIRED", output)
        self.assertIn("embedded-graphic", output)
        self.assertIn("math-0002.png", output)
        self.assertIn("No safe automatic value is available", output)

        state = load_project_backfill_resolutions(self.config, review)
        self.assertEqual(state.decisions[0].decision, "custom")
        self.assertEqual(
            state.decisions[0].resolved_value,
            "Reviewed safe abstract",
        )


    def test_apply_stages_only_after_resolution(self):
        with patch("builtins.input", side_effect=[""]), redirect_stdout(
            StringIO()
        ), redirect_stderr(StringIO()):
            self.assertEqual(
                main([
                    "--config",
                    str(self.config_path),
                    "backfill",
                    "--resolve",
                ]),
                0,
            )

        self.assertFalse(self.config.paths.collected.exists())

        stdout = StringIO()
        stderr = StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main([
                "--config",
                str(self.config_path),
                "backfill",
                "--apply",
            ])

        self.assertEqual(code, 0, stderr.getvalue())
        staged = read_bibliography(self.config.paths.collected)
        self.assertEqual(staged[0].abstract, "Candidate abstract")
        self.assertIn("Changes to stage", stdout.getvalue())

    def test_generation_requires_field(self):
        stderr = StringIO()
        with redirect_stdout(StringIO()), redirect_stderr(stderr):
            code = main([
                "--config",
                str(self.config_path),
                "backfill",
            ])

        self.assertEqual(code, 1)
        self.assertIn("at least one --field is required", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
