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
