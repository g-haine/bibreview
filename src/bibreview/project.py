"""Project-state orchestration built on canonical BibReview primitives."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from .config import BibReviewConfig
from .identity import IdentityError, normalize_doi
from .pipeline.merge import MergeResult, merge_publications
from .storage import (
    StorageError,
    atomic_write_batch,
    backup_path,
    bibliography_data,
    json_bytes,
    read_bibliography,
)


class ProjectStateError(ValueError):
    """Raised when project state cannot be merged safely."""


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
