"""Reviewed and deterministic repair of tracked BibTeX hygiene defects."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import re
from types import MappingProxyType
from typing import Any, Mapping

from .bibtex_edit import (
    BibtexEditError,
    bibtex_field_value,
    canonicalize_bibtex,
    update_bibtex_fields,
)
from .config import BibReviewConfig
from .project_identity_hygiene import (
    BibtexContributorFinding,
    project_bibtex_contributor_hygiene,
)
from .storage import atomic_write_batch, backup_path, read_json, write_json


BIBTEX_REPAIR_SCHEMA_VERSION = 1
_DECISIONS = frozenset({"replacement", "deferred"})


def _fingerprint(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()


def _split_top_level_and(value: str) -> tuple[str, ...]:
    """Split contributor values on top-level BibTeX 'and' separators."""
    result: list[str] = []
    start = 0
    depth = 0
    index = 0
    while index < len(value):
        char = value[index]
        if char == "{" and (index == 0 or value[index - 1] != "\\"):
            depth += 1
        elif char == "}" and depth > 0 and value[index - 1] != "\\":
            depth -= 1
        if depth == 0:
            match = re.match(r"\s+and\s+", value[index:], flags=re.IGNORECASE)
            if match is not None:
                result.append(value[start:index])
                index += match.end()
                start = index
                continue
        index += 1
    result.append(value[start:])
    return tuple(result)


def _repair_empty_contributor_segments(text: str, field: str) -> str:
    value = bibtex_field_value(text, field)
    if value is None:
        raise BibtexEditError(f"BibTeX has no {field} field")
    segments = _split_top_level_and(value)
    retained = tuple(segment.strip() for segment in segments if segment.strip())
    if len(retained) == len(segments):
        raise BibtexEditError(f"BibTeX {field} has no empty contributor segment")
    if not retained:
        raise BibtexEditError(f"BibTeX {field} contains no usable contributors")
    return update_bibtex_fields(text, {field: " and ".join(retained)})


@dataclass(frozen=True)
class BibtexRepairProposal:
    publication_id: str
    doi: str
    title: str
    path: str
    kind: str
    field: str
    reason: str
    deterministic: bool
    original_fingerprint: str
    repaired_text: str | None = None

    @property
    def key(self) -> str:
        return f"{self.publication_id}:{self.kind}:{self.field}:{self.path}"

    def data(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "publication_id": self.publication_id,
            "doi": self.doi,
            "title": self.title,
            "path": self.path,
            "kind": self.kind,
            "field": self.field,
            "reason": self.reason,
            "deterministic": self.deterministic,
            "original_fingerprint": self.original_fingerprint,
        }


@dataclass(frozen=True)
class BibtexRepairReview:
    proposals: tuple[BibtexRepairProposal, ...]

    @property
    def deterministic(self) -> tuple[BibtexRepairProposal, ...]:
        return tuple(item for item in self.proposals if item.deterministic)

    @property
    def review_required(self) -> tuple[BibtexRepairProposal, ...]:
        return tuple(item for item in self.proposals if not item.deterministic)

    def summary(self) -> str:
        return (
            "Tracked BibTeX hygiene repair review\n"
            f"  Proposals              : {len(self.proposals)}\n"
            f"  Deterministic repairs  : {len(self.deterministic)}\n"
            f"  Review required        : {len(self.review_required)}"
        )

    def data(self) -> dict[str, Any]:
        return {
            "proposals": [item.data() for item in self.proposals],
            "deterministic_repairs": len(self.deterministic),
            "review_required": len(self.review_required),
        }


@dataclass(frozen=True)
class BibtexRepairDecision:
    key: str
    original_fingerprint: str
    decision: str
    replacement_text: str | None = None

    def data(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "original_fingerprint": self.original_fingerprint,
            "decision": self.decision,
            "replacement_text": self.replacement_text,
        }


@dataclass(frozen=True)
class BibtexRepairState:
    decisions: tuple[BibtexRepairDecision, ...] = ()

    def data(self) -> dict[str, Any]:
        return {
            "schema_version": BIBTEX_REPAIR_SCHEMA_VERSION,
            "decisions": [item.data() for item in self.decisions],
        }


@dataclass(frozen=True)
class ProjectBibtexRepairPlan:
    review: BibtexRepairReview
    outputs: Mapping[Path, bytes]
    backups: tuple[Path, ...]
    changed_paths: tuple[Path, ...]

    @property
    def changed(self) -> bool:
        return bool(self.changed_paths)

    def summary(self) -> str:
        return (
            "Tracked BibTeX hygiene repair application\n"
            f"  Repairs applied : {len(self.changed_paths)}\n"
            f"  Backups created : {len(self.backups)}"
        )

    def data(self) -> dict[str, Any]:
        return {
            "review": self.review.data(),
            "changed_paths": [str(path) for path in self.changed_paths],
            "backups": [str(path) for path in self.backups],
        }


def bibtex_repair_state_path(config: BibReviewConfig) -> Path:
    return config.audit.report.with_name("bibtex-hygiene-resolutions.json")


def project_bibtex_repair_review(config: BibReviewConfig) -> BibtexRepairReview:
    report = project_bibtex_contributor_hygiene(config)
    proposals: list[BibtexRepairProposal] = []
    seen: set[tuple[str, str, str]] = set()

    for finding in report.findings:
        if finding.kind not in {
            "malformed-contributor-list",
            "missing-contributor-field",
            "invalid-bibtex",
        }:
            continue
        identity = (finding.publication_id, finding.kind, finding.field)
        if identity in seen:
            continue
        seen.add(identity)
        path = Path(finding.path)
        try:
            original = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            original = ""
        repaired: str | None = None
        deterministic = False
        if finding.kind == "malformed-contributor-list":
            repaired = _repair_empty_contributor_segments(original, finding.field)
            deterministic = True
        elif finding.kind == "missing-contributor-field":
            repaired = update_bibtex_fields(
                original,
                {finding.field: finding.canonical_value},
            )
            deterministic = True

        proposals.append(
            BibtexRepairProposal(
                publication_id=finding.publication_id,
                doi=finding.doi or "",
                title=finding.title,
                path=finding.path,
                kind=finding.kind,
                field=finding.field,
                reason=finding.reason,
                deterministic=deterministic,
                original_fingerprint=_fingerprint(original),
                repaired_text=repaired,
            )
        )
    return BibtexRepairReview(proposals=tuple(proposals))


def load_bibtex_repair_state(config: BibReviewConfig) -> BibtexRepairState:
    path = bibtex_repair_state_path(config)
    if not path.exists():
        return BibtexRepairState()
    raw = read_json(path, dict)
    if raw.get("schema_version") != BIBTEX_REPAIR_SCHEMA_VERSION:
        raise ValueError("unsupported BibTeX hygiene resolution schema")
    values = raw.get("decisions")
    if not isinstance(values, list):
        raise ValueError("BibTeX hygiene resolutions decisions must be a list")
    decisions: list[BibtexRepairDecision] = []
    for index, item in enumerate(values, 1):
        if not isinstance(item, Mapping):
            raise ValueError(f"BibTeX hygiene decision {index} must be an object")
        key = item.get("key")
        fingerprint = item.get("original_fingerprint")
        decision = item.get("decision")
        replacement = item.get("replacement_text")
        if not isinstance(key, str) or not key:
            raise ValueError("BibTeX hygiene decision key must be a string")
        if not isinstance(fingerprint, str) or len(fingerprint) != 64:
            raise ValueError("BibTeX hygiene decision fingerprint must be SHA-256")
        if decision not in _DECISIONS:
            raise ValueError("unsupported BibTeX hygiene decision")
        if decision == "replacement" and (
            not isinstance(replacement, str) or not replacement.strip()
        ):
            raise ValueError("replacement BibTeX decision requires replacement_text")
        if decision == "deferred" and replacement is not None:
            raise ValueError("deferred BibTeX decision cannot contain replacement_text")
        decisions.append(
            BibtexRepairDecision(
                key=key,
                original_fingerprint=fingerprint,
                decision=decision,
                replacement_text=replacement,
            )
        )
    return BibtexRepairState(decisions=tuple(decisions))


def save_bibtex_repair_state(
    config: BibReviewConfig,
    state: BibtexRepairState,
) -> None:
    write_json(bibtex_repair_state_path(config), state.data())


def record_bibtex_replacement(
    state: BibtexRepairState,
    proposal: BibtexRepairProposal,
    replacement_text: str | None,
) -> BibtexRepairState:
    decision = "deferred" if replacement_text is None else "replacement"
    normalized = None
    if replacement_text is not None:
        normalized = canonicalize_bibtex(replacement_text)
    record = BibtexRepairDecision(
        key=proposal.key,
        original_fingerprint=proposal.original_fingerprint,
        decision=decision,
        replacement_text=normalized,
    )
    retained = tuple(item for item in state.decisions if item.key != proposal.key)
    return BibtexRepairState(decisions=(*retained, record))


def unresolved_bibtex_repair_proposals(
    review: BibtexRepairReview,
    state: BibtexRepairState,
) -> tuple[BibtexRepairProposal, ...]:
    current = {item.key: item for item in state.decisions}
    unresolved: list[BibtexRepairProposal] = []
    for proposal in review.review_required:
        decision = current.get(proposal.key)
        if decision is None or decision.decision == "deferred":
            unresolved.append(proposal)
            continue
        if decision.original_fingerprint != proposal.original_fingerprint:
            unresolved.append(proposal)
    return tuple(unresolved)


def plan_project_bibtex_repair(
    config: BibReviewConfig,
    *,
    safe_only: bool,
) -> ProjectBibtexRepairPlan:
    review = project_bibtex_repair_review(config)
    state = load_bibtex_repair_state(config)
    decisions = {item.key: item for item in state.decisions}
    outputs: dict[Path, bytes] = {}
    backups: list[Path] = []
    changed_paths: list[Path] = []

    proposals = review.deterministic if safe_only else review.proposals
    if not safe_only:
        unresolved = unresolved_bibtex_repair_proposals(review, state)
        if unresolved:
            raise ValueError(
                f"{len(unresolved)} BibTeX repair proposal(s) still require review"
            )

    for proposal in proposals:
        path = Path(proposal.path)
        original = path.read_text(encoding="utf-8")
        if _fingerprint(original) != proposal.original_fingerprint:
            raise ValueError(
                f"{path}: BibTeX changed since repair review; rerun hygiene --bibtex --review"
            )
        if proposal.deterministic:
            replacement = proposal.repaired_text
        else:
            decision = decisions[proposal.key]
            if decision.original_fingerprint != proposal.original_fingerprint:
                raise ValueError(
                    f"{path}: saved BibTeX replacement is stale; review again"
                )
            replacement = decision.replacement_text
        if replacement is None or replacement == original:
            continue
        backup = backup_path(
            config.paths.archive,
            f"bibtex-{path.stem}",
            path.suffix or ".bib",
        )
        outputs[backup] = original.encode("utf-8")
        outputs[path] = replacement.encode("utf-8")
        backups.append(backup)
        changed_paths.append(path)

    return ProjectBibtexRepairPlan(
        review=review,
        outputs=MappingProxyType(outputs),
        backups=tuple(backups),
        changed_paths=tuple(changed_paths),
    )


def apply_project_bibtex_repair(plan: ProjectBibtexRepairPlan) -> None:
    if plan.outputs:
        atomic_write_batch(plan.outputs)
