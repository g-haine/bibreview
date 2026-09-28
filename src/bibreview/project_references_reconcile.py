"""Explicit reconciliation for reference changes merged before ledger support."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from .config import BibReviewConfig
from .pipeline.references import references_fingerprint
from .project import ProjectStateError
from .project_references import project_references_review
from .references_resolution import (
    load_project_reference_resolutions,
    matching_reference_resolution,
    record_reconciled_current_resolution,
    reference_resolution_path,
)
from .storage import atomic_write_batch, json_bytes, read_bibliography


@dataclass(frozen=True)
class ReferenceReconciliationChange:
    """One old report entry explicitly reconciled to current canonical state."""

    publication_id: str
    doi: str
    title: str
    source_fingerprint: str
    current_fingerprint: str

    def data(self) -> dict[str, Any]:
        return {
            "publication_id": self.publication_id,
            "doi": self.doi,
            "title": self.title,
            "source_fingerprint": self.source_fingerprint,
            "current_fingerprint": self.current_fingerprint,
        }


@dataclass(frozen=True)
class ProjectReferencesReconcileAppliedPlan:
    """Read-only plan for one historical applied-state reconciliation."""

    audited_publications: int
    actionable_publications: int
    changes: tuple[ReferenceReconciliationChange, ...]
    already_ledgered_publication_ids: tuple[str, ...]
    source_current_publication_ids: tuple[str, ...]
    outputs: Mapping[Path, bytes]

    @property
    def changed(self) -> bool:
        return bool(self.changes)

    def summary(self) -> str:
        return (
            "Historical reference reconciliation\n"
            f"  Audited publications : {self.audited_publications}\n"
            f"  Actionable evidence  : {self.actionable_publications}\n"
            f"  Adopt current canon  : {len(self.changes)}\n"
            f"  Already ledgered     : {len(self.already_ledgered_publication_ids)}\n"
            f"  Source still current : {len(self.source_current_publication_ids)}"
        )

    def data(self) -> dict[str, Any]:
        return {
            "audited_publications": self.audited_publications,
            "actionable_publications": self.actionable_publications,
            "adopt_current_canonical": len(self.changes),
            "already_ledgered": len(self.already_ledgered_publication_ids),
            "source_still_current": len(self.source_current_publication_ids),
            "already_ledgered_publication_ids": list(
                self.already_ledgered_publication_ids
            ),
            "source_current_publication_ids": list(
                self.source_current_publication_ids
            ),
            "changes": [change.data() for change in self.changes],
        }


def _optional_bibliography(path: Path):
    return read_bibliography(path) if path.exists() else ()


def plan_project_references_reconcile_applied(
    config: BibReviewConfig,
) -> ProjectReferencesReconcileAppliedPlan:
    """Adopt current canonical fingerprints for an explicitly reviewed old batch.

    This command exists only for reference changes that were already inspected,
    staged and merged before the persistent resolution ledger existed. It does
    not infer that a changed canonical list is safe; invoking the command is the
    explicit maintainer assertion that these stale report entries correspond to
    previously reviewed applied work.
    """
    if not isinstance(config, BibReviewConfig):
        raise ProjectStateError("config must be a BibReviewConfig")

    staged = _optional_bibliography(config.paths.collected)
    if staged:
        raise ProjectStateError(
            f"{config.paths.collected}: contains {len(staged)} staged publication(s); "
            "merge or clear staging before reconciling applied reference history"
        )

    review = project_references_review(config)
    state = load_project_reference_resolutions(config)
    initial_state = state
    canonical = {
        publication.id: publication
        for publication in read_bibliography(config.paths.bibliography)
    }

    changes: list[ReferenceReconciliationChange] = []
    already_ledgered: list[str] = []
    source_current: list[str] = []
    actionable = 0

    for item in review.items:
        if item.classification not in {"safe-update", "review-required"}:
            continue
        actionable += 1
        publication = canonical.get(item.publication_id)
        if publication is None:
            raise ProjectStateError(
                f"{item.publication_id}: canonical publication is missing"
            )
        current_fingerprint = references_fingerprint(publication.references)
        persisted = matching_reference_resolution(state, item)
        if persisted is not None:
            if current_fingerprint not in {
                persisted.source_fingerprint,
                persisted.resolved_fingerprint,
            }:
                raise ProjectStateError(
                    f"{item.publication_id}: stale reference resolution; canonical "
                    "references match neither source nor resolved fingerprint"
                )
            already_ledgered.append(publication.id)
            continue

        if current_fingerprint == item.current_fingerprint:
            source_current.append(publication.id)
            continue

        state = record_reconciled_current_resolution(
            state,
            item,
            publication.references,
        )
        changes.append(
            ReferenceReconciliationChange(
                publication_id=publication.id,
                doi=publication.doi or publication.id,
                title=publication.title,
                source_fingerprint=item.current_fingerprint,
                current_fingerprint=current_fingerprint,
            )
        )

    outputs: dict[Path, bytes] = {}
    if state != initial_state:
        outputs[reference_resolution_path(config)] = json_bytes(state.data())

    return ProjectReferencesReconcileAppliedPlan(
        audited_publications=review.audited_publications,
        actionable_publications=actionable,
        changes=tuple(changes),
        already_ledgered_publication_ids=tuple(already_ledgered),
        source_current_publication_ids=tuple(source_current),
        outputs=MappingProxyType(outputs),
    )


def apply_project_references_reconcile_applied(
    plan: ProjectReferencesReconcileAppliedPlan,
) -> None:
    """Persist one explicit historical reconciliation plan."""
    if not isinstance(plan, ProjectReferencesReconcileAppliedPlan):
        raise ProjectStateError(
            "plan must be a ProjectReferencesReconcileAppliedPlan"
        )
    if plan.outputs:
        atomic_write_batch(plan.outputs)


__all__ = [
    "ProjectReferencesReconcileAppliedPlan",
    "ReferenceReconciliationChange",
    "apply_project_references_reconcile_applied",
    "plan_project_references_reconcile_applied",
]
