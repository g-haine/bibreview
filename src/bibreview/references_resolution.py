"""Persistent human decisions for reference-refresh review cases."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from .config import BibReviewConfig
from .model import Reference
from .pipeline.references import (
    ReferenceRefreshResult,
    reference_data,
    reference_from_data,
    references_fingerprint,
)
from .project import ProjectStateError
from .project_references import (
    ProjectReferencesReview,
    _review_reference_alignment,
    project_references_review,
    safe_reference_requires_human_review,
)
from .storage import read_json, write_json


REFERENCE_RESOLUTION_SCHEMA_VERSION = 1
_REFERENCE_DECISIONS = frozenset(
    {"keep-canonical", "use-provider", "custom", "deferred"}
)
_TERMINAL_DECISIONS = frozenset({"keep-canonical", "use-provider", "custom"})


@dataclass(frozen=True)
class ReferenceResolutionCandidate:
    """One genuinely ambiguous publication presented for human review."""

    position: int
    total: int
    proposal: ReferenceRefreshResult
    current_references: tuple[Reference, ...]

    @property
    def key(self) -> str:
        return reference_resolution_key(self.proposal)


@dataclass(frozen=True)
class ReferenceResolutionDecision:
    """One persisted decision tied to exact source/provider evidence."""

    key: str
    publication_id: str
    doi: str
    title: str
    decision: str
    source_fingerprint: str
    proposed_fingerprint: str
    resolved_fingerprint: str | None
    custom_references: tuple[Reference, ...] = ()

    def data(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "publication_id": self.publication_id,
            "doi": self.doi,
            "title": self.title,
            "decision": self.decision,
            "source_fingerprint": self.source_fingerprint,
            "proposed_fingerprint": self.proposed_fingerprint,
            "resolved_fingerprint": self.resolved_fingerprint,
            "custom_references": [
                reference_data(reference) for reference in self.custom_references
            ],
        }


@dataclass(frozen=True)
class ReferenceResolutionState:
    """Cumulative reference decisions across an append-only campaign report."""

    decisions: tuple[ReferenceResolutionDecision, ...] = ()

    def data(self) -> dict[str, Any]:
        return {
            "schema_version": REFERENCE_RESOLUTION_SCHEMA_VERSION,
            "decisions": [decision.data() for decision in self.decisions],
        }

    def summary(self, *, unresolved: int = 0) -> str:
        counts = reference_resolution_counts(self)
        return (
            "Reference resolution\n"
            f"  Keep canonical : {counts['keep-canonical']}\n"
            f"  Use provider   : {counts['use-provider']}\n"
            f"  Custom         : {counts['custom']}\n"
            f"  Deferred       : {counts['deferred']}\n"
            f"  Unresolved     : {unresolved}"
        )


def reference_resolution_path(config: BibReviewConfig) -> Path:
    """Return cumulative reference decisions beside campaign/report state."""
    path = config.references.report.with_name("resolutions.json")
    if path in {config.references.report, config.references.campaign}:
        raise ProjectStateError("reference resolution path collides with campaign state")
    return path


def reference_resolution_key(item: ReferenceRefreshResult) -> str:
    """Return an evidence-versioned key that survives later campaign growth."""
    return (
        f"{item.publication_id}:"
        f"{item.current_fingerprint}:"
        f"{item.proposed_fingerprint}"
    )


def _valid_fingerprint(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64


def reference_resolution_state_from_data(value: Any) -> ReferenceResolutionState:
    """Strictly decode cumulative reference resolution state."""
    if not isinstance(value, Mapping):
        raise ProjectStateError("reference resolutions must be an object")
    if set(value) != {"schema_version", "decisions"}:
        raise ProjectStateError("invalid reference resolution fields")
    if value.get("schema_version") != REFERENCE_RESOLUTION_SCHEMA_VERSION:
        raise ProjectStateError(
            "unsupported reference resolution schema_version: "
            f"{value.get('schema_version')!r}"
        )
    raw_decisions = value.get("decisions")
    if not isinstance(raw_decisions, list):
        raise ProjectStateError("reference resolutions decisions must be a list")

    decisions: list[ReferenceResolutionDecision] = []
    for index, raw in enumerate(raw_decisions, 1):
        if not isinstance(raw, Mapping):
            raise ProjectStateError(
                f"reference resolution decision {index} must be an object"
            )
        required = {
            "key",
            "publication_id",
            "doi",
            "title",
            "decision",
            "source_fingerprint",
            "proposed_fingerprint",
            "resolved_fingerprint",
            "custom_references",
        }
        if set(raw) != required:
            raise ProjectStateError(
                f"reference resolution decision {index} has invalid fields"
            )
        for name in (
            "key",
            "publication_id",
            "doi",
            "title",
            "decision",
            "source_fingerprint",
            "proposed_fingerprint",
        ):
            item = raw[name]
            if not isinstance(item, str) or (
                name not in {"title"} and not item
            ):
                raise ProjectStateError(
                    f"reference resolution decision {index}.{name} must be a string"
                )
        decision = raw["decision"]
        if decision not in _REFERENCE_DECISIONS:
            raise ProjectStateError(
                f"reference resolution decision {index}.decision is unsupported"
            )
        if not _valid_fingerprint(raw["source_fingerprint"]) or not _valid_fingerprint(
            raw["proposed_fingerprint"]
        ):
            raise ProjectStateError(
                f"reference resolution decision {index} has invalid evidence fingerprint"
            )
        resolved_fingerprint = raw["resolved_fingerprint"]
        if decision == "deferred":
            if resolved_fingerprint is not None:
                raise ProjectStateError(
                    f"reference resolution decision {index} deferred state "
                    "must not define resolved_fingerprint"
                )
        elif not _valid_fingerprint(resolved_fingerprint):
            raise ProjectStateError(
                f"reference resolution decision {index} requires resolved_fingerprint"
            )

        raw_custom = raw["custom_references"]
        if not isinstance(raw_custom, list):
            raise ProjectStateError(
                f"reference resolution decision {index}.custom_references must be a list"
            )
        try:
            custom = tuple(reference_from_data(item) for item in raw_custom)
        except (TypeError, ValueError) as error:
            raise ProjectStateError(
                f"reference resolution decision {index}.custom_references: {error}"
            ) from error
        if decision == "custom":
            if references_fingerprint(custom) != resolved_fingerprint:
                raise ProjectStateError(
                    f"reference resolution decision {index} custom fingerprint mismatch"
                )
        elif custom:
            raise ProjectStateError(
                f"reference resolution decision {index} must not define custom references"
            )

        expected_key = (
            f"{raw['publication_id']}:"
            f"{raw['source_fingerprint']}:"
            f"{raw['proposed_fingerprint']}"
        )
        if raw["key"] != expected_key:
            raise ProjectStateError(
                f"reference resolution decision {index}.key is inconsistent"
            )

        decisions.append(
            ReferenceResolutionDecision(
                key=raw["key"],
                publication_id=raw["publication_id"],
                doi=raw["doi"],
                title=raw["title"],
                decision=decision,
                source_fingerprint=raw["source_fingerprint"],
                proposed_fingerprint=raw["proposed_fingerprint"],
                resolved_fingerprint=resolved_fingerprint,
                custom_references=custom,
            )
        )

    keys = [item.key for item in decisions]
    if len(keys) != len(set(keys)):
        raise ProjectStateError("reference resolutions contain duplicate decision keys")
    return ReferenceResolutionState(decisions=tuple(decisions))


def load_project_reference_resolutions(
    config: BibReviewConfig,
) -> ReferenceResolutionState:
    """Load cumulative decisions without invalidating them when reports grow."""
    path = reference_resolution_path(config)
    if not path.exists():
        return ReferenceResolutionState()
    return reference_resolution_state_from_data(read_json(path, dict))


def save_project_reference_resolutions(
    config: BibReviewConfig,
    state: ReferenceResolutionState,
) -> None:
    """Persist cumulative reference decisions."""
    write_json(reference_resolution_path(config), state.data())


def matching_reference_resolution(
    state: ReferenceResolutionState,
    item: ReferenceRefreshResult,
) -> ReferenceResolutionDecision | None:
    """Return the decision for this exact source/provider evidence pair."""
    key = reference_resolution_key(item)
    return next((decision for decision in state.decisions if decision.key == key), None)


def record_reference_resolution(
    state: ReferenceResolutionState,
    candidate: ReferenceResolutionCandidate,
    *,
    decision: str,
    custom_references: tuple[Reference, ...] = (),
) -> ReferenceResolutionState:
    """Record or replace one human decision for exact persisted evidence."""
    if decision not in _REFERENCE_DECISIONS:
        raise ProjectStateError(f"unsupported reference decision: {decision}")
    item = candidate.proposal
    if decision == "keep-canonical":
        resolved = item.current_fingerprint
        custom_references = ()
    elif decision == "use-provider":
        resolved = item.proposed_fingerprint
        custom_references = ()
    elif decision == "custom":
        if not isinstance(custom_references, tuple):
            raise ProjectStateError("custom reference resolution must be a tuple")
        resolved = references_fingerprint(custom_references)
    else:
        resolved = None
        custom_references = ()

    stored = ReferenceResolutionDecision(
        key=candidate.key,
        publication_id=item.publication_id,
        doi=item.doi or item.publication_id,
        title=item.title,
        decision=decision,
        source_fingerprint=item.current_fingerprint,
        proposed_fingerprint=item.proposed_fingerprint,
        resolved_fingerprint=resolved,
        custom_references=custom_references,
    )
    decisions = list(state.decisions)
    for index, existing in enumerate(decisions):
        if existing.key == stored.key:
            decisions[index] = stored
            break
    else:
        decisions.append(stored)
    return replace(state, decisions=tuple(decisions))


def reference_resolution_counts(
    state: ReferenceResolutionState,
) -> Mapping[str, int]:
    counts = {name: 0 for name in sorted(_REFERENCE_DECISIONS)}
    for decision in state.decisions:
        counts[decision.decision] += 1
    return MappingProxyType(counts)


def reference_resolution_candidates(
    review: ProjectReferencesReview,
    state: ReferenceResolutionState,
) -> tuple[ReferenceResolutionCandidate, ...]:
    """Return only genuine human decisions not already terminally resolved."""
    raw: list[tuple[ReferenceRefreshResult, tuple[Reference, ...]]] = []
    for item in review.items:
        if item.classification != "review-required":
            continue
        current = review.current_references.get(item.publication_id)
        if current is None:
            raise ProjectStateError(
                f"{item.publication_id}: canonical publication is missing"
            )
        current_fingerprint = references_fingerprint(current)
        decision = matching_reference_resolution(state, item)

        if decision is not None and decision.decision in _TERMINAL_DECISIONS:
            if current_fingerprint not in {
                decision.source_fingerprint,
                decision.resolved_fingerprint,
            }:
                raise ProjectStateError(
                    f"{item.publication_id}: stale reference resolution; canonical "
                    "references match neither source nor resolved fingerprint"
                )
            continue

        if current_fingerprint != item.current_fingerprint:
            # Old report evidence may legitimately predate an already merged
            # deterministic batch, but a human decision cannot be inferred from
            # that newer canonical state.
            continue

        if safe_reference_requires_human_review(item, current):
            raw.append((item, current))

    total = len(raw)
    return tuple(
        ReferenceResolutionCandidate(
            position=index,
            total=total,
            proposal=item,
            current_references=current,
        )
        for index, (item, current) in enumerate(raw, 1)
    )


def unresolved_reference_candidates(
    review: ProjectReferencesReview,
    state: ReferenceResolutionState,
) -> tuple[ReferenceResolutionCandidate, ...]:
    """Return unresolved and deferred human decisions."""
    return reference_resolution_candidates(review, state)


def format_reference_resolution_candidate(
    candidate: ReferenceResolutionCandidate,
) -> str:
    """Render one ambiguous parent publication with comparison alignment."""
    item = candidate.proposal
    lines = [
        f"[{candidate.position}/{candidate.total}] "
        f"{item.doi or item.publication_id} — {item.title}",
        "",
        f"Reason      : {item.reason}",
        f"References  : {item.current_count} -> {item.provider_count}",
        f"Source fp   : {item.current_fingerprint}",
        f"Provider fp : {item.proposed_fingerprint}",
        "",
        "Ambiguous comparison:",
    ]
    rows = _review_reference_alignment(
        candidate.current_references,
        item.proposed_references,
    )
    for row in rows:
        if row.kind == "unchanged":
            continue
        current_index = "-" if row.current_index is None else str(row.current_index)
        provider_index = "-" if row.provider_index is None else str(row.provider_index)
        current_doi = (
            row.current.identifiers.get("doi", "(none)")
            if row.current is not None
            else "(missing)"
        )
        provider_doi = (
            row.proposed.identifiers.get("doi", "(none)")
            if row.proposed is not None
            else "(missing)"
        )
        current_citation = (
            row.current.citation if row.current is not None else "(missing)"
        )
        provider_citation = (
            row.proposed.citation if row.proposed is not None else "(missing)"
        )
        lines.extend(
            [
                "",
                f"  Current {current_index} / Provider {provider_index} [{row.kind}]",
                f"    Current DOI : {current_doi}",
                f"    Provider DOI: {provider_doi}",
                f"    Current     : {current_citation}",
                f"    Provider    : {provider_citation}",
            ]
        )
    return "\n".join(lines)


def load_custom_reference_file(path: str | Path) -> tuple[Reference, ...]:
    """Load a reviewed exact reference list from a small JSON file."""
    source = Path(path)
    try:
        value = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError) as error:
        raise ProjectStateError(f"{source}: cannot read custom references: {error}") from error
    if isinstance(value, Mapping) and set(value) == {"references"}:
        value = value["references"]
    if not isinstance(value, list):
        raise ProjectStateError(
            f"{source}: custom reference file must be a list or {{\"references\": [...]}}"
        )
    try:
        return tuple(reference_from_data(item) for item in value)
    except (TypeError, ValueError) as error:
        raise ProjectStateError(f"{source}: invalid custom reference: {error}") from error


def current_reference_resolution_summary(
    review: ProjectReferencesReview,
    state: ReferenceResolutionState,
) -> str:
    """Return cumulative decisions plus current unresolved human workload."""
    candidates = unresolved_reference_candidates(review, state)
    return state.summary(unresolved=len(candidates))


__all__ = [
    "ReferenceResolutionCandidate",
    "ReferenceResolutionDecision",
    "ReferenceResolutionState",
    "current_reference_resolution_summary",
    "format_reference_resolution_candidate",
    "load_custom_reference_file",
    "load_project_reference_resolutions",
    "matching_reference_resolution",
    "record_reference_resolution",
    "reference_resolution_candidates",
    "reference_resolution_counts",
    "reference_resolution_path",
    "reference_resolution_state_from_data",
    "save_project_reference_resolutions",
    "unresolved_reference_candidates",
]
