"""Resumable human decisions for missing-field backfill proposals."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from textwrap import fill
from types import MappingProxyType
from typing import Any, Mapping

from .config import BibReviewConfig
from .pipeline.backfill import BackfillCandidate
from .project import ProjectStateError
from .project_backfill import (
    BackfillReview,
    backfill_review_fingerprint,
    load_project_backfill_review,
)
from .storage import read_json, write_json


BACKFILL_RESOLUTION_SCHEMA_VERSION = 1
_DECISIONS = frozenset({"accepted", "custom", "rejected", "deferred"})


@dataclass(frozen=True)
class BackfillResolutionCandidate:
    """One missing-field proposal presented for explicit human review."""

    position: int
    total: int
    proposal: BackfillCandidate

    @property
    def key(self) -> str:
        return self.proposal.key


@dataclass(frozen=True)
class BackfillResolutionDecision:
    """Persisted human decision for one backfill proposal."""

    key: str
    publication_id: str
    doi: str
    title: str
    field: str
    decision: str
    resolved_value: str | None

    def data(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "publication_id": self.publication_id,
            "doi": self.doi,
            "title": self.title,
            "field": self.field,
            "decision": self.decision,
            "resolved_value": self.resolved_value,
        }


@dataclass(frozen=True)
class BackfillResolutionState:
    """Versioned decisions tied to one exact persisted backfill review."""

    review_fingerprint: str
    total_proposals: int
    decisions: tuple[BackfillResolutionDecision, ...] = ()

    def data(self) -> dict[str, Any]:
        return {
            "schema_version": BACKFILL_RESOLUTION_SCHEMA_VERSION,
            "review_fingerprint": self.review_fingerprint,
            "total_proposals": self.total_proposals,
            "decisions": [item.data() for item in self.decisions],
        }

    def summary(self) -> str:
        counts = backfill_resolution_counts(self)
        return (
            "Backfill resolution\n"
            f"  Proposals  : {self.total_proposals}\n"
            f"  Accepted   : {counts['accepted']}\n"
            f"  Custom     : {counts['custom']}\n"
            f"  Rejected   : {counts['rejected']}\n"
            f"  Deferred   : {counts['deferred']}\n"
            f"  Unresolved : {counts['unresolved']}"
        )


def backfill_resolution_path(config: BibReviewConfig) -> Path:
    """Return resolution state beside the configured audit report."""
    path = config.audit.report.with_name("backfill-resolutions.json")
    if path in {config.audit.report, config.audit.campaign}:
        raise ProjectStateError("backfill resolution path collides with audit state")
    return path


def backfill_resolution_candidates(
    review: BackfillReview,
) -> tuple[BackfillResolutionCandidate, ...]:
    """Return deterministic human-review candidates."""
    total = len(review.candidates)
    return tuple(
        BackfillResolutionCandidate(position=index, total=total, proposal=item)
        for index, item in enumerate(review.candidates, 1)
    )


def backfill_resolution_state_from_data(value: Any) -> BackfillResolutionState:
    if not isinstance(value, Mapping):
        raise ProjectStateError("backfill resolutions must be an object")
    if value.get("schema_version") != BACKFILL_RESOLUTION_SCHEMA_VERSION:
        raise ProjectStateError(
            "unsupported backfill resolution schema_version: "
            f"{value.get('schema_version')!r}"
        )
    fingerprint = value.get("review_fingerprint")
    total = value.get("total_proposals")
    raw_decisions = value.get("decisions")
    if not isinstance(fingerprint, str) or len(fingerprint) != 64:
        raise ProjectStateError(
            "backfill resolutions review_fingerprint must be a SHA-256 string"
        )
    if not isinstance(total, int) or isinstance(total, bool) or total < 0:
        raise ProjectStateError(
            "backfill resolutions total_proposals must be non-negative"
        )
    if not isinstance(raw_decisions, list):
        raise ProjectStateError("backfill resolutions decisions must be a list")

    decisions: list[BackfillResolutionDecision] = []
    for index, raw in enumerate(raw_decisions, 1):
        if not isinstance(raw, Mapping):
            raise ProjectStateError(
                f"backfill resolution decision {index} must be an object"
            )
        strings: dict[str, str] = {}
        for name in ("key", "publication_id", "doi", "title", "field", "decision"):
            item = raw.get(name)
            if not isinstance(item, str) or (name != "title" and not item):
                raise ProjectStateError(
                    f"backfill resolution decision {index}.{name} must be a string"
                )
            strings[name] = item
        decision = strings["decision"]
        if decision not in _DECISIONS:
            raise ProjectStateError(
                f"backfill resolution decision {index}.decision is unsupported"
            )
        resolved = raw.get("resolved_value")
        if resolved is not None and not isinstance(resolved, str):
            raise ProjectStateError(
                f"backfill resolution decision {index}.resolved_value must be a string or null"
            )
        if decision in {"accepted", "custom"} and not resolved:
            raise ProjectStateError(
                f"backfill resolution decision {index} requires resolved_value"
            )
        if decision in {"rejected", "deferred"} and resolved is not None:
            raise ProjectStateError(
                f"backfill resolution decision {index} must not define resolved_value"
            )
        if strings["key"] != f"{strings['publication_id']}:{strings['field']}":
            raise ProjectStateError(
                f"backfill resolution decision {index}.key is inconsistent"
            )
        decisions.append(
            BackfillResolutionDecision(
                key=strings["key"],
                publication_id=strings["publication_id"],
                doi=strings["doi"],
                title=strings["title"],
                field=strings["field"],
                decision=decision,
                resolved_value=resolved,
            )
        )
    keys = [item.key for item in decisions]
    if len(keys) != len(set(keys)):
        raise ProjectStateError("backfill resolutions contain duplicate decision keys")
    if len(decisions) > total:
        raise ProjectStateError(
            "backfill resolutions contain more decisions than proposals"
        )
    return BackfillResolutionState(
        review_fingerprint=fingerprint,
        total_proposals=total,
        decisions=tuple(decisions),
    )


def load_project_backfill_resolutions(
    config: BibReviewConfig,
    review: BackfillReview | None = None,
) -> BackfillResolutionState:
    """Load/resume decisions and reject stale state after proposal changes."""
    review = review or load_project_backfill_review(config)
    fingerprint = backfill_review_fingerprint(review)
    candidates = backfill_resolution_candidates(review)
    path = backfill_resolution_path(config)

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
            f"{path}: backfill resolutions do not match the current proposal set; "
            "archive or remove the stale resolution file before resolving"
        )

    by_key = {item.key: item for item in candidates}
    for decision in state.decisions:
        candidate = by_key.get(decision.key)
        if candidate is None:
            raise ProjectStateError(
                f"{path}: resolved proposal is absent from current review: {decision.key}"
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
        if proposal.review_required and decision.decision == "accepted":
            raise ProjectStateError(
                f"{path}: review-required proposal cannot be accepted directly: "
                f"{decision.key}"
            )
    return state


def record_backfill_resolution(
    state: BackfillResolutionState,
    candidate: BackfillResolutionCandidate,
    *,
    decision: str,
    resolved_value: str | None = None,
) -> BackfillResolutionState:
    """Record or replace one human decision."""
    if decision not in _DECISIONS:
        raise ProjectStateError(f"unsupported backfill decision: {decision}")
    if decision == "accepted":
        resolved_value = candidate.proposal.proposed_value
    elif decision == "custom":
        if not isinstance(resolved_value, str) or not resolved_value.strip():
            raise ProjectStateError("custom backfill resolution requires a value")
        resolved_value = resolved_value.strip()
    else:
        resolved_value = None

    proposal = candidate.proposal
    item = BackfillResolutionDecision(
        key=candidate.key,
        publication_id=proposal.publication_id,
        doi=proposal.doi,
        title=proposal.title,
        field=proposal.field,
        decision=decision,
        resolved_value=resolved_value,
    )
    decisions = list(state.decisions)
    for index, existing in enumerate(decisions):
        if existing.key == item.key:
            decisions[index] = item
            break
    else:
        decisions.append(item)
    return replace(state, decisions=tuple(decisions))


def save_project_backfill_resolutions(
    config: BibReviewConfig,
    state: BackfillResolutionState,
) -> None:
    """Persist resumable backfill decisions."""
    write_json(backfill_resolution_path(config), state.data())


def backfill_resolution_counts(
    state: BackfillResolutionState,
) -> Mapping[str, int]:
    """Return deterministic resolution progress counts."""
    counts = {name: 0 for name in sorted(_DECISIONS)}
    for item in state.decisions:
        counts[item.decision] += 1
    counts["unresolved"] = (
        state.total_proposals
        - counts["accepted"]
        - counts["custom"]
        - counts["rejected"]
        - counts["deferred"]
    )
    return MappingProxyType(counts)


def unresolved_backfill_candidates(
    review: BackfillReview,
    state: BackfillResolutionState,
) -> tuple[BackfillResolutionCandidate, ...]:
    """Return unresolved and deferred proposals for the next session."""
    terminal = {
        item.key
        for item in state.decisions
        if item.decision in {"accepted", "custom", "rejected"}
    }
    return tuple(
        candidate
        for candidate in backfill_resolution_candidates(review)
        if candidate.key not in terminal
    )


def format_backfill_resolution_candidate(
    candidate: BackfillResolutionCandidate,
) -> str:
    """Format one proposal for interactive human review."""
    proposal = candidate.proposal
    rendered = fill(
        proposal.proposed_value,
        width=100,
        initial_indent="  ",
        subsequent_indent="  ",
    )
    return "\n".join(
        (
            f"[{candidate.position}/{candidate.total}] {proposal.doi} — {proposal.title}",
            "",
            f"Field: {proposal.field}",
            "Current:",
            "  (missing)",
            "",
            "Proposed:",
            rendered,
        )
    )
