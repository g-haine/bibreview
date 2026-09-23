"""Promote completed human audit resolutions into canonical review staging."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from .audit_resolution import (
    AuditResolutionState,
    actionable_resolution_candidates,
    load_project_audit_resolutions,
    resolution_counts,
)
from .bibtex_edit import BibtexEditError, bibtex_field_names, update_bibtex_fields
from .config import BibReviewConfig
from .model import Publication
from .pipeline.audit import AuditValue, publication_audit_record
from .project import ProjectStateError
from .project_audit import project_audit_review
from .reviewed_fields import apply_reviewed_field, bibtex_field_for, bibtex_value
from .storage import (
    atomic_write_batch,
    backup_path,
    bibliography_document_data,
    json_bytes,
    read_bibliography,
)


@dataclass(frozen=True)
class AuditApplyChange:
    """One accepted/custom human resolution promoted into review staging."""

    publication_id: str
    doi: str
    title: str
    field: str
    decision: str
    before: AuditValue
    after: AuditValue
    bibtex_field: str | None = None

    def data(self) -> dict[str, Any]:
        def serial(value: AuditValue) -> str | list[str]:
            return list(value) if isinstance(value, tuple) else value

        return {
            "publication_id": self.publication_id,
            "doi": self.doi,
            "title": self.title,
            "field": self.field,
            "decision": self.decision,
            "before": serial(self.before),
            "after": serial(self.after),
            "bibtex_field": self.bibtex_field,
        }


@dataclass(frozen=True)
class AuditApplyNoOp:
    """One accepted/custom resolution already equal to canonical metadata."""

    publication_id: str
    doi: str
    title: str
    field: str
    decision: str
    value: AuditValue

    def data(self) -> dict[str, Any]:
        value: str | list[str]
        value = list(self.value) if isinstance(self.value, tuple) else self.value
        return {
            "publication_id": self.publication_id,
            "doi": self.doi,
            "title": self.title,
            "field": self.field,
            "decision": self.decision,
            "value": value,
        }


@dataclass(frozen=True)
class ProjectAuditApplyPlan:
    """Read-only plan for applying completed audit resolutions."""

    state: AuditResolutionState
    changes: tuple[AuditApplyChange, ...]
    no_ops: tuple[AuditApplyNoOp, ...]
    outputs: Mapping[Path, bytes]
    bibtex_backups: tuple[Path, ...]
    affected_publication_ids: tuple[str, ...]

    @property
    def changed(self) -> bool:
        """Whether applying the plan would write staging/BibTeX state."""
        return bool(self.outputs)

    @property
    def bibtex_files_affected(self) -> int:
        """Return the number of tracked BibTeX files that would change."""
        return len(self.bibtex_backups)

    def summary(self) -> str:
        counts = resolution_counts(self.state)
        return (
            "Audit resolution application\n"
            f"  Actionable findings   : {self.state.total_actionable}\n"
            f"  Accepted              : {counts['accepted']}\n"
            f"  Custom                : {counts['custom']}\n"
            f"  Rejected              : {counts['rejected']}\n"
            f"  No-op resolutions     : {len(self.no_ops)}\n"
            f"  Changes to stage      : {len(self.changes)}\n"
            f"  Publications affected : {len(self.affected_publication_ids)}\n"
            f"  BibTeX files affected : {self.bibtex_files_affected}"
        )

    def data(self) -> dict[str, Any]:
        counts = resolution_counts(self.state)
        return {
            "actionable_findings": self.state.total_actionable,
            "accepted": counts["accepted"],
            "custom": counts["custom"],
            "rejected": counts["rejected"],
            "deferred": counts["deferred"],
            "unresolved": counts["unresolved"],
            "no_op_resolutions": len(self.no_ops),
            "changes_to_stage": len(self.changes),
            "publications_affected": len(self.affected_publication_ids),
            "bibtex_files_affected": self.bibtex_files_affected,
            "changes": [change.data() for change in self.changes],
            "no_ops": [item.data() for item in self.no_ops],
        }


def _optional_bibliography(path: Path) -> tuple[Publication, ...]:
    return read_bibliography(path) if path.exists() else ()


def plan_project_audit_apply(config: BibReviewConfig) -> ProjectAuditApplyPlan:
    """Plan completed audit decisions into collected staging plus tracked BibTeX."""
    if not isinstance(config, BibReviewConfig):
        raise ProjectStateError("config must be a BibReviewConfig")

    staged = _optional_bibliography(config.paths.collected)
    if staged:
        raise ProjectStateError(
            f"{config.paths.collected}: contains {len(staged)} staged publication(s); "
            "merge the existing batch before applying audit resolutions"
        )

    review = project_audit_review(config)
    state = load_project_audit_resolutions(config, review)
    counts = resolution_counts(state)
    if counts["deferred"] or counts["unresolved"]:
        raise ProjectStateError(
            "audit resolutions must be complete before --apply "
            f"({counts['deferred']} deferred, {counts['unresolved']} unresolved)"
        )

    candidates = actionable_resolution_candidates(review)
    decisions = {decision.key: decision for decision in state.decisions}
    candidate_keys = {candidate.key for candidate in candidates}
    if set(decisions) != candidate_keys:
        raise ProjectStateError(
            "audit resolution decisions do not cover the complete actionable review"
        )

    canonical = read_bibliography(config.paths.bibliography)
    originals = {publication.id: publication for publication in canonical}
    updated = dict(originals)
    raw_changes: list[AuditApplyChange] = []
    no_ops: list[AuditApplyNoOp] = []
    changed_ids: list[str] = []
    changed_seen: set[str] = set()

    for candidate in candidates:
        publication = originals.get(candidate.publication_id)
        if publication is None:
            raise ProjectStateError(
                f"{candidate.publication_id}: canonical publication is missing"
            )
        field = candidate.finding.field
        current_fields = publication_audit_record(publication).fields
        current = current_fields[field]
        expected = candidate.finding.canonical_value
        if current != expected:
            raise ProjectStateError(
                f"{candidate.key}: stale canonical value; current metadata no longer "
                "matches the audited value"
            )

        decision = decisions[candidate.key]
        if decision.decision == "rejected":
            continue
        if decision.decision not in {"accepted", "custom"}:
            raise ProjectStateError(
                f"{candidate.key}: unsupported apply decision {decision.decision}"
            )
        if decision.resolved_value is None:
            raise ProjectStateError(
                f"{candidate.key}: accepted/custom resolution has no resolved value"
            )
        if decision.resolved_value == current:
            no_ops.append(
                AuditApplyNoOp(
                    publication_id=candidate.publication_id,
                    doi=publication.doi or candidate.publication_id,
                    title=publication.title,
                    field=field,
                    decision=decision.decision,
                    value=current,
                )
            )
            continue

        revised = apply_reviewed_field(
            updated[candidate.publication_id],
            field,
            decision.resolved_value,
        )
        projected = publication_audit_record(revised).fields[field]
        if projected != decision.resolved_value:
            raise ProjectStateError(
                f"{candidate.key}: resolved value cannot be represented canonically"
            )
        updated[candidate.publication_id] = revised
        if candidate.publication_id not in changed_seen:
            changed_seen.add(candidate.publication_id)
            changed_ids.append(candidate.publication_id)
        raw_changes.append(
            AuditApplyChange(
                publication_id=candidate.publication_id,
                doi=publication.doi or candidate.publication_id,
                title=publication.title,
                field=field,
                decision=decision.decision,
                before=current,
                after=decision.resolved_value,
            )
        )

    outputs: dict[Path, bytes] = {}
    backups: list[Path] = []
    changes_by_publication: dict[str, list[tuple[int, AuditApplyChange]]] = {}
    for index, change in enumerate(raw_changes):
        changes_by_publication.setdefault(change.publication_id, []).append(
            (index, change)
        )

    finalized = list(raw_changes)
    for publication_id in changed_ids:
        publication = updated[publication_id]
        grouped = changes_by_publication[publication_id]
        target = config.paths.bibtex / f"{publication.permalink}.bib"
        text: str | None = None
        existing_fields: frozenset[str] = frozenset()
        bib_updates: dict[str, str] = {}

        for index, change in grouped:
            if text is None and target.exists():
                try:
                    text = target.read_text(encoding="utf-8")
                    existing_fields = bibtex_field_names(text)
                except (OSError, UnicodeError, BibtexEditError) as error:
                    raise ProjectStateError(
                        f"{target}: cannot inspect tracked BibTeX safely: {error}"
                    ) from error

            bib_field = bibtex_field_for(
                change.field,
                publication,
                existing_fields,
            )
            finalized[index] = replace(change, bibtex_field=bib_field)
            if bib_field is None:
                continue
            if not publication.permalink:
                raise ProjectStateError(
                    f"{publication_id}: publication has no permalink for BibTeX update"
                )
            if text is None:
                raise ProjectStateError(
                    f"{target}: tracked BibTeX is required to synchronize "
                    f"resolved field {change.field}"
                )
            rendered = bibtex_value(change.field, change.after)
            previous = bib_updates.get(bib_field)
            if previous is not None and previous != rendered:
                raise ProjectStateError(
                    f"{publication_id}: multiple audit corrections map to incompatible "
                    f"BibTeX field {bib_field}"
                )
            bib_updates[bib_field] = rendered

        if bib_updates and text is not None:
            try:
                revised_bibtex = update_bibtex_fields(text, bib_updates)
            except BibtexEditError as error:
                raise ProjectStateError(
                    f"{target}: cannot apply BibTeX corrections safely: {error}"
                ) from error
            new_bytes = revised_bibtex.encode("utf-8")
            old_bytes = text.encode("utf-8")
            if new_bytes != old_bytes:
                archive = backup_path(
                    config.paths.archive,
                    publication.permalink,
                    ".bib",
                )
                outputs[archive] = old_bytes
                outputs[target] = new_bytes
                backups.append(archive)

    staged_publications = tuple(updated[publication_id] for publication_id in changed_ids)
    if staged_publications:
        outputs[config.paths.collected] = json_bytes(
            bibliography_document_data(staged_publications)
        )

    return ProjectAuditApplyPlan(
        state=state,
        changes=tuple(finalized),
        no_ops=tuple(no_ops),
        outputs=MappingProxyType(outputs),
        bibtex_backups=tuple(backups),
        affected_publication_ids=tuple(changed_ids),
    )


def apply_project_audit_apply(plan: ProjectAuditApplyPlan) -> None:
    """Apply a previously prepared audit-resolution promotion plan."""
    if not isinstance(plan, ProjectAuditApplyPlan):
        raise ProjectStateError("plan must be a ProjectAuditApplyPlan")
    if plan.outputs:
        atomic_write_batch(plan.outputs)


def _format_value(value: AuditValue) -> str:
    if isinstance(value, tuple):
        return "; ".join(value) if value else "(missing)"
    return value if value else "(missing)"


def format_project_audit_apply_plan(plan: ProjectAuditApplyPlan) -> str:
    """Format the verbose human-readable audit application plan."""
    lines = [plan.summary()]
    for change in plan.changes:
        lines.extend(
            (
                "",
                f"{change.doi} — {change.title}",
                f"  {change.field}",
                f"    current : {_format_value(change.before)}",
                f"    staged  : {_format_value(change.after)}",
                f"    source  : {change.decision}",
                "    BibTeX  : "
                + (
                    change.bibtex_field
                    if change.bibtex_field is not None
                    else "not applicable"
                ),
            )
        )
    for item in plan.no_ops:
        lines.extend(
            (
                "",
                f"{item.doi} — {item.title}",
                f"  {item.field}",
                f"    current : {_format_value(item.value)}",
                "    staged  : unchanged",
                f"    source  : {item.decision} (no-op)",
                "    BibTeX  : unchanged",
            )
        )
    return "\n".join(lines)
