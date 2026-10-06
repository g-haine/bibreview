"""Resumable end-to-end initialization campaigns for new BibReview projects."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Iterable, Mapping

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
from .identifier_state import (
    IdentifierToken,
    doi_values,
    identifier_tokens_bytes,
    read_identifier_tokens,
)
from .identity import STRONG_IDENTIFIER_NAMES, normalize_doi
from .pipeline.discover import (
    EnrichmentLookup,
    WorkProvider,
    discover as discover_publications,
)
from .project import ProjectStateError
from .providers.http import HttpError
from .reporting import Reporter
from .storage import atomic_write_batch, json_bytes, read_bibliography, read_json


INIT_REPORT_SCHEMA_VERSION = 1
_INIT_OUTCOMES = frozenset({"queued", "review", "rejected", "skipped"})


@dataclass(frozen=True)
class InitReportEntry:
    """Persisted screening outcome for one initialization candidate."""

    doi: str
    batch_id: str
    attempt: int
    outcome: str

    def data(self) -> dict[str, Any]:
        return {
            "doi": self.doi,
            "batch_id": self.batch_id,
            "attempt": self.attempt,
            "outcome": self.outcome,
        }


@dataclass(frozen=True)
class InitReport:
    """Versioned screening history for one initialization campaign."""

    campaign_items: tuple[str, ...]
    entries: tuple[InitReportEntry, ...] = ()

    def data(self) -> dict[str, Any]:
        return {
            "schema_version": INIT_REPORT_SCHEMA_VERSION,
            "campaign_items": list(self.campaign_items),
            "entries": [entry.data() for entry in self.entries],
        }


@dataclass(frozen=True)
class ProjectInitBatchPlan:
    """Read-only plan for creating/resuming one initialization batch."""

    campaign: Campaign
    report: InitReport
    batch: CampaignBatch | None
    outputs: Mapping[Path, bytes]

    @property
    def changed(self) -> bool:
        return bool(self.outputs)

    @property
    def screening_keys(self) -> tuple[str, ...]:
        if self.batch is None:
            return ()
        outcomes = {entry.doi for entry in self.report.entries}
        states = {item.key: item.state for item in self.campaign.items}
        return tuple(
            key
            for key in self.batch.keys
            if states.get(key) == "active" and key not in outcomes
        )

    @property
    def needs_screening(self) -> bool:
        return bool(self.screening_keys)

    def summary(self) -> str:
        progress = campaign_progress(self.campaign)
        batch = self.batch.id if self.batch is not None else "none"
        return f"{progress.summary()}; current-batch: {batch}"


@dataclass(frozen=True)
class ProjectInitStatus:
    """Current initialization state derived from campaign plus ordinary project files."""

    total: int
    unscreened: int
    queued: int
    review: int
    staged: int
    merged: int
    rejected: int
    skipped: int
    retryable: int
    failed: int
    current_batch: str | None
    batches_opened: int
    batches_closed: int

    @property
    def complete(self) -> bool:
        return (
            self.unscreened == 0
            and self.queued == 0
            and self.review == 0
            and self.staged == 0
            and self.retryable == 0
            and self.current_batch is None
        )

    @property
    def successful(self) -> bool:
        return self.complete and self.failed == 0

    def data(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "unscreened": self.unscreened,
            "queued": self.queued,
            "review": self.review,
            "staged": self.staged,
            "merged": self.merged,
            "rejected": self.rejected,
            "skipped": self.skipped,
            "retryable": self.retryable,
            "failed": self.failed,
            "current_batch": self.current_batch,
            "batches_opened": self.batches_opened,
            "batches_closed": self.batches_closed,
            "complete": self.complete,
            "successful": self.successful,
        }

    def summary(self) -> str:
        return (
            "Initialization campaign\n"
            f"  Total         : {self.total}\n"
            f"  Unscreened    : {self.unscreened}\n"
            f"  Pending       : {self.queued}\n"
            f"  Manual review : {self.review}\n"
            f"  Staged        : {self.staged}\n"
            f"  Merged        : {self.merged}\n"
            f"  Rejected      : {self.rejected}\n"
            f"  Skipped       : {self.skipped}\n"
            f"  Retryable     : {self.retryable}\n"
            f"  Failed        : {self.failed}\n"
            f"  Batches       : {self.batches_closed}/{self.batches_opened}"
        )


@dataclass(frozen=True)
class ProjectInitExecution:
    """Summary after screening/reconciling one initialization batch."""

    batch_id: str
    screened: int
    queued: int
    review: int
    rejected: int
    skipped: int
    retryable: int
    campaign: Campaign
    report: InitReport
    status: ProjectInitStatus

    def summary(self) -> str:
        return (
            f"Initialization batch {self.batch_id}\n"
            f"  Screened      : {self.screened}\n"
            f"  Pending       : {self.queued}\n"
            f"  Manual review : {self.review}\n"
            f"  Rejected      : {self.rejected}\n"
            f"  Skipped       : {self.skipped}\n"
            f"  Retryable     : {self.retryable}\n"
            f"{self.status.summary()}"
        )


@dataclass(frozen=True)
class InitRescreenChange:
    """One proposed initialization relevance reclassification."""

    doi: str
    previous: str
    proposed: str

    def data(self) -> dict[str, str]:
        return {
            "doi": self.doi,
            "previous": self.previous,
            "proposed": self.proposed,
        }


@dataclass(frozen=True)
class ProjectInitRescreenPlan:
    """Read-only provider-backed rescreen plan for the current open batch."""

    batch_id: str
    screened: int
    queued: int
    review: int
    rejected: int
    unchanged: int
    retryable: int
    preserved: int
    changes: tuple[InitRescreenChange, ...]
    campaign: Campaign
    report: InitReport
    outputs: Mapping[Path, bytes]

    @property
    def changed(self) -> bool:
        return bool(self.outputs)

    def data(self) -> dict[str, Any]:
        return {
            "batch_id": self.batch_id,
            "screened": self.screened,
            "queued": self.queued,
            "review": self.review,
            "rejected": self.rejected,
            "unchanged": self.unchanged,
            "retryable": self.retryable,
            "preserved": self.preserved,
            "changes": [change.data() for change in self.changes],
        }

    def summary(self) -> str:
        return (
            f"Initialization rescreen {self.batch_id}\n"
            f"  Screened      : {self.screened}\n"
            f"  Pending       : {self.queued}\n"
            f"  Manual review : {self.review}\n"
            f"  Rejected      : {self.rejected}\n"
            f"  Unchanged     : {self.unchanged}\n"
            f"  Retryable     : {self.retryable}\n"
            f"  Preserved     : {self.preserved}\n"
            f"  Changes       : {len(self.changes)}"
        )


@dataclass(frozen=True)
class ProjectInitReviewContext:
    """Initialization context for one manual relevance-review candidate."""

    doi: str
    batch_id: str
    attempt: int

    def data(self) -> dict[str, Any]:
        return {
            "doi": self.doi,
            "batch_id": self.batch_id,
            "attempt": self.attempt,
        }


@dataclass(frozen=True)
class ProjectInitReviewTransitionPlan:
    """Initialization-state side of one explicit human relevance decision."""

    doi: str
    decision: str
    batch_id: str
    outputs: Mapping[Path, bytes]

    @property
    def changed(self) -> bool:
        return bool(self.outputs)


def _campaign_items(campaign: Campaign) -> tuple[str, ...]:
    return tuple(item.key for item in campaign.items)


def init_report_from_data(value: Any) -> InitReport:
    """Strictly load one initialization report."""
    if not isinstance(value, Mapping):
        raise ProjectStateError("initialization report must be an object")
    if value.get("schema_version") != INIT_REPORT_SCHEMA_VERSION:
        raise ProjectStateError(
            "unsupported initialization report schema_version: "
            f"{value.get('schema_version')!r}"
        )
    raw_items = value.get("campaign_items")
    raw_entries = value.get("entries")
    if not isinstance(raw_items, list) or any(
        not isinstance(item, str) or not item for item in raw_items
    ):
        raise ProjectStateError(
            "initialization report campaign_items must be a list of strings"
        )
    if not isinstance(raw_entries, list):
        raise ProjectStateError("initialization report entries must be a list")

    entries: list[InitReportEntry] = []
    for index, raw in enumerate(raw_entries, 1):
        if not isinstance(raw, Mapping):
            raise ProjectStateError(
                f"initialization report entry {index} must be an object"
            )
        doi = raw.get("doi")
        batch_id = raw.get("batch_id")
        attempt = raw.get("attempt")
        outcome = raw.get("outcome")
        if not isinstance(doi, str) or not doi:
            raise ProjectStateError(
                f"initialization report entry {index}.doi must be a non-empty string"
            )
        try:
            doi = normalize_doi(doi)
        except ValueError as error:
            raise ProjectStateError(
                f"invalid initialization report DOI {doi!r}: {error}"
            ) from error
        if not isinstance(batch_id, str) or not batch_id:
            raise ProjectStateError(
                f"initialization report entry {index}.batch_id must be a non-empty string"
            )
        if (
            not isinstance(attempt, int)
            or isinstance(attempt, bool)
            or attempt < 1
        ):
            raise ProjectStateError(
                f"initialization report entry {index}.attempt must be positive"
            )
        if outcome not in _INIT_OUTCOMES:
            raise ProjectStateError(
                f"initialization report entry {index}.outcome is unsupported"
            )
        entries.append(
            InitReportEntry(
                doi=doi,
                batch_id=batch_id,
                attempt=attempt,
                outcome=outcome,
            )
        )

    if len({entry.doi for entry in entries}) != len(entries):
        raise ProjectStateError("initialization report contains duplicate DOI entries")
    report = InitReport(
        campaign_items=tuple(raw_items),
        entries=tuple(entries),
    )
    return report


def _validate_report_against_campaign(
    campaign: Campaign,
    report: InitReport,
) -> None:
    if report.campaign_items != _campaign_items(campaign):
        raise ProjectStateError(
            "initialization report campaign_items do not match campaign state"
        )
    items = {item.key: item for item in campaign.items}
    history: dict[str, list[str]] = {key: [] for key in items}
    for batch in campaign.batches:
        for key in batch.keys:
            history[key].append(batch.id)

    entries = {entry.doi: entry for entry in report.entries}
    for doi, entry in entries.items():
        item = items.get(doi)
        if item is None:
            raise ProjectStateError(
                f"{doi}: initialization report entry is not in campaign"
            )
        if entry.attempt > item.attempts:
            raise ProjectStateError(
                f"{doi}: initialization report attempt exceeds campaign attempts"
            )
        appearances = history[doi]
        if (
            entry.attempt > len(appearances)
            or appearances[entry.attempt - 1] != entry.batch_id
        ):
            raise ProjectStateError(
                f"{doi}: initialization report batch/attempt mismatch"
            )
        if item.state != "active" and entry.attempt != item.attempts:
            raise ProjectStateError(
                f"{doi}: initialization report is stale for campaign item"
            )
    for item in campaign.items:
        if item.state == "completed" and item.key not in entries:
            raise ProjectStateError(
                f"{item.key}: completed initialization item has no report entry"
            )


def _read_state(config: BibReviewConfig) -> tuple[Campaign, InitReport]:
    campaign_path = config.initialization.campaign
    report_path = config.initialization.report
    if campaign_path.exists() != report_path.exists():
        raise ProjectStateError(
            "initialization campaign and report must either both exist or both be absent"
        )
    if not campaign_path.exists():
        raise ProjectStateError("initialization campaign has not been started")
    try:
        campaign = campaign_from_data(read_json(campaign_path, dict))
    except (CampaignError, ValueError) as error:
        raise ProjectStateError(f"{campaign_path}: {error}") from error
    if campaign.kind != "init":
        raise ProjectStateError(
            f"{campaign_path}: campaign kind is {campaign.kind!r}, expected 'init'"
        )
    report = init_report_from_data(read_json(report_path, dict))
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
    report: InitReport,
) -> Mapping[Path, bytes]:
    outputs: dict[Path, bytes] = {}
    _put_if_changed(
        outputs,
        config.initialization.campaign,
        json_bytes(campaign_data(campaign)),
    )
    _put_if_changed(
        outputs,
        config.initialization.report,
        json_bytes(report.data()),
    )
    return MappingProxyType(outputs)


def _queue_dois(path: Path) -> tuple[str, ...]:
    try:
        return doi_values(
            read_identifier_tokens(
                path,
                allowed_kinds=STRONG_IDENTIFIER_NAMES,
                allow_legacy_doi=True,
            )
        )
    except ValueError as error:
        raise ProjectStateError(str(error)) from error


def _doi_bytes(values: Iterable[str]) -> bytes:
    return identifier_tokens_bytes(
        IdentifierToken("doi", value) for value in values
    )


def _append_unique(values: list[str], addition: str) -> None:
    if addition not in values:
        values.append(addition)


def _canonical_dois(config: BibReviewConfig) -> set[str]:
    if not config.paths.bibliography.exists():
        return set()
    return {
        publication.doi
        for publication in read_bibliography(config.paths.bibliography)
        if publication.doi is not None
    }


def _staged_dois(config: BibReviewConfig) -> set[str]:
    if not config.paths.collected.exists():
        return set()
    return {
        publication.doi
        for publication in read_bibliography(config.paths.collected)
        if publication.doi is not None
    }


def _project_sets(config: BibReviewConfig) -> dict[str, set[str]]:
    values = {
        "queued": set(_queue_dois(config.paths.pending)),
        "review": set(_queue_dois(config.paths.review)),
        "rejected": set(_queue_dois(config.paths.rejected)),
        "staged": _staged_dois(config),
        "merged": _canonical_dois(config),
    }

    # Collection deliberately stages accepted pending DOI values without
    # removing them from newID.txt. Therefore queued/staged overlap is the
    # expected transient state between collect and merge.
    incompatible = (
        ("queued", "review"),
        ("queued", "rejected"),
        ("review", "rejected"),
        ("review", "staged"),
        ("review", "merged"),
        ("rejected", "staged"),
        ("rejected", "merged"),
        ("staged", "merged"),
    )
    for left, right in incompatible:
        overlap = values[left] & values[right]
        if overlap:
            sample = sorted(overlap)[0]
            raise ProjectStateError(
                f"{sample}: initialization candidate appears in both "
                f"{left} and {right} project state"
            )
    return values


def _ensure_startable(config: BibReviewConfig) -> None:
    canonical = (
        read_bibliography(config.paths.bibliography)
        if config.paths.bibliography.exists()
        else ()
    )
    if canonical:
        raise ProjectStateError(
            "initialization requires an empty canonical bibliography"
        )
    staged = (
        read_bibliography(config.paths.collected)
        if config.paths.collected.exists()
        else ()
    )
    if staged:
        raise ProjectStateError(
            "initialization requires empty collected staging"
        )
    for label, path in (
        ("pending", config.paths.pending),
        ("review", config.paths.review),
        ("rejected", config.paths.rejected),
    ):
        values = _queue_dois(path)
        if values:
            raise ProjectStateError(
                f"initialization requires an empty {label} acquisition queue"
            )
    if not config.discovery.query:
        raise ProjectStateError("discovery.query must not be empty")


def validate_project_init_start(config: BibReviewConfig) -> None:
    """Validate that a new initialization campaign may be created."""
    if not isinstance(config, BibReviewConfig):
        raise ProjectStateError("config must be a BibReviewConfig")
    if (
        config.initialization.campaign.exists()
        or config.initialization.report.exists()
    ):
        if (
            config.initialization.campaign.exists()
            != config.initialization.report.exists()
        ):
            raise ProjectStateError(
                "initialization campaign and report must either both exist or both be absent"
            )
        return
    _ensure_startable(config)


def _normalize_candidates(
    candidates: Iterable[str],
    *,
    excluded_doi_substrings: Iterable[str] = (),
) -> tuple[str, ...]:
    excluded = tuple(
        str(value).strip().lower()
        for value in excluded_doi_substrings
        if str(value).strip()
    )
    result: list[str] = []
    seen: set[str] = set()
    for raw in candidates:
        doi = normalize_doi(raw)
        if any(fragment in doi for fragment in excluded):
            continue
        if doi not in seen:
            seen.add(doi)
            result.append(doi)
    return tuple(result)


def _current_open_batch(campaign: Campaign) -> CampaignBatch | None:
    opened = [batch for batch in campaign.batches if not batch.closed]
    if len(opened) > 1:
        raise ProjectStateError("initialization campaign has more than one open batch")
    return opened[0] if opened else None


def _reconcile_open_batch(
    config: BibReviewConfig,
    campaign: Campaign,
    report: InitReport,
) -> Campaign:
    batch = _current_open_batch(campaign)
    if batch is None:
        return campaign
    entries = {entry.doi: entry for entry in report.entries}
    project = _project_sets(config)
    updated = campaign

    for key in batch.keys:
        item = next(candidate for candidate in updated.items if candidate.key == key)
        if item.state != "active":
            continue
        entry = entries.get(key)
        if entry is None:
            continue
        if entry.outcome == "skipped" or key in project["rejected"] or key in project["merged"]:
            try:
                updated = record_item_result(
                    updated,
                    batch_id=batch.id,
                    key=key,
                    state="completed",
                )
            except CampaignError as error:
                raise ProjectStateError(str(error)) from error
            continue
        if (
            key in project["queued"]
            or key in project["review"]
            or key in project["staged"]
        ):
            continue
        raise ProjectStateError(
            f"{key}: screened initialization candidate disappeared from "
            "pending/review/staging/canonical/rejected state"
        )
    return updated


def plan_project_init_batch(
    config: BibReviewConfig,
    *,
    candidates: Iterable[str] | None = None,
    batch_size: int | None = None,
) -> ProjectInitBatchPlan:
    """Create/resume an initialization campaign and open one stable batch."""
    if not isinstance(config, BibReviewConfig):
        raise ProjectStateError("config must be a BibReviewConfig")

    campaign_path = config.initialization.campaign
    report_path = config.initialization.report
    if campaign_path.exists() != report_path.exists():
        raise ProjectStateError(
            "initialization campaign and report must either both exist or both be absent"
        )

    if campaign_path.exists():
        campaign, report = _read_state(config)
    else:
        _ensure_startable(config)
        if candidates is None:
            raise ProjectStateError(
                "initialization candidate universe is required for a new campaign"
            )
        normalized = _normalize_candidates(
            candidates,
            excluded_doi_substrings=config.discovery.exclude_doi_substrings,
        )
        try:
            campaign = create_campaign(
                "init",
                normalized,
                batch_size=config.initialization.batch_size,
            )
        except (CampaignError, ValueError) as error:
            raise ProjectStateError(str(error)) from error
        report = InitReport(campaign_items=_campaign_items(campaign))

    campaign = _reconcile_open_batch(config, campaign, report)
    current = _current_open_batch(campaign)
    if current is not None:
        states = {item.key: item.state for item in campaign.items}
        if not any(states[key] == "active" for key in current.keys):
            try:
                campaign = close_batch(campaign, batch_id=current.id)
            except CampaignError as error:
                raise ProjectStateError(str(error)) from error

    try:
        campaign, batch = open_next_batch(campaign, batch_size=batch_size)
    except CampaignError as error:
        raise ProjectStateError(str(error)) from error

    _validate_report_against_campaign(campaign, report)
    return ProjectInitBatchPlan(
        campaign=campaign,
        report=report,
        batch=batch,
        outputs=_state_outputs(config, campaign, report),
    )


def apply_project_init_plan(plan: ProjectInitBatchPlan) -> None:
    """Persist one initialization planning/checkpoint state."""
    if not isinstance(plan, ProjectInitBatchPlan):
        raise ProjectStateError("plan must be a ProjectInitBatchPlan")
    if plan.outputs:
        atomic_write_batch(plan.outputs)


def _replace_report_entry(
    report: InitReport,
    entry: InitReportEntry,
) -> InitReport:
    entries = [item for item in report.entries if item.doi != entry.doi]
    entries.append(entry)
    positions = {
        key: index for index, key in enumerate(report.campaign_items)
    }
    entries.sort(key=lambda item: positions[item.doi])
    return InitReport(
        campaign_items=report.campaign_items,
        entries=tuple(entries),
    )


def project_init_review_context(
    config: BibReviewConfig,
    doi: str,
) -> ProjectInitReviewContext | None:
    """Return current init context when a DOI awaits human relevance review."""
    normalized = normalize_doi(doi)
    campaign_exists = config.initialization.campaign.exists()
    report_exists = config.initialization.report.exists()
    if campaign_exists != report_exists:
        raise ProjectStateError(
            "initialization campaign and report must either both exist or both be absent"
        )
    if not campaign_exists:
        return None

    campaign, report = _read_state(config)
    if normalized not in set(_campaign_items(campaign)):
        return None

    batch = _current_open_batch(campaign)
    if batch is None or normalized not in batch.keys:
        raise ProjectStateError(
            f"{normalized}: manual review belongs to initialization state "
            "outside the current open batch"
        )

    item = next(candidate for candidate in campaign.items if candidate.key == normalized)
    entry = next((candidate for candidate in report.entries if candidate.doi == normalized), None)
    if item.state != "active" or entry is None or entry.outcome != "review":
        raise ProjectStateError(
            f"{normalized}: initialization state does not currently require "
            "manual relevance review"
        )
    return ProjectInitReviewContext(
        doi=normalized,
        batch_id=batch.id,
        attempt=entry.attempt,
    )


def plan_project_init_review_transition(
    config: BibReviewConfig,
    *,
    doi: str,
    decision: str,
) -> ProjectInitReviewTransitionPlan | None:
    """Plan the initialization-state transition for one KEEP/REJECT decision."""
    normalized_decision = decision.strip().lower()
    if normalized_decision not in {"keep", "reject"}:
        raise ProjectStateError("relevance review decision must be 'keep' or 'reject'")

    context = project_init_review_context(config, doi)
    if context is None:
        return None

    campaign, report = _read_state(config)
    entry = next(item for item in report.entries if item.doi == context.doi)
    outcome = "queued" if normalized_decision == "keep" else "rejected"
    report = _replace_report_entry(
        report,
        InitReportEntry(
            doi=entry.doi,
            batch_id=entry.batch_id,
            attempt=entry.attempt,
            outcome=outcome,
        ),
    )

    if normalized_decision == "reject":
        try:
            campaign = record_item_result(
                campaign,
                batch_id=context.batch_id,
                key=context.doi,
                state="completed",
            )
            batch = _current_open_batch(campaign)
            if batch is not None and batch.id == context.batch_id:
                states = {item.key: item.state for item in campaign.items}
                if not any(states[key] == "active" for key in batch.keys):
                    campaign = close_batch(campaign, batch_id=batch.id)
        except CampaignError as error:
            raise ProjectStateError(str(error)) from error

    _validate_report_against_campaign(campaign, report)
    return ProjectInitReviewTransitionPlan(
        doi=context.doi,
        decision=normalized_decision,
        batch_id=context.batch_id,
        outputs=_state_outputs(config, campaign, report),
    )


def _checkpoint(
    config: BibReviewConfig,
    campaign: Campaign,
    report: InitReport,
    *,
    pending: list[str],
    review_queue: list[str],
    rejected: list[str],
) -> None:
    outputs = dict(_state_outputs(config, campaign, report))
    _put_if_changed(outputs, config.paths.pending, _doi_bytes(pending))
    _put_if_changed(outputs, config.paths.review, _doi_bytes(review_queue))
    _put_if_changed(outputs, config.paths.rejected, _doi_bytes(rejected))
    if outputs:
        atomic_write_batch(outputs)


def _attempt_for(campaign: Campaign, doi: str) -> int:
    return next(item.attempts for item in campaign.items if item.key == doi)


def plan_project_init_rescreen(
    config: BibReviewConfig,
    *,
    provider: WorkProvider,
    enrichment_lookup: EnrichmentLookup | None = None,
    reporter: Reporter | None = None,
) -> ProjectInitRescreenPlan:
    """Re-evaluate machine-screened active candidates in the current batch.

    The plan is read-only. Candidates already staged/merged/rejected are never
    revisited. A queue/report mismatch is treated as an explicit human decision
    and preserved rather than overwritten.
    """
    progress_reporter = reporter or Reporter()
    campaign, report = _read_state(config)
    campaign = _reconcile_open_batch(config, campaign, report)
    batch = _current_open_batch(campaign)
    if batch is None:
        raise ProjectStateError(
            "initialization campaign has no open batch to rescreen"
        )

    project = _project_sets(config)
    entries = {entry.doi: entry for entry in report.entries}
    pending = list(_queue_dois(config.paths.pending))
    review_queue = list(_queue_dois(config.paths.review))
    rejected = list(_queue_dois(config.paths.rejected))

    screened = queued_count = review_count = rejected_count = 0
    unchanged = retryable = preserved = 0
    changes: list[InitRescreenChange] = []

    for key in batch.keys:
        item = next(candidate for candidate in campaign.items if candidate.key == key)
        entry = entries.get(key)
        if item.state != "active" or entry is None:
            continue
        if (
            key in project["staged"]
            or key in project["merged"]
            or key in project["rejected"]
        ):
            continue

        if key in project["queued"]:
            previous = "queued"
        elif key in project["review"]:
            previous = "review"
        else:
            continue

        if entry.outcome not in {"queued", "review"} or entry.outcome != previous:
            preserved += 1
            continue

        progress_reporter.step(f"Rescreen {key}")
        try:
            result = discover_publications(
                (key,),
                provider=provider,
                known=(),
                rejected=(),
                patterns=config.relevance.patterns,
                reject_patterns=config.relevance.reject_patterns,
                unmatched=config.relevance.unmatched,
                accepted_types=config.discovery.accepted_types,
                excluded_doi_substrings=(),
                enrichment_lookup=enrichment_lookup,
                reporter=progress_reporter,
            )
        except (HttpError, OSError, ValueError, TypeError):
            retryable += 1
            continue

        if result.queued:
            proposed = "queued"
            queued_count += 1
        elif result.review:
            proposed = "review"
            review_count += 1
        elif result.rejected:
            proposed = "rejected"
            rejected_count += 1
        else:
            raise ProjectStateError(
                f"{key}: rescreen produced no applicable initialization outcome"
            )

        screened += 1
        if proposed == previous:
            unchanged += 1
            continue

        pending = [doi for doi in pending if doi != key]
        review_queue = [doi for doi in review_queue if doi != key]

        if proposed == "queued":
            _append_unique(pending, key)
        elif proposed == "review":
            _append_unique(review_queue, key)
        else:
            _append_unique(rejected, key)
            try:
                campaign = record_item_result(
                    campaign,
                    batch_id=batch.id,
                    key=key,
                    state="completed",
                )
            except CampaignError as error:
                raise ProjectStateError(str(error)) from error

        updated_entry = InitReportEntry(
            doi=key,
            batch_id=entry.batch_id,
            attempt=entry.attempt,
            outcome=proposed,
        )
        report = _replace_report_entry(report, updated_entry)
        entries[key] = updated_entry
        changes.append(
            InitRescreenChange(
                doi=key,
                previous=previous,
                proposed=proposed,
            )
        )

    states = {item.key: item.state for item in campaign.items}
    if not any(states[key] == "active" for key in batch.keys):
        try:
            campaign = close_batch(campaign, batch_id=batch.id)
        except CampaignError as error:
            raise ProjectStateError(str(error)) from error

    _validate_report_against_campaign(campaign, report)
    outputs = dict(_state_outputs(config, campaign, report))
    _put_if_changed(outputs, config.paths.pending, _doi_bytes(pending))
    _put_if_changed(outputs, config.paths.review, _doi_bytes(review_queue))
    _put_if_changed(outputs, config.paths.rejected, _doi_bytes(rejected))

    return ProjectInitRescreenPlan(
        batch_id=batch.id,
        screened=screened,
        queued=queued_count,
        review=review_count,
        rejected=rejected_count,
        unchanged=unchanged,
        retryable=retryable,
        preserved=preserved,
        changes=tuple(changes),
        campaign=campaign,
        report=report,
        outputs=MappingProxyType(outputs),
    )


def apply_project_init_rescreen(plan: ProjectInitRescreenPlan) -> None:
    """Apply one previously reviewed current-batch rescreen plan."""
    if not isinstance(plan, ProjectInitRescreenPlan):
        raise ProjectStateError("plan must be a ProjectInitRescreenPlan")
    if plan.outputs:
        atomic_write_batch(plan.outputs)


def project_init_status(config: BibReviewConfig) -> ProjectInitStatus:
    """Return current initialization progress without provider access."""
    campaign, report = _read_state(config)
    project = _project_sets(config)
    entries = {entry.doi: entry for entry in report.entries}
    campaign_keys = set(_campaign_items(campaign))
    skipped = {
        doi
        for doi, entry in entries.items()
        if entry.outcome == "skipped"
    }
    states = {item.key: item.state for item in campaign.items}
    unscreened = sum(
        state == "pending" or (state == "active" and doi not in entries)
        for doi, state in states.items()
    )
    current = _current_open_batch(campaign)
    progress = campaign_progress(campaign)
    staged = campaign_keys & project["staged"]
    merged = campaign_keys & project["merged"]
    queued = (campaign_keys & project["queued"]) - staged - merged
    return ProjectInitStatus(
        total=len(campaign.items),
        unscreened=unscreened,
        queued=len(queued),
        review=len(campaign_keys & project["review"]),
        staged=len(staged - merged),
        merged=len(merged),
        rejected=len(campaign_keys & project["rejected"]),
        skipped=len(skipped),
        retryable=progress.retryable,
        failed=progress.failed,
        current_batch=current.id if current is not None else None,
        batches_opened=progress.batches_opened,
        batches_closed=progress.batches_closed,
    )


def execute_project_init_batch(
    config: BibReviewConfig,
    *,
    batch_id: str,
    provider: WorkProvider,
    enrichment_lookup: EnrichmentLookup | None = None,
    reporter: Reporter | None = None,
) -> ProjectInitExecution:
    """Screen unscreened items in the persisted current initialization batch.

    Screened queued/review candidates remain active until ordinary collect/merge
    or explicit rejection resolves them. Each provider outcome is checkpointed
    immediately so interruption never repeats completed screening work.
    """
    progress_reporter = reporter or Reporter()
    campaign, report = _read_state(config)
    batch = _current_open_batch(campaign)
    if batch is None or batch.id != batch_id:
        raise ProjectStateError(
            f"{batch_id}: is not the current open initialization batch"
        )

    campaign = _reconcile_open_batch(config, campaign, report)
    entries = {entry.doi: entry for entry in report.entries}
    pending = list(_queue_dois(config.paths.pending))
    review_queue = list(_queue_dois(config.paths.review))
    rejected = list(_queue_dois(config.paths.rejected))
    _checkpoint(
        config,
        campaign,
        report,
        pending=pending,
        review_queue=review_queue,
        rejected=rejected,
    )

    screened = queued_count = review_count = rejected_count = 0
    skipped_count = retryable_count = 0

    for key in batch.keys:
        item = next(candidate for candidate in campaign.items if candidate.key == key)
        if item.state != "active" or key in entries:
            continue

        progress_reporter.step(f"Initialize {key}")
        try:
            result = discover_publications(
                (key,),
                provider=provider,
                known=(),
                rejected=(),
                patterns=config.relevance.patterns,
                reject_patterns=config.relevance.reject_patterns,
                unmatched=config.relevance.unmatched,
                accepted_types=config.discovery.accepted_types,
                excluded_doi_substrings=config.discovery.exclude_doi_substrings,
                enrichment_lookup=enrichment_lookup,
                reporter=progress_reporter,
            )
        except (HttpError, OSError, ValueError, TypeError) as error:
            detail = str(error) if isinstance(error, HttpError) else (
                "provider response could not be normalized"
            )
            try:
                campaign = record_item_result(
                    campaign,
                    batch_id=batch.id,
                    key=key,
                    state="retryable",
                    detail=detail,
                )
            except CampaignError as campaign_error:
                raise ProjectStateError(str(campaign_error)) from campaign_error
            retryable_count += 1
            screened += 1
            _checkpoint(
                config,
                campaign,
                report,
                pending=pending,
                review_queue=review_queue,
                rejected=rejected,
            )
            continue

        if result.queued:
            outcome = "queued"
            _append_unique(pending, key)
            queued_count += 1
        elif result.review:
            outcome = "review"
            _append_unique(review_queue, key)
            review_count += 1
        elif result.rejected:
            outcome = "rejected"
            _append_unique(rejected, key)
            rejected_count += 1
        elif result.skipped:
            outcome = "skipped"
            skipped_count += 1
        else:
            raise ProjectStateError(
                f"{key}: discovery produced no initialization outcome"
            )

        entry = InitReportEntry(
            doi=key,
            batch_id=batch.id,
            attempt=_attempt_for(campaign, key),
            outcome=outcome,
        )
        report = _replace_report_entry(report, entry)
        entries[key] = entry
        if outcome in {"rejected", "skipped"}:
            try:
                campaign = record_item_result(
                    campaign,
                    batch_id=batch.id,
                    key=key,
                    state="completed",
                )
            except CampaignError as error:
                raise ProjectStateError(str(error)) from error

        screened += 1
        _validate_report_against_campaign(campaign, report)
        _checkpoint(
            config,
            campaign,
            report,
            pending=pending,
            review_queue=review_queue,
            rejected=rejected,
        )

    campaign = _reconcile_open_batch(config, campaign, report)
    states = {item.key: item.state for item in campaign.items}
    if not any(states[key] == "active" for key in batch.keys):
        try:
            campaign = close_batch(campaign, batch_id=batch.id)
        except CampaignError as error:
            raise ProjectStateError(str(error)) from error
        _checkpoint(
            config,
            campaign,
            report,
            pending=pending,
            review_queue=review_queue,
            rejected=rejected,
        )

    status = project_init_status(config)
    return ProjectInitExecution(
        batch_id=batch.id,
        screened=screened,
        queued=queued_count,
        review=review_count,
        rejected=rejected_count,
        skipped=skipped_count,
        retryable=retryable_count,
        campaign=campaign,
        report=report,
        status=status,
    )
