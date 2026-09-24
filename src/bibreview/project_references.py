"""Project-state orchestration for resumable reference refresh inventories."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from .campaign import (
    Campaign,
    CampaignBatch,
    CampaignError,
    CampaignItem,
    campaign_data,
    campaign_from_data,
    campaign_progress,
    close_batch,
    create_campaign,
    open_next_batch,
    record_item_result,
)
from .config import BibReviewConfig
from .model import Publication
from .pipeline.collect import BatchWorkProvider
from .pipeline.references import (
    ReferenceReconstruction,
    ReferenceRefreshResult,
    compare_reference_reconstruction,
    reconstruct_provider_references,
    reference_refresh_result_from_data,
    references_fingerprint,
)
from .project import ProjectStateError
from .providers.http import HttpError
from .reporting import Reporter
from .storage import (
    atomic_write_batch,
    json_bytes,
    read_bibliography,
    read_json,
)


REFERENCES_REPORT_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class ReferenceReportEntry:
    """Latest persisted reference-refresh result for one campaign item."""

    publication_id: str
    batch_id: str
    attempt: int
    result: ReferenceRefreshResult


@dataclass(frozen=True)
class ReferenceReport:
    """Machine-readable reference-refresh results for one campaign snapshot."""

    campaign_items: tuple[str, ...]
    entries: tuple[ReferenceReportEntry, ...] = ()


@dataclass(frozen=True)
class ProjectReferencesBatchPlan:
    """Read-only plan for creating/resuming one reference-refresh batch."""

    campaign: Campaign
    report: ReferenceReport
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
class ProjectReferencesCheckpointPlan:
    """Plan for checkpointing one reference-refresh result."""

    campaign: Campaign
    report: ReferenceReport
    outputs: Mapping[Path, bytes]


@dataclass(frozen=True)
class ProjectReferencesClosePlan:
    """Plan for closing one fully checkpointed reference-refresh batch."""

    campaign: Campaign
    report: ReferenceReport
    outputs: Mapping[Path, bytes]


@dataclass(frozen=True)
class ProjectReferencesExecution:
    """Summary of one fully processed and closed reference-refresh batch."""

    batch_id: str
    processed_count: int
    completed_count: int
    retryable_count: int
    failed_count: int
    classifications: Mapping[str, int]
    campaign: Campaign
    report: ReferenceReport

    def summary(self) -> str:
        progress = campaign_progress(self.campaign)
        class_text = ", ".join(
            f"{name}: {count}"
            for name, count in self.classifications.items()
            if count
        ) or "none"
        return (
            f"Reference batch {self.batch_id} complete\n"
            f"  This batch : {self.processed_count} processed "
            f"({self.completed_count} completed, "
            f"{self.retryable_count} retryable, {self.failed_count} failed)\n"
            f"  Results    : {class_text}\n"
            f"  Campaign   : {progress.completed + progress.retryable + progress.failed} "
            f"/ {progress.total} processed\n"
            f"  Remaining  : {progress.pending} pending\n"
            f"  Batches    : {progress.batches_closed} closed"
        )


@dataclass(frozen=True)
class ProjectReferencesReview:
    """Offline summary derived from the persisted reference-refresh report."""

    audited_publications: int
    classification_counts: Mapping[str, int]
    safe_updates: int
    review_required: int
    unavailable: int
    items: tuple[ReferenceRefreshResult, ...]

    def summary(self) -> str:
        return (
            "Reference refresh review\n"
            f"  Audited publications : {self.audited_publications}\n"
            f"  Safe updates         : {self.safe_updates}\n"
            f"  Review required      : {self.review_required}\n"
            f"  Unavailable          : {self.unavailable}"
        )

    def data(self) -> dict[str, object]:
        return {
            "audited_publications": self.audited_publications,
            "classification_counts": dict(self.classification_counts),
            "safe_updates": self.safe_updates,
            "review_required": self.review_required,
            "unavailable": self.unavailable,
            "items": [item.data() for item in self.items],
        }


def _campaign_items(campaign: Campaign) -> tuple[str, ...]:
    return tuple(item.key for item in campaign.items)


def references_report_data(report: ReferenceReport) -> dict[str, Any]:
    """Serialize one reference-refresh report deterministically."""
    if not isinstance(report, ReferenceReport):
        raise ProjectStateError("report must be a ReferenceReport")
    positions = {key: index for index, key in enumerate(report.campaign_items)}
    if len(positions) != len(report.campaign_items):
        raise ProjectStateError("reference report campaign_items must be unique")
    entries = sorted(
        report.entries,
        key=lambda entry: positions.get(entry.publication_id, len(positions)),
    )
    return {
        "schema_version": REFERENCES_REPORT_SCHEMA_VERSION,
        "campaign_items": list(report.campaign_items),
        "results": [
            {
                "publication_id": entry.publication_id,
                "batch_id": entry.batch_id,
                "attempt": entry.attempt,
                "result": entry.result.data(),
            }
            for entry in entries
        ],
    }


def references_report_from_data(value: Mapping[str, Any]) -> ReferenceReport:
    """Strictly decode one persisted reference-refresh report."""
    if not isinstance(value, Mapping):
        raise ProjectStateError("reference report must be an object")
    if set(value) != {"schema_version", "campaign_items", "results"}:
        raise ProjectStateError("invalid reference report fields")
    version = value["schema_version"]
    if (
        not isinstance(version, int)
        or isinstance(version, bool)
        or version != REFERENCES_REPORT_SCHEMA_VERSION
    ):
        raise ProjectStateError(
            f"unsupported reference report schema version: {version!r}"
        )

    raw_items = value["campaign_items"]
    if (
        not isinstance(raw_items, list)
        or any(not isinstance(item, str) or not item for item in raw_items)
    ):
        raise ProjectStateError(
            "reference report campaign_items must contain non-empty strings"
        )
    campaign_items = tuple(raw_items)
    if len(set(campaign_items)) != len(campaign_items):
        raise ProjectStateError("reference report campaign_items must be unique")

    raw_results = value["results"]
    if not isinstance(raw_results, list):
        raise ProjectStateError("reference report results must be a list")

    entries: list[ReferenceReportEntry] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_results, 1):
        if (
            not isinstance(raw, Mapping)
            or set(raw) != {"publication_id", "batch_id", "attempt", "result"}
        ):
            raise ProjectStateError(
                f"reference report results[{index}] has invalid fields"
            )
        publication_id = raw["publication_id"]
        batch_id = raw["batch_id"]
        attempt = raw["attempt"]
        if (
            not isinstance(publication_id, str)
            or publication_id not in campaign_items
        ):
            raise ProjectStateError(
                f"reference report results[{index}].publication_id is not a campaign item"
            )
        if publication_id in seen:
            raise ProjectStateError(
                f"reference report contains duplicate result for {publication_id}"
            )
        seen.add(publication_id)
        if not isinstance(batch_id, str) or not batch_id:
            raise ProjectStateError(
                f"reference report results[{index}].batch_id must be non-empty"
            )
        if (
            not isinstance(attempt, int)
            or isinstance(attempt, bool)
            or attempt <= 0
        ):
            raise ProjectStateError(
                f"reference report results[{index}].attempt must be positive"
            )
        try:
            result = reference_refresh_result_from_data(raw["result"])
        except (TypeError, ValueError) as error:
            raise ProjectStateError(
                f"reference report results[{index}]: {error}"
            ) from error
        if result.publication_id != publication_id:
            raise ProjectStateError(
                f"reference report results[{index}]: publication_id mismatch"
            )
        entries.append(
            ReferenceReportEntry(
                publication_id=publication_id,
                batch_id=batch_id,
                attempt=attempt,
                result=result,
            )
        )

    positions = {key: index for index, key in enumerate(campaign_items)}
    entries.sort(key=lambda entry: positions[entry.publication_id])
    return ReferenceReport(
        campaign_items=campaign_items,
        entries=tuple(entries),
    )


def _validate_report_against_campaign(
    campaign: Campaign,
    report: ReferenceReport,
) -> None:
    expected = _campaign_items(campaign)
    if report.campaign_items != expected:
        raise ProjectStateError(
            "reference campaign/report item snapshots do not match"
        )
    items = {item.key: item for item in campaign.items}
    entries = {entry.publication_id: entry for entry in report.entries}
    appearances: dict[str, list[str]] = {key: [] for key in expected}
    for batch in campaign.batches:
        for key in batch.keys:
            appearances[key].append(batch.id)

    for key, item in items.items():
        entry = entries.get(key)
        if item.state in {"completed", "retryable", "failed"} and entry is None:
            raise ProjectStateError(
                f"{key}: reference campaign has {item.state} state without report result"
            )
        if entry is None:
            continue
        if entry.attempt > item.attempts:
            raise ProjectStateError(
                f"{key}: reference report attempt exceeds campaign attempts"
            )
        history = appearances[key]
        if entry.attempt > len(history) or history[entry.attempt - 1] != entry.batch_id:
            raise ProjectStateError(
                f"{key}: reference report batch/attempt does not match campaign history"
            )
        if item.state != "active" and entry.attempt != item.attempts:
            raise ProjectStateError(
                f"{key}: reference report is stale for completed campaign item"
            )


def _read_state(config: BibReviewConfig) -> tuple[Campaign, ReferenceReport]:
    campaign_path = config.references.campaign
    report_path = config.references.report
    if campaign_path.exists() != report_path.exists():
        raise ProjectStateError(
            "reference campaign and report must either both exist or both be absent"
        )
    if not campaign_path.exists():
        raise ProjectStateError("reference refresh campaign has not been started")
    try:
        campaign = campaign_from_data(read_json(campaign_path, dict))
    except (CampaignError, ValueError) as error:
        raise ProjectStateError(f"{campaign_path}: {error}") from error
    if campaign.kind != "references":
        raise ProjectStateError(
            f"{campaign_path}: campaign kind is {campaign.kind!r}, "
            "expected 'references'"
        )
    report = references_report_from_data(read_json(report_path, dict))
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
    report: ReferenceReport,
    *,
    include_report: bool = True,
) -> Mapping[Path, bytes]:
    outputs: dict[Path, bytes] = {}
    _put_if_changed(
        outputs,
        config.references.campaign,
        json_bytes(campaign_data(campaign)),
    )
    if include_report:
        _put_if_changed(
            outputs,
            config.references.report,
            json_bytes(references_report_data(report)),
        )
    return MappingProxyType(outputs)


def _synchronize_universe(
    campaign: Campaign,
    report: ReferenceReport,
    current_ids: tuple[str, ...],
    *,
    full: bool,
) -> tuple[Campaign, ReferenceReport]:
    existing = {item.key for item in campaign.items}
    additions = tuple(
        CampaignItem(key=key)
        for key in current_ids
        if key not in existing
    )
    synchronized = replace(campaign, items=campaign.items + additions)

    if full:
        progress = campaign_progress(synchronized)
        if progress.open_batch is not None:
            raise ProjectStateError(
                "--full cannot reset references while a batch is open; "
                "resume the current batch first"
            )
        current = set(current_ids)
        synchronized = replace(
            synchronized,
            items=tuple(
                replace(item, state="retryable", detail="")
                if item.key in current and item.attempts > 0
                else item
                for item in synchronized.items
            ),
        )

    updated_report = replace(
        report,
        campaign_items=_campaign_items(synchronized),
    )
    _validate_report_against_campaign(synchronized, updated_report)
    return synchronized, updated_report


def plan_project_references_batch(
    config: BibReviewConfig,
    *,
    batch_size: int | None = None,
    full: bool = False,
) -> ProjectReferencesBatchPlan:
    """Create/resume the reference-refresh campaign and open one stable batch."""
    if not isinstance(config, BibReviewConfig):
        raise ProjectStateError("config must be a BibReviewConfig")
    if not config.paths.bibliography.exists():
        raise ProjectStateError(
            f"{config.paths.bibliography}: canonical bibliography does not exist"
        )

    campaign_path = config.references.campaign
    report_path = config.references.report
    if campaign_path.exists() != report_path.exists():
        raise ProjectStateError(
            "reference campaign and report must either both exist or both be absent"
        )

    publications = read_bibliography(config.paths.bibliography)
    current_ids = tuple(publication.id for publication in publications)

    if campaign_path.exists():
        campaign, report = _read_state(config)
        campaign, report = _synchronize_universe(
            campaign,
            report,
            current_ids,
            full=full,
        )
    else:
        try:
            campaign = create_campaign(
                "references",
                current_ids,
                batch_size=config.references.batch_size,
            )
        except CampaignError as error:
            raise ProjectStateError(str(error)) from error
        report = ReferenceReport(campaign_items=_campaign_items(campaign))

    try:
        updated, batch = open_next_batch(campaign, batch_size=batch_size)
    except CampaignError as error:
        raise ProjectStateError(str(error)) from error

    return ProjectReferencesBatchPlan(
        campaign=updated,
        report=report,
        batch=batch,
        outputs=_state_outputs(config, updated, report),
    )


def plan_project_references_checkpoint(
    config: BibReviewConfig,
    *,
    batch_id: str,
    result: ReferenceRefreshResult,
    state: str,
) -> ProjectReferencesCheckpointPlan:
    """Checkpoint one publication result immediately."""
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
    replacement = ReferenceReportEntry(
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
        key: index for index, key in enumerate(report.campaign_items)
    }
    entries.sort(key=lambda entry: positions[entry.publication_id])
    updated_report = ReferenceReport(
        campaign_items=report.campaign_items,
        entries=tuple(entries),
    )
    _validate_report_against_campaign(updated_campaign, updated_report)
    return ProjectReferencesCheckpointPlan(
        campaign=updated_campaign,
        report=updated_report,
        outputs=_state_outputs(config, updated_campaign, updated_report),
    )


def plan_project_references_close(
    config: BibReviewConfig,
    *,
    batch_id: str,
) -> ProjectReferencesClosePlan:
    """Close one fully checkpointed reference-refresh batch."""
    campaign, report = _read_state(config)
    try:
        updated = close_batch(campaign, batch_id=batch_id)
    except CampaignError as error:
        raise ProjectStateError(str(error)) from error
    _validate_report_against_campaign(updated, report)
    return ProjectReferencesClosePlan(
        campaign=updated,
        report=report,
        outputs=_state_outputs(
            config,
            updated,
            report,
            include_report=False,
        ),
    )


def apply_project_references_plan(
    plan: (
        ProjectReferencesBatchPlan
        | ProjectReferencesCheckpointPlan
        | ProjectReferencesClosePlan
    ),
) -> None:
    """Persist reference-refresh campaign/report state only."""
    if not isinstance(
        plan,
        (
            ProjectReferencesBatchPlan,
            ProjectReferencesCheckpointPlan,
            ProjectReferencesClosePlan,
        ),
    ):
        raise ProjectStateError("plan must be a project references plan")
    if plan.outputs:
        atomic_write_batch(plan.outputs)


def _unavailable_result(
    publication: Publication,
    reason: str,
) -> ReferenceRefreshResult:
    reconstruction = ReferenceReconstruction(
        available=False,
        reason=reason,
    )
    return compare_reference_reconstruction(publication, reconstruction)


def _missing_canonical_result(publication_id: str) -> ReferenceRefreshResult:
    empty = references_fingerprint(())
    return ReferenceRefreshResult(
        publication_id=publication_id,
        doi=None,
        title="",
        classification="unavailable",
        reason="canonical-publication-missing",
        current_count=0,
        provider_count=0,
        changed_indices=(),
        current_fingerprint=empty,
        proposed_fingerprint=empty,
        proposed_references=(),
        provider_refusals=(),
    )


def _batch_capability(provider: BatchWorkProvider) -> tuple[int, Any]:
    size = getattr(provider, "BATCH_SIZE", 0)
    works = getattr(provider, "works", None)
    if (
        not isinstance(size, int)
        or isinstance(size, bool)
        or size < 1
        or not callable(works)
    ):
        raise ProjectStateError(
            "reference batch provider must expose positive BATCH_SIZE and works()"
        )
    return size, works


def _prefetch_work_messages(
    provider: BatchWorkProvider,
    dois: tuple[str, ...],
    *,
    reporter: Reporter,
) -> tuple[dict[str, Mapping[str, Any]], set[str]]:
    size, works = _batch_capability(provider)
    messages: dict[str, Mapping[str, Any]] = {}
    failed: set[str] = set()

    for offset in range(0, len(dois), size):
        chunk = dois[offset : offset + size]
        try:
            batch = works(chunk)
            if not isinstance(batch, Mapping):
                raise TypeError("batch work response must be a mapping")
            for doi in chunk:
                message = batch.get(doi)
                if message is not None and not isinstance(message, Mapping):
                    raise TypeError(
                        f"batch work response for {doi} must be a mapping"
                    )
                if isinstance(message, Mapping):
                    messages[doi] = message
        except (HttpError, OSError, ValueError, TypeError) as error:
            failed.update(chunk)
            reporter.warning(
                f"CrossRef reference batch of {len(chunk)} DOI values: {error}"
            )
    return messages, failed


def execute_project_references_batch(
    config: BibReviewConfig,
    *,
    batch_id: str,
    batch_provider: BatchWorkProvider,
    reporter: Reporter | None = None,
) -> ProjectReferencesExecution:
    """Process active items in one persisted reference-refresh batch."""
    progress_reporter = reporter or Reporter()
    campaign, report = _read_state(config)
    open_batches = [batch for batch in campaign.batches if not batch.closed]
    if len(open_batches) != 1 or open_batches[0].id != batch_id:
        raise ProjectStateError(
            f"{batch_id}: is not the current open references batch"
        )
    batch = open_batches[0]
    _batch_capability(batch_provider)

    publications = {
        publication.id: publication
        for publication in read_bibliography(config.paths.bibliography)
    }
    states = {item.key: item.state for item in campaign.items}
    active_dois = tuple(
        dict.fromkeys(
            publication.doi
            for key in batch.keys
            if states.get(key) == "active"
            for publication in (publications.get(key),)
            if publication is not None and publication.doi is not None
        )
    )
    messages, failed_dois = _prefetch_work_messages(
        batch_provider,
        active_dois,
        reporter=progress_reporter,
    )

    processed = completed = retryable = failed = 0
    classifications: Counter[str] = Counter()

    for key in batch.keys:
        if states.get(key) != "active":
            continue
        publication = publications.get(key)
        if publication is None:
            result = _missing_canonical_result(key)
            state = "failed"
        elif publication.doi is None:
            result = _unavailable_result(
                publication,
                "canonical-publication-without-doi",
            )
            state = "completed"
        elif publication.doi in failed_dois:
            result = _unavailable_result(
                publication,
                "provider-batch-error",
            )
            state = "retryable"
        else:
            message = messages.get(publication.doi)
            if message is None:
                result = _unavailable_result(
                    publication,
                    "provider-work-missing",
                )
            else:
                reconstruction = reconstruct_provider_references(message)
                result = compare_reference_reconstruction(
                    publication,
                    reconstruction,
                )
            state = "completed"

        progress_reporter.step(
            f"References {publication.doi if publication else key}: "
            f"{result.classification}"
        )
        checkpoint = plan_project_references_checkpoint(
            config,
            batch_id=batch_id,
            result=result,
            state=state,
        )
        apply_project_references_plan(checkpoint)
        campaign = checkpoint.campaign
        report = checkpoint.report
        states[key] = state
        processed += 1
        classifications[result.classification] += 1
        if state == "completed":
            completed += 1
        elif state == "retryable":
            retryable += 1
        else:
            failed += 1

    close = plan_project_references_close(config, batch_id=batch_id)
    apply_project_references_plan(close)

    ordered_counts = MappingProxyType(
        {
            name: classifications[name]
            for name in (
                "unchanged",
                "safe-update",
                "review-required",
                "unavailable",
            )
        }
    )
    return ProjectReferencesExecution(
        batch_id=batch_id,
        processed_count=processed,
        completed_count=completed,
        retryable_count=retryable,
        failed_count=failed,
        classifications=ordered_counts,
        campaign=close.campaign,
        report=close.report,
    )


def project_references_review(
    config: BibReviewConfig,
) -> ProjectReferencesReview:
    """Build an offline review from the persisted reference-refresh report."""
    _, report = _read_state(config)
    counts: Counter[str] = Counter(
        entry.result.classification for entry in report.entries
    )
    items = tuple(
        entry.result
        for entry in report.entries
        if entry.result.classification != "unchanged"
    )
    return ProjectReferencesReview(
        audited_publications=len(report.entries),
        classification_counts=MappingProxyType(dict(sorted(counts.items()))),
        safe_updates=counts["safe-update"],
        review_required=counts["review-required"],
        unavailable=counts["unavailable"],
        items=items,
    )


def format_project_references_review(
    review: ProjectReferencesReview,
    *,
    verbose: bool = False,
) -> str:
    """Format the offline reference-refresh review."""
    text = review.summary()
    if not verbose:
        return text

    lines = [text]
    for item in review.items:
        identity = item.doi or item.publication_id
        lines.extend(
            (
                "",
                f"{identity} — {item.title}",
                f"  Classification: {item.classification}",
                f"  Reason        : {item.reason}",
                f"  References    : {item.current_count} -> {item.provider_count}",
                "  Changed       : "
                + (
                    ", ".join(str(index) for index in item.changed_indices)
                    if item.changed_indices
                    else "(none)"
                ),
            )
        )
        if item.provider_refusals:
            lines.append(
                "  Provider refusals: "
                + ", ".join(
                    f"{index}:{reason}"
                    for index, reason in item.provider_refusals
                )
            )
    return "\n".join(lines)
