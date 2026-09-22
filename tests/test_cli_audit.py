from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from bibreview.cli import main
from bibreview.config import load_config
from bibreview.identity import new_publication_id
from bibreview.model import Author, Publication
from bibreview.pipeline.audit import AuditComparison, AuditResult, ProviderEvidence
from bibreview.project_audit import (
    apply_project_audit_plan,
    audit_report_from_data,
    plan_project_audit_batch,
    plan_project_audit_checkpoint,
    plan_project_audit_close,
)
from bibreview.storage import read_json, write_bibliography


CONFIG = """\
schema_version: 1
project:
  name: Example Review
  slug: example-review
audit:
  campaign: state/audit-campaign.json
  report: state/audit-report.json
  batch_size: 50
site:
  enabled: false
"""


class FakeAuditSource:
    name = "crossref"

    def __init__(self):
        self.calls = []

    def evidence(self, doi):
        self.calls.append(doi)
        return ProviderEvidence(
            provider=self.name,
            identifiers={"doi": doi},
            fields={
                "title": "Audited publication",
                "publication_year": "2026",
            },
        )


class AuditCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config_path = self.root / "bibreview.yml"
        self.config_path.write_text(CONFIG, encoding="utf-8")
        self.config = load_config(self.config_path)
        self.publication = Publication(
            id=new_publication_id(),
            identifiers={"doi": "10.1000/audit"},
            title="Audited publication",
            authors=(Author(literal="Example Author"),),
            publication_year="2026",
            permalink="audited-publication",
        )
        self.config.paths.bibliography.parent.mkdir(parents=True, exist_ok=True)
        write_bibliography(
            self.config.paths.bibliography,
            (self.publication,),
        )

    def snapshot(self):
        return {
            str(path.relative_to(self.root)): path.read_bytes()
            for path in self.root.rglob("*")
            if path.is_file()
        }

    def test_default_config_path_is_bibreview_yml_in_current_directory(self):
        stdout = StringIO()
        stderr = StringIO()
        previous = Path.cwd()
        try:
            os.chdir(self.root)
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = main(["validate"])
        finally:
            os.chdir(previous)

        self.assertEqual(code, 0, stderr.getvalue())
        self.assertIn("Configuration valid:", stdout.getvalue())

    def seed_legacy_reclassifiable_report(self):
        start = plan_project_audit_batch(self.config, batch_size=1)
        apply_project_audit_plan(start)
        result = AuditResult(
            publication_id=self.publication.id,
            identifiers=self.publication.identifiers,
            permalink=self.publication.permalink,
            title=self.publication.title,
            comparisons=(
                AuditComparison(
                    provider="crossref",
                    field="pages",
                    classification="substantive-difference",
                    canonical_value="10--20",
                    provider_value="10-20",
                ),
            ),
            provider_issues=(),
            disagreements=(),
        )
        checkpoint = plan_project_audit_checkpoint(
            self.config,
            batch_id=start.batch.id,
            result=result,
            state="completed",
        )
        apply_project_audit_plan(checkpoint)
        apply_project_audit_plan(
            plan_project_audit_close(self.config, batch_id=start.batch.id)
        )

    def test_reclassify_is_offline_and_supports_dry_run(self):
        self.seed_legacy_reclassifiable_report()
        report_before = self.config.audit.report.read_bytes()
        campaign_before = self.config.audit.campaign.read_bytes()
        stdout = StringIO()
        stderr = StringIO()

        with patch("bibreview.cli.build_audit_services") as services, redirect_stdout(
            stdout
        ), redirect_stderr(stderr):
            code = main([
                "--config",
                str(self.config_path),
                "--dry-run",
                "audit",
                "--reclassify",
                "--json",
            ])

        self.assertEqual(code, 0, stderr.getvalue())
        services.assert_not_called()
        payload = json.loads(stdout.getvalue())
        self.assertTrue(payload["dry_run"])
        self.assertTrue(payload["changed"])
        self.assertEqual(payload["changed_comparisons"], 1)
        self.assertEqual(
            payload["after_counts"],
            {"formatting-only": 1},
        )
        self.assertEqual(self.config.audit.report.read_bytes(), report_before)
        self.assertEqual(self.config.audit.campaign.read_bytes(), campaign_before)

        stdout = StringIO()
        stderr = StringIO()
        with patch("bibreview.cli.build_audit_services") as services, redirect_stdout(
            stdout
        ), redirect_stderr(stderr):
            code = main([
                "--config",
                str(self.config_path),
                "audit",
                "--reclassify",
                "--json",
            ])

        self.assertEqual(code, 0, stderr.getvalue())
        services.assert_not_called()
        report = audit_report_from_data(
            read_json(self.config.audit.report, dict)
        )
        self.assertEqual(
            report.entries[0].result.comparisons[0].classification,
            "formatting-only",
        )
        self.assertEqual(self.config.audit.campaign.read_bytes(), campaign_before)

    def test_review_is_read_only_and_uses_current_rules_in_memory(self):
        self.seed_legacy_reclassifiable_report()
        report_before = self.config.audit.report.read_bytes()
        campaign_before = self.config.audit.campaign.read_bytes()
        stdout = StringIO()
        stderr = StringIO()

        with patch("bibreview.cli.build_audit_services") as services, redirect_stdout(
            stdout
        ), redirect_stderr(stderr):
            code = main([
                "--config",
                str(self.config_path),
                "audit",
                "--review",
                "--json",
            ])

        self.assertEqual(code, 0, stderr.getvalue())
        services.assert_not_called()
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["audited_publications"], 1)
        self.assertEqual(payload["flagged_publications"], 0)
        self.assertEqual(payload["actionable_findings"], 0)
        self.assertEqual(self.config.audit.report.read_bytes(), report_before)
        self.assertEqual(self.config.audit.campaign.read_bytes(), campaign_before)

    def test_dry_run_selects_batch_without_provider_or_state_writes(self):
        before = self.snapshot()
        stdout = StringIO()
        stderr = StringIO()

        with patch("bibreview.cli.build_audit_services") as services, redirect_stdout(
            stdout
        ), redirect_stderr(stderr):
            code = main([
                "--config",
                str(self.config_path),
                "--dry-run",
                "audit",
                "--batch-size",
                "1",
                "--json",
            ])

        self.assertEqual(code, 0, stderr.getvalue())
        services.assert_not_called()
        payload = json.loads(stdout.getvalue())
        self.assertTrue(payload["dry_run"])
        self.assertEqual(payload["batch_id"], "batch-0001")
        self.assertEqual(payload["keys"], [self.publication.id])
        self.assertEqual(before, self.snapshot())
        self.assertFalse(self.config.audit.campaign.exists())
        self.assertFalse(self.config.audit.report.exists())

    def test_audit_processes_one_batch_and_reports_completion(self):
        source = FakeAuditSource()
        stdout = StringIO()
        stderr = StringIO()

        with patch(
            "bibreview.cli.build_audit_services",
            return_value=SimpleNamespace(sources=(source,)),
        ), redirect_stdout(stdout), redirect_stderr(stderr):
            code = main([
                "--config",
                str(self.config_path),
                "audit",
                "--batch-size",
                "1",
                "--json",
            ])

        self.assertEqual(code, 0, stderr.getvalue())
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["batch_id"], "batch-0001")
        self.assertEqual(payload["processed"], 1)
        self.assertEqual(payload["completed"], 1)
        self.assertEqual(payload["retryable"], 0)
        self.assertTrue(payload["progress"]["exhausted"])
        self.assertEqual(source.calls, ["10.1000/audit"])

        report = audit_report_from_data(
            read_json(self.config.audit.report, dict)
        )
        self.assertEqual(len(report.entries), 1)
        self.assertEqual(report.entries[0].publication_id, self.publication.id)

        stdout = StringIO()
        stderr = StringIO()
        with patch("bibreview.cli.build_audit_services") as services, redirect_stdout(
            stdout
        ), redirect_stderr(stderr):
            code = main([
                "--config",
                str(self.config_path),
                "audit",
                "--json",
            ])

        self.assertEqual(code, 0, stderr.getvalue())
        services.assert_not_called()
        complete = json.loads(stdout.getvalue())
        self.assertIsNone(complete["batch_id"])
        self.assertEqual(complete["processed"], 0)
        self.assertTrue(complete["progress"]["successful"])


if __name__ == "__main__":
    unittest.main()
