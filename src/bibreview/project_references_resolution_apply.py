"""Stage completed human reference resolutions for ordinary merge."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from .config import BibReviewConfig
from .identity import publication_identity_label
from .pipeline.references import references_fingerprint
from .project import ProjectStateError
from .project_references import project_references_review
from .references_resolution import (
    is_human_reference_decision,
    load_project_reference_resolutions,
    matching_reference_resolution,
    unresolved_reference_candidates,
)
from .storage import (
    atomic_write_batch,
    bibliography_document_data,
    json_bytes,
    read_bibliography,
)


@dataclass(frozen=True)
class ReferenceResolutionApplyChange:
    """One human-resolved publication staged for ordinary merge."""

    publication_id: str
    doi: str
    title: str
    decision: str
    source_fingerprint: str
    resolved_fingerprint: str

    def data(self) -> dict[str, Any]:
        return {
            "publication_id": self.publication_id,
            "doi": self.doi,
            "title": self.title,
            "decision": self.decision,
            "source_fingerprint": self.source_fingerprint,
            "resolved_fingerprint": self.resolved_fingerprint,
        }


@dataclass(frozen=True)
class ProjectReferencesResolutionApplyPlan:
    """Read-only staging plan for completed explicit human decisions."""

    changes: tuple[ReferenceResolutionApplyChange, ...]
    outputs: Mapping[Path, bytes]
    completed_publication_ids: tuple[str, ...]
    kept_canonical_ids: tuple[str, ...]

    @property
    def changed(self) -> bool:
        return bool(self.changes)

    def summary(self) -> str:
        return (
            "Reference reviewed application\n"
            f"  Publications to stage : {len(self.changes)}\n"
            f"  Already completed     : {len(self.completed_publication_ids)}\n"
            f"  Keep canonical        : {len(self.kept_canonical_ids)}"
        )

    def data(self) -> dict[str, Any]:
        return {
            "publications_to_stage": len(self.changes),
            "already_completed": len(self.completed_publication_ids),
            "keep_canonical": len(self.kept_canonical_ids),
            "completed_publication_ids": list(self.completed_publication_ids),
            "kept_canonical_ids": list(self.kept_canonical_ids),
            "changes": [change.data() for change in self.changes],
        }


def _optional_bibliography(path: Path):
    return read_bibliography(path) if path.exists() else ()


def plan_project_references_resolution_apply(
    config: BibReviewConfig,
) -> ProjectReferencesResolutionApplyPlan:
    """Stage only terminal explicit human reference decisions."""
    staged = _optional_bibliography(config.paths.collected)
    if staged:
        raise ProjectStateError(
            f"{config.paths.collected}: contains {len(staged)} staged publication(s); "
            "merge the existing batch before applying reference decisions"
        )

    review = project_references_review(config)
    state = load_project_reference_resolutions(config)
    unresolved = unresolved_reference_candidates(review, state)
    if unresolved:
        raise ProjectStateError(
            "reference decisions must be complete before --apply "
            f"({len(unresolved)} unresolved/deferred)"
        )

    canonical = read_bibliography(config.paths.bibliography)
    originals = {publication.id: publication for publication in canonical}
    staged_publications = []
    changes: list[ReferenceResolutionApplyChange] = []
    completed_ids: list[str] = []
    kept_ids: list[str] = []

    for item in review.items:
        decision = matching_reference_resolution(state, item)
        if (
            decision is None
            or decision.decision == "deferred"
            or not is_human_reference_decision(decision)
        ):
            continue
        publication = originals.get(item.publication_id)
        if publication is None:
            raise ProjectStateError(
                f"{item.publication_id}: canonical publication is missing"
            )

        current_fingerprint = references_fingerprint(publication.references)
        if current_fingerprint == decision.resolved_fingerprint:
            completed_ids.append(publication.id)
            continue
        if current_fingerprint != decision.source_fingerprint:
            raise ProjectStateError(
                f"{publication.id}: stale reference resolution; canonical references "
                "match neither source nor resolved fingerprint"
            )

        if decision.decision == "keep-canonical":
            kept_ids.append(publication.id)
            continue
        if decision.decision == "use-provider":
            resolved_references = item.proposed_references
        elif decision.decision == "custom":
            resolved_references = decision.custom_references
        else:
            raise ProjectStateError(
                f"{publication.id}: unsupported reference decision "
                f"{decision.decision!r}"
            )

        resolved_fingerprint = references_fingerprint(resolved_references)
        if resolved_fingerprint != decision.resolved_fingerprint:
            raise ProjectStateError(
                f"{publication.id}: resolved reference fingerprint mismatch"
            )
        if resolved_references == publication.references:
            completed_ids.append(publication.id)
            continue

        staged_publications.append(
            replace(publication, references=resolved_references)
        )
        changes.append(
            ReferenceResolutionApplyChange(
                publication_id=publication.id,
                doi=publication_identity_label(publication.id, publication.doi),
                title=publication.title,
                decision=decision.decision,
                source_fingerprint=decision.source_fingerprint,
                resolved_fingerprint=decision.resolved_fingerprint,
            )
        )

    outputs: dict[Path, bytes] = {}
    if staged_publications:
        outputs[config.paths.collected] = json_bytes(
            bibliography_document_data(tuple(staged_publications))
        )

    return ProjectReferencesResolutionApplyPlan(
        changes=tuple(changes),
        outputs=MappingProxyType(outputs),
        completed_publication_ids=tuple(completed_ids),
        kept_canonical_ids=tuple(kept_ids),
    )


def apply_project_references_resolution_apply(
    plan: ProjectReferencesResolutionApplyPlan,
) -> None:
    """Write one prepared human reference staging plan."""
    if not isinstance(plan, ProjectReferencesResolutionApplyPlan):
        raise ProjectStateError(
            "plan must be a ProjectReferencesResolutionApplyPlan"
        )
    if plan.outputs:
        atomic_write_batch(plan.outputs)


__all__ = [
    "ProjectReferencesResolutionApplyPlan",
    "ReferenceResolutionApplyChange",
    "apply_project_references_resolution_apply",
    "plan_project_references_resolution_apply",
]
