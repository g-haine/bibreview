"""Promote completed safe refresh decisions into reviewable staging."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from .backfill_resolution import backfill_resolution_counts
from .bibtex_edit import BibtexEditError, bibtex_field_names, update_bibtex_fields
from .config import BibReviewConfig
from .project import ProjectStateError
from .project_refresh import load_project_refresh_review
from .refresh_resolution import load_project_refresh_resolutions
from .reviewed_fields import apply_reviewed_field, bibtex_field_for, bibtex_value
from .text import is_missing_metadata_value
from .storage import (
    atomic_write_batch,
    backup_path,
    bibliography_document_data,
    json_bytes,
    read_bibliography,
)


@dataclass(frozen=True)
class RefreshApplyChange:
    """One accepted/custom safe refresh proposal promoted into staging."""

    publication_id: str
    doi: str
    title: str
    field: str
    decision: str
    value: str
    bibtex_field: str | None = None

    def data(self) -> dict[str, Any]:
        return {
            "publication_id": self.publication_id,
            "doi": self.doi,
            "title": self.title,
            "field": self.field,
            "decision": self.decision,
            "value": self.value,
            "bibtex_field": self.bibtex_field,
        }


@dataclass(frozen=True)
class ProjectRefreshApplyPlan:
    """Read-only plan for applying completed safe refresh decisions."""

    changes: tuple[RefreshApplyChange, ...]
    outputs: Mapping[Path, bytes]
    bibtex_backups: tuple[Path, ...]
    affected_publication_ids: tuple[str, ...]
    accepted: int
    custom: int
    rejected: int

    @property
    def changed(self) -> bool:
        return bool(self.outputs)

    @property
    def bibtex_files_affected(self) -> int:
        return len(self.bibtex_backups)

    def summary(self) -> str:
        return (
            "Refresh application\n"
            f"  Accepted              : {self.accepted}\n"
            f"  Custom                : {self.custom}\n"
            f"  Rejected              : {self.rejected}\n"
            f"  Changes to stage      : {len(self.changes)}\n"
            f"  Publications affected : {len(self.affected_publication_ids)}\n"
            f"  BibTeX files affected : {self.bibtex_files_affected}"
        )

    def data(self) -> dict[str, Any]:
        return {
            "accepted": self.accepted,
            "custom": self.custom,
            "rejected": self.rejected,
            "changes_to_stage": len(self.changes),
            "publications_affected": len(self.affected_publication_ids),
            "bibtex_files_affected": self.bibtex_files_affected,
            "changes": [item.data() for item in self.changes],
        }


def plan_project_refresh_apply(
    config: BibReviewConfig,
) -> ProjectRefreshApplyPlan:
    """Stage only human-approved fills from one safe refresh review."""
    staged = (
        read_bibliography(config.paths.collected)
        if config.paths.collected.exists()
        else ()
    )
    if staged:
        raise ProjectStateError(
            f"{config.paths.collected}: contains {len(staged)} staged publication(s); "
            "merge the existing batch before applying refresh decisions"
        )

    review = load_project_refresh_review(config)
    state = load_project_refresh_resolutions(config, review)
    counts = backfill_resolution_counts(state)
    if counts["deferred"] or counts["unresolved"]:
        raise ProjectStateError(
            "refresh decisions must be complete before --apply "
            f"({counts['deferred']} deferred, {counts['unresolved']} unresolved)"
        )

    decisions = {item.key: item for item in state.decisions}
    proposal_keys = {item.key for item in review.proposals}
    if set(decisions) != proposal_keys:
        raise ProjectStateError(
            "refresh decisions do not cover the complete proposal set"
        )

    canonical = read_bibliography(config.paths.bibliography)
    originals = {publication.id: publication for publication in canonical}
    updated = dict(originals)
    changes: list[RefreshApplyChange] = []
    changed_ids: list[str] = []
    changed_seen: set[str] = set()

    for proposal in review.proposals:
        publication = originals.get(proposal.publication_id)
        if publication is None:
            raise ProjectStateError(
                f"{proposal.publication_id}: canonical publication is missing"
            )
        current = getattr(publication, proposal.field)
        if not is_missing_metadata_value(proposal.field, current):
            raise ProjectStateError(
                f"{proposal.key}: stale refresh proposal; canonical field is no longer missing"
            )

        decision = decisions[proposal.key]
        if proposal.review_required and decision.decision == "accepted":
            raise ProjectStateError(
                f"{proposal.key}: review-required refresh evidence cannot be "
                "accepted directly"
            )
        if decision.decision == "rejected":
            continue
        if decision.decision not in {"accepted", "custom"}:
            raise ProjectStateError(
                f"{proposal.key}: unsupported refresh decision {decision.decision}"
            )
        value = decision.resolved_value
        if (
            not isinstance(value, str)
            or is_missing_metadata_value(proposal.field, value)
        ):
            raise ProjectStateError(
                f"{proposal.key}: accepted/custom refresh decision has no meaningful value"
            )

        updated[proposal.publication_id] = apply_reviewed_field(
            updated[proposal.publication_id],
            proposal.field,
            value,
        )
        if proposal.publication_id not in changed_seen:
            changed_seen.add(proposal.publication_id)
            changed_ids.append(proposal.publication_id)
        changes.append(
            RefreshApplyChange(
                publication_id=proposal.publication_id,
                doi=proposal.doi,
                title=proposal.title,
                field=proposal.field,
                decision=decision.decision,
                value=value,
            )
        )

    outputs: dict[Path, bytes] = {}
    backups: list[Path] = []
    finalized = list(changes)
    grouped: dict[str, list[tuple[int, RefreshApplyChange]]] = {}
    for index, change in enumerate(changes):
        grouped.setdefault(change.publication_id, []).append((index, change))

    for publication_id in changed_ids:
        publication = updated[publication_id]
        target = config.paths.bibtex / f"{publication.permalink}.bib"
        if not target.exists():
            continue
        try:
            text = target.read_text(encoding="utf-8")
            existing_fields = bibtex_field_names(text)
        except (OSError, UnicodeError, BibtexEditError) as error:
            raise ProjectStateError(
                f"{target}: cannot inspect tracked BibTeX safely: {error}"
            ) from error

        bib_updates: dict[str, str] = {}
        for index, change in grouped[publication_id]:
            bib_field = bibtex_field_for(
                change.field,
                publication,
                existing_fields,
            )
            finalized[index] = replace(change, bibtex_field=bib_field)
            if bib_field is None:
                continue
            rendered = bibtex_value(change.field, change.value)
            previous = bib_updates.get(bib_field)
            if previous is not None and previous != rendered:
                raise ProjectStateError(
                    f"{publication_id}: incompatible refresh values map to "
                    f"BibTeX field {bib_field}"
                )
            bib_updates[bib_field] = rendered

        if not bib_updates:
            continue
        try:
            revised = update_bibtex_fields(text, bib_updates)
        except BibtexEditError as error:
            raise ProjectStateError(
                f"{target}: cannot apply reviewed refresh safely: {error}"
            ) from error
        old_bytes = text.encode("utf-8")
        new_bytes = revised.encode("utf-8")
        if old_bytes == new_bytes:
            continue
        archive = backup_path(
            config.paths.archive,
            publication.permalink,
            ".bib",
        )
        outputs[archive] = old_bytes
        outputs[target] = new_bytes
        backups.append(archive)

    staged_publications = tuple(updated[item] for item in changed_ids)
    if staged_publications:
        outputs[config.paths.collected] = json_bytes(
            bibliography_document_data(staged_publications)
        )

    return ProjectRefreshApplyPlan(
        changes=tuple(finalized),
        outputs=MappingProxyType(outputs),
        bibtex_backups=tuple(backups),
        affected_publication_ids=tuple(changed_ids),
        accepted=counts["accepted"],
        custom=counts["custom"],
        rejected=counts["rejected"],
    )


def apply_project_refresh_apply(plan: ProjectRefreshApplyPlan) -> None:
    """Apply one prepared safe refresh application plan."""
    if not isinstance(plan, ProjectRefreshApplyPlan):
        raise ProjectStateError("plan must be a ProjectRefreshApplyPlan")
    if plan.outputs:
        atomic_write_batch(plan.outputs)
