"""Resumable human decisions for actionable audit findings."""

from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import json
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from .config import BibReviewConfig
from .pipeline.audit import AuditReviewFinding, AuditValue
from .project import ProjectStateError
from .project_audit import AuditPublicationReview, ProjectAuditReview
from .storage import read_json, write_json


AUDIT_RESOLUTION_SCHEMA_VERSION = 1
_DECISIONS = frozenset({"accepted", "custom", "rejected", "deferred"})


@dataclass(frozen=True)
class AuditResolutionCandidate:
    """One actionable finding presented for an explicit human decision."""

    position: int
    total: int
    publication_id: str
    identifiers: Mapping[str, str]
    title: str
    finding: AuditReviewFinding
    proposed_value: AuditValue | None

    @property
    def key(self) -> str:
        return f"{self.publication_id}:{self.finding.field}"


@dataclass(frozen=True)
class AuditResolutionDecision:
    """Persisted human decision for one actionable audit finding."""

    key: str
    publication_id: str
    identifiers: Mapping[str, str]
    title: str
    field: str
    classification: str
    canonical_value: AuditValue
    provider_values: tuple[tuple[str, AuditValue], ...]
    decision: str
    resolved_value: AuditValue | None

    def data(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "publication_id": self.publication_id,
            "identifiers": dict(self.identifiers),
            "title": self.title,
            "field": self.field,
            "classification": self.classification,
            "canonical_value": _json_value(self.canonical_value),
            "provider_values": [
                [provider, _json_value(value)]
                for provider, value in self.provider_values
            ],
            "decision": self.decision,
            "resolved_value": (
                None
                if self.resolved_value is None
                else _json_value(self.resolved_value)
            ),
        }


@dataclass(frozen=True)
class AuditResolutionState:
    """Versioned resumable decisions tied to one exact actionable review."""

    review_fingerprint: str
    total_actionable: int
    decisions: tuple[AuditResolutionDecision, ...] = ()

    def data(self) -> dict[str, Any]:
        return {
            "schema_version": AUDIT_RESOLUTION_SCHEMA_VERSION,
            "review_fingerprint": self.review_fingerprint,
            "total_actionable": self.total_actionable,
            "decisions": [decision.data() for decision in self.decisions],
        }

    def summary(self) -> str:
        counts = resolution_counts(self)
        return (
            "Audit resolution\n"
            f"  Actionable findings : {self.total_actionable}\n"
            f"  Accepted            : {counts['accepted']}\n"
            f"  Custom              : {counts['custom']}\n"
            f"  Rejected            : {counts['rejected']}\n"
            f"  Deferred            : {counts['deferred']}\n"
            f"  Unresolved          : {counts['unresolved']}"
        )


def audit_resolution_path(config: BibReviewConfig) -> Path:
    """Return the resolution state path beside the configured audit report."""
    if not isinstance(config, BibReviewConfig):
        raise ProjectStateError("config must be a BibReviewConfig")
    return config.audit.report.with_name("resolutions.json")


def _json_value(value: AuditValue) -> str | list[str]:
    if isinstance(value, str):
        return value
    return list(value)


def _audit_value(value: Any, *, name: str) -> AuditValue:
    if isinstance(value, str):
        return value
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return tuple(value)
    raise ProjectStateError(f"{name} must be a string or list of strings")


def _optional_audit_value(value: Any, *, name: str) -> AuditValue | None:
    if value is None:
        return None
    return _audit_value(value, name=name)


def _proposed_value(finding: AuditReviewFinding) -> AuditValue | None:
    """Return one exact common provider value, never an arbitrary representative."""
    if not finding.provider_values:
        return None
    first = finding.provider_values[0][1]
    if all(value == first for _, value in finding.provider_values[1:]):
        return first
    return None


def actionable_resolution_candidates(
    review: ProjectAuditReview,
) -> tuple[AuditResolutionCandidate, ...]:
    """Flatten actionable findings in deterministic review order."""
    if not isinstance(review, ProjectAuditReview):
        raise ProjectStateError("review must be a ProjectAuditReview")

    raw: list[tuple[AuditPublicationReview, AuditReviewFinding]] = []
    seen: set[str] = set()
    for item in review.items:
        for finding in item.findings:
            if not finding.actionable:
                continue
            key = f"{item.publication_id}:{finding.field}"
            if key in seen:
                raise ProjectStateError(
                    "actionable audit review contains duplicate publication/field "
                    f"key: {key}"
                )
            seen.add(key)
            raw.append((item, finding))

    total = len(raw)
    return tuple(
        AuditResolutionCandidate(
            position=index,
            total=total,
            publication_id=item.publication_id,
            identifiers=item.identifiers,
            title=item.title,
            finding=finding,
            proposed_value=_proposed_value(finding),
        )
        for index, (item, finding) in enumerate(raw, 1)
    )


def audit_review_fingerprint(review: ProjectAuditReview) -> str:
    """Fingerprint only the actionable evidence that human decisions depend on."""
    payload = [
        {
            "publication_id": candidate.publication_id,
            "identifiers": dict(candidate.identifiers),
            "title": candidate.title,
            "field": candidate.finding.field,
            "classification": candidate.finding.classification,
            "canonical_value": _json_value(candidate.finding.canonical_value),
            "provider_values": [
                [provider, _json_value(value)]
                for provider, value in candidate.finding.provider_values
            ],
            "detail": candidate.finding.detail,
        }
        for candidate in actionable_resolution_candidates(review)
    ]
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _decision_from_data(value: Any, *, index: int) -> AuditResolutionDecision:
    if not isinstance(value, Mapping):
        raise ProjectStateError(f"audit resolution decision {index} must be an object")

    def required_string(name: str) -> str:
        raw = value.get(name)
        if not isinstance(raw, str) or not raw:
            raise ProjectStateError(
                f"audit resolution decision {index}.{name} must be a non-empty string"
            )
        return raw

    identifiers_raw = value.get("identifiers")
    if not isinstance(identifiers_raw, Mapping) or any(
        not isinstance(key, str)
        or not key
        or not isinstance(item, str)
        or not item
        for key, item in identifiers_raw.items()
    ):
        raise ProjectStateError(
            f"audit resolution decision {index}.identifiers must map strings to strings"
        )

    provider_values_raw = value.get("provider_values")
    if not isinstance(provider_values_raw, list):
        raise ProjectStateError(
            f"audit resolution decision {index}.provider_values must be a list"
        )
    provider_values: list[tuple[str, AuditValue]] = []
    for provider_index, pair in enumerate(provider_values_raw, 1):
        if (
            not isinstance(pair, list)
            or len(pair) != 2
            or not isinstance(pair[0], str)
            or not pair[0]
        ):
            raise ProjectStateError(
                "audit resolution decision "
                f"{index}.provider_values[{provider_index}] must be [provider, value]"
            )
        provider_values.append(
            (
                pair[0],
                _audit_value(
                    pair[1],
                    name=(
                        "audit resolution decision "
                        f"{index}.provider_values[{provider_index}][1]"
                    ),
                ),
            )
        )

    decision = required_string("decision")
    if decision not in _DECISIONS:
        raise ProjectStateError(
            f"audit resolution decision {index}.decision is unsupported: {decision}"
        )
    resolved_value = _optional_audit_value(
        value.get("resolved_value"),
        name=f"audit resolution decision {index}.resolved_value",
    )
    if decision in {"accepted", "custom"} and resolved_value is None:
        raise ProjectStateError(
            f"audit resolution decision {index} requires resolved_value"
        )
    if decision in {"rejected", "deferred"} and resolved_value is not None:
        raise ProjectStateError(
            f"audit resolution decision {index} must not define resolved_value"
        )

    return AuditResolutionDecision(
        key=required_string("key"),
        publication_id=required_string("publication_id"),
        identifiers=MappingProxyType(dict(identifiers_raw)),
        title=required_string("title"),
        field=required_string("field"),
        classification=required_string("classification"),
        canonical_value=_audit_value(
            value.get("canonical_value"),
            name=f"audit resolution decision {index}.canonical_value",
        ),
        provider_values=tuple(provider_values),
        decision=decision,
        resolved_value=resolved_value,
    )


def audit_resolution_state_from_data(value: Any) -> AuditResolutionState:
    """Strictly validate one persisted audit-resolution document."""
    if not isinstance(value, Mapping):
        raise ProjectStateError("audit resolutions must be an object")
    if value.get("schema_version") != AUDIT_RESOLUTION_SCHEMA_VERSION:
        raise ProjectStateError(
            "unsupported audit resolution schema_version: "
            f"{value.get('schema_version')!r}"
        )
    fingerprint = value.get("review_fingerprint")
    if not isinstance(fingerprint, str) or len(fingerprint) != 64:
        raise ProjectStateError(
            "audit resolutions review_fingerprint must be a SHA-256 string"
        )
    total = value.get("total_actionable")
    if not isinstance(total, int) or isinstance(total, bool) or total < 0:
        raise ProjectStateError(
            "audit resolutions total_actionable must be a non-negative integer"
        )
    raw_decisions = value.get("decisions")
    if not isinstance(raw_decisions, list):
        raise ProjectStateError("audit resolutions decisions must be a list")
    decisions = tuple(
        _decision_from_data(item, index=index)
        for index, item in enumerate(raw_decisions, 1)
    )
    keys = [item.key for item in decisions]
    if len(keys) != len(set(keys)):
        raise ProjectStateError("audit resolutions contain duplicate decision keys")
    if len(decisions) > total:
        raise ProjectStateError(
            "audit resolutions contain more decisions than actionable findings"
        )
    return AuditResolutionState(
        review_fingerprint=fingerprint,
        total_actionable=total,
        decisions=decisions,
    )


def load_project_audit_resolutions(
    config: BibReviewConfig,
    review: ProjectAuditReview,
) -> AuditResolutionState:
    """Load/resume decisions, rejecting stale state after review evidence changes."""
    fingerprint = audit_review_fingerprint(review)
    total = len(actionable_resolution_candidates(review))
    path = audit_resolution_path(config)
    if not path.exists():
        return AuditResolutionState(
            review_fingerprint=fingerprint,
            total_actionable=total,
        )

    state = audit_resolution_state_from_data(read_json(path, dict))
    if (
        state.review_fingerprint != fingerprint
        or state.total_actionable != total
    ):
        raise ProjectStateError(
            f"{path}: audit resolutions do not match the current actionable review; "
            "archive or remove the stale resolution file before starting a new review"
        )

    valid_keys = {candidate.key for candidate in actionable_resolution_candidates(review)}
    unknown = [decision.key for decision in state.decisions if decision.key not in valid_keys]
    if unknown:
        raise ProjectStateError(
            f"{path}: audit resolutions contain finding(s) absent from current review: "
            + ", ".join(unknown)
        )
    return state


def record_audit_resolution(
    state: AuditResolutionState,
    candidate: AuditResolutionCandidate,
    *,
    decision: str,
    resolved_value: AuditValue | None = None,
) -> AuditResolutionState:
    """Record or replace one human decision without touching canonical metadata."""
    if decision not in _DECISIONS:
        raise ProjectStateError(f"unsupported audit resolution decision: {decision}")
    if decision == "accepted":
        if candidate.proposed_value is None:
            raise ProjectStateError(
                "accepted resolution requires one exact common provider value; "
                "use a custom resolution instead"
            )
        resolved_value = candidate.proposed_value
    elif decision == "custom":
        if resolved_value is None:
            raise ProjectStateError("custom resolution requires a value")
    else:
        resolved_value = None

    item = AuditResolutionDecision(
        key=candidate.key,
        publication_id=candidate.publication_id,
        identifiers=candidate.identifiers,
        title=candidate.title,
        field=candidate.finding.field,
        classification=candidate.finding.classification,
        canonical_value=candidate.finding.canonical_value,
        provider_values=candidate.finding.provider_values,
        decision=decision,
        resolved_value=resolved_value,
    )
    decisions = [entry for entry in state.decisions if entry.key != item.key]
    decisions.append(item)

    order = {
        candidate.key: candidate.position
        for candidate in actionable_resolution_candidates_for_state_guard(state, candidate)
    }
    decisions.sort(key=lambda entry: order.get(entry.key, state.total_actionable + 1))
    return replace(state, decisions=tuple(decisions))


def actionable_resolution_candidates_for_state_guard(
    state: AuditResolutionState,
    current: AuditResolutionCandidate,
) -> tuple[AuditResolutionCandidate, ...]:
    """Small ordering guard for isolated decision updates.

    Decisions are normally appended in review order by the CLI. Existing decisions keep
    their relative order; the current candidate receives its known review position.
    """
    placeholders = [
        AuditResolutionCandidate(
            position=index,
            total=state.total_actionable,
            publication_id=decision.publication_id,
            identifiers=decision.identifiers,
            title=decision.title,
            finding=AuditReviewFinding(
                field=decision.field,
                classification=decision.classification,
                providers=tuple(provider for provider, _ in decision.provider_values),
                canonical_value=decision.canonical_value,
                provider_values=decision.provider_values,
                actionable=True,
            ),
            proposed_value=None,
        )
        for index, decision in enumerate(state.decisions, 1)
    ]
    placeholders.append(current)
    return tuple(placeholders)


def save_project_audit_resolutions(
    config: BibReviewConfig,
    state: AuditResolutionState,
) -> None:
    """Atomically persist one resumable human-resolution state."""
    write_json(audit_resolution_path(config), state.data())


def resolution_counts(state: AuditResolutionState) -> Mapping[str, int]:
    """Return deterministic counts for resolution progress."""
    counts = {name: 0 for name in sorted(_DECISIONS)}
    for item in state.decisions:
        counts[item.decision] += 1
    terminal = counts["accepted"] + counts["custom"] + counts["rejected"]
    counts["unresolved"] = state.total_actionable - terminal - counts["deferred"]
    return MappingProxyType(counts)


def unresolved_resolution_candidates(
    review: ProjectAuditReview,
    state: AuditResolutionState,
) -> tuple[AuditResolutionCandidate, ...]:
    """Return undecided and deferred findings once each for the next session."""
    terminal = {
        item.key
        for item in state.decisions
        if item.decision in {"accepted", "custom", "rejected"}
    }
    return tuple(
        candidate
        for candidate in actionable_resolution_candidates(review)
        if candidate.key not in terminal
    )


def format_audit_resolution_candidate(candidate: AuditResolutionCandidate) -> str:
    """Format one actionable finding for interactive human review."""
    doi = candidate.identifiers.get("doi", candidate.publication_id)
    finding = candidate.finding
    lines = [
        f"[{candidate.position}/{candidate.total}] {doi} — {candidate.title}",
        "",
        f"Field: {finding.field}",
        f"Classification: {finding.classification}",
        "Current:",
        f"  {_format_value(finding.canonical_value)}",
        "",
        "Evidence:",
    ]
    for provider, value in finding.provider_values:
        lines.append(f"  {provider}: {_format_value(value)}")
    lines.append("")
    if candidate.proposed_value is None:
        lines.append("Proposed: no single exact provider representation")
        lines.append(
            "Use f VALUE to choose the canonical representation explicitly."
        )
    else:
        lines.extend(
            [
                "Proposed:",
                f"  {_format_value(candidate.proposed_value)}",
            ]
        )
    if finding.detail:
        lines.extend(["", f"Reason: {finding.detail}"])
    return "\n".join(lines)


def _format_value(value: AuditValue) -> str:
    if isinstance(value, tuple):
        return "; ".join(value) if value else "(missing)"
    return value if value else "(missing)"


def parse_custom_resolution_value(
    raw: str,
    candidate: AuditResolutionCandidate,
) -> AuditValue:
    """Parse custom CLI text using the finding's scalar/tuple value shape."""
    if not isinstance(raw, str) or not raw.strip():
        raise ProjectStateError("custom resolution value cannot be empty")

    tuple_valued = isinstance(candidate.finding.canonical_value, tuple) or any(
        isinstance(value, tuple) for _, value in candidate.finding.provider_values
    )
    text = raw.strip()
    if not tuple_valued:
        return text

    try:
        value = json.loads(text)
    except json.JSONDecodeError as error:
        raise ProjectStateError(
            'tuple-valued corrections require a JSON string array, '
            'for example f ["Ada Lovelace", "Alan Turing"]'
        ) from error
    if not isinstance(value, list) or not value or any(
        not isinstance(item, str) or not item for item in value
    ):
        raise ProjectStateError(
            "tuple-valued corrections require a non-empty JSON string array"
        )
    return tuple(value)
