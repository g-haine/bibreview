"""Resumable human decisions for canonical abstract hygiene migration."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from textwrap import fill
from types import MappingProxyType
from typing import Any, Mapping

from .config import BibReviewConfig
from .project import ProjectStateError
from .project_hygiene import (
    HygieneProposal,
    HygieneReview,
    hygiene_review_fingerprint,
    load_project_hygiene_review,
)
from .storage import read_json, write_json


HYGIENE_RESOLUTION_SCHEMA_VERSION = 1
_DECISIONS = frozenset({"accepted", "custom", "rejected", "deferred"})


@dataclass(frozen=True)
class HygieneResolutionCandidate:
    """One canonical hygiene proposal presented for human review."""

    position: int
    total: int
    proposal: HygieneProposal

    @property
    def key(self) -> str:
        return self.proposal.key


@dataclass(frozen=True)
class HygieneResolutionDecision:
    """Persisted human decision for one hygiene proposal."""

    key: str
    publication_id: str
    doi: str
    title: str
    decision: str
    resolved_value: str | None

    def data(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "publication_id": self.publication_id,
            "doi": self.doi,
            "title": self.title,
            "decision": self.decision,
            "resolved_value": self.resolved_value,
        }


@dataclass(frozen=True)
class HygieneResolutionState:
    """Versioned decisions tied to one exact hygiene review."""

    review_fingerprint: str
    total_proposals: int
    decisions: tuple[HygieneResolutionDecision, ...] = ()

    def data(self) -> dict[str, Any]:
        return {
            "schema_version": HYGIENE_RESOLUTION_SCHEMA_VERSION,
            "review_fingerprint": self.review_fingerprint,
            "total_proposals": self.total_proposals,
            "decisions": [item.data() for item in self.decisions],
        }

    def summary(self) -> str:
        counts = hygiene_resolution_counts(self)
        return (
            "Hygiene resolution\n"
            f"  Proposals  : {self.total_proposals}\n"
            f"  Accepted   : {counts['accepted']}\n"
            f"  Custom     : {counts['custom']}\n"
            f"  Rejected   : {counts['rejected']}\n"
            f"  Deferred   : {counts['deferred']}\n"
            f"  Unresolved : {counts['unresolved']}"
        )


def hygiene_resolution_path(config: BibReviewConfig) -> Path:
    """Return hygiene resolution state beside audit files."""
    path = config.audit.report.with_name("hygiene-resolutions.json")
    if path in {config.audit.report, config.audit.campaign}:
        raise ProjectStateError("hygiene resolution path collides with audit state")
    return path


def hygiene_resolution_candidates(
    review: HygieneReview,
) -> tuple[HygieneResolutionCandidate, ...]:
    """Return deterministic human-review candidates."""
    total = len(review.proposals)
    return tuple(
        HygieneResolutionCandidate(position=index, total=total, proposal=item)
        for index, item in enumerate(review.proposals, 1)
    )


def hygiene_resolution_state_from_data(value: Any) -> HygieneResolutionState:
    """Strictly load persisted hygiene resolution state."""
    if not isinstance(value, Mapping):
        raise ProjectStateError("hygiene resolutions must be an object")
    if value.get("schema_version") != HYGIENE_RESOLUTION_SCHEMA_VERSION:
        raise ProjectStateError(
            "unsupported hygiene resolution schema_version: "
            f"{value.get('schema_version')!r}"
        )

    fingerprint = value.get("review_fingerprint")
    total = value.get("total_proposals")
    raw_decisions = value.get("decisions")
    if not isinstance(fingerprint, str) or len(fingerprint) != 64:
        raise ProjectStateError(
            "hygiene resolutions review_fingerprint must be a SHA-256 string"
        )
    if not isinstance(total, int) or isinstance(total, bool) or total < 0:
        raise ProjectStateError(
            "hygiene resolutions total_proposals must be non-negative"
        )
    if not isinstance(raw_decisions, list):
        raise ProjectStateError("hygiene resolutions decisions must be a list")

    decisions: list[HygieneResolutionDecision] = []
    for index, raw in enumerate(raw_decisions, 1):
        if not isinstance(raw, Mapping):
            raise ProjectStateError(
                f"hygiene resolution decision {index} must be an object"
            )

        strings: dict[str, str] = {}
        for name in ("key", "publication_id", "doi", "title", "decision"):
            item = raw.get(name)
            if not isinstance(item, str):
                raise ProjectStateError(
                    f"hygiene resolution decision {index}.{name} must be a string"
                )
            if name in {"key", "publication_id", "decision"} and not item:
                raise ProjectStateError(
                    f"hygiene resolution decision {index}.{name} must not be empty"
                )
            strings[name] = item

        decision = strings["decision"]
        if decision not in _DECISIONS:
            raise ProjectStateError(
                f"hygiene resolution decision {index}.decision is unsupported"
            )

        resolved = raw.get("resolved_value")
        if resolved is not None and not isinstance(resolved, str):
            raise ProjectStateError(
                f"hygiene resolution decision {index}.resolved_value "
                "must be a string or null"
            )
        if decision in {"accepted", "custom"} and not resolved:
            raise ProjectStateError(
                f"hygiene resolution decision {index} requires resolved_value"
            )
        if decision in {"rejected", "deferred"} and resolved is not None:
            raise ProjectStateError(
                f"hygiene resolution decision {index} must not define resolved_value"
            )
        if strings["key"] != f"{strings['publication_id']}:abstract":
            raise ProjectStateError(
                f"hygiene resolution decision {index}.key is inconsistent"
            )

        decisions.append(
            HygieneResolutionDecision(
                key=strings["key"],
                publication_id=strings["publication_id"],
                doi=strings["doi"],
                title=strings["title"],
                decision=decision,
                resolved_value=resolved,
            )
        )

    keys = [item.key for item in decisions]
    if len(keys) != len(set(keys)):
        raise ProjectStateError("hygiene resolutions contain duplicate decision keys")
    if len(decisions) > total:
        raise ProjectStateError(
            "hygiene resolutions contain more decisions than proposals"
        )

    return HygieneResolutionState(
        review_fingerprint=fingerprint,
        total_proposals=total,
        decisions=tuple(decisions),
    )


def load_project_hygiene_resolutions(
    config: BibReviewConfig,
    review: HygieneReview | None = None,
) -> HygieneResolutionState:
    """Load/resume hygiene decisions and reject stale proposal state."""
    review = review or load_project_hygiene_review(config)
    fingerprint = hygiene_review_fingerprint(review)
    candidates = hygiene_resolution_candidates(review)
    path = hygiene_resolution_path(config)

    if not path.exists():
        return HygieneResolutionState(
            review_fingerprint=fingerprint,
            total_proposals=len(candidates),
        )

    state = hygiene_resolution_state_from_data(read_json(path, dict))
    if (
        state.review_fingerprint != fingerprint
        or state.total_proposals != len(candidates)
    ):
        raise ProjectStateError(
            f"{path}: hygiene resolutions do not match the current proposal set; "
            "archive or remove the stale resolution file before resolving"
        )

    by_key = {item.key: item for item in candidates}
    for decision in state.decisions:
        candidate = by_key.get(decision.key)
        if candidate is None:
            raise ProjectStateError(
                f"{path}: resolved proposal is absent from current review: "
                f"{decision.key}"
            )
        proposal = candidate.proposal
        if (
            decision.publication_id != proposal.publication_id
            or decision.doi != proposal.doi
            or decision.title != proposal.title
        ):
            raise ProjectStateError(
                f"{path}: resolution metadata is inconsistent for {decision.key}"
            )
        if proposal.review_required and decision.decision == "accepted":
            raise ProjectStateError(
                f"{path}: review-required hygiene proposal cannot be accepted "
                f"directly: {decision.key}"
            )

    return state


def record_hygiene_resolution(
    state: HygieneResolutionState,
    candidate: HygieneResolutionCandidate,
    *,
    decision: str,
    resolved_value: str | None = None,
) -> HygieneResolutionState:
    """Record or replace one human hygiene decision."""
    if decision not in _DECISIONS:
        raise ProjectStateError(f"unsupported hygiene decision: {decision}")

    proposal = candidate.proposal
    if decision == "accepted":
        if proposal.review_required:
            raise ProjectStateError(
                "review-required hygiene proposal cannot be accepted directly; "
                "use a custom value, reject, or defer"
            )
        resolved_value = proposal.proposed_abstract
    elif decision == "custom":
        if not isinstance(resolved_value, str) or not resolved_value.strip():
            raise ProjectStateError("custom hygiene resolution requires a value")
        resolved_value = resolved_value.strip()
    else:
        resolved_value = None

    item = HygieneResolutionDecision(
        key=candidate.key,
        publication_id=proposal.publication_id,
        doi=proposal.doi,
        title=proposal.title,
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


def save_project_hygiene_resolutions(
    config: BibReviewConfig,
    state: HygieneResolutionState,
) -> None:
    """Persist resumable historical hygiene decisions."""
    write_json(hygiene_resolution_path(config), state.data())


def hygiene_resolution_counts(
    state: HygieneResolutionState,
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


def unresolved_hygiene_candidates(
    review: HygieneReview,
    state: HygieneResolutionState,
) -> tuple[HygieneResolutionCandidate, ...]:
    """Return unresolved and deferred proposals for the next session."""
    terminal = {
        item.key
        for item in state.decisions
        if item.decision in {"accepted", "custom", "rejected"}
    }
    return tuple(
        candidate
        for candidate in hygiene_resolution_candidates(review)
        if candidate.key not in terminal
    )


def format_hygiene_resolution_candidate(
    candidate: HygieneResolutionCandidate,
) -> str:
    """Format one canonical abstract proposal for explicit human review."""
    proposal = candidate.proposal
    identity = proposal.doi or proposal.publication_id
    lines = [
        f"[{candidate.position}/{candidate.total}] {identity} — {proposal.title}",
        "",
        "Field: abstract",
        "Families: " + ", ".join(proposal.families),
        f"Normalizer: {proposal.reason}",
        "",
        "Current:",
        fill(
            proposal.current_abstract,
            width=100,
            initial_indent="  ",
            subsequent_indent="  ",
        ),
        "",
    ]

    if proposal.review_required:
        lines.extend((
            "Status: REVIEW REQUIRED",
            "No safe automatic normalized abstract is available.",
        ))
    else:
        lines.extend((
            "Proposed:",
            fill(
                proposal.proposed_abstract,
                width=100,
                initial_indent="  ",
                subsequent_indent="  ",
            ),
        ))

    return "\n".join(lines).rstrip() + "\n"
