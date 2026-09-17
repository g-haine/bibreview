"""Project-state orchestration built on canonical BibReview primitives."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from .config import BibReviewConfig
from .identity import IdentityError, normalize_doi
from .pipeline.authors import (
    AuthorMappingPlan,
    apply_safe_author_mappings as apply_safe_author_mapping_data,
    plan_author_mappings,
)
from .pipeline.collect import (
    BibtexLookup,
    CitationLookup,
    CollectionResult,
    EnrichmentLookup,
    WorkProvider,
    collect as collect_publications,
)
from .pipeline.merge import MergeResult, merge_publications
from .reporting import Reporter
from .storage import (
    StorageError,
    atomic_write_batch,
    backup_path,
    bibliography_data,
    json_bytes,
    read_bibliography,
    read_json,
)


class ProjectStateError(ValueError):
    """Raised when project state cannot be updated safely."""


@dataclass(frozen=True)
class ProjectCollectionPlan:
    """Read-only description of one canonical collection operation."""

    result: CollectionResult
    outputs: Mapping[Path, bytes]
    existing_count: int

    @property
    def changed(self) -> bool:
        """Whether applying this plan would write project state."""
        return bool(self.outputs)

    def summary(self) -> str:
        """Return a compact human-readable operation summary."""
        return (
            f"submitted: {self.result.submitted_count}; "
            f"candidates: {len(self.result.candidates)}; "
            f"collected: {len(self.result.items)}; "
            f"unavailable: {len(self.result.unavailable)}; "
            f"existing: {self.existing_count}"
        )


@dataclass(frozen=True)
class ProjectAuthorMappingPlan:
    """Read-only description of one author-mapping analysis/application."""

    before: AuthorMappingPlan
    after: AuthorMappingPlan
    outputs: Mapping[Path, bytes]
    applied_count: int

    @property
    def changed(self) -> bool:
        """Whether applying this plan would update project mapping state."""
        return bool(self.outputs)


@dataclass(frozen=True)
class ProjectMergePlan:
    """Read-only description of one project merge operation."""

    result: MergeResult
    outputs: Mapping[Path, bytes]
    backup: Path | None
    incoming_count: int
    rejected_count: int
    known_count: int
    pending_count: int

    @property
    def changed(self) -> bool:
        """Whether applying this plan would write project state."""
        return bool(self.outputs)

    def summary(self) -> str:
        """Return a compact human-readable operation summary."""
        return (
            f"incoming: {self.incoming_count}; added: {len(self.result.added_ids)}; "
            f"updated: {len(self.result.updated_ids)}; unchanged: {len(self.result.unchanged_ids)}; "
            f"rejected: {self.rejected_count}; retained: {len(self.result.publications)}"
        )


def _optional_bibliography(path: Path) -> tuple:
    if not path.exists():
        return ()
    return read_bibliography(path)


def _doi_lines(path: Path) -> tuple[str, ...]:
    """Read a simple BibReview DOI state file, ignoring comments and blanks."""
    if not path.exists():
        return ()
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        raise
    values: list[str] = []
    seen: set[str] = set()
    for number, raw in enumerate(lines, 1):
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


def _lines_bytes(values: tuple[str, ...] | list[str]) -> bytes:
    return b"".join(value.encode("utf-8") + b"\n" for value in values)


def _put_if_changed(outputs: dict[Path, bytes], path: Path, content: bytes) -> None:
    """Add one output only when its on-disk bytes would actually change."""
    if path.exists() and path.read_bytes() == content:
        return
    outputs[path] = content


def plan_project_collection(
    config: BibReviewConfig,
    *,
    provider: WorkProvider,
    enrichment_lookup: EnrichmentLookup | None = None,
    citation_lookup: CitationLookup | None = None,
    bibtex_lookup: BibtexLookup | None = None,
    reporter: Reporter | None = None,
) -> ProjectCollectionPlan:
    """Build a complete collection plan without mutating project files.

    ``paths.pending`` is the DOI input queue. New publications are written to
    canonical ``paths.collected`` staging and remain pending until a later
    ``bibreview merge`` accepts or rejects them. DOI values unavailable from the
    metadata provider also remain pending so a later collection run can retry
    them.

    A non-empty staging bibliography is never overwritten: the caller must merge
    or otherwise resolve the previous batch before collecting another one.
    """
    paths = config.paths
    existing = _optional_bibliography(paths.bibliography)
    staged = _optional_bibliography(paths.collected)
    if staged:
        raise ProjectStateError(
            f"{paths.collected}: contains {len(staged)} staged publication(s); "
            "merge the existing batch before collecting again"
        )

    submitted = _doi_lines(paths.pending)
    known = list(_doi_lines(paths.known))
    known.extend(
        publication.doi
        for publication in existing
        if publication.doi is not None
    )

    used_slugs = {
        publication.permalink
        for publication in existing
        if publication.permalink
    }
    if paths.bibtex.exists():
        used_slugs.update(path.stem for path in paths.bibtex.glob("*.bib"))

    result = collect_publications(
        submitted,
        provider=provider,
        known=known,
        used_slugs=used_slugs,
        enrichment_lookup=enrichment_lookup,
        citation_lookup=citation_lookup,
        bibtex_lookup=bibtex_lookup,
        reporter=reporter,
    )

    outputs: dict[Path, bytes] = {}
    collected_bytes = json_bytes(bibliography_data(result.publications))
    pending_bytes = _lines_bytes(list(result.candidates))

    if result.candidates or paths.collected.exists():
        _put_if_changed(outputs, paths.collected, collected_bytes)
    if result.candidates or paths.pending.exists():
        _put_if_changed(outputs, paths.pending, pending_bytes)

    for item in result.items:
        if item.bibtex is None:
            continue
        slug = item.publication.permalink
        if not slug:
            raise ProjectStateError(
                f"{item.publication.id}: collected publication has no permalink for BibTeX output"
            )
        _put_if_changed(outputs, paths.bibtex / f"{slug}.bib", item.bibtex.encode("utf-8"))

    return ProjectCollectionPlan(
        result=result,
        outputs=MappingProxyType(outputs),
        existing_count=len(existing),
    )


def apply_project_collection(plan: ProjectCollectionPlan) -> None:
    """Apply a previously prepared project collection plan atomically per file."""
    if not isinstance(plan, ProjectCollectionPlan):
        raise ProjectStateError("plan must be a ProjectCollectionPlan")
    if plan.outputs:
        atomic_write_batch(plan.outputs)


def plan_project_author_mappings(
    config: BibReviewConfig,
    *,
    apply_safe: bool = False,
) -> ProjectAuthorMappingPlan:
    """Analyze canonical publication authors and optionally plan safe mappings.

    Ambiguous proposals are never written automatically. Missing mapping state is
    treated as an empty mapping so new projects can bootstrap the file through
    ``--apply-safe``.
    """
    publications = _optional_bibliography(config.paths.bibliography)
    mapping = (
        read_json(config.paths.author_mappings, dict)
        if config.paths.author_mappings.exists()
        else {}
    )
    before = plan_author_mappings(publications, mapping)
    if not apply_safe or not before.safe:
        return ProjectAuthorMappingPlan(
            before=before,
            after=before,
            outputs=MappingProxyType({}),
            applied_count=0,
        )

    updated = apply_safe_author_mapping_data(mapping, before)
    after = plan_author_mappings(publications, updated)
    outputs: dict[Path, bytes] = {}
    _put_if_changed(outputs, config.paths.author_mappings, json_bytes(updated))
    return ProjectAuthorMappingPlan(
        before=before,
        after=after,
        outputs=MappingProxyType(outputs),
        applied_count=len(before.safe),
    )


def apply_project_author_mappings(plan: ProjectAuthorMappingPlan) -> None:
    """Apply a previously prepared safe author-mapping plan."""
    if not isinstance(plan, ProjectAuthorMappingPlan):
        raise ProjectStateError("plan must be a ProjectAuthorMappingPlan")
    if plan.outputs:
        atomic_write_batch(plan.outputs)


def plan_project_merge(config: BibReviewConfig) -> ProjectMergePlan:
    """Build a complete merge plan without mutating project files.

    ``paths.collected`` is the canonical staging bibliography produced by the
    collection workflow. Accepted staged publications are merged into the main
    bibliography, their DOI values are moved from ``pending`` to ``known``, and
    the staging bibliography is emptied. DOI values present in ``rejected`` are
    discarded from the staged batch and removed from ``pending``/``review``.

    Historical PHRAISE queue byte conventions deliberately do not live here;
    they remain migration compatibility concerns.
    """
    paths = config.paths
    existing = _optional_bibliography(paths.bibliography)
    incoming = _optional_bibliography(paths.collected)
    known = list(_doi_lines(paths.known))
    pending = list(_doi_lines(paths.pending))
    rejected = set(_doi_lines(paths.rejected))
    review = list(_doi_lines(paths.review))

    accepted = [publication for publication in incoming if publication.doi not in rejected]
    rejected_count = len(incoming) - len(accepted)
    result = merge_publications(existing, accepted)

    if not incoming:
        return ProjectMergePlan(
            result=result,
            outputs=MappingProxyType({}),
            backup=None,
            incoming_count=0,
            rejected_count=0,
            known_count=len(known),
            pending_count=len(pending),
        )

    accepted_dois = [publication.doi for publication in accepted if publication.doi]
    processed = set(accepted_dois) | rejected

    known_seen = set(known)
    for doi in accepted_dois:
        if doi not in known_seen:
            known_seen.add(doi)
            known.append(doi)

    pending = [doi for doi in pending if doi not in processed]
    review = [doi for doi in review if doi not in processed]

    backup = None
    outputs: dict[Path, bytes] = {}
    if paths.bibliography.exists():
        backup = backup_path(paths.archive, "bibliography", ".json")
        outputs[backup] = paths.bibliography.read_bytes()

    outputs[paths.bibliography] = json_bytes(bibliography_data(result.publications))
    outputs[paths.collected] = json_bytes([])
    outputs[paths.known] = _lines_bytes(known)
    outputs[paths.pending] = _lines_bytes(pending)
    outputs[paths.review] = _lines_bytes(review)

    return ProjectMergePlan(
        result=result,
        outputs=MappingProxyType(outputs),
        backup=backup,
        incoming_count=len(incoming),
        rejected_count=rejected_count,
        known_count=len(known),
        pending_count=len(pending),
    )


def apply_project_merge(plan: ProjectMergePlan) -> None:
    """Apply a previously prepared project merge plan."""
    if not isinstance(plan, ProjectMergePlan):
        raise ProjectStateError("plan must be a ProjectMergePlan")
    if plan.outputs:
        atomic_write_batch(plan.outputs)
