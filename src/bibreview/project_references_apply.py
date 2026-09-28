"""Stage deterministic safe reference maintenance changes for ordinary merge."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from .config import BibReviewConfig
from .pipeline.references import references_fingerprint
from .project import ProjectStateError
from .project_references import (
    project_references_review,
    safe_reference_projection,
    safe_reference_requires_human_review,
)
from .storage import (
    atomic_write_batch,
    bibliography_document_data,
    json_bytes,
    read_bibliography,
)


@dataclass(frozen=True)
class ReferenceSafeApplyChange:
    """One publication carrying deterministic reference changes into staging."""

    publication_id: str
    doi: str
    title: str
    source_classification: str
    explanation: str | None
    inserted_references: int
    citation_updates: int
    identifier_updates: int

    def data(self) -> dict[str, Any]:
        return {
            "publication_id": self.publication_id,
            "doi": self.doi,
            "title": self.title,
            "source_classification": self.source_classification,
            "explanation": self.explanation,
            "inserted_references": self.inserted_references,
            "citation_updates": self.citation_updates,
            "identifier_updates": self.identifier_updates,
        }


@dataclass(frozen=True)
class ProjectReferencesSafeApplyPlan:
    """Read-only plan for deterministic reference changes staged for merge."""

    audited_publications: int
    changes: tuple[ReferenceSafeApplyChange, ...]
    outputs: Mapping[Path, bytes]
    affected_publication_ids: tuple[str, ...]
    review_required_publications: int
    human_review_publication_ids: tuple[str, ...]
    partially_staged_human_review_ids: tuple[str, ...]

    @property
    def changed(self) -> bool:
        return bool(self.outputs)

    @property
    def human_reviews_remaining(self) -> int:
        return len(self.human_review_publication_ids)

    @property
    def auto_resolved_reviews(self) -> int:
        return self.review_required_publications - self.human_reviews_remaining

    @property
    def partially_staged_human_reviews(self) -> int:
        return len(self.partially_staged_human_review_ids)

    @property
    def unstaged_human_reviews(self) -> int:
        return self.human_reviews_remaining - self.partially_staged_human_reviews

    @property
    def inserted_references(self) -> int:
        return sum(item.inserted_references for item in self.changes)

    @property
    def citation_updates(self) -> int:
        return sum(item.citation_updates for item in self.changes)

    @property
    def identifier_updates(self) -> int:
        return sum(item.identifier_updates for item in self.changes)

    def summary(self) -> str:
        return (
            "Reference safe application\n"
            f"  Audited publications : {self.audited_publications}\n"
            f"  Publications to stage: {len(self.affected_publication_ids)}\n"
            f"  Review-required input: {self.review_required_publications}\n"
            f"  Auto-resolved reviews: {self.auto_resolved_reviews}\n"
            f"  Human reviews remain : {self.human_reviews_remaining}\n"
            f"    Partially staged    : {self.partially_staged_human_reviews}\n"
            f"    Not staged          : {self.unstaged_human_reviews}\n"
            f"  References inserted  : {self.inserted_references}\n"
            f"  Citation updates     : {self.citation_updates}\n"
            f"  Identifier updates   : {self.identifier_updates}"
        )

    def data(self) -> dict[str, Any]:
        return {
            "audited_publications": self.audited_publications,
            "publications_to_stage": len(self.affected_publication_ids),
            "review_required_publications": self.review_required_publications,
            "auto_resolved_reviews": self.auto_resolved_reviews,
            "human_reviews_remaining": self.human_reviews_remaining,
            "partially_staged_human_reviews": self.partially_staged_human_reviews,
            "unstaged_human_reviews": self.unstaged_human_reviews,
            "human_review_publication_ids": list(self.human_review_publication_ids),
            "references_inserted": self.inserted_references,
            "citation_updates": self.citation_updates,
            "identifier_updates": self.identifier_updates,
            "changes": [item.data() for item in self.changes],
        }


def _optional_bibliography(path: Path):
    return read_bibliography(path) if path.exists() else ()


def plan_project_references_safe_apply(
    config: BibReviewConfig,
) -> ProjectReferencesSafeApplyPlan:
    """Plan only deterministic, non-destructive reference changes into staging."""
    if not isinstance(config, BibReviewConfig):
        raise ProjectStateError("config must be a BibReviewConfig")

    staged = _optional_bibliography(config.paths.collected)
    if staged:
        raise ProjectStateError(
            f"{config.paths.collected}: contains {len(staged)} staged publication(s); "
            "merge the existing batch before applying safe reference changes"
        )

    review = project_references_review(config)
    canonical = read_bibliography(config.paths.bibliography)
    originals = {publication.id: publication for publication in canonical}
    changes: list[ReferenceSafeApplyChange] = []
    changed_ids: list[str] = []
    human_review_ids: list[str] = []
    partially_staged_human_review_ids: list[str] = []
    staged_publications = []

    for item in review.items:
        if item.classification not in {"safe-update", "review-required"}:
            continue

        publication = originals.get(item.publication_id)
        if publication is None:
            raise ProjectStateError(
                f"{item.publication_id}: canonical publication is missing"
            )
        if references_fingerprint(publication.references) != item.current_fingerprint:
            raise ProjectStateError(
                f"{item.publication_id}: stale reference review; canonical references "
                "no longer match the persisted refresh evidence"
            )

        projection = safe_reference_projection(item, publication.references)
        requires_human_review = safe_reference_requires_human_review(
            item,
            publication.references,
        )
        if item.classification == "review-required" and requires_human_review:
            human_review_ids.append(publication.id)
            if projection.changed:
                partially_staged_human_review_ids.append(publication.id)

        if not projection.changed:
            continue
        if projection.references == publication.references:
            raise ProjectStateError(
                f"{item.publication_id}: safe reference projection reports changes "
                "without changing canonical references"
            )

        staged_publications.append(
            replace(publication, references=projection.references)
        )
        changed_ids.append(publication.id)
        changes.append(
            ReferenceSafeApplyChange(
                publication_id=publication.id,
                doi=publication.doi or publication.id,
                title=publication.title,
                source_classification=item.classification,
                explanation=review.explanations.get(publication.id),
                inserted_references=projection.inserted_references,
                citation_updates=projection.citation_updates,
                identifier_updates=projection.identifier_updates,
            )
        )

    outputs: dict[Path, bytes] = {}
    if staged_publications:
        outputs[config.paths.collected] = json_bytes(
            bibliography_document_data(tuple(staged_publications))
        )

    return ProjectReferencesSafeApplyPlan(
        audited_publications=review.audited_publications,
        changes=tuple(changes),
        outputs=MappingProxyType(outputs),
        affected_publication_ids=tuple(changed_ids),
        review_required_publications=review.review_required,
        human_review_publication_ids=tuple(human_review_ids),
        partially_staged_human_review_ids=tuple(partially_staged_human_review_ids),
    )


def apply_project_references_safe_apply(
    plan: ProjectReferencesSafeApplyPlan,
) -> None:
    """Write a prepared safe-reference application plan to normal staging."""
    if not isinstance(plan, ProjectReferencesSafeApplyPlan):
        raise ProjectStateError(
            "plan must be a ProjectReferencesSafeApplyPlan"
        )
    if plan.outputs:
        atomic_write_batch(plan.outputs)
