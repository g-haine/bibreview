"""Project-state orchestration for non-destructive resumable audits."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from .campaign import (
    Campaign,
    CampaignBatch,
    CampaignError,
    campaign_data,
    campaign_from_data,
    campaign_progress,
    close_batch,
    create_campaign,
    open_next_batch,
    record_item_result,
)
from .config import BibReviewConfig
from .pipeline.audit import (
    AuditComparison,
    AuditError,
    AuditProviderIssue,
    AuditResult,
    ProviderEvidence,
    audit_result_data,
    audit_result_from_data,
    compare_audit_record,
    publication_audit_record,
)
from .project import ProjectStateError
from .providers.audit import AuditEvidenceSource
from .providers.http import HttpError
from .reporting import Reporter
from .storage import (
    atomic_write_batch,
    json_bytes,
    read_bibliography,
    read_json,
)


AUDIT_REPORT_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class AuditReportEntry:
    """Latest persisted audit result for one campaign item."""

    publication_id: str
    batch_id: str
    attempt: int
    result: AuditResult


@dataclass(frozen=True)
class AuditReport:
    """Human-reviewable machine-readable audit results for one campaign snapshot."""

    campaign_items: tuple[str, ...]
    entries: tuple[AuditReportEntry, ...] = ()


@dataclass(frozen=True)
class ProjectAuditBatchPlan:
    """Read-only plan for creating/resuming and opening one audit batch."""

    campaign: Campaign
    report: AuditReport
    batch: CampaignBatch | None
    outputs: Mapping[Path, bytes]

    @property
    def changed(self) -> bool:
        return bool(self.outputs)

    def summary(self) -> str:
        progress = campaign_progress(self.campaign)
        batch = self.batch.id if self.batch is not None else "none"
        return f"{progress.summary()}; current-batch: {batch}"


@dataclass(frozen=True)
class ProjectAuditCheckpointPlan:
    """Read-only plan for checkpointing one publication audit result."""

    campaign: Campaign
    report: AuditReport
    outputs: Mapping[Path, bytes]

    @property
    def changed(self) -> bool:
        return bool(self.outputs)


@dataclass(frozen=True)
class ProjectAuditClosePlan:
    """Read-only plan for explicitly closing one completed audit batch."""

    campaign: Campaign
    report: AuditReport
    outputs: Mapping[Path, bytes]

    @property
    def changed(self) -> bool:
        return bool(self.outputs)


@dataclass(frozen=True)
class ProjectAuditExecution:
    """Summary of one fully processed and closed audit batch."""

    batch_id: str
    processed_count: int
    completed_count: int
    retryable_count: int
    failed_count: int
    campaign: Campaign
    report: AuditReport

    def summary(self) -> str:
        progress = campaign_progress(self.campaign)
        campaign_processed = progress.completed + progress.retryable + progress.failed
        return (
            f"Audit batch {self.batch_id} complete\n"
            f"  This batch : {self.processed_count} processed "
            f"({self.completed_count} completed, "
            f"{self.retryable_count} retryable, {self.failed_count} failed)\n"
            f"  Campaign   : {campaign_processed} / {progress.total} processed "
            f"({progress.completed} completed, "
            f"{progress.retryable} retryable, {progress.failed} failed)\n"
            f"  Remaining  : {progress.pending} pending\n"
            f"  Batches    : {progress.batches_closed} closed"
        )


def _campaign_items(campaign: Campaign) -> tuple[str, ...]:
    return tuple(item.key for item in campaign.items)


def audit_report_data(report: AuditReport) -> dict[str, Any]:
    """Serialize one audit report deterministically in campaign item order."""
    if not isinstance(report, AuditReport):
        raise ProjectStateError("report must be an AuditReport")
    positions = {key: index for index, key in enumerate(report.campaign_items)}
    if len(positions) != len(report.campaign_items):
        raise ProjectStateError("audit report campaign_items must be unique")

    entries = sorted(
        report.entries,
        key=lambda entry: positions.get(entry.publication_id, len(positions)),
    )
    return {
        "schema_version": AUDIT_REPORT_SCHEMA_VERSION,
        "campaign_items": list(report.campaign_items),
        "results": [
            {
                "publication_id": entry.publication_id,
                "batch_id": entry.batch_id,
                "attempt": entry.attempt,
                "result": audit_result_data(entry.result),
            }
            for entry in entries
        ],
    }


def audit_report_from_data(value: Mapping[str, Any]) -> AuditReport:
    """Read and strictly validate one persisted audit report document."""
    if not isinstance(value, Mapping):
        raise ProjectStateError("audit report must be an object")
    required = {"schema_version", "campaign_items", "results"}
    missing = required - value.keys()
    unknown = value.keys() - required
    if missing:
        raise ProjectStateError(
            "audit report missing fields: " + ", ".join(sorted(missing))
        )
    if unknown:
        raise ProjectStateError(
            "audit report has unknown fields: " + ", ".join(sorted(unknown))
        )
    version = value["schema_version"]
    if (
        not isinstance(version, int)
        or isinstance(version, bool)
        or version != AUDIT_REPORT_SCHEMA_VERSION
    ):
        raise ProjectStateError(
            f"unsupported audit report schema version: {version!r}"
        )

    raw_items = value["campaign_items"]
    if (
        not isinstance(raw_items, list)
        or any(not isinstance(item, str) or not item for item in raw_items)
    ):
        raise ProjectStateError(
            "audit report campaign_items must be a list of non-empty strings"
        )
    campaign_items = tuple(raw_items)
    if len(set(campaign_items)) != len(campaign_items):
        raise ProjectStateError("audit report campaign_items must be unique")

    raw_results = value["results"]
    if not isinstance(raw_results, list):
        raise ProjectStateError("audit report results must be a list")
    entries: list[AuditReportEntry] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_results, 1):
        if not isinstance(raw, Mapping) or set(raw) != {
            "publication_id",
            "batch_id",
            "attempt",
            "result",
        }:
            raise ProjectStateError(
                f"audit report results[{index}] must contain publication_id, "
                "batch_id, attempt, and result"
            )
        publication_id = raw["publication_id"]
        batch_id = raw["batch_id"]
        attempt = raw["attempt"]
        if (
            not isinstance(publication_id, str)
            or publication_id not in campaign_items
        ):
            raise ProjectStateError(
                f"audit report results[{index}].publication_id is not a campaign item"
            )
        if publication_id in seen:
            raise ProjectStateError(
                f"audit report contains duplicate result for {publication_id}"
            )
        seen.add(publication_id)
        if not isinstance(batch_id, str) or not batch_id:
            raise ProjectStateError(
                f"audit report results[{index}].batch_id must be a non-empty string"
            )
        if (
            not isinstance(attempt, int)
            or isinstance(attempt, bool)
            or attempt <= 0
        ):
            raise ProjectStateError(
                f"audit report results[{index}].attempt must be a positive integer"
            )
        try:
            result = audit_result_from_data(raw["result"])
        except (AuditError, ValueError) as error:
            raise ProjectStateError(
                f"audit report results[{index}]: {error}"
            ) from error
        if result.publication_id != publication_id:
            raise ProjectStateError(
                f"audit report results[{index}]: result publication_id mismatch"
            )
        entries.append(
            AuditReportEntry(
                publication_id=publication_id,
                batch_id=batch_id,
                attempt=attempt,
                result=result,
            )
        )

    positions = {key: index for index, key in enumerate(campaign_items)}
    entries.sort(key=lambda entry: positions[entry.publication_id])
    return AuditReport(
        campaign_items=campaign_items,
        entries=tuple(entries),
    )


def _validate_report_against_campaign(
    campaign: Campaign,
    report: AuditReport,
) -> None:
    expected_items = _campaign_items(campaign)
    if report.campaign_items != expected_items:
        raise ProjectStateError(
            "audit campaign/report item snapshots do not match"
        )

    items = {item.key: item for item in campaign.items}
    appearances: dict[str, list[str]] = {key: [] for key in expected_items}
    for batch in campaign.batches:
        for key in batch.keys:
            appearances[key].append(batch.id)

    entries = {entry.publication_id: entry for entry in report.entries}
    for key, item in items.items():
        entry = entries.get(key)
        if item.state in {"completed", "retryable", "failed"} and entry is None:
            raise ProjectStateError(
                f"{key}: audit campaign has {item.state} state without report result"
            )
        if entry is None:
            continue
        if entry.attempt > item.attempts:
            raise ProjectStateError(
                f"{key}: audit report attempt exceeds campaign attempts"
            )
        history = appearances[key]
        if entry.attempt > len(history) or history[entry.attempt - 1] != entry.batch_id:
            raise ProjectStateError(
                f"{key}: audit report batch/attempt does not match campaign history"
            )
        if item.state != "active" and entry.attempt != item.attempts:
            raise ProjectStateError(
                f"{key}: audit report is stale for completed campaign item"
            )


def _read_state(config: BibReviewConfig) -> tuple[Campaign, AuditReport]:
    campaign_path = config.audit.campaign
    report_path = config.audit.report
    if campaign_path.exists() != report_path.exists():
        raise ProjectStateError(
            "audit campaign and report must either both exist or both be absent"
        )
    if not campaign_path.exists():
        raise ProjectStateError("audit campaign has not been started")

    try:
        campaign = campaign_from_data(read_json(campaign_path, dict))
    except (CampaignError, ValueError) as error:
        raise ProjectStateError(f"{campaign_path}: {error}") from error
    if campaign.kind != "audit":
        raise ProjectStateError(
            f"{campaign_path}: campaign kind is {campaign.kind!r}, expected 'audit'"
        )
    report = audit_report_from_data(read_json(report_path, dict))
    _validate_report_against_campaign(campaign, report)
    return campaign, report


def _put_if_changed(
    outputs: dict[Path, bytes],
    path: Path,
    content: bytes,
) -> None:
    if path.exists() and path.read_bytes() == content:
        return
    outputs[path] = content


def _state_outputs(
    config: BibReviewConfig,
    campaign: Campaign,
    report: AuditReport,
    *,
    include_report: bool = True,
) -> Mapping[Path, bytes]:
    outputs: dict[Path, bytes] = {}
    _put_if_changed(
        outputs,
        config.audit.campaign,
        json_bytes(campaign_data(campaign)),
    )
    if include_report:
        _put_if_changed(
            outputs,
            config.audit.report,
            json_bytes(audit_report_data(report)),
        )
    return MappingProxyType(outputs)


def plan_project_audit_batch(
    config: BibReviewConfig,
    *,
    batch_size: int | None = None,
) -> ProjectAuditBatchPlan:
    """Create/resume an audit campaign and open its stable current batch."""
    if not isinstance(config, BibReviewConfig):
        raise ProjectStateError("config must be a BibReviewConfig")

    campaign_path = config.audit.campaign
    report_path = config.audit.report
    if campaign_path.exists() != report_path.exists():
        raise ProjectStateError(
            "audit campaign and report must either both exist or both be absent"
        )

    if campaign_path.exists():
        campaign, report = _read_state(config)
    else:
        if not config.paths.bibliography.exists():
            raise ProjectStateError(
                f"{config.paths.bibliography}: canonical bibliography does not exist"
            )
        publications = read_bibliography(config.paths.bibliography)
        try:
            campaign = create_campaign(
                "audit",
                (publication.id for publication in publications),
                batch_size=config.audit.batch_size,
            )
        except CampaignError as error:
            raise ProjectStateError(str(error)) from error
        report = AuditReport(campaign_items=_campaign_items(campaign))

    try:
        updated, batch = open_next_batch(campaign, batch_size=batch_size)
    except CampaignError as error:
        raise ProjectStateError(str(error)) from error

    outputs = _state_outputs(config, updated, report)
    return ProjectAuditBatchPlan(
        campaign=updated,
        report=report,
        batch=batch,
        outputs=outputs,
    )


def plan_project_audit_checkpoint(
    config: BibReviewConfig,
    *,
    batch_id: str,
    result: AuditResult,
    state: str,
) -> ProjectAuditCheckpointPlan:
    """Checkpoint one item result and replace any older retry result."""
    if not isinstance(result, AuditResult):
        raise ProjectStateError("result must be an AuditResult")
    campaign, report = _read_state(config)

    try:
        updated_campaign = record_item_result(
            campaign,
            batch_id=batch_id,
            key=result.publication_id,
            state=state,
        )
    except CampaignError as error:
        raise ProjectStateError(str(error)) from error

    item = next(
        candidate
        for candidate in updated_campaign.items
        if candidate.key == result.publication_id
    )
    replacement = AuditReportEntry(
        publication_id=result.publication_id,
        batch_id=batch_id,
        attempt=item.attempts,
        result=result,
    )
    entries = [
        entry
        for entry in report.entries
        if entry.publication_id != result.publication_id
    ]
    entries.append(replacement)
    positions = {
        key: index
        for index, key in enumerate(report.campaign_items)
    }
    entries.sort(key=lambda entry: positions[entry.publication_id])
    updated_report = AuditReport(
        campaign_items=report.campaign_items,
        entries=tuple(entries),
    )
    _validate_report_against_campaign(updated_campaign, updated_report)

    return ProjectAuditCheckpointPlan(
        campaign=updated_campaign,
        report=updated_report,
        outputs=_state_outputs(config, updated_campaign, updated_report),
    )


def plan_project_audit_close(
    config: BibReviewConfig,
    *,
    batch_id: str,
) -> ProjectAuditClosePlan:
    """Close one fully checkpointed audit batch without touching report content."""
    campaign, report = _read_state(config)
    try:
        updated = close_batch(campaign, batch_id=batch_id)
    except CampaignError as error:
        raise ProjectStateError(str(error)) from error
    _validate_report_against_campaign(updated, report)

    return ProjectAuditClosePlan(
        campaign=updated,
        report=report,
        outputs=_state_outputs(
            config,
            updated,
            report,
            include_report=False,
        ),
    )


def apply_project_audit_plan(
    plan: ProjectAuditBatchPlan | ProjectAuditCheckpointPlan | ProjectAuditClosePlan,
) -> None:
    """Apply one prepared audit-state plan atomically per destination."""
    if not isinstance(
        plan,
        (ProjectAuditBatchPlan, ProjectAuditCheckpointPlan, ProjectAuditClosePlan),
    ):
        raise ProjectStateError("plan must be a project audit plan")
    if plan.outputs:
        atomic_write_batch(plan.outputs)



def _provider_failure_evidence(
    source: AuditEvidenceSource,
    error: Exception,
) -> ProviderEvidence:
    """Convert one provider failure into sanitized audit evidence."""
    if isinstance(error, HttpError):
        status = (
            "error"
            if error.status_code in {400, 401, 403}
            else "unavailable"
        )
        detail = str(error)
    else:
        status = "error"
        detail = "provider response could not be normalized"
    return ProviderEvidence(
        provider=source.name,
        status=status,
        detail=detail,
    )


def _missing_canonical_result(publication_id: str) -> AuditResult:
    """Represent an audit UUID that disappeared from current canonical state."""
    return AuditResult(
        publication_id=publication_id,
        identifiers=MappingProxyType({}),
        permalink="",
        title="",
        comparisons=(
            AuditComparison(
                provider="bibreview",
                field="publication_id",
                classification="identity-problem",
                canonical_value=publication_id,
                provider_value="missing-from-current-canonical-bibliography",
            ),
        ),
        provider_issues=(),
        disagreements=(),
    )


def execute_project_audit_batch(
    config: BibReviewConfig,
    *,
    batch_id: str,
    sources: tuple[AuditEvidenceSource, ...],
    reporter: Reporter | None = None,
) -> ProjectAuditExecution:
    """Process only still-active items in one already persisted audit batch.

    Every item is checkpointed immediately after comparison. If the process is
    interrupted, already-checkpointed items remain completed/retryable/failed
    while untouched items stay active in the same open batch.
    """
    progress_reporter = reporter or Reporter()
    campaign, report = _read_state(config)
    open_batches = [batch for batch in campaign.batches if not batch.closed]
    if len(open_batches) != 1 or open_batches[0].id != batch_id:
        raise ProjectStateError(
            f"{batch_id}: is not the current open audit batch"
        )
    batch = open_batches[0]

    if not isinstance(sources, tuple):
        sources = tuple(sources)
    if not sources:
        raise ProjectStateError("audit requires at least one evidence source")
    if any(not hasattr(source, "name") or not hasattr(source, "evidence") for source in sources):
        raise ProjectStateError("audit sources must provide name and evidence()")
    names = [source.name for source in sources]
    if len(set(names)) != len(names):
        raise ProjectStateError("audit source names must be unique")

    publications = {
        publication.id: publication
        for publication in read_bibliography(config.paths.bibliography)
    }
    states = {item.key: item.state for item in campaign.items}

    processed = 0
    completed = 0
    retryable = 0
    failed = 0

    for key in batch.keys:
        if states.get(key) != "active":
            continue

        publication = publications.get(key)
        if publication is None:
            result = _missing_canonical_result(key)
            state = "failed"
            progress_reporter.warning(
                f"audit {key}: canonical publication no longer exists; "
                "recorded as identity problem"
            )
        else:
            progress_reporter.step(
                f"Audit {publication.doi or publication.id}: {publication.title}"
            )
            record = publication_audit_record(publication)
            evidences: list[ProviderEvidence] = []
            had_provider_failure = False

            if publication.doi is None:
                evidences.extend(
                    ProviderEvidence(
                        provider=source.name,
                        status="unavailable",
                        detail="canonical publication has no DOI",
                    )
                    for source in sources
                )
            else:
                for source in sources:
                    try:
                        evidence = source.evidence(publication.doi)
                    except (HttpError, OSError, ValueError, TypeError) as error:
                        evidence = _provider_failure_evidence(source, error)
                        had_provider_failure = True
                        progress_reporter.warning(
                            f"{source.name}: {evidence.detail}"
                        )
                    evidences.append(evidence)

            result = compare_audit_record(record, tuple(evidences))
            state = "retryable" if had_provider_failure else "completed"

        checkpoint = plan_project_audit_checkpoint(
            config,
            batch_id=batch_id,
            result=result,
            state=state,
        )
        apply_project_audit_plan(checkpoint)
        campaign = checkpoint.campaign
        report = checkpoint.report
        states[key] = state
        processed += 1
        if state == "completed":
            completed += 1
        elif state == "retryable":
            retryable += 1
        else:
            failed += 1

    close = plan_project_audit_close(config, batch_id=batch_id)
    apply_project_audit_plan(close)

    return ProjectAuditExecution(
        batch_id=batch_id,
        processed_count=processed,
        completed_count=completed,
        retryable_count=retryable,
        failed_count=failed,
        campaign=close.campaign,
        report=close.report,
    )
