"""Resumable human decisions for contributor hygiene migration."""

from __future__ import annotations

from dataclasses import dataclass, replace
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Mapping

from .config import BibReviewConfig
from .project import ProjectStateError
from .storage import read_json, write_json


SCHEMA_VERSION = 1
_DECISIONS = frozenset({"keep", "remove", "deferred"})


@dataclass(frozen=True)
class ContributorHygieneCandidate:
    publication_id: str
    doi: str
    title: str
    role: str
    index: int
    name: str
    source_fields: Mapping[str, object]

    @property
    def key(self) -> str:
        return f"{self.publication_id}:{self.role}:{self.index}:{self.name}"

    def data(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "publication_id": self.publication_id,
            "doi": self.doi,
            "title": self.title,
            "role": self.role,
            "index": self.index,
            "name": self.name,
            "source_fields": dict(self.source_fields),
        }


@dataclass(frozen=True)
class ContributorHygieneReview:
    scanned_publications: int
    candidates: tuple[ContributorHygieneCandidate, ...]

    def summary(self) -> str:
        publications = len({item.publication_id for item in self.candidates})
        return (
            "Canonical contributor hygiene review\n"
            f"  Publications scanned : {self.scanned_publications}\n"
            f"  Affected publications: {publications}\n"
            f"  Review candidates     : {len(self.candidates)}"
        )

    def data(self) -> dict[str, Any]:
        return {
            "scanned_publications": self.scanned_publications,
            "affected_publications": len(
                {item.publication_id for item in self.candidates}
            ),
            "review_candidates": len(self.candidates),
            "candidates": [item.data() for item in self.candidates],
        }


@dataclass(frozen=True)
class ContributorHygieneDecision:
    key: str
    decision: str

    def data(self) -> dict[str, str]:
        return {"key": self.key, "decision": self.decision}


@dataclass(frozen=True)
class ContributorHygieneState:
    review_fingerprint: str
    total_candidates: int
    decisions: tuple[ContributorHygieneDecision, ...] = ()

    def data(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "review_fingerprint": self.review_fingerprint,
            "total_candidates": self.total_candidates,
            "decisions": [item.data() for item in self.decisions],
        }


def contributor_hygiene_resolution_path(config: BibReviewConfig) -> Path:
    return config.audit.report.with_name("contributor-hygiene-resolutions.json")


def contributor_hygiene_pending_cleanup_path(config: BibReviewConfig) -> Path:
    return config.audit.report.with_name("contributor-hygiene-pending-cleanup.json")


def review_fingerprint(review: ContributorHygieneReview) -> str:
    payload = [item.data() for item in review.candidates]
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def load_contributor_hygiene_state(
    config: BibReviewConfig,
    review: ContributorHygieneReview,
) -> ContributorHygieneState:
    path = contributor_hygiene_resolution_path(config)
    expected = review_fingerprint(review)
    if not path.exists():
        return ContributorHygieneState(
            review_fingerprint=expected,
            total_candidates=len(review.candidates),
        )
    raw = read_json(path, dict)
    if raw.get("schema_version") != SCHEMA_VERSION:
        raise ProjectStateError("unsupported contributor hygiene resolution schema")
    if raw.get("review_fingerprint") != expected:
        raise ProjectStateError(
            "contributor hygiene resolutions do not match the current canonical review"
        )
    total = raw.get("total_candidates")
    if total != len(review.candidates):
        raise ProjectStateError(
            "contributor hygiene resolution count does not match current review"
        )
    raw_decisions = raw.get("decisions")
    if not isinstance(raw_decisions, list):
        raise ProjectStateError("contributor hygiene decisions must be a list")
    decisions: list[ContributorHygieneDecision] = []
    valid_keys = {item.key for item in review.candidates}
    for raw_decision in raw_decisions:
        if not isinstance(raw_decision, Mapping):
            raise ProjectStateError("contributor hygiene decision must be an object")
        key = raw_decision.get("key")
        decision = raw_decision.get("decision")
        if not isinstance(key, str) or key not in valid_keys:
            raise ProjectStateError("contributor hygiene decision key is stale or invalid")
        if decision not in _DECISIONS:
            raise ProjectStateError("unsupported contributor hygiene decision")
        decisions.append(ContributorHygieneDecision(key=key, decision=decision))
    if len({item.key for item in decisions}) != len(decisions):
        raise ProjectStateError("duplicate contributor hygiene decisions")
    return ContributorHygieneState(
        review_fingerprint=expected,
        total_candidates=total,
        decisions=tuple(decisions),
    )


def save_contributor_hygiene_state(
    config: BibReviewConfig,
    state: ContributorHygieneState,
) -> None:
    write_json(contributor_hygiene_resolution_path(config), state.data())


def record_contributor_hygiene_decision(
    state: ContributorHygieneState,
    candidate: ContributorHygieneCandidate,
    *,
    decision: str,
) -> ContributorHygieneState:
    if decision not in _DECISIONS:
        raise ProjectStateError(f"unsupported contributor hygiene decision: {decision}")
    current = {item.key: item for item in state.decisions}
    current[candidate.key] = ContributorHygieneDecision(
        key=candidate.key,
        decision=decision,
    )
    return replace(
        state,
        decisions=tuple(
            current[key]
            for key in sorted(current)
        ),
    )


def unresolved_contributor_hygiene_candidates(
    review: ContributorHygieneReview,
    state: ContributorHygieneState,
) -> tuple[ContributorHygieneCandidate, ...]:
    resolved = {
        item.key
        for item in state.decisions
        if item.decision in {"keep", "remove"}
    }
    return tuple(item for item in review.candidates if item.key not in resolved)


def contributor_hygiene_counts(
    review: ContributorHygieneReview,
    state: ContributorHygieneState,
) -> dict[str, int]:
    values = {"keep": 0, "remove": 0, "deferred": 0}
    for item in state.decisions:
        values[item.decision] += 1
    values["unresolved"] = len(
        unresolved_contributor_hygiene_candidates(review, state)
    )
    return values
