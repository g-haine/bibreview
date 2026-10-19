"""Project-level reviewed migration for suspicious canonical contributors."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from .config import BibReviewConfig
from .contributor_hygiene import publication_suspicious_contributors
from .contributor_hygiene_resolution import (
    ContributorHygieneReview,
    ContributorHygieneCandidate,
    contributor_hygiene_counts,
    contributor_hygiene_pending_cleanup_path,
    load_contributor_hygiene_state,
)
from .project import ProjectStateError
from .storage import (
    atomic_write_batch,
    bibliography_document_data,
    json_bytes,
    read_bibliography,
    read_bibliography_document,
)


@dataclass(frozen=True)
class ContributorHygieneChange:
    publication_id: str
    doi: str
    removed_names: tuple[str, ...]

    def data(self) -> dict[str, Any]:
        return {
            "publication_id": self.publication_id,
            "doi": self.doi,
            "removed_names": list(self.removed_names),
        }


@dataclass(frozen=True)
class ProjectContributorHygieneApplyPlan:
    review: ContributorHygieneReview
    changes: tuple[ContributorHygieneChange, ...]
    outputs: Mapping[Path, bytes]

    @property
    def changed(self) -> bool:
        return bool(self.outputs)

    def summary(self) -> str:
        return (
            "Canonical contributor hygiene application\n"
            f"  Candidates            : {len(self.review.candidates)}\n"
            f"  Publications affected : {len(self.changes)}\n"
            f"  Contributors removed  : {sum(len(item.removed_names) for item in self.changes)}"
        )

    def data(self) -> dict[str, Any]:
        return {
            "review": self.review.data(),
            "changes": [item.data() for item in self.changes],
        }


def project_contributor_hygiene_review(
    config: BibReviewConfig,
) -> ContributorHygieneReview:
    publications = read_bibliography(config.paths.bibliography)
    candidates: list[ContributorHygieneCandidate] = []
    for publication in publications:
        for role, index, name, source_fields in publication_suspicious_contributors(
            publication
        ):
            candidates.append(
                ContributorHygieneCandidate(
                    publication_id=publication.id,
                    doi=publication.doi or "",
                    title=publication.title,
                    role=role,
                    index=index,
                    name=name,
                    source_fields=source_fields,
                )
            )
    return ContributorHygieneReview(
        scanned_publications=len(publications),
        candidates=tuple(candidates),
    )


def format_project_contributor_hygiene_review(
    review: ContributorHygieneReview,
    *,
    verbose: bool = False,
) -> str:
    lines = [review.summary()]
    if not verbose:
        if review.candidates:
            lines.append(
                "Use -v hygiene --authors --review to inspect contributor evidence."
            )
        return "\n".join(lines)
    grouped: dict[str, list[ContributorHygieneCandidate]] = {}
    for item in review.candidates:
        grouped.setdefault(item.publication_id, []).append(item)
    for position, items in enumerate(grouped.values(), 1):
        first = items[0]
        lines.extend(
            [
                "",
                f"[{position}/{len(grouped)}] {first.doi or first.publication_id} — {first.title}",
            ]
        )
        for item in items:
            lines.append(
                f"  ? {item.role}[{item.index + 1}]: {item.name}"
            )
            if item.source_fields:
                lines.append(f"    Source fields: {dict(item.source_fields)}")
    return "\n".join(lines)


def plan_project_contributor_hygiene_apply(
    config: BibReviewConfig,
) -> ProjectContributorHygieneApplyPlan:
    review = project_contributor_hygiene_review(config)
    state = load_contributor_hygiene_state(config, review)
    counts = contributor_hygiene_counts(review, state)
    if counts["unresolved"] or counts["deferred"]:
        raise ProjectStateError(
            "contributor hygiene decisions must be complete before apply"
        )

    staged_existing = (
        read_bibliography(config.paths.collected)
        if config.paths.collected.exists()
        else ()
    )
    if staged_existing:
        raise ProjectStateError(
            f"{config.paths.collected}: contains {len(staged_existing)} staged publication(s); "
            "merge the existing batch before applying contributor hygiene"
        )

    decisions = {item.key: item.decision for item in state.decisions}
    canonical_document = read_bibliography_document(config.paths.bibliography)
    staged = []
    changes: list[ContributorHygieneChange] = []
    removed_author_names: list[str] = []

    for publication in canonical_document.publications:
        remove_author_indexes: set[int] = set()
        remove_editor_indexes: set[int] = set()
        removed_names: list[str] = []
        for candidate in review.candidates:
            if candidate.publication_id != publication.id:
                continue
            if decisions.get(candidate.key) != "remove":
                continue
            if candidate.role == "author":
                remove_author_indexes.add(candidate.index)
            else:
                remove_editor_indexes.add(candidate.index)
            removed_names.append(candidate.name)

        if not removed_names:
            continue
        authors = tuple(
            author
            for index, author in enumerate(publication.authors)
            if index not in remove_author_indexes
        )
        editors = tuple(
            editor
            for index, editor in enumerate(publication.editors)
            if index not in remove_editor_indexes
        )
        if not authors and not editors:
            raise ProjectStateError(
                f"{publication.doi or publication.id}: contributor hygiene would "
                "remove every author/editor"
            )
        staged.append(replace(publication, authors=authors, editors=editors))
        changes.append(
            ContributorHygieneChange(
                publication_id=publication.id,
                doi=publication.doi or "",
                removed_names=tuple(removed_names),
            )
        )
        removed_author_names.extend(
            candidate.name
            for candidate in review.candidates
            if candidate.publication_id == publication.id
            and candidate.role == "author"
            and decisions.get(candidate.key) == "remove"
        )

    outputs: dict[Path, bytes] = {}
    if staged:
        outputs[config.paths.collected] = json_bytes(
            bibliography_document_data(staged)
        )
        outputs[contributor_hygiene_pending_cleanup_path(config)] = json_bytes(
            {
                "schema_version": 1,
                "removed_author_names": sorted(set(removed_author_names)),
            }
        )
    return ProjectContributorHygieneApplyPlan(
        review=review,
        changes=tuple(changes),
        outputs=MappingProxyType(outputs),
    )


def apply_project_contributor_hygiene_apply(
    plan: ProjectContributorHygieneApplyPlan,
) -> None:
    if plan.outputs:
        atomic_write_batch(plan.outputs)
