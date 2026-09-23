"""Resumable human decisions for safe refresh proposals."""

from __future__ import annotations

from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from .backfill_resolution import (
    BackfillResolutionCandidate,
    BackfillResolutionState,
    backfill_resolution_counts,
    backfill_resolution_state_from_data,
    format_backfill_resolution_candidate,
    record_backfill_resolution,
)
from .config import BibReviewConfig
from .project import ProjectStateError
from .project_refresh import (
    RefreshReview,
    load_project_refresh_review,
    refresh_review_fingerprint,
)
from .storage import read_json, write_json


def refresh_resolution_path(config: BibReviewConfig) -> Path:
    """Return refresh resolution state beside configured audit state."""
    path = config.audit.report.with_name("refresh-resolutions.json")
    if path in {config.audit.report, config.audit.campaign}:
        raise ProjectStateError("refresh resolution path collides with audit state")
    return path


def refresh_resolution_candidates(
    review: RefreshReview,
) -> tuple[BackfillResolutionCandidate, ...]:
    """Wrap safe refresh proposals in the shared backfill resolver model."""
    total = len(review.proposals)
    return tuple(
        BackfillResolutionCandidate(position=index, total=total, proposal=item)
        for index, item in enumerate(review.proposals, 1)
    )


def load_project_refresh_resolutions(
    config: BibReviewConfig,
    review: RefreshReview | None = None,
) -> BackfillResolutionState:
    """Load/resume decisions and reject stale state after refresh evidence changes."""
    review = review or load_project_refresh_review(config)
    fingerprint = refresh_review_fingerprint(review)
    candidates = refresh_resolution_candidates(review)
    path = refresh_resolution_path(config)

    if not path.exists():
        return BackfillResolutionState(
            review_fingerprint=fingerprint,
            total_proposals=len(candidates),
        )

    state = backfill_resolution_state_from_data(read_json(path, dict))
    if (
        state.review_fingerprint != fingerprint
        or state.total_proposals != len(candidates)
    ):
        raise ProjectStateError(
            f"{path}: refresh resolutions do not match the current refresh review; "
            "archive or remove the stale resolution file before resolving"
        )

    by_key = {item.key: item for item in candidates}
    for decision in state.decisions:
        candidate = by_key.get(decision.key)
        if candidate is None:
            raise ProjectStateError(
                f"{path}: resolved proposal is absent from current refresh review: "
                f"{decision.key}"
            )
        proposal = candidate.proposal
        if (
            decision.publication_id != proposal.publication_id
            or decision.doi != proposal.doi
            or decision.title != proposal.title
            or decision.field != proposal.field
        ):
            raise ProjectStateError(
                f"{path}: resolution metadata is inconsistent for {decision.key}"
            )
    return state


def save_project_refresh_resolutions(
    config: BibReviewConfig,
    state: BackfillResolutionState,
) -> None:
    """Persist resumable refresh decisions."""
    write_json(refresh_resolution_path(config), state.data())


def unresolved_refresh_candidates(
    review: RefreshReview,
    state: BackfillResolutionState,
) -> tuple[BackfillResolutionCandidate, ...]:
    """Return unresolved and deferred safe refresh proposals."""
    terminal = {
        item.key
        for item in state.decisions
        if item.decision in {"accepted", "custom", "rejected"}
    }
    return tuple(
        candidate
        for candidate in refresh_resolution_candidates(review)
        if candidate.key not in terminal
    )


def refresh_resolution_summary(state: BackfillResolutionState) -> str:
    """Return a refresh-specific summary using shared decision counts."""
    counts: Mapping[str, int] = backfill_resolution_counts(state)
    return (
        "Refresh resolution\n"
        f"  Proposals  : {state.total_proposals}\n"
        f"  Accepted   : {counts['accepted']}\n"
        f"  Custom     : {counts['custom']}\n"
        f"  Rejected   : {counts['rejected']}\n"
        f"  Deferred   : {counts['deferred']}\n"
        f"  Unresolved : {counts['unresolved']}"
    )


__all__ = [
    "BackfillResolutionCandidate",
    "BackfillResolutionState",
    "backfill_resolution_counts",
    "format_backfill_resolution_candidate",
    "load_project_refresh_resolutions",
    "record_backfill_resolution",
    "refresh_resolution_candidates",
    "refresh_resolution_path",
    "refresh_resolution_summary",
    "save_project_refresh_resolutions",
    "unresolved_refresh_candidates",
]
