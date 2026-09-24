"""Stage explicitly reviewed canonical abstract hygiene changes."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from .config import BibReviewConfig
from .hygiene import scan_abstract_hygiene
from .hygiene_resolution import (
    HygieneResolutionState,
    hygiene_resolution_counts,
    load_project_hygiene_resolutions,
)
from .project import ProjectStateError
from .project_hygiene import load_project_hygiene_review
from .storage import (
    atomic_write_batch,
    bibliography_document_data,
    json_bytes,
    read_bibliography,
)


@dataclass(frozen=True)
class HygieneApplyChange:
    """One reviewed abstract replacement staged for ordinary merge."""

    publication_id: str
    doi: str
    title: str
    decision: str
    before: str
    after: str


@dataclass(frozen=True)
class ProjectHygieneApplyPlan:
    """Offline staging plan for completed historical hygiene decisions."""

    state: HygieneResolutionState
    changes: tuple[HygieneApplyChange, ...]
    outputs: Mapping[Path, bytes]
    affected_publication_ids: tuple[str, ...]

    @property
    def changed(self) -> bool:
        return bool(self.changes)

    def summary(self) -> str:
        return (
            "Hygiene apply\n"
            f"  Reviewed changes : {len(self.changes)}\n"
            f"  Publications     : {len(self.affected_publication_ids)}"
        )


def _validate_clean_resolved_abstract(publication, value: str, key: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ProjectStateError(
            f"{key}: accepted/custom hygiene decision has no meaningful value"
        )
    probe = replace(publication, abstract=value)
    report = scan_abstract_hygiene((probe,))
    if report.findings:
        families = ", ".join(report.findings[0].families)
        raise ProjectStateError(
            f"{key}: resolved abstract still contains suspicious hygiene markup "
            f"({families}); provide a canonical-ready custom value"
        )


def plan_project_hygiene_apply(
    config: BibReviewConfig,
) -> ProjectHygieneApplyPlan:
    """Stage only completed, non-stale human hygiene decisions."""
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

    review = load_project_hygiene_review(config)
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
            "hygiene decisions do not cover the complete proposal set"
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
        if publication.abstract != proposal.current_abstract:
            raise ProjectStateError(
                f"{proposal.key}: stale hygiene proposal; canonical abstract "
                "no longer matches the reviewed value"
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
        if value is None:
            raise ProjectStateError(
                f"{proposal.key}: accepted/custom hygiene decision has no value"
            )
        _validate_clean_resolved_abstract(publication, value, proposal.key)

        if value == publication.abstract:
            continue

        updated[proposal.publication_id] = replace(
            publication,
            abstract=value,
        )
        changed_ids.append(proposal.publication_id)
        changes.append(
            HygieneApplyChange(
                publication_id=proposal.publication_id,
                doi=proposal.doi,
                title=proposal.title,
                decision=decision.decision,
                before=proposal.current_abstract,
                after=value,
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
    """Apply one prepared hygiene staging plan."""
    if not isinstance(plan, ProjectHygieneApplyPlan):
        raise ProjectStateError("plan must be a ProjectHygieneApplyPlan")
    if plan.outputs:
        atomic_write_batch(plan.outputs)
