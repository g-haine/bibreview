"""Project-level offline relevance analysis and legacy evidence backfill."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from .config import BibReviewConfig
from .identifier_state import doi_values, read_identifier_tokens
from .identity import STRONG_IDENTIFIER_NAMES
from .pipeline.discover import (
    EnrichmentLookup,
    WorkProvider,
    discover as discover_publications,
)
from .project import ProjectStateError
from .providers.http import HttpError
from .relevance import (
    RelevanceEvidence,
    analyze_relevance,
    merge_relevance_evidence,
    read_relevance_evidence,
    relevance_evidence_data,
    with_relevance_context,
)
from .reporting import Reporter
from .storage import (
    atomic_write_batch,
    json_bytes,
    read_bibliography,
    read_json,
)


@dataclass(frozen=True)
class ProjectRelevanceBackfillPlan:
    """Read-only plan that reconstructs missing legacy relevance evidence."""

    existing: int
    canonical_added: int
    provider_added: int
    unavailable: tuple[str, ...]
    outputs: Mapping[Path, bytes]

    @property
    def changed(self) -> bool:
        return bool(self.outputs)

    def data(self) -> dict[str, object]:
        return {
            "existing": self.existing,
            "canonical_added": self.canonical_added,
            "provider_added": self.provider_added,
            "unavailable": list(self.unavailable),
            "changed": self.changed,
        }

    def summary(self) -> str:
        return (
            f"existing: {self.existing}; "
            f"canonical backfill: {self.canonical_added}; "
            f"provider backfill: {self.provider_added}; "
            f"unavailable: {len(self.unavailable)}"
        )


def _queue_dois(path: Path) -> tuple[str, ...]:
    if not path.exists():
        return ()
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


def _canonical_publications(config: BibReviewConfig):
    if not config.paths.bibliography.exists():
        return ()
    return read_bibliography(config.paths.bibliography)


def _init_context(config: BibReviewConfig) -> dict[str, tuple[str, int]]:
    """Best-effort batch context for legacy evidence without mutating init state."""

    if not config.initialization.report.exists():
        return {}
    value = read_json(config.initialization.report, dict)
    raw_entries = value.get("entries")
    if not isinstance(raw_entries, list):
        return {}
    result: dict[str, tuple[str, int]] = {}
    for item in raw_entries:
        if not isinstance(item, dict):
            continue
        doi = item.get("doi")
        batch_id = item.get("batch_id")
        attempt = item.get("attempt")
        if (
            isinstance(doi, str)
            and isinstance(batch_id, str)
            and isinstance(attempt, int)
            and not isinstance(attempt, bool)
            and attempt >= 1
        ):
            result[doi.lower()] = (batch_id, attempt)
    return result


def project_relevance_labels(
    config: BibReviewConfig,
    entries: tuple[RelevanceEvidence, ...] | None = None,
) -> dict[str, tuple[str, str]]:
    """Join retained evidence to final project state with explicit label provenance."""

    evidence = entries if entries is not None else read_relevance_evidence(
        config.relevance.evidence
    )
    canonical = {
        publication.doi
        for publication in _canonical_publications(config)
        if publication.doi is not None
    }
    rejected = set(_queue_dois(config.paths.rejected))
    labels: dict[str, tuple[str, str]] = {}

    for entry in evidence:
        if entry.human_decision:
            label = entry.human_decision
            if label == "keep" and entry.doi in rejected:
                raise ProjectStateError(
                    f"{entry.doi}: human KEEP evidence conflicts with rejected project state"
                )
            if label == "reject" and entry.doi in canonical:
                raise ProjectStateError(
                    f"{entry.doi}: human REJECT evidence conflicts with canonical project state"
                )
            labels[entry.doi] = (label, "human")
        elif entry.doi in canonical:
            labels[entry.doi] = ("keep", "canonical")
        elif entry.doi in rejected:
            labels[entry.doi] = ("reject", "terminal")

    return labels


def project_relevance_analysis(config: BibReviewConfig) -> dict[str, object]:
    """Analyze persisted relevance evidence without provider access."""

    evidence = read_relevance_evidence(config.relevance.evidence)
    labels = project_relevance_labels(config, evidence)
    return analyze_relevance(
        evidence,
        labels,
        accept_patterns=config.relevance.patterns,
        reject_patterns=config.relevance.reject_patterns,
        unmatched=config.relevance.unmatched,
    )


def _canonical_author_names(publication) -> tuple[str, ...]:
    names: list[str] = []
    for author in publication.authors:
        literal = (author.literal or "").strip()
        if literal:
            names.append(literal)
            continue
        name = " ".join(
            part
            for part in (
                (author.given or "").strip(),
                (author.family or "").strip(),
            )
            if part
        )
        if name:
            names.append(name)
    return tuple(names)


def _canonical_backfill_entry(
    publication,
    *,
    context: tuple[str, int] | None,
) -> RelevanceEvidence:
    entry = RelevanceEvidence(
        doi=publication.doi,
        title=publication.title,
        abstract=publication.abstract,
        keywords=publication.keywords,
        work_type=publication.type,
        authors=_canonical_author_names(publication),
        container_title=publication.container_title,
        screening_outcome="unknown",
        source="canonical-backfill",
    )
    if context is None:
        return entry
    return with_relevance_context(
        entry,
        batch_id=context[0],
        attempt=context[1],
    )


def plan_project_relevance_backfill(
    config: BibReviewConfig,
    *,
    provider: WorkProvider,
    enrichment_lookup: EnrichmentLookup | None = None,
    reporter: Reporter | None = None,
) -> ProjectRelevanceBackfillPlan:
    """Reconstruct evidence for projects created before persistent screening evidence.

    Canonical publications are reconstructed locally from canonical metadata.
    Only rejected DOI records that still lack evidence require provider access.
    The plan never changes bibliography or relevance decisions.
    """

    progress = reporter or Reporter(-1)
    existing = read_relevance_evidence(config.relevance.evidence)
    known = {entry.doi for entry in existing}
    context = _init_context(config)

    additions: list[RelevanceEvidence] = []
    canonical_added = 0
    publications = _canonical_publications(config)
    for publication in publications:
        doi = publication.doi
        if doi is None or doi in known:
            continue
        additions.append(
            _canonical_backfill_entry(
                publication,
                context=context.get(doi),
            )
        )
        known.add(doi)
        canonical_added += 1

    rejected = _queue_dois(config.paths.rejected)
    provider_added = 0
    unavailable: list[str] = []
    for doi in rejected:
        if doi in known:
            continue
        progress.step(f"Backfill relevance evidence {doi}")
        try:
            result = discover_publications(
                (doi,),
                provider=provider,
                known=(),
                rejected=(),
                patterns=config.relevance.patterns,
                reject_patterns=config.relevance.reject_patterns,
                unmatched=config.relevance.unmatched,
                accepted_types=config.discovery.accepted_types,
                excluded_doi_substrings=(),
                enrichment_lookup=enrichment_lookup,
                reporter=progress,
            )
        except (HttpError, OSError, ValueError, TypeError):
            unavailable.append(doi)
            continue

        if not result.evidence:
            unavailable.append(doi)
            continue
        entry = result.evidence[0]
        entry = replace(
            entry,
            screening_outcome="unknown",
            accept_matches=(),
            reject_matches=(),
            source="backfill",
        )
        batch = context.get(doi)
        if batch is not None:
            entry = with_relevance_context(
                entry,
                batch_id=batch[0],
                attempt=batch[1],
            )
        additions.append(entry)
        known.add(doi)
        provider_added += 1

    merged = merge_relevance_evidence(existing, additions)
    outputs: dict[Path, bytes] = {}
    content = json_bytes(relevance_evidence_data(merged))
    if additions or config.relevance.evidence.exists():
        if (
            not config.relevance.evidence.exists()
            or config.relevance.evidence.read_bytes() != content
        ):
            outputs[config.relevance.evidence] = content

    return ProjectRelevanceBackfillPlan(
        existing=len(existing),
        canonical_added=canonical_added,
        provider_added=provider_added,
        unavailable=tuple(unavailable),
        outputs=MappingProxyType(outputs),
    )


def apply_project_relevance_backfill(plan: ProjectRelevanceBackfillPlan) -> None:
    """Persist a previously planned relevance-evidence compatibility backfill."""

    if not isinstance(plan, ProjectRelevanceBackfillPlan):
        raise ProjectStateError("plan must be a ProjectRelevanceBackfillPlan")
    if plan.outputs:
        atomic_write_batch(plan.outputs)
