"""Auditable corrections of project relevance decisions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from .config import BibReviewConfig
from .identifier_state import (
    IdentifierToken,
    REGISTRY_IDENTIFIER_NAMES,
    canonical_registry_token,
    doi_values,
    identifier_tokens_bytes,
    read_identifier_tokens,
)
from .identity import STRONG_IDENTIFIER_NAMES, normalize_doi
from .project import ProjectStateError
from .project_init import (
    ProjectInitCorrectionContext,
    plan_project_init_correction_transition,
    project_init_correction_context,
)
from .project_review import plan_project_relevance_review_decision
from .relevance import (
    RelevanceEvidence,
    read_relevance_evidence,
    record_relevance_correction,
    relevance_evidence_data,
)
from .storage import (
    BibliographyMetadata,
    atomic_write_batch,
    backup_path,
    bibliography_document_data,
    json_bytes,
    read_bibliography_document,
)


def _queue_dois(path: Path) -> tuple[str, ...]:
    try:
        return doi_values(
            read_identifier_tokens(
                path,
                allowed_kinds=STRONG_IDENTIFIER_NAMES,
                allow_legacy_doi=True,
            )
        )
    except ValueError as error:
        raise ProjectStateError(str(error)) from error


def _registry_dois(path: Path) -> tuple[str, ...]:
    try:
        return doi_values(
            read_identifier_tokens(
                path,
                allowed_kinds=REGISTRY_IDENTIFIER_NAMES,
                allow_legacy_doi=True,
            )
        )
    except ValueError as error:
        raise ProjectStateError(str(error)) from error


def _doi_bytes(values: list[str]) -> bytes:
    return identifier_tokens_bytes(IdentifierToken("doi", value) for value in values)


def _put_if_changed(outputs: dict[Path, bytes], path: Path, content: bytes) -> None:
    if path.exists() and path.read_bytes() == content:
        return
    outputs[path] = content


def _append_unique(values: list[str], doi: str) -> list[str]:
    return values if doi in values else [*values, doi]


def _canonical_registry(publications) -> list[IdentifierToken]:
    return [
        canonical_registry_token(publication.id, publication.doi)
        for publication in publications
    ]


@dataclass(frozen=True)
class ProjectRelevanceCorrectionState:
    """All project surfaces relevant to one DOI correction."""

    doi: str
    canonical: bool
    pending: bool
    review: bool
    rejected: bool
    staged: bool
    registry: bool
    evidence: RelevanceEvidence | None
    title: str = ""
    initialization: ProjectInitCorrectionContext | None = None

    @property
    def surfaces(self) -> tuple[str, ...]:
        return tuple(
            name
            for name, present in (
                ("canonical", self.canonical),
                ("pending", self.pending),
                ("review", self.review),
                ("rejected", self.rejected),
                ("staged", self.staged),
                ("registry", self.registry),
                ("evidence", self.evidence is not None),
                ("initialization", self.initialization is not None),
            )
            if present
        )

    @property
    def current_decision(self) -> str | None:
        if self.canonical and not self.rejected:
            return "keep"
        if self.rejected and not self.canonical:
            return "reject"
        return None

    @property
    def decision_provenance(self) -> str:
        if self.evidence is None:
            return "terminal"
        if self.evidence.correction_decision:
            return "correction"
        if self.evidence.human_decision:
            return "human"
        return "terminal"

    def data(self) -> dict[str, object]:
        return {
            "doi": self.doi,
            "title": self.title,
            "surfaces": list(self.surfaces),
            "current_decision": self.current_decision,
            "decision_provenance": self.decision_provenance,
            "screening_outcome": (
                self.evidence.screening_outcome if self.evidence is not None else None
            ),
            "human_decision": (
                self.evidence.human_decision if self.evidence is not None else None
            ),
            "correction_decision": (
                self.evidence.correction_decision if self.evidence is not None else None
            ),
            "evidence": (
                {
                    "title": self.evidence.title,
                    "abstract": self.evidence.abstract,
                    "keywords": list(self.evidence.keywords),
                    "type": self.evidence.work_type,
                    "accept_matches": list(self.evidence.accept_matches),
                    "reject_matches": list(self.evidence.reject_matches),
                }
                if self.evidence is not None
                else None
            ),
            "initialization": (
                self.initialization.data() if self.initialization is not None else None
            ),
        }


@dataclass(frozen=True)
class ProjectRelevanceCorrectionPlan:
    """Read-only, explicit relevance transition across project state."""

    state: ProjectRelevanceCorrectionState
    decision: str
    outputs: Mapping[Path, bytes]
    deletes: tuple[Path, ...]
    backup: Path | None
    initialization_batch: str | None = None

    @property
    def changed(self) -> bool:
        return bool(self.outputs or self.deletes)

    def data(self) -> dict[str, object]:
        return {
            "state": self.state.data(),
            "decision": self.decision,
            "changed": self.changed,
            "deletes": [str(path) for path in self.deletes],
            "backup": str(self.backup) if self.backup is not None else None,
            "initialization_batch": self.initialization_batch,
        }

    def summary(self) -> str:
        before = self.state.current_decision or "in-flight"
        return f"{self.state.doi}: {before.upper()} -> {self.decision.upper()}"


def inspect_project_relevance_correction(
    config: BibReviewConfig,
    *,
    doi: str,
) -> ProjectRelevanceCorrectionState:
    """Locate a DOI across all relevance state without changing project files."""

    normalized = normalize_doi(doi)
    canonical_document = (
        read_bibliography_document(config.paths.bibliography)
        if config.paths.bibliography.exists()
        else None
    )
    canonical_publications = tuple(
        publication
        for publication in (
            canonical_document.publications if canonical_document is not None else ()
        )
        if publication.doi == normalized
    )
    staged_document = (
        read_bibliography_document(config.paths.collected)
        if config.paths.collected.exists()
        else None
    )
    staged_publications = tuple(
        publication
        for publication in (
            staged_document.publications if staged_document is not None else ()
        )
        if publication.doi == normalized
    )
    if len(canonical_publications) > 1 or len(staged_publications) > 1:
        raise ProjectStateError(
            f"{normalized}: DOI occurs more than once in canonical or staged bibliography"
        )

    evidence = next(
        (
            entry
            for entry in read_relevance_evidence(config.relevance.evidence)
            if entry.doi == normalized
        ),
        None,
    )
    return ProjectRelevanceCorrectionState(
        doi=normalized,
        canonical=bool(canonical_publications),
        pending=normalized in _queue_dois(config.paths.pending),
        review=normalized in _queue_dois(config.paths.review),
        rejected=normalized in _queue_dois(config.paths.rejected),
        staged=bool(staged_publications),
        registry=normalized in _registry_dois(config.paths.known),
        evidence=evidence,
        title=(
            canonical_publications[0].title
            if canonical_publications
            else staged_publications[0].title
            if staged_publications
            else evidence.title
            if evidence is not None
            else ""
        ),
        initialization=project_init_correction_context(config, normalized),
    )


def _remove(values: list[str], doi: str) -> list[str]:
    return [value for value in values if value != doi]


def _safe_bibtex_path(config: BibReviewConfig, doi: str, publication) -> Path | None:
    if not publication.permalink:
        return None
    path = config.paths.bibtex / f"{publication.permalink}.bib"
    try:
        path.resolve().relative_to(config.paths.bibtex.resolve())
    except ValueError as error:
        raise ProjectStateError(f"{doi}: unsafe BibTeX path for publication") from error
    return path


def plan_project_relevance_correction(
    config: BibReviewConfig,
    *,
    doi: str,
    decision: str,
) -> ProjectRelevanceCorrectionPlan:
    """Plan an auditable KEEP/REJECT correction without writing project files."""

    state = inspect_project_relevance_correction(config, doi=doi)
    normalized_decision = decision.strip().lower()
    if normalized_decision not in {"keep", "reject"}:
        raise ProjectStateError("relevance correction decision must be 'keep' or 'reject'")
    if not state.surfaces:
        raise ProjectStateError(f"{state.doi}: DOI is not present in project relevance state")
    if (
        state.initialization is not None
        and not (state.canonical or state.pending or state.review or state.rejected or state.staged)
    ):
        raise ProjectStateError(
            f"{state.doi}: initialization candidate has no corresponding project state"
        )
    if state.canonical and state.rejected:
        raise ProjectStateError(
            f"{state.doi}: canonical KEEP conflicts with rejected terminal state"
        )
    if state.review:
        # A review candidate has not yet received a terminal decision.  Reuse
        # the ordinary human-review planner so the provenance remains human,
        # rather than calling a first decision a correction.
        if state.canonical or state.pending or state.rejected or state.staged:
            raise ProjectStateError(
                f"{state.doi}: review DOI appears in an incompatible project state"
            )
        review_plan = plan_project_relevance_review_decision(
            config, doi=state.doi, decision=normalized_decision
        )
        return ProjectRelevanceCorrectionPlan(
            state=state,
            decision=normalized_decision,
            outputs=review_plan.outputs,
            deletes=(),
            backup=None,
            initialization_batch=review_plan.init_batch,
        )

    current = state.current_decision
    in_flight_keep = state.pending or state.staged
    if (
        (normalized_decision == "keep" and (current == "keep" or in_flight_keep))
        or (normalized_decision == "reject" and current == "reject" and not state.staged)
    ):
        return ProjectRelevanceCorrectionPlan(
            state=state,
            decision=normalized_decision,
            outputs=MappingProxyType({}),
            deletes=(),
            backup=None,
        )
    if state.evidence is None:
        raise ProjectStateError(
            f"{state.doi}: relevance evidence is required before correction; "
            "run 'bibreview relevance --backfill-evidence' first"
        )

    outputs: dict[Path, bytes] = {}
    deletes: list[Path] = []
    backup: Path | None = None
    paths = config.paths

    evidence_before = read_relevance_evidence(config.relevance.evidence)
    evidence_after = record_relevance_correction(
        evidence_before,
        doi=state.doi,
        decision=normalized_decision,
    )
    _put_if_changed(
        outputs,
        config.relevance.evidence,
        json_bytes(relevance_evidence_data(evidence_after)),
    )

    pending = list(_queue_dois(paths.pending))
    rejected = list(_queue_dois(paths.rejected))
    if normalized_decision == "keep":
        rejected = _remove(rejected, state.doi)
        pending = _append_unique(pending, state.doi)
    else:
        pending = _remove(pending, state.doi)
        rejected = _append_unique(rejected, state.doi)
    _put_if_changed(outputs, paths.pending, _doi_bytes(pending))
    _put_if_changed(outputs, paths.rejected, _doi_bytes(rejected))

    canonical_document = (
        read_bibliography_document(paths.bibliography)
        if paths.bibliography.exists()
        else None
    )
    canonical_publications = (
        canonical_document.publications if canonical_document is not None else ()
    )
    if normalized_decision == "reject" and state.canonical:
        assert canonical_document is not None
        removed = tuple(
            publication for publication in canonical_document.publications
            if publication.doi == state.doi
        )
        retained = tuple(
            publication for publication in canonical_document.publications
            if publication.doi != state.doi
        )
        backup = backup_path(paths.archive, "bibliography", ".json")
        outputs[backup] = paths.bibliography.read_bytes()
        metadata = BibliographyMetadata(
            schema_version=canonical_document.metadata.schema_version,
            last_update=date.today(),
        )
        outputs[paths.bibliography] = json_bytes(
            bibliography_document_data(retained, metadata=metadata)
        )
        for publication in removed:
            bibtex = _safe_bibtex_path(config, state.doi, publication)
            if bibtex is not None and bibtex.exists() and bibtex not in deletes:
                outputs[backup_path(paths.archive, "bibtex", ".bib")] = bibtex.read_bytes()
                deletes.append(bibtex)
        canonical_publications = retained

    if normalized_decision == "reject" and state.staged:
        document = read_bibliography_document(paths.collected)
        removed = tuple(
            publication for publication in document.publications if publication.doi == state.doi
        )
        retained = tuple(
            publication for publication in document.publications if publication.doi != state.doi
        )
        outputs[backup_path(paths.archive, "collected", ".json")] = paths.collected.read_bytes()
        metadata = BibliographyMetadata(
            schema_version=document.metadata.schema_version,
            last_update=date.today(),
        )
        outputs[paths.collected] = json_bytes(
            bibliography_document_data(retained, metadata=metadata)
        )
        for publication in removed:
            bibtex = _safe_bibtex_path(config, state.doi, publication)
            if bibtex is not None and bibtex.exists() and bibtex not in deletes:
                outputs[backup_path(paths.archive, "bibtex", ".bib")] = bibtex.read_bytes()
                deletes.append(bibtex)

    _put_if_changed(
        outputs,
        paths.known,
        identifier_tokens_bytes(_canonical_registry(canonical_publications)),
    )

    init_transition = plan_project_init_correction_transition(
        config, doi=state.doi, decision=normalized_decision
    )
    if init_transition is not None:
        outputs.update(init_transition.outputs)

    return ProjectRelevanceCorrectionPlan(
        state=state,
        decision=normalized_decision,
        outputs=MappingProxyType(outputs),
        deletes=tuple(deletes),
        backup=backup,
        initialization_batch=(init_transition.batch_id if init_transition else None),
    )


def apply_project_relevance_correction(
    plan: ProjectRelevanceCorrectionPlan,
) -> None:
    """Apply one previously planned correction after atomically staging writes."""

    if not isinstance(plan, ProjectRelevanceCorrectionPlan):
        raise ProjectStateError("plan must be a ProjectRelevanceCorrectionPlan")
    if plan.outputs:
        atomic_write_batch(plan.outputs)
    for path in plan.deletes:
        path.unlink(missing_ok=True)


def format_project_relevance_correction_state(
    state: ProjectRelevanceCorrectionState,
) -> str:
    """Render an offline correction-state inspection for people and terminals."""

    lines = [
        f"DOI: {state.doi}",
        f"Title: {state.title or '(unavailable)'}",
        "Surfaces: " + (", ".join(state.surfaces) or "none"),
        "Terminal relevance: "
        + (state.current_decision.upper() if state.current_decision else "none"),
        f"Current provenance: {state.decision_provenance}",
    ]
    if state.evidence is not None:
        lines.extend(
            (
                f"Historical screening: {state.evidence.screening_outcome}",
                f"Abstract: {state.evidence.abstract or '(none)'}",
                "Keywords: "
                + (", ".join(state.evidence.keywords) or "(none)"),
                "Initial human decision: "
                + (state.evidence.human_decision.upper() or "none"),
                "Recorded correction: "
                + (state.evidence.correction_decision.upper() or "none"),
            )
        )
    else:
        lines.append("Relevance evidence: unavailable")
    if state.initialization is not None:
        lines.append(
            "Initialization: "
            f"{state.initialization.batch_id} (attempt {state.initialization.attempt}, "
            f"{state.initialization.outcome}, {state.initialization.item_state})"
        )
    return "\n".join(lines)
