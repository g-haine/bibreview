"""Resumable human decisions for actionable audit findings."""

from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import json
import re
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from .config import BibReviewConfig
from .pipeline.audit import AuditReviewFinding, AuditValue
from .project import ProjectStateError
from .project_audit import ProjectAuditReview
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
    """Persisted decision for one actionable finding."""

    key: str
    publication_id: str
    doi: str
    title: str
    field: str
    decision: str
    resolved_value: AuditValue | None

    def data(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "publication_id": self.publication_id,
            "doi": self.doi,
            "title": self.title,
            "field": self.field,
            "decision": self.decision,
            "resolved_value": _json_value(self.resolved_value),
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
            "decisions": [item.data() for item in self.decisions],
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
    path = config.audit.report.with_name("resolutions.json")
    if path in {config.audit.report, config.audit.campaign}:
        raise ProjectStateError(
            "audit resolution path would collide with configured audit state"
        )
    return path


def _json_value(value: AuditValue | None) -> str | list[str] | None:
    if value is None or isinstance(value, str):
        return value
    return list(value)


def _audit_value(value: Any, *, name: str) -> AuditValue:
    if isinstance(value, str):
        return value
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return tuple(value)
    raise ProjectStateError(f"{name} must be a string or list of strings")


def _optional_audit_value(value: Any, *, name: str) -> AuditValue | None:
    return None if value is None else _audit_value(value, name=name)


def _proposal_value(field: str, value: AuditValue) -> AuditValue:
    """Normalize only the human-facing proposal representation."""
    if field != "pages" or not isinstance(value, str):
        return value
    return re.sub(
        r"(?<=[0-9A-Za-z])[-–—](?=[0-9A-Za-z])",
        "--",
        value,
    )


def _exact_proposal(finding: AuditReviewFinding) -> AuditValue | None:
    """Return one safe common proposal without changing stored provider evidence."""
    if not finding.provider_values:
        return None
    values = tuple(
        _proposal_value(finding.field, value)
        for _, value in finding.provider_values
    )
    first = values[0]
    return first if all(value == first for value in values[1:]) else None


def actionable_resolution_candidates(
    review: ProjectAuditReview,
) -> tuple[AuditResolutionCandidate, ...]:
    """Flatten actionable findings in deterministic review order."""
    if not isinstance(review, ProjectAuditReview):
        raise ProjectStateError("review must be a ProjectAuditReview")

    findings = [
        (item, finding)
        for item in review.items
        for finding in item.findings
        if finding.actionable
    ]
    keys = [f"{item.publication_id}:{finding.field}" for item, finding in findings]
    if len(keys) != len(set(keys)):
        raise ProjectStateError(
            "actionable audit review contains duplicate publication/field keys"
        )

    total = len(findings)
    return tuple(
        AuditResolutionCandidate(
            position=index,
            total=total,
            publication_id=item.publication_id,
            identifiers=item.identifiers,
            title=item.title,
            finding=finding,
            proposed_value=_exact_proposal(finding),
        )
        for index, (item, finding) in enumerate(findings, 1)
    )


def audit_review_fingerprint(review: ProjectAuditReview) -> str:
    """Fingerprint the actionable evidence that resolution decisions depend on."""
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
    total = value.get("total_actionable")
    raw_decisions = value.get("decisions")
    if not isinstance(fingerprint, str) or len(fingerprint) != 64:
        raise ProjectStateError(
            "audit resolutions review_fingerprint must be a SHA-256 string"
        )
    if not isinstance(total, int) or isinstance(total, bool) or total < 0:
        raise ProjectStateError(
            "audit resolutions total_actionable must be a non-negative integer"
        )
    if not isinstance(raw_decisions, list):
        raise ProjectStateError("audit resolutions decisions must be a list")

    decisions: list[AuditResolutionDecision] = []
    for index, raw in enumerate(raw_decisions, 1):
        if not isinstance(raw, Mapping):
            raise ProjectStateError(
                f"audit resolution decision {index} must be an object"
            )

        strings: dict[str, str] = {}
        for name in ("key", "publication_id", "doi", "title", "field", "decision"):
            item = raw.get(name)
            if not isinstance(item, str) or not item:
                raise ProjectStateError(
                    f"audit resolution decision {index}.{name} "
                    "must be a non-empty string"
                )
            strings[name] = item

        decision = strings["decision"]
        if decision not in _DECISIONS:
            raise ProjectStateError(
                f"audit resolution decision {index}.decision is unsupported: "
                f"{decision}"
            )
        resolved = _optional_audit_value(
            raw.get("resolved_value"),
            name=f"audit resolution decision {index}.resolved_value",
        )
        if decision in {"accepted", "custom"} and resolved is None:
            raise ProjectStateError(
                f"audit resolution decision {index} requires resolved_value"
            )
        if decision in {"rejected", "deferred"} and resolved is not None:
            raise ProjectStateError(
                f"audit resolution decision {index} must not define resolved_value"
            )
        if strings["key"] != f"{strings['publication_id']}:{strings['field']}":
            raise ProjectStateError(
                f"audit resolution decision {index}.key is inconsistent"
            )

        decisions.append(
            AuditResolutionDecision(
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
        raise ProjectStateError("audit resolutions contain duplicate decision keys")
    if len(decisions) > total:
        raise ProjectStateError(
            "audit resolutions contain more decisions than actionable findings"
        )
    return AuditResolutionState(
        review_fingerprint=fingerprint,
        total_actionable=total,
        decisions=tuple(decisions),
    )


def load_project_audit_resolutions(
    config: BibReviewConfig,
    review: ProjectAuditReview,
) -> AuditResolutionState:
    """Load/resume decisions and reject stale state after review changes."""
    candidates = actionable_resolution_candidates(review)
    fingerprint = audit_review_fingerprint(review)
    path = audit_resolution_path(config)

    if not path.exists():
        return AuditResolutionState(
            review_fingerprint=fingerprint,
            total_actionable=len(candidates),
        )

    state = audit_resolution_state_from_data(read_json(path, dict))
    if (
        state.review_fingerprint != fingerprint
        or state.total_actionable != len(candidates)
    ):
        raise ProjectStateError(
            f"{path}: audit resolutions do not match the current actionable review; "
            "archive or remove the stale resolution file before starting a new review"
        )

    candidates_by_key = {item.key: item for item in candidates}
    for decision in state.decisions:
        candidate = candidates_by_key.get(decision.key)
        if candidate is None:
            raise ProjectStateError(
                f"{path}: resolution finding is absent from the current review: "
                f"{decision.key}"
            )
        doi = candidate.identifiers.get("doi", candidate.publication_id)
        if (
            decision.publication_id != candidate.publication_id
            or decision.doi != doi
            or decision.title != candidate.title
            or decision.field != candidate.finding.field
        ):
            raise ProjectStateError(
                f"{path}: resolution metadata is inconsistent for {decision.key}"
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
        doi=candidate.identifiers.get("doi", candidate.publication_id),
        title=candidate.title,
        field=candidate.finding.field,
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
    counts["unresolved"] = (
        state.total_actionable
        - counts["accepted"]
        - counts["custom"]
        - counts["rejected"]
        - counts["deferred"]
    )
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
    lines.extend(
        f"  {provider}: {_format_value(value)}"
        for provider, value in finding.provider_values
    )
    lines.append("")
    if candidate.proposed_value is None:
        lines.extend(
            [
                "Proposed: no single exact provider representation",
                "Use f VALUE to choose the canonical representation explicitly.",
            ]
        )
    else:
        lines.extend(
            ["Proposed:", f"  {_format_value(candidate.proposed_value)}"]
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
            "tuple-valued corrections require a JSON string array, "
            'for example f ["Ada Lovelace", "Alan Turing"]'
        ) from error
    if not isinstance(value, list) or not value or any(
        not isinstance(item, str) or not item for item in value
    ):
        raise ProjectStateError(
            "tuple-valued corrections require a non-empty JSON string array"
        )
    return tuple(value)
