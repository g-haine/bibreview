"""Promote completed human backfill decisions into reviewable staging."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from .backfill_resolution import (
    BackfillResolutionState,
    backfill_resolution_counts,
    load_project_backfill_resolutions,
)
from .config import BibReviewConfig
from .pipeline.backfill import BACKFILL_FIELDS
from .project import ProjectStateError
from .project_backfill import load_project_backfill_review
from .text import is_missing_metadata_value
from .storage import (
    atomic_write_batch,
    bibliography_document_data,
    json_bytes,
    read_bibliography,
)


@dataclass(frozen=True)
class BackfillApplyChange:
    """One accepted/custom proposal promoted into staging."""

    publication_id: str
    doi: str
    title: str
    field: str
    decision: str
    value: str

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
class ProjectBackfillApplyPlan:
    """Read-only plan for applying completed backfill decisions."""

    state: BackfillResolutionState
    changes: tuple[BackfillApplyChange, ...]
    outputs: Mapping[Path, bytes]
    affected_publication_ids: tuple[str, ...]

    @property
    def changed(self) -> bool:
        return bool(self.outputs)

    def summary(self) -> str:
        counts = backfill_resolution_counts(self.state)
        return (
            "Backfill application\n"
            f"  Proposals             : {self.state.total_proposals}\n"
            f"  Accepted              : {counts['accepted']}\n"
            f"  Custom                : {counts['custom']}\n"
            f"  Rejected              : {counts['rejected']}\n"
            f"  Changes to stage      : {len(self.changes)}\n"
            f"  Publications affected : {len(self.affected_publication_ids)}"
        )

    def data(self) -> dict[str, Any]:
        counts = backfill_resolution_counts(self.state)
        return {
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


def plan_project_backfill_apply(
    config: BibReviewConfig,
) -> ProjectBackfillApplyPlan:
    """Stage only explicitly accepted/custom missing-field proposals."""
    staged = (
        read_bibliography(config.paths.collected)
        if config.paths.collected.exists()
        else ()
    )
    if staged:
        raise ProjectStateError(
            f"{config.paths.collected}: contains {len(staged)} staged publication(s); "
            "merge the existing batch before applying backfill decisions"
        )

    review = load_project_backfill_review(config)
    state = load_project_backfill_resolutions(config, review)
    counts = backfill_resolution_counts(state)
    if counts["deferred"] or counts["unresolved"]:
        raise ProjectStateError(
            "backfill decisions must be complete before --apply "
            f"({counts['deferred']} deferred, {counts['unresolved']} unresolved)"
        )

    decisions = {item.key: item for item in state.decisions}
    proposal_keys = {item.key for item in review.candidates}
    if set(decisions) != proposal_keys:
        raise ProjectStateError(
            "backfill decisions do not cover the complete proposal set"
        )

    canonical = read_bibliography(config.paths.bibliography)
    originals = {publication.id: publication for publication in canonical}
    updated = dict(originals)
    changes: list[BackfillApplyChange] = []
    changed_ids: list[str] = []
    changed_seen: set[str] = set()

    for proposal in review.candidates:
        publication = originals.get(proposal.publication_id)
        if publication is None:
            raise ProjectStateError(
                f"{proposal.publication_id}: canonical publication is missing"
            )
        if proposal.field not in BACKFILL_FIELDS:
            raise ProjectStateError(
                f"{proposal.key}: unsupported backfill field {proposal.field}"
            )
        if not is_missing_metadata_value(
            proposal.field,
            getattr(publication, proposal.field),
        ):
            raise ProjectStateError(
                f"{proposal.key}: stale proposal; canonical field is no longer missing"
            )

        decision = decisions[proposal.key]
        if proposal.review_required and decision.decision == "accepted":
            raise ProjectStateError(
                f"{proposal.key}: review-required evidence cannot be accepted directly"
            )
        if decision.decision == "rejected":
            continue
        if decision.decision not in {"accepted", "custom"}:
            raise ProjectStateError(
                f"{proposal.key}: unsupported apply decision {decision.decision}"
            )
        value = decision.resolved_value
        if (
            not isinstance(value, str)
            or is_missing_metadata_value(proposal.field, value)
        ):
            raise ProjectStateError(
                f"{proposal.key}: accepted/custom decision has no meaningful resolved value"
            )

        updated[proposal.publication_id] = replace(
            updated[proposal.publication_id],
            **{proposal.field: value},
        )
        if proposal.publication_id not in changed_seen:
            changed_seen.add(proposal.publication_id)
            changed_ids.append(proposal.publication_id)
        changes.append(
            BackfillApplyChange(
                publication_id=proposal.publication_id,
                doi=proposal.doi,
                title=publication.title,
                field=proposal.field,
                decision=decision.decision,
                value=value,
            )
        )

    outputs: dict[Path, bytes] = {}
    staged_publications = tuple(updated[item] for item in changed_ids)
    if staged_publications:
        outputs[config.paths.collected] = json_bytes(
            bibliography_document_data(staged_publications)
        )

    return ProjectBackfillApplyPlan(
        state=state,
        changes=tuple(changes),
        outputs=MappingProxyType(outputs),
        affected_publication_ids=tuple(changed_ids),
    )


def apply_project_backfill_apply(plan: ProjectBackfillApplyPlan) -> None:
    """Apply one prepared backfill application plan."""
    if not isinstance(plan, ProjectBackfillApplyPlan):
        raise ProjectStateError("plan must be a ProjectBackfillApplyPlan")
    if plan.outputs:
        atomic_write_batch(plan.outputs)
