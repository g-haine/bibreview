"""Persist explicit human decisions that two author identities are distinct."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from .config import BibReviewConfig
from .pipeline.authors import AuthorMappingError, validate_author_mappings
from .storage import StorageError, json_bytes, read_json

SCHEMA_VERSION = 1


def author_distinct_path(config: BibReviewConfig) -> Path:
    return config.paths.author_mappings.with_name("author_distinct.json")


def canonical_pair(left: str, right: str) -> tuple[str, str]:
    a = left.strip()
    b = right.strip()
    if not a or not b:
        raise AuthorMappingError("author identity slugs must be non-empty")
    if a == b:
        raise AuthorMappingError("reviewed-distinct identities must differ")
    return tuple(sorted((a, b)))


def pair_fingerprint(mapping: Mapping[str, object], pair: tuple[str, str]) -> str:
    canonical, _ = validate_author_mappings(mapping)
    left, right = canonical_pair(*pair)
    if left not in canonical:
        raise AuthorMappingError(f"unknown author identity {left!r}")
    if right not in canonical:
        raise AuthorMappingError(f"unknown author identity {right!r}")
    payload = {
        "slugs": [left, right],
        "names": {left: list(canonical[left]), right: list(canonical[right])},
    }
    digest = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return "sha256:" + digest


@dataclass(frozen=True)
class ReviewedDistinctDecision:
    slugs: tuple[str, str]
    fingerprint: str

    def data(self) -> dict[str, Any]:
        return {"slugs": list(self.slugs), "fingerprint": self.fingerprint}


@dataclass(frozen=True)
class ReviewedDistinctState:
    decisions: tuple[ReviewedDistinctDecision, ...] = ()

    def data(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "decisions": [decision.data() for decision in self.decisions],
        }


def load_reviewed_distinct_state(config: BibReviewConfig) -> ReviewedDistinctState:
    path = author_distinct_path(config)
    if not path.exists():
        return ReviewedDistinctState()
    raw = read_json(path, dict)
    if raw.get("schema_version") != SCHEMA_VERSION:
        raise StorageError(
            f"{path}: unsupported reviewed-distinct schema version "
            f"{raw.get('schema_version')!r}"
        )
    records = raw.get("decisions")
    if not isinstance(records, list):
        raise StorageError(f"{path}: decisions must be a list")

    decisions: list[ReviewedDistinctDecision] = []
    seen: set[tuple[str, str]] = set()
    for index, record in enumerate(records, 1):
        if not isinstance(record, dict) or set(record) != {"slugs", "fingerprint"}:
            raise StorageError(
                f"{path}: decisions[{index}] must contain slugs and fingerprint"
            )
        slugs = record["slugs"]
        fingerprint = record["fingerprint"]
        if (
            not isinstance(slugs, list)
            or len(slugs) != 2
            or any(not isinstance(value, str) for value in slugs)
        ):
            raise StorageError(
                f"{path}: decisions[{index}].slugs must contain two strings"
            )
        pair = canonical_pair(slugs[0], slugs[1])
        if pair in seen:
            raise StorageError(f"{path}: duplicate reviewed-distinct pair {pair!r}")
        seen.add(pair)
        if not isinstance(fingerprint, str) or not fingerprint.startswith("sha256:"):
            raise StorageError(
                f"{path}: decisions[{index}].fingerprint must be a sha256 fingerprint"
            )
        decisions.append(
            ReviewedDistinctDecision(slugs=pair, fingerprint=fingerprint)
        )
    decisions.sort(key=lambda item: item.slugs)
    return ReviewedDistinctState(tuple(decisions))


def reviewed_distinct_status(
    mapping: Mapping[str, object],
    candidate_pairs: set[tuple[str, str]],
    state: ReviewedDistinctState,
) -> tuple[frozenset[tuple[str, str]], int]:
    valid: set[tuple[str, str]] = set()
    stale = 0
    for decision in state.decisions:
        try:
            current = pair_fingerprint(mapping, decision.slugs)
        except AuthorMappingError:
            stale += 1
            continue
        if decision.slugs in candidate_pairs and current == decision.fingerprint:
            valid.add(decision.slugs)
        else:
            stale += 1
    return frozenset(valid), stale


def mark_reviewed_distinct(
    mapping: Mapping[str, object],
    state: ReviewedDistinctState,
    *,
    left_slug: str,
    right_slug: str,
    candidate_pairs: set[tuple[str, str]],
) -> ReviewedDistinctState:
    pair = canonical_pair(left_slug, right_slug)
    if pair not in candidate_pairs:
        raise AuthorMappingError(
            f"author identity pair {pair!r} is not a current initial/full-name candidate"
        )
    decision = ReviewedDistinctDecision(
        slugs=pair,
        fingerprint=pair_fingerprint(mapping, pair),
    )
    updated = {item.slugs: item for item in state.decisions if item.slugs != pair}
    updated[pair] = decision
    return ReviewedDistinctState(tuple(updated[key] for key in sorted(updated)))


def reconcile_reviewed_distinct_state(
    state: ReviewedDistinctState,
    *,
    operation: str,
    source_slug: str,
    target_slug: str,
    updated_mapping: Mapping[str, object],
) -> ReviewedDistinctState:
    if operation == "merge":
        affected = {source_slug, target_slug}
        return ReviewedDistinctState(
            tuple(
                item
                for item in state.decisions
                if not affected.intersection(item.slugs)
            )
        )
    if operation != "rename":
        return state

    updated: dict[tuple[str, str], ReviewedDistinctDecision] = {}
    for item in state.decisions:
        values = [
            target_slug if slug == source_slug else slug
            for slug in item.slugs
        ]
        pair = canonical_pair(values[0], values[1])
        try:
            fingerprint = pair_fingerprint(updated_mapping, pair)
        except AuthorMappingError:
            continue
        updated[pair] = ReviewedDistinctDecision(
            slugs=pair,
            fingerprint=fingerprint,
        )
    return ReviewedDistinctState(tuple(updated[key] for key in sorted(updated)))


def reviewed_distinct_bytes(state: ReviewedDistinctState) -> bytes:
    return json_bytes(state.data())
