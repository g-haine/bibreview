"""Project-state orchestration for refreshing existing publications."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from .config import BibReviewConfig
from .identity import IdentityError, normalize_doi
from .pipeline.collect import BibtexLookup, CitationLookup, EnrichmentLookup, WorkProvider
from .pipeline.refresh import RefreshResult, refresh as refresh_publications
from .project import ProjectStateError
from .reporting import Reporter
from .storage import (
    atomic_write_batch,
    backup_path,
    bibliography_document_data,
    json_bytes,
    read_bibliography,
)


@dataclass(frozen=True)
class ProjectRefreshPlan:
    """Read-only description of one canonical refresh/recollect operation."""

    result: RefreshResult
    outputs: Mapping[Path, bytes]
    orphaned_known: tuple[str, ...]
    bibtex_backups: tuple[Path, ...]

    @property
    def changed(self) -> bool:
        """Whether applying this plan would update project state."""
        return bool(self.outputs)

    def summary(self) -> str:
        """Return a compact human-readable operation summary."""
        return (
            f"scanned: {self.result.scanned_count}; "
            f"eligible: {self.result.eligible_count}; "
            f"stale: {len(self.result.candidates)}; "
            f"refreshed: {len(self.result.items)}; "
            f"unavailable: {len(self.result.unavailable)}; "
            f"orphaned-known: {len(self.orphaned_known)}"
        )


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
    """Plan refresh/recollection without deleting canonical publications.

    Existing publications remain authoritative until a later ``bibreview merge``.
    Refreshed versions are written to ``paths.collected`` staging. A non-empty
    staging bibliography is never overwritten.

    As a separate state-consistency repair, DOI values present in ``known`` but
    absent from the canonical bibliography are removed from ``known`` and queued
    in ``pending`` so the normal collection workflow can recover them.
    """
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

    result = refresh_publications(
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

    outputs: dict[Path, bytes] = {}
    backups: list[Path] = []
    if result.items:
        _put_if_changed(
            outputs,
            paths.collected,
            json_bytes(bibliography_document_data(result.publications)),
        )
        for item in result.items:
            slug = item.publication.permalink
            target = paths.bibtex / f"{slug}.bib"
            current = item.bibtex.encode("utf-8")
            if target.exists():
                old = target.read_bytes()
                if old != current:
                    archive = backup_path(paths.archive, slug, ".bib")
                    outputs[archive] = old
                    backups.append(archive)
            _put_if_changed(outputs, target, current)

    known = list(_doi_lines(paths.known))
    in_bibliography = {
        publication.doi
        for publication in existing
        if publication.doi is not None
    }
    orphaned_known = tuple(sorted(doi for doi in known if doi not in in_bibliography))
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
        result=result,
        outputs=MappingProxyType(outputs),
        orphaned_known=orphaned_known,
        bibtex_backups=tuple(backups),
    )


def apply_project_refresh(plan: ProjectRefreshPlan) -> None:
    """Apply a previously prepared refresh plan atomically per destination."""
    if not isinstance(plan, ProjectRefreshPlan):
        raise ProjectStateError("plan must be a ProjectRefreshPlan")
    if plan.outputs:
        atomic_write_batch(plan.outputs)
