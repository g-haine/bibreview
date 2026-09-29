"""Promote completed historical hygiene decisions into reviewable staging."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from .config import BibReviewConfig
from .hygiene_resolution import (
    HygieneResolutionState,
    hygiene_resolution_counts,
    load_project_hygiene_resolutions,
)
from .project import ProjectStateError
from .project_hygiene import project_hygiene_migration_review
from .storage import (
    atomic_write_batch,
    bibliography_document_data,
    json_bytes,
    read_bibliography,
)


@dataclass(frozen=True)
class HygieneApplyChange:
    """One accepted/custom historical hygiene migration staged for ordinary merge."""

    publication_id: str
    doi: str
    title: str
    decision: str
    value: str
    field: str = "abstract"

    def data(self) -> dict[str, Any]:
        return {
            "publication_id": self.publication_id,
            "doi": self.doi,
            "title": self.title,
            "field": self.field,
            "decision": self.decision,
            "value": self.value,
        }


@dataclass(frozen=True)
class ProjectHygieneApplyPlan:
    """Read-only application plan for completed hygiene migration decisions."""

    state: HygieneResolutionState
    changes: tuple[HygieneApplyChange, ...]
    outputs: Mapping[Path, bytes]
    affected_publication_ids: tuple[str, ...]

    @property
    def changed(self) -> bool:
        return bool(self.outputs)

    def summary(self) -> str:
        counts = hygiene_resolution_counts(self.state)
        return (
            f"Canonical {self.state.field} hygiene application\n"
            f"  Proposals             : {self.state.total_proposals}\n"
            f"  Accepted              : {counts['accepted']}\n"
            f"  Custom                : {counts['custom']}\n"
            f"  Rejected              : {counts['rejected']}\n"
            f"  Changes to stage      : {len(self.changes)}\n"
            f"  Publications affected : {len(self.affected_publication_ids)}"
        )

    def data(self) -> dict[str, Any]:
        counts = hygiene_resolution_counts(self.state)
        return {
            "field": self.state.field,
            "proposals": self.state.total_proposals,
            "accepted": counts["accepted"],
            "custom": counts["custom"],
            "rejected": counts["rejected"],
            "deferred": counts["deferred"],
            "unresolved": counts["unresolved"],
            "changes_to_stage": len(self.changes),
            "publications_affected": len(self.affected_publication_ids),
            "changes": [item.data() for item in self.changes],
        }


def _publication_field_value(publication, field: str) -> str:
    if field == "abstract":
        return publication.abstract
    if field == "title":
        return publication.title
    raise ProjectStateError(f"unsupported hygiene application field: {field}")


def _replace_publication_field(publication, field: str, value: str):
    if field == "abstract":
        return replace(publication, abstract=value)
    if field == "title":
        # Existing permalinks are intentionally preserved. Historical title
        # hygiene must never regenerate or rewrite a canonical URL.
        return replace(publication, title=value)
    raise ProjectStateError(f"unsupported hygiene application field: {field}")


def plan_project_hygiene_apply(
    config: BibReviewConfig,
    *,
    field: str = "abstract",
) -> ProjectHygieneApplyPlan:
    """Stage only explicitly accepted/custom historical hygiene migrations."""
    staged = (
        read_bibliography(config.paths.collected)
        if config.paths.collected.exists()
        else ()
    )
    if staged:
        raise ProjectStateError(
            f"{config.paths.collected}: contains {len(staged)} staged publication(s); "
            "merge the existing batch before applying hygiene decisions"
        )

    review = project_hygiene_migration_review(config, field=field)
    state = load_project_hygiene_resolutions(config, review)
    counts = hygiene_resolution_counts(state)
    if counts["deferred"] or counts["unresolved"]:
        raise ProjectStateError(
            "hygiene decisions must be complete before --apply "
            f"({counts['deferred']} deferred, {counts['unresolved']} unresolved)"
        )

    decisions = {item.key: item for item in state.decisions}
    proposal_keys = {item.key for item in review.proposals}
    if set(decisions) != proposal_keys:
        raise ProjectStateError(
            "hygiene decisions do not cover the complete migration proposal set"
        )

    canonical = read_bibliography(config.paths.bibliography)
    originals = {publication.id: publication for publication in canonical}
    updated = dict(originals)
    changes: list[HygieneApplyChange] = []
    changed_ids: list[str] = []

    for proposal in review.proposals:
        publication = originals.get(proposal.publication_id)
        if publication is None:
            raise ProjectStateError(
                f"{proposal.publication_id}: canonical publication is missing"
            )
        current_value = _publication_field_value(publication, proposal.field)
        if current_value != proposal.current_value:
            raise ProjectStateError(
                f"{proposal.key}: stale proposal; canonical {proposal.field} changed"
            )

        decision = decisions[proposal.key]
        if proposal.review_required and decision.decision == "accepted":
            raise ProjectStateError(
                f"{proposal.key}: review-required hygiene proposal cannot be "
                "accepted directly"
            )
        if decision.decision == "rejected":
            continue
        if decision.decision not in {"accepted", "custom"}:
            raise ProjectStateError(
                f"{proposal.key}: unsupported apply decision {decision.decision}"
            )

        value = decision.resolved_value
        if not isinstance(value, str) or not value.strip():
            raise ProjectStateError(
                f"{proposal.key}: accepted/custom decision has no meaningful "
                f"resolved {proposal.field}"
            )
        if value == current_value:
            raise ProjectStateError(
                f"{proposal.key}: accepted/custom decision does not change "
                f"the canonical {proposal.field}; reject the proposal instead"
            )

        updated[proposal.publication_id] = _replace_publication_field(
            publication,
            proposal.field,
            value,
        )
        changed_ids.append(proposal.publication_id)
        changes.append(
            HygieneApplyChange(
                publication_id=proposal.publication_id,
                doi=proposal.doi,
                title=publication.title,
                decision=decision.decision,
                value=value,
                field=proposal.field,
            )
        )

    outputs: dict[Path, bytes] = {}
    staged_publications = tuple(updated[item] for item in changed_ids)
    if staged_publications:
        outputs[config.paths.collected] = json_bytes(
            bibliography_document_data(staged_publications)
        )

    return ProjectHygieneApplyPlan(
        state=state,
        changes=tuple(changes),
        outputs=MappingProxyType(outputs),
        affected_publication_ids=tuple(changed_ids),
    )


def apply_project_hygiene_apply(plan: ProjectHygieneApplyPlan) -> None:
    """Apply one prepared historical hygiene staging plan."""
    if not isinstance(plan, ProjectHygieneApplyPlan):
        raise ProjectStateError("plan must be a ProjectHygieneApplyPlan")
    if plan.outputs:
        atomic_write_batch(plan.outputs)
