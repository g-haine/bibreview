"""Persist networked missing-field proposals outside canonical staging."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from .config import BibReviewConfig
from .pipeline.backfill import BackfillCandidate, BackfillResult, backfill
from .pipeline.collect import (
    BatchWorkProvider,
    EnrichmentLookup,
    EnrichmentManyLookup,
    WorkProvider,
)
from .project import ProjectStateError
from .reporting import Reporter
from .storage import atomic_write_batch, json_bytes, read_bibliography, read_json


BACKFILL_REVIEW_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class BackfillReview:
    """Versioned local proposal set awaiting explicit human decisions."""

    fields: tuple[str, ...]
    types: tuple[str, ...]
    scanned_count: int
    eligible_count: int
    candidates: tuple[BackfillCandidate, ...]
    unavailable: tuple[str, ...]
    no_value: tuple[str, ...]

    def data(self) -> dict[str, Any]:
        return {
            "schema_version": BACKFILL_REVIEW_SCHEMA_VERSION,
            "fields": list(self.fields),
            "types": list(self.types),
            "scanned_count": self.scanned_count,
            "eligible_count": self.eligible_count,
            "candidates": [
                {
                    "publication_id": item.publication_id,
                    "doi": item.doi,
                    "title": item.title,
                    "field": item.field,
                    "proposed_value": item.proposed_value,
                }
                for item in self.candidates
            ],
            "unavailable": list(self.unavailable),
            "no_value": list(self.no_value),
        }

    def summary(self) -> str:
        return (
            "Backfill proposals\n"
            f"  Scanned     : {self.scanned_count}\n"
            f"  Eligible    : {self.eligible_count}\n"
            f"  Proposals   : {len(self.candidates)}\n"
            f"  No value    : {len(self.no_value)}\n"
            f"  Unavailable : {len(self.unavailable)}"
        )


@dataclass(frozen=True)
class ProjectBackfillPlan:
    """Read-only plan for persisting one proposal set locally."""

    review: BackfillReview
    outputs: Mapping[Path, bytes]

    @property
    def changed(self) -> bool:
        return bool(self.outputs)

    def summary(self) -> str:
        return self.review.summary()


def backfill_review_path(config: BibReviewConfig) -> Path:
    """Return the local proposal path beside audit state."""
    path = config.audit.report.with_name("backfill.json")
    if path in {config.audit.report, config.audit.campaign}:
        raise ProjectStateError("backfill review path collides with audit state")
    return path


def backfill_review_fingerprint(review: BackfillReview) -> str:
    """Fingerprint the exact proposal set used by human decisions."""
    encoded = json.dumps(
        review.data(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def backfill_review_from_data(value: Any) -> BackfillReview:
    """Strictly load one persisted backfill proposal set."""
    if not isinstance(value, Mapping):
        raise ProjectStateError("backfill review must be an object")
    if value.get("schema_version") != BACKFILL_REVIEW_SCHEMA_VERSION:
        raise ProjectStateError(
            "unsupported backfill review schema_version: "
            f"{value.get('schema_version')!r}"
        )

    def strings(name: str) -> tuple[str, ...]:
        raw = value.get(name)
        if not isinstance(raw, list) or any(not isinstance(item, str) for item in raw):
            raise ProjectStateError(f"backfill review {name} must be a list of strings")
        return tuple(raw)

    fields = strings("fields")
    types = strings("types")
    unavailable = strings("unavailable")
    no_value = strings("no_value")
    scanned = value.get("scanned_count")
    eligible = value.get("eligible_count")
    if not isinstance(scanned, int) or isinstance(scanned, bool) or scanned < 0:
        raise ProjectStateError("backfill review scanned_count must be non-negative")
    if not isinstance(eligible, int) or isinstance(eligible, bool) or eligible < 0:
        raise ProjectStateError("backfill review eligible_count must be non-negative")

    raw_candidates = value.get("candidates")
    if not isinstance(raw_candidates, list):
        raise ProjectStateError("backfill review candidates must be a list")
    candidates: list[BackfillCandidate] = []
    for index, raw in enumerate(raw_candidates, 1):
        if not isinstance(raw, Mapping):
            raise ProjectStateError(f"backfill candidate {index} must be an object")
        data: dict[str, str] = {}
        for name in ("publication_id", "doi", "title", "field", "proposed_value"):
            item = raw.get(name)
            if not isinstance(item, str) or (name != "title" and not item):
                raise ProjectStateError(
                    f"backfill candidate {index}.{name} must be a string"
                )
            data[name] = item
        candidates.append(BackfillCandidate(**data))
    keys = [item.key for item in candidates]
    if len(keys) != len(set(keys)):
        raise ProjectStateError("backfill review contains duplicate publication/field keys")

    return BackfillReview(
        fields=fields,
        types=types,
        scanned_count=scanned,
        eligible_count=eligible,
        candidates=tuple(candidates),
        unavailable=unavailable,
        no_value=no_value,
    )


def load_project_backfill_review(config: BibReviewConfig) -> BackfillReview:
    """Load the currently persisted backfill proposal set."""
    path = backfill_review_path(config)
    if not path.exists():
        raise ProjectStateError(
            f"{path}: no backfill proposals; run bibreview backfill --field FIELD first"
        )
    return backfill_review_from_data(read_json(path, dict))


def plan_project_backfill(
    config: BibReviewConfig,
    *,
    provider: WorkProvider,
    fields: tuple[str, ...],
    types: tuple[str, ...] = (),
    enrichment_lookup: EnrichmentLookup | None = None,
    batch_provider: BatchWorkProvider | None = None,
    enrichment_many_lookup: EnrichmentManyLookup | None = None,
    reporter: Reporter | None = None,
) -> ProjectBackfillPlan:
    """Build and persist proposals without touching collected/canonical state."""
    staged = (
        read_bibliography(config.paths.collected)
        if config.paths.collected.exists()
        else ()
    )
    if staged:
        raise ProjectStateError(
            f"{config.paths.collected}: contains {len(staged)} staged publication(s); "
            "merge the existing batch before proposing a backfill"
        )
    existing = (
        read_bibliography(config.paths.bibliography)
        if config.paths.bibliography.exists()
        else ()
    )
    result: BackfillResult = backfill(
        existing,
        provider=provider,
        fields=fields,
        types=types,
        enrichment_lookup=enrichment_lookup,
        batch_provider=batch_provider,
        enrichment_many_lookup=enrichment_many_lookup,
        reporter=reporter,
    )
    review = BackfillReview(
        fields=tuple(dict.fromkeys(fields)),
        types=tuple(dict.fromkeys(types)),
        scanned_count=result.scanned_count,
        eligible_count=result.eligible_count,
        candidates=result.candidates,
        unavailable=result.unavailable,
        no_value=result.no_value,
    )
    path = backfill_review_path(config)
    content = json_bytes(review.data())
    outputs: dict[Path, bytes] = {}
    if not path.exists() or path.read_bytes() != content:
        outputs[path] = content
    return ProjectBackfillPlan(review=review, outputs=MappingProxyType(outputs))


def apply_project_backfill_plan(plan: ProjectBackfillPlan) -> None:
    """Persist one prepared proposal set locally."""
    if not isinstance(plan, ProjectBackfillPlan):
        raise ProjectStateError("plan must be a ProjectBackfillPlan")
    if plan.outputs:
        atomic_write_batch(plan.outputs)
