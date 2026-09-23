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

from bibreview.cli import _enable_interactive_line_editing, main
from bibreview.config import load_config
from bibreview.identity import new_publication_id
from bibreview.model import Author, Publication
from bibreview.pipeline.audit import (
    AuditComparison,
    AuditResult,
    AuditReviewFinding,
    ProviderEvidence,
)
from bibreview.project_audit import (
    AuditPublicationReview,
    ProjectAuditReview,
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


class AuditResolverLineEditingTests(unittest.TestCase):
    def test_readline_is_loaded_for_interactive_line_editing(self):
        with patch("bibreview.cli.import_module") as import_module_mock:
            _enable_interactive_line_editing()

        import_module_mock.assert_called_once_with("readline")

    def test_missing_readline_is_a_graceful_fallback(self):
        with patch("bibreview.cli.import_module", side_effect=ImportError):
            _enable_interactive_line_editing()


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

    def test_resolve_interactively_accepts_custom_rejects_and_defers(self):
        review = ProjectAuditReview(
            audited_publications=1,
            flagged_publications=1,
            actionable_findings=4,
            informational_findings=0,
            provider_issues=0,
            items=(
                AuditPublicationReview(
                    publication_id=self.publication.id,
                    identifiers=self.publication.identifiers,
                    permalink=self.publication.permalink,
                    title=self.publication.title,
                    findings=(
                        AuditReviewFinding(
                            field="volume",
                            classification="canonical-missing",
                            providers=("crossref", "openalex"),
                            canonical_value="",
                            provider_values=(
                                ("crossref", "48"),
                                ("openalex", "48"),
                            ),
                            actionable=True,
                            detail="corroborated by 2 independent providers",
                        ),
                        AuditReviewFinding(
                            field="title",
                            classification="substantive-difference",
                            providers=("crossref", "semantic_scholar"),
                            canonical_value="Old title",
                            provider_values=(
                                ("crossref", "New title"),
                                ("semantic_scholar", "New Title"),
                            ),
                            actionable=True,
                            detail="corroborated by 2 independent providers",
                        ),
                        AuditReviewFinding(
                            field="issue",
                            classification="canonical-missing",
                            providers=("crossref", "openalex"),
                            canonical_value="",
                            provider_values=(
                                ("crossref", "8"),
                                ("openalex", "8"),
                            ),
                            actionable=True,
                            detail="corroborated by 2 independent providers",
                        ),
                        AuditReviewFinding(
                            field="pages",
                            classification="canonical-missing",
                            providers=("crossref", "openalex"),
                            canonical_value="",
                            provider_values=(
                                ("crossref", "1-9"),
                                ("openalex", "1-9"),
                            ),
                            actionable=True,
                            detail="corroborated by 2 independent providers",
                        ),
                    ),
                ),
            ),
        )
        stdout = StringIO()
        stderr = StringIO()

        with patch(
            "bibreview.cli.project_audit_review",
            return_value=review,
        ), patch(
            "bibreview.cli._enable_interactive_line_editing",
        ) as line_editing, patch(
            "builtins.input",
            side_effect=["", "f Preferred title", "n", "s"],
        ), redirect_stdout(stdout), redirect_stderr(stderr):
            code = main([
                "--config",
                str(self.config_path),
                "audit",
                "--resolve",
            ])

        self.assertEqual(code, 0, stderr.getvalue())
        line_editing.assert_called_once_with()
        self.assertIn("[1/4] 10.1000/audit", stdout.getvalue())
        self.assertIn("Proposed: no single exact provider representation", stdout.getvalue())
        resolution_path = self.root / "state/resolutions.json"
        payload = read_json(resolution_path, dict)
        decisions = {item["field"]: item for item in payload["decisions"]}
        self.assertEqual(decisions["volume"]["decision"], "accepted")
        self.assertEqual(decisions["volume"]["resolved_value"], "48")
        self.assertEqual(decisions["title"]["decision"], "custom")
        self.assertEqual(decisions["title"]["resolved_value"], "Preferred title")
        self.assertEqual(decisions["issue"]["decision"], "rejected")
        self.assertEqual(decisions["pages"]["decision"], "deferred")
        self.assertIn("Accepted            : 1", stdout.getvalue())
        self.assertIn("Custom              : 1", stdout.getvalue())
        self.assertIn("Rejected            : 1", stdout.getvalue())
        self.assertIn("Deferred            : 1", stdout.getvalue())

    def test_resolve_quit_preserves_prior_decisions_and_resume_skips_them(self):
        review = ProjectAuditReview(
            audited_publications=1,
            flagged_publications=1,
            actionable_findings=2,
            informational_findings=0,
            provider_issues=0,
            items=(
                AuditPublicationReview(
                    publication_id=self.publication.id,
                    identifiers=self.publication.identifiers,
                    permalink=self.publication.permalink,
                    title=self.publication.title,
                    findings=(
                        AuditReviewFinding(
                            field="volume",
                            classification="canonical-missing",
                            providers=("crossref", "openalex"),
                            canonical_value="",
                            provider_values=(("crossref", "48"), ("openalex", "48")),
                            actionable=True,
                        ),
                        AuditReviewFinding(
                            field="issue",
                            classification="canonical-missing",
                            providers=("crossref", "openalex"),
                            canonical_value="",
                            provider_values=(("crossref", "8"), ("openalex", "8")),
                            actionable=True,
                        ),
                    ),
                ),
            ),
        )

        with patch(
            "bibreview.cli.project_audit_review",
            return_value=review,
        ), patch(
            "builtins.input",
            side_effect=["", "q"],
        ), redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            self.assertEqual(
                main(["--config", str(self.config_path), "audit", "--resolve"]),
                0,
            )

        first = read_json(self.root / "state/resolutions.json", dict)
        self.assertEqual(len(first["decisions"]), 1)
        self.assertEqual(first["decisions"][0]["field"], "volume")

        stdout = StringIO()
        with patch(
            "bibreview.cli.project_audit_review",
            return_value=review,
        ), patch(
            "builtins.input",
            side_effect=[""],
        ), redirect_stdout(stdout), redirect_stderr(StringIO()):
            self.assertEqual(
                main(["--config", str(self.config_path), "audit", "--resolve"]),
                0,
            )

        second = read_json(self.root / "state/resolutions.json", dict)
        self.assertEqual(len(second["decisions"]), 2)
        self.assertNotIn("[1/2]", stdout.getvalue())
        self.assertIn("[2/2]", stdout.getvalue())

    def test_resolve_accepts_semicolon_separated_tuple_custom_value(self):
        review = ProjectAuditReview(
            audited_publications=1,
            flagged_publications=1,
            actionable_findings=1,
            informational_findings=0,
            provider_issues=0,
            items=(
                AuditPublicationReview(
                    publication_id=self.publication.id,
                    identifiers=self.publication.identifiers,
                    permalink=self.publication.permalink,
                    title=self.publication.title,
                    findings=(
                        AuditReviewFinding(
                            field="authors",
                            classification="substantive-difference",
                            providers=("crossref", "openalex"),
                            canonical_value=(
                                "Nguyen Thanh Sang",
                                "Tan Chee Keong",
                            ),
                            provider_values=(
                                (
                                    "crossref",
                                    (
                                        "Nguyen Thanh Sang",
                                        "Tan Chee Keong",
                                        "Mohd Azlan, Hussain",
                                    ),
                                ),
                                (
                                    "openalex",
                                    (
                                        "Nguyen Thanh Sang",
                                        "Tan Chee Keong",
                                        "Mohd Azlan, Hussain",
                                    ),
                                ),
                            ),
                            actionable=True,
                        ),
                    ),
                ),
            ),
        )
        stdout = StringIO()
        stderr = StringIO()
        with patch(
            "bibreview.cli.project_audit_review",
            return_value=review,
        ), patch(
            "builtins.input",
            side_effect=[
                "f Nguyen Thanh Sang; Tan Chee Keong; Hussain Mohd Azlan"
            ],
        ), redirect_stdout(stdout), redirect_stderr(stderr):
            code = main([
                "--config",
                str(self.config_path),
                "audit",
                "--resolve",
            ])

        self.assertEqual(code, 0, stderr.getvalue())
        payload = read_json(self.root / "state/resolutions.json", dict)
        self.assertEqual(
            payload["decisions"][0]["resolved_value"],
            [
                "Nguyen Thanh Sang",
                "Tan Chee Keong",
                "Hussain Mohd Azlan",
            ],
        )
        self.assertEqual(payload["decisions"][0]["decision"], "custom")

    def test_resolve_dry_run_does_not_write_resolution_state(self):
        review = ProjectAuditReview(
            audited_publications=1,
            flagged_publications=1,
            actionable_findings=1,
            informational_findings=0,
            provider_issues=0,
            items=(
                AuditPublicationReview(
                    publication_id=self.publication.id,
                    identifiers=self.publication.identifiers,
                    permalink=self.publication.permalink,
                    title=self.publication.title,
                    findings=(
                        AuditReviewFinding(
                            field="volume",
                            classification="canonical-missing",
                            providers=("crossref", "openalex"),
                            canonical_value="",
                            provider_values=(("crossref", "48"), ("openalex", "48")),
                            actionable=True,
                        ),
                    ),
                ),
            ),
        )
        stdout = StringIO()
        stderr = StringIO()
        with patch(
            "bibreview.cli.project_audit_review",
            return_value=review,
        ), patch(
            "builtins.input",
            side_effect=[""],
        ), redirect_stdout(stdout), redirect_stderr(stderr):
            code = main([
                "--config",
                str(self.config_path),
                "--dry-run",
                "audit",
                "--resolve",
            ])

        self.assertEqual(code, 0, stderr.getvalue())
        self.assertFalse((self.root / "state/resolutions.json").exists())
        self.assertIn("Dry run: Audit resolution", stdout.getvalue())

    def test_resolve_rejects_json_and_quiet_modes(self):
        for extra in (["--json"],):
            stdout = StringIO()
            stderr = StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = main([
                    "--config",
                    str(self.config_path),
                    "audit",
                    "--resolve",
                    *extra,
                ])
            self.assertEqual(code, 1)
            self.assertIn("interactive --resolve", stderr.getvalue())

        stdout = StringIO()
        stderr = StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main([
                "--config",
                str(self.config_path),
                "--quiet",
                "audit",
                "--resolve",
            ])
        self.assertEqual(code, 1)
        self.assertIn("interactive --resolve", stderr.getvalue())

    def test_apply_dry_run_plans_without_writing(self):
        plan = SimpleNamespace(
            changed=True,
            bibtex_backups=(),
            summary=lambda: (
                "Audit resolution application\n"
                "  Actionable findings   : 1\n"
                "  Accepted              : 1\n"
                "  Custom                : 0\n"
                "  Rejected              : 0\n"
                "  No-op resolutions     : 0\n"
                "  Changes to stage      : 1\n"
                "  Publications affected : 1\n"
                "  BibTeX files affected : 1"
            ),
            data=lambda: {
                "actionable_findings": 1,
                "accepted": 1,
                "custom": 0,
                "rejected": 0,
                "deferred": 0,
                "unresolved": 0,
                "no_op_resolutions": 0,
                "changes_to_stage": 1,
                "publications_affected": 1,
                "bibtex_files_affected": 1,
                "changes": [],
                "no_ops": [],
            },
        )
        stdout = StringIO()
        stderr = StringIO()
        with patch(
            "bibreview.cli.plan_project_audit_apply",
            return_value=plan,
        ), patch(
            "bibreview.cli.apply_project_audit_apply",
        ) as apply_plan, redirect_stdout(stdout), redirect_stderr(stderr):
            code = main([
                "--config",
                str(self.config_path),
                "--dry-run",
                "audit",
                "--apply",
            ])

        self.assertEqual(code, 0, stderr.getvalue())
        apply_plan.assert_not_called()
        self.assertIn(
            "Dry run: Audit resolution application",
            stdout.getvalue(),
        )
        self.assertIn("Staging:", stdout.getvalue())

    def test_apply_json_applies_plan_and_returns_metrics(self):
        plan = SimpleNamespace(
            changed=True,
            bibtex_backups=(),
            summary=lambda: "Audit resolution application",
            data=lambda: {
                "actionable_findings": 92,
                "accepted": 55,
                "custom": 24,
                "rejected": 13,
                "deferred": 0,
                "unresolved": 0,
                "no_op_resolutions": 1,
                "changes_to_stage": 78,
                "publications_affected": 60,
                "bibtex_files_affected": 42,
                "changes": [],
                "no_ops": [
                    {
                        "publication_id": "example",
                        "doi": "10.1000/example",
                        "title": "Example",
                        "field": "title",
                        "decision": "custom",
                        "value": "Example",
                    }
                ],
            },
        )
        stdout = StringIO()
        stderr = StringIO()
        with patch(
            "bibreview.cli.plan_project_audit_apply",
            return_value=plan,
        ), patch(
            "bibreview.cli.apply_project_audit_apply",
        ) as apply_plan, redirect_stdout(stdout), redirect_stderr(stderr):
            code = main([
                "--config",
                str(self.config_path),
                "audit",
                "--apply",
                "--json",
            ])

        self.assertEqual(code, 0, stderr.getvalue())
        apply_plan.assert_called_once_with(plan)
        payload = json.loads(stdout.getvalue())
        self.assertFalse(payload["dry_run"])
        self.assertEqual(payload["accepted"], 55)
        self.assertEqual(payload["custom"], 24)
        self.assertEqual(payload["rejected"], 13)
        self.assertEqual(payload["no_op_resolutions"], 1)
        self.assertEqual(payload["changes_to_stage"], 78)
        self.assertEqual(payload["no_ops"][0]["decision"], "custom")

    def test_apply_rejects_batch_size(self):
        stdout = StringIO()
        stderr = StringIO()
        with patch("bibreview.cli.plan_project_audit_apply") as planner, redirect_stdout(
            stdout
        ), redirect_stderr(stderr):
            code = main([
                "--config",
                str(self.config_path),
                "audit",
                "--apply",
                "--batch-size",
                "1",
            ])

        self.assertEqual(code, 1)
        planner.assert_not_called()
        self.assertIn("--batch-size cannot be used with --apply", stderr.getvalue())

    def test_full_audit_flag_is_forwarded_to_batch_planner(self):
        plan = SimpleNamespace(
            batch=None,
            campaign=create_campaign("audit", [self.publication.id], batch_size=1),
            summary=lambda: "full audit plan",
        )
        stdout = StringIO()
        stderr = StringIO()
        with patch(
            "bibreview.cli.plan_project_audit_batch",
            return_value=plan,
        ) as planner, redirect_stdout(stdout), redirect_stderr(stderr):
            code = main([
                "--config",
                str(self.config_path),
                "--dry-run",
                "audit",
                "--full",
                "--json",
            ])

        self.assertEqual(code, 0, stderr.getvalue())
        planner.assert_called_once_with(
            self.config,
            batch_size=None,
            full=True,
        )

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
