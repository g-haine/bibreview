"""Project-state orchestration for resumable reference refresh inventories."""

from __future__ import annotations

from collections import Counter
import re
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
from .citation_format import format_crossref_citations
from .config import BibReviewConfig
from .model import Publication, Reference
from .pipeline.collect import BatchWorkProvider
from .pipeline.references import (
    ReferenceReconstruction,
    ReferenceRefreshResult,
    compare_reference_reconstruction,
    reconstruct_provider_references,
    reconstruction_dois,
    reference_refresh_result_from_data,
    references_fingerprint,
    with_formatted_doi_citations,
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


REFERENCES_REPORT_SCHEMA_VERSION = 2


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
    cited_doi_count: int
    formatted_citation_count: int
    unavailable_citation_count: int
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
            f"  DOI round  : {self.cited_doi_count} unique, "
            f"{self.formatted_citation_count} formatted, "
            f"{self.unavailable_citation_count} unavailable\n"
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
    current_references: Mapping[str, tuple[Reference, ...]]
    explanations: Mapping[str, str]
    explanation_counts: Mapping[str, int]

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
            "explanation_counts": dict(self.explanation_counts),
            "items": [
                {**item.data(), "explanation": self.explanations.get(item.publication_id)}
                for item in self.items
            ],
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
        if version == 1:
            raise ProjectStateError(
                "reference report schema version 1 predates the DOI citation "
                "second round; archive both reference campaign/report files "
                "and start a fresh campaign"
            )
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
    label: str = "reference",
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
                f"CrossRef {label} batch of {len(chunk)} DOI values: {error}"
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
        label="parent-reference",
    )

    reconstructions: dict[str, ReferenceReconstruction] = {}
    for key in batch.keys:
        if states.get(key) != "active":
            continue
        publication = publications.get(key)
        if (
            publication is None
            or publication.doi is None
            or publication.doi in failed_dois
        ):
            continue
        message = messages.get(publication.doi)
        if message is not None:
            reconstructions[key] = reconstruct_provider_references(message)

    cited_dois = tuple(
        dict.fromkeys(
            doi
            for key in batch.keys
            for reconstruction in (reconstructions.get(key),)
            if reconstruction is not None and reconstruction.available
            for doi in reconstruction_dois(reconstruction)
        )
    )
    citation_messages, citation_failed_dois = _prefetch_work_messages(
        batch_provider,
        cited_dois,
        reporter=progress_reporter,
        label="cited-DOI metadata",
    )
    citation_batch = format_crossref_citations(citation_messages)
    formatted_citations = dict(citation_batch.citations)
    for doi, detail in citation_batch.errors.items():
        progress_reporter.warning(
            f"{doi}: {detail}"
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
                reconstruction = reconstructions.get(publication.id)
                if reconstruction is None:
                    reconstruction = reconstruct_provider_references(message)
                referenced_dois = set(reconstruction_dois(reconstruction))
                if referenced_dois & citation_failed_dois:
                    result = _unavailable_result(
                        publication,
                        "citation-metadata-batch-error",
                    )
                    state = "retryable"
                else:
                    reconstruction = with_formatted_doi_citations(
                        reconstruction,
                        formatted_citations,
                    )
                    result = compare_reference_reconstruction(
                        publication,
                        reconstruction,
                    )
                    state = "completed"

        identity = (
            publication.doi or publication.id
            if publication is not None
            else key
        )
        progress_reporter.step(
            f"References {identity}: {result.classification}"
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
        cited_doi_count=len(cited_dois),
        formatted_citation_count=len(formatted_citations),
        unavailable_citation_count=(
            len(cited_dois) - len(formatted_citations)
        ),
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
    publications = {
        publication.id: publication
        for publication in read_bibliography(config.paths.bibliography)
    }
    current_references = MappingProxyType(
        {
            item.publication_id: publications[item.publication_id].references
            for item in items
            if item.publication_id in publications
        }
    )
    explanations = {
        item.publication_id: _review_reference_explanation(
            item,
            current_references.get(item.publication_id, ()),
        )
        for item in items
        if item.classification == "review-required"
    }
    explanation_counts = Counter(explanations.values())
    return ProjectReferencesReview(
        audited_publications=len(report.entries),
        classification_counts=MappingProxyType(dict(sorted(counts.items()))),
        safe_updates=counts["safe-update"],
        review_required=counts["review-required"],
        unavailable=counts["unavailable"],
        items=items,
        current_references=current_references,
        explanations=MappingProxyType(explanations),
        explanation_counts=MappingProxyType(dict(sorted(explanation_counts.items()))),
    )


@dataclass(frozen=True)
class ReferenceReviewDiff:
    """One comparison-only alignment row for human review."""

    kind: str
    current_index: int | None
    provider_index: int | None
    current: Reference | None
    proposed: Reference | None


def _reference_doi(reference: Reference) -> str | None:
    return reference.identifiers.get("doi")


def _review_reference_alignment(
    current: tuple[Reference, ...],
    proposed: tuple[Reference, ...],
) -> tuple[ReferenceReviewDiff, ...]:
    """Align references for review without changing provider order.

    Unique DOI identity is used only as comparison evidence. A longest
    increasing subsequence of unique shared DOI matches provides stable
    anchors even when an earlier provider insertion would otherwise make a
    greedy match discard later correspondences. The returned rows follow
    provider order; canonical-only removals are inserted immediately before
    the next matched provider row (or at the end). Ambiguous/non-DOI regions
    are kept positional rather than guessed.
    """
    current_dois: dict[str, list[int]] = {}
    provider_dois: dict[str, list[int]] = {}
    for index, reference in enumerate(current):
        doi = _reference_doi(reference)
        if doi:
            current_dois.setdefault(doi, []).append(index)
    for index, reference in enumerate(proposed):
        doi = _reference_doi(reference)
        if doi:
            provider_dois.setdefault(doi, []).append(index)

    anchors = sorted(
        (
            provider_positions[0],
            current_dois[doi][0],
        )
        for doi, provider_positions in provider_dois.items()
        if len(provider_positions) == 1
        and len(current_dois.get(doi, ())) == 1
    )

    # Keep the largest order-preserving set of DOI anchors. A greedy pass is
    # insufficient: one moved/out-of-order DOI can otherwise hide many valid
    # later matches and recreate the positional cascade the review alignment
    # is intended to avoid.
    lengths = [1] * len(anchors)
    previous: list[int | None] = [None] * len(anchors)
    best = -1
    for index, (_, current_index) in enumerate(anchors):
        for candidate in range(index):
            if (
                anchors[candidate][1] < current_index
                and lengths[candidate] + 1 > lengths[index]
            ):
                lengths[index] = lengths[candidate] + 1
                previous[index] = candidate
        if best < 0 or lengths[index] > lengths[best]:
            best = index

    monotone: list[tuple[int, int]] = []
    while best >= 0:
        monotone.append(anchors[best])
        predecessor = previous[best]
        if predecessor is None:
            break
        best = predecessor
    monotone.reverse()

    rows: list[ReferenceReviewDiff] = []
    previous_provider = -1
    previous_current = -1
    for provider_anchor, current_anchor in (*monotone, (len(proposed), len(current))):
        provider_gap = list(range(previous_provider + 1, provider_anchor))
        current_gap = list(range(previous_current + 1, current_anchor))
        common = min(len(provider_gap), len(current_gap))

        for offset in range(common):
            provider_index = provider_gap[offset]
            current_index = current_gap[offset]
            left = current[current_index]
            right = proposed[provider_index]
            rows.append(
                ReferenceReviewDiff(
                    kind="changed" if left != right else "unchanged",
                    current_index=current_index + 1,
                    provider_index=provider_index + 1,
                    current=left,
                    proposed=right,
                )
            )
        for current_index in current_gap[common:]:
            rows.append(
                ReferenceReviewDiff(
                    kind="removed",
                    current_index=current_index + 1,
                    provider_index=None,
                    current=current[current_index],
                    proposed=None,
                )
            )
        for provider_index in provider_gap[common:]:
            rows.append(
                ReferenceReviewDiff(
                    kind="inserted",
                    current_index=None,
                    provider_index=provider_index + 1,
                    current=None,
                    proposed=proposed[provider_index],
                )
            )

        if provider_anchor < len(proposed):
            left = current[current_anchor]
            right = proposed[provider_anchor]
            rows.append(
                ReferenceReviewDiff(
                    kind=(
                        "unchanged"
                        if left == right
                        else "changed"
                    ),
                    current_index=current_anchor + 1,
                    provider_index=provider_anchor + 1,
                    current=left,
                    proposed=right,
                )
            )
        previous_provider = provider_anchor
        previous_current = current_anchor

    return tuple(rows)


_DOI_DASH_TRANSLATION = str.maketrans({
    "\u2010": "-",
    "\u2011": "-",
    "\u2012": "-",
    "\u2013": "-",
    "\u2014": "-",
    "\u2212": "-",
})


def _comparison_doi(doi: str | None) -> str | None:
    """Normalize typography only for DOI comparison evidence."""
    return doi.translate(_DOI_DASH_TRANSLATION).casefold() if doi else None


_CITATION_TOKEN_RE = re.compile(r"\w+", re.UNICODE)
_TRAILING_YEAR_RE = re.compile(r"\\s+\\(\\d{4}\\)$")


def _comparison_citation_tokens(citation: str) -> tuple[str, ...]:
    """Return lexical citation evidence while ignoring punctuation and spacing."""
    return tuple(_CITATION_TOKEN_RE.findall(citation.casefold()))


def _citation_formatting_equivalent(current: str, proposed: str) -> bool:
    """Recognize citation drift limited to punctuation, spacing, or case."""
    return _comparison_citation_tokens(current) == _comparison_citation_tokens(proposed)


def _citation_wrapper_artifact(current: str, proposed: str) -> bool:
    """Recognize a duplicated author/year wrapper around an intact citation."""
    position = current.find(proposed)
    if position <= 0:
        return False
    prefix = current[:position].strip()
    suffix = current[position + len(proposed):]
    return (
        len(prefix) <= 80
        and prefix.endswith(".")
        and bool(_comparison_citation_tokens(prefix))
        and (not suffix or _TRAILING_YEAR_RE.fullmatch(suffix) is not None)
    )


def _citation_metadata_enrichment(current: str, proposed: str) -> bool:
    """Recognize strict lexical enrichment without dropping canonical tokens."""
    current_tokens = Counter(_comparison_citation_tokens(current))
    proposed_tokens = Counter(_comparison_citation_tokens(proposed))
    return (
        bool(current_tokens)
        and current_tokens != proposed_tokens
        and current_tokens <= proposed_tokens
    )


def _citation_drift_evidence(current: Reference, proposed: Reference) -> str | None:
    """Classify deterministic identity/text evidence for one changed citation."""
    current_doi = _comparison_doi(_reference_doi(current))
    proposed_doi = _comparison_doi(_reference_doi(proposed))
    if current_doi is not None and current_doi == proposed_doi:
        return "same-doi"
    if current_doi is not None or proposed_doi is not None:
        return None
    if _citation_formatting_equivalent(current.citation, proposed.citation):
        return "formatting"
    if _citation_wrapper_artifact(current.citation, proposed.citation):
        return "wrapper-artifact"
    if _citation_metadata_enrichment(current.citation, proposed.citation):
        return "metadata-enrichment"
    return None


def _review_reference_explanation(
    item: ReferenceRefreshResult,
    current: tuple[Reference, ...],
) -> str:
    """Explain review-required drift without changing its safety classification."""
    proposed = item.proposed_references
    structural = len(current) != len(proposed)
    rows = _review_reference_alignment(current, proposed) if structural or item.reason.startswith("reference-identifiers-changed:") else ()

    if structural:
        inserted = [row for row in rows if row.kind == "inserted"]
        removed = [row for row in rows if row.kind == "removed"]
        changed = [row for row in rows if row.kind == "changed"]
        identities_stable = all(
            _comparison_doi(_reference_doi(row.current))
            == _comparison_doi(_reference_doi(row.proposed))
            for row in changed
            if row.current is not None and row.proposed is not None
        )
        if inserted and not removed and identities_stable:
            return "explained-provider-expansion"
        return "ambiguous-structural-drift"

    if item.reason.startswith("reference-identifiers-changed:"):
        mismatches = [
            row
            for row in rows
            if row.current is not None
            and row.proposed is not None
            and _reference_doi(row.current) != _reference_doi(row.proposed)
        ]
        if mismatches and all(
            _comparison_doi(_reference_doi(row.current))
            == _comparison_doi(_reference_doi(row.proposed))
            for row in mismatches
        ):
            return "identifier-typography-normalization"
        if mismatches and all(
            _reference_doi(row.current) is None
            and _reference_doi(row.proposed) is not None
            for row in mismatches
        ):
            return "provider-added-identifier"
        return "ambiguous-identifier-drift"

    if item.reason.startswith("reference-citation-drift:"):
        changed = [
            index
            for index in item.changed_indices
            if index <= len(current) and index <= len(proposed)
        ]
        evidence = [
            _citation_drift_evidence(current[index - 1], proposed[index - 1])
            for index in changed
        ]
        if changed and all(kind == "same-doi" for kind in evidence):
            # This is identity evidence only. It deliberately does not claim
            # that the citation text differs by formatting alone.
            return "same-doi-citation-drift"
        if changed and all(kind in {"same-doi", "formatting"} for kind in evidence):
            return "citation-formatting-drift"
        if (
            changed
            and "wrapper-artifact" in evidence
            and all(
                kind in {"same-doi", "formatting", "wrapper-artifact"}
                for kind in evidence
            )
        ):
            return "citation-wrapper-artifact"
        if (
            changed
            and "metadata-enrichment" in evidence
            and all(
                kind in {"same-doi", "formatting", "metadata-enrichment"}
                for kind in evidence
            )
        ):
            return "citation-metadata-enrichment"
        return "ambiguous-citation-drift"

    return "unclassified-review-drift"


def format_project_references_review(
    review: ProjectReferencesReview,
    *,
    verbose: int = 0,
) -> str:
    """Format the offline reference-refresh review."""
    text = review.summary()
    if verbose < 1:
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
                "  Explanation   : "
                + review.explanations.get(item.publication_id, "(not applicable)"),
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
        if verbose < 2:
            continue

        current = review.current_references.get(item.publication_id, ())
        proposed = item.proposed_references
        structural = (
            len(current) != len(proposed)
            or item.reason.startswith("reference-identifiers-changed:")
        )
        if structural:
            differences = tuple(
                row
                for row in _review_reference_alignment(current, proposed)
                if row.kind != "unchanged"
            )
        else:
            differences = tuple(
                ReferenceReviewDiff(
                    kind="changed",
                    current_index=index,
                    provider_index=index,
                    current=current[index - 1] if index <= len(current) else None,
                    proposed=proposed[index - 1] if index <= len(proposed) else None,
                )
                for index in item.changed_indices
            )

        for row in differences:
            left = row.current
            right = row.proposed
            left_doi = _reference_doi(left) if left is not None else None
            right_doi = _reference_doi(right) if right is not None else None
            current_position = row.current_index or "-"
            provider_position = row.provider_index or "-"
            lines.extend(
                (
                    "",
                    f"  Reference diff — {row.kind}",
                    f"    Current pos : {current_position}",
                    f"    Provider pos: {provider_position}",
                    f"    Current DOI : {left_doi or '(none)'}",
                    f"    Proposed DOI: {right_doi or '(none)'}",
                    "    Current     : "
                    + (left.citation if left is not None else "(missing)"),
                    "    Proposed    : "
                    + (right.citation if right is not None else "(missing)"),
                )
            )
    return "\n".join(lines)
