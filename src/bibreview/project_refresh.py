"""Project-state orchestration for safe, human-reviewed refresh proposals."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from .config import BibReviewConfig
from .identity import IdentityError, normalize_doi
from .pipeline.audit import AuditValue
from .pipeline.backfill import BackfillCandidate
from .pipeline.collect import BibtexLookup, CitationLookup, EnrichmentLookup, WorkProvider
from .pipeline.refresh import RefreshDifference, RefreshResult, refresh as refresh_publications
from .project import ProjectStateError
from .reporting import Reporter
from .storage import (
    atomic_write_batch,
    json_bytes,
    read_bibliography,
    read_json,
)


REFRESH_REVIEW_SCHEMA_VERSION = 1


def _json_value(value: AuditValue) -> str | list[str]:
    return list(value) if isinstance(value, tuple) else value


def _audit_value(value: Any, *, name: str) -> AuditValue:
    if isinstance(value, str):
        return value
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return tuple(value)
    raise ProjectStateError(f"{name} must be a string or list of strings")


@dataclass(frozen=True)
class RefreshReview:
    """Persisted safe proposals and non-promotable collateral differences."""

    scanned_count: int
    eligible_count: int
    stale_dois: tuple[str, ...]
    proposals: tuple[BackfillCandidate, ...]
    collateral: tuple[RefreshDifference, ...]
    unavailable: tuple[str, ...]
    reasons: Mapping[str, str]

    def data(self) -> dict[str, Any]:
        return {
            "schema_version": REFRESH_REVIEW_SCHEMA_VERSION,
            "scanned_count": self.scanned_count,
            "eligible_count": self.eligible_count,
            "stale_dois": list(self.stale_dois),
            "proposals": [
                {
                    "publication_id": item.publication_id,
                    "doi": item.doi,
                    "title": item.title,
                    "field": item.field,
                    "proposed_value": item.proposed_value,
                }
                for item in self.proposals
            ],
            "collateral": [
                {
                    "publication_id": item.publication_id,
                    "doi": item.doi,
                    "title": item.title,
                    "field": item.field,
                    "classification": item.classification,
                    "current_value": _json_value(item.current_value),
                    "proposed_value": _json_value(item.proposed_value),
                }
                for item in self.collateral
            ],
            "unavailable": list(self.unavailable),
            "reasons": dict(self.reasons),
        }

    def summary(self) -> str:
        return (
            "Refresh review\n"
            f"  Scanned              : {self.scanned_count}\n"
            f"  Eligible             : {self.eligible_count}\n"
            f"  Stale BibTeX         : {len(self.stale_dois)}\n"
            f"  Safe proposals       : {len(self.proposals)}\n"
            f"  Collateral differences: {len(self.collateral)}\n"
            f"  Unavailable          : {len(self.unavailable)}"
        )


@dataclass(frozen=True)
class ProjectRefreshPlan:
    """Read-only plan for persisting refresh review state and queue repairs."""

    review: RefreshReview
    outputs: Mapping[Path, bytes]
    orphaned_known: tuple[str, ...]

    @property
    def changed(self) -> bool:
        return bool(self.outputs)

    def summary(self) -> str:
        suffix = f"\n  Orphaned known       : {len(self.orphaned_known)}"
        return self.review.summary() + suffix


def refresh_review_path(config: BibReviewConfig) -> Path:
    """Return the local refresh-review path beside configured audit state."""
    path = config.audit.report.with_name("refresh.json")
    if path in {config.audit.report, config.audit.campaign}:
        raise ProjectStateError("refresh review path collides with audit state")
    return path


def refresh_review_fingerprint(review: RefreshReview) -> str:
    """Fingerprint the exact refresh proposals and collateral evidence."""
    encoded = json.dumps(
        review.data(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def refresh_review_from_data(value: Any) -> RefreshReview:
    """Strictly load one persisted refresh-review document."""
    if not isinstance(value, Mapping):
        raise ProjectStateError("refresh review must be an object")
    if value.get("schema_version") != REFRESH_REVIEW_SCHEMA_VERSION:
        raise ProjectStateError(
            "unsupported refresh review schema_version: "
            f"{value.get('schema_version')!r}"
        )

    def strings(name: str) -> tuple[str, ...]:
        raw = value.get(name)
        if not isinstance(raw, list) or any(not isinstance(item, str) for item in raw):
            raise ProjectStateError(f"refresh review {name} must be a list of strings")
        return tuple(raw)

    scanned = value.get("scanned_count")
    eligible = value.get("eligible_count")
    if not isinstance(scanned, int) or isinstance(scanned, bool) or scanned < 0:
        raise ProjectStateError("refresh review scanned_count must be non-negative")
    if not isinstance(eligible, int) or isinstance(eligible, bool) or eligible < 0:
        raise ProjectStateError("refresh review eligible_count must be non-negative")

    raw_proposals = value.get("proposals")
    if not isinstance(raw_proposals, list):
        raise ProjectStateError("refresh review proposals must be a list")
    proposals: list[BackfillCandidate] = []
    for index, raw in enumerate(raw_proposals, 1):
        if not isinstance(raw, Mapping):
            raise ProjectStateError(f"refresh proposal {index} must be an object")
        data: dict[str, str] = {}
        for name in ("publication_id", "doi", "title", "field", "proposed_value"):
            item = raw.get(name)
            if not isinstance(item, str) or (name != "title" and not item):
                raise ProjectStateError(
                    f"refresh proposal {index}.{name} must be a string"
                )
            data[name] = item
        proposals.append(BackfillCandidate(**data))

    raw_collateral = value.get("collateral")
    if not isinstance(raw_collateral, list):
        raise ProjectStateError("refresh review collateral must be a list")
    collateral: list[RefreshDifference] = []
    for index, raw in enumerate(raw_collateral, 1):
        if not isinstance(raw, Mapping):
            raise ProjectStateError(
                f"refresh collateral {index} must be an object"
            )
        text: dict[str, str] = {}
        for name in (
            "publication_id",
            "doi",
            "title",
            "field",
            "classification",
        ):
            item = raw.get(name)
            if not isinstance(item, str) or (name != "title" and not item):
                raise ProjectStateError(
                    f"refresh collateral {index}.{name} must be a string"
                )
            text[name] = item
        collateral.append(
            RefreshDifference(
                publication_id=text["publication_id"],
                doi=text["doi"],
                title=text["title"],
                field=text["field"],
                classification=text["classification"],
                current_value=_audit_value(
                    raw.get("current_value"),
                    name=f"refresh collateral {index}.current_value",
                ),
                proposed_value=_audit_value(
                    raw.get("proposed_value"),
                    name=f"refresh collateral {index}.proposed_value",
                ),
            )
        )

    raw_reasons = value.get("reasons")
    if not isinstance(raw_reasons, Mapping) or any(
        not isinstance(key, str) or not isinstance(item, str)
        for key, item in raw_reasons.items()
    ):
        raise ProjectStateError("refresh review reasons must be a string mapping")

    proposal_keys = [item.key for item in proposals]
    if len(proposal_keys) != len(set(proposal_keys)):
        raise ProjectStateError(
            "refresh review contains duplicate publication/field proposals"
        )

    return RefreshReview(
        scanned_count=scanned,
        eligible_count=eligible,
        stale_dois=strings("stale_dois"),
        proposals=tuple(proposals),
        collateral=tuple(collateral),
        unavailable=strings("unavailable"),
        reasons=MappingProxyType(dict(raw_reasons)),
    )


def load_project_refresh_review(config: BibReviewConfig) -> RefreshReview:
    """Load the currently persisted safe refresh review."""
    path = refresh_review_path(config)
    if not path.exists():
        raise ProjectStateError(
            f"{path}: no refresh review; run bibreview refresh first"
        )
    return refresh_review_from_data(read_json(path, dict))


def _optional_bibliography(path: Path) -> tuple:
    return read_bibliography(path) if path.exists() else ()


def _doi_lines(path: Path) -> tuple[str, ...]:
    if not path.exists():
        return ()
    values: list[str] = []
    seen: set[str] = set()
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        value = raw.strip()
        if not value or value.startswith("#"):
            continue
        try:
            doi = normalize_doi(value)
        except IdentityError as error:
            raise ProjectStateError(f"{path}: line {number}: {error}") from error
        if doi not in seen:
            seen.add(doi)
            values.append(doi)
    return tuple(values)


def _lines_bytes(values: list[str] | tuple[str, ...]) -> bytes:
    return b"".join(value.encode("utf-8") + b"\n" for value in values)


def _put_if_changed(outputs: dict[Path, bytes], path: Path, content: bytes) -> None:
    if path.exists() and path.read_bytes() == content:
        return
    outputs[path] = content


def plan_project_refresh(
    config: BibReviewConfig,
    *,
    provider: WorkProvider,
    enrichment_lookup: EnrichmentLookup | None = None,
    citation_lookup: CitationLookup | None = None,
    bibtex_lookup: BibtexLookup,
    reporter: Reporter | None = None,
) -> ProjectRefreshPlan:
    """Plan safe refresh evidence without writing staging or tracked BibTeX."""
    paths = config.paths
    existing = _optional_bibliography(paths.bibliography)
    staged = _optional_bibliography(paths.collected)
    if staged:
        raise ProjectStateError(
            f"{paths.collected}: contains {len(staged)} staged publication(s); "
            "merge the existing batch before refreshing"
        )

    def stored_bibtex(publication) -> str | None:
        target = paths.bibtex / f"{publication.permalink}.bib"
        return target.read_text(encoding="utf-8") if target.exists() else None

    result: RefreshResult = refresh_publications(
        existing,
        provider=provider,
        stored_bibtex_lookup=stored_bibtex,
        bibtex_lookup=bibtex_lookup,
        types=config.refresh.types,
        when_missing_any=config.refresh.when_missing_any,
        enrichment_lookup=enrichment_lookup,
        citation_lookup=citation_lookup,
        reporter=reporter,
    )
    review = RefreshReview(
        scanned_count=result.scanned_count,
        eligible_count=result.eligible_count,
        stale_dois=result.candidates,
        proposals=result.proposals,
        collateral=result.collateral,
        unavailable=result.unavailable,
        reasons=MappingProxyType({
            item.doi: item.reason
            for item in result.items
        }),
    )

    outputs: dict[Path, bytes] = {}
    review_path = refresh_review_path(config)
    review_bytes = json_bytes(review.data())
    _put_if_changed(outputs, review_path, review_bytes)

    known = list(_doi_lines(paths.known))
    in_bibliography = {
        publication.doi
        for publication in existing
        if publication.doi is not None
    }
    orphaned_known = tuple(
        sorted(doi for doi in known if doi not in in_bibliography)
    )
    if orphaned_known:
        orphaned = set(orphaned_known)
        retained_known = [doi for doi in known if doi not in orphaned]
        pending = list(_doi_lines(paths.pending))
        pending_seen = set(pending)
        for doi in orphaned_known:
            if doi not in pending_seen:
                pending.append(doi)
                pending_seen.add(doi)
        _put_if_changed(outputs, paths.known, _lines_bytes(retained_known))
        _put_if_changed(outputs, paths.pending, _lines_bytes(pending))

    return ProjectRefreshPlan(
        review=review,
        outputs=MappingProxyType(outputs),
        orphaned_known=orphaned_known,
    )


def apply_project_refresh(plan: ProjectRefreshPlan) -> None:
    """Persist refresh review state and safe queue repairs only."""
    if not isinstance(plan, ProjectRefreshPlan):
        raise ProjectStateError("plan must be a ProjectRefreshPlan")
    if plan.outputs:
        atomic_write_batch(plan.outputs)


def _format_value(value: AuditValue) -> str:
    if isinstance(value, tuple):
        return "; ".join(value) if value else "(missing)"
    return value if value else "(missing)"


def format_project_refresh_review(
    review: RefreshReview,
    *,
    verbose: bool = False,
) -> str:
    """Format safe proposals plus non-promotable collateral differences."""
    lines = [review.summary()]
    if not verbose:
        if review.collateral:
            lines.append(
                "Use -v refresh --review to inspect every collateral difference."
            )
        return "\n".join(lines)

    for proposal in review.proposals:
        lines.extend((
            "",
            f"{proposal.doi} — {proposal.title}",
            f"  SAFE MISSING FIELD: {proposal.field}",
            "    current : (missing)",
            f"    proposed: {proposal.proposed_value}",
        ))
    for item in review.collateral:
        lines.extend((
            "",
            f"{item.doi} — {item.title}",
            f"  COLLATERAL (never auto-applied): {item.field}",
            f"    class   : {item.classification}",
            f"    current : {_format_value(item.current_value)}",
            f"    provider: {_format_value(item.proposed_value)}",
        ))
    return "\n".join(lines)
