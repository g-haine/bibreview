"""First-class human relevance review over ordinary BibReview DOI queues."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping
from urllib.parse import quote

from .config import BibReviewConfig
from .identifier_state import (
    IdentifierToken,
    doi_values,
    identifier_tokens_bytes,
    read_identifier_tokens,
)
from .identity import STRONG_IDENTIFIER_NAMES, normalize_doi
from .pipeline.discover import EnrichmentLookup, WorkProvider, matching_patterns
from .pipeline.enrich import crossref_enrichment
from .project import ProjectStateError
from .project_init import (
    plan_project_init_review_transition,
    project_init_review_context,
)
from .providers.base import Enrichment
from .relevance import (
    read_relevance_evidence,
    record_human_relevance_decision,
    relevance_evidence_data,
)
from .reporting import Reporter
from .storage import atomic_write_batch, json_bytes


@dataclass(frozen=True)
class RelevanceReviewCase:
    """Provider-backed evidence for one DOI awaiting a human relevance decision."""

    doi: str
    title: str
    work_type: str
    abstract: str
    abstract_source: str
    keywords: tuple[str, ...]
    accept_matches: tuple[str, ...]
    reject_matches: tuple[str, ...]
    metadata_available: bool
    init_batch: str | None = None
    init_attempt: int | None = None

    def data(self) -> dict[str, Any]:
        return {
            "doi": self.doi,
            "title": self.title,
            "type": self.work_type,
            "abstract": self.abstract,
            "abstract_source": self.abstract_source,
            "keywords": list(self.keywords),
            "accept_matches": list(self.accept_matches),
            "reject_matches": list(self.reject_matches),
            "metadata_available": self.metadata_available,
            "initialization": (
                {
                    "batch_id": self.init_batch,
                    "attempt": self.init_attempt,
                }
                if self.init_batch is not None
                else None
            ),
        }


@dataclass(frozen=True)
class ProjectRelevanceReviewDecisionPlan:
    """Read-only state transition for one explicit KEEP/REJECT decision."""

    doi: str
    decision: str
    outputs: Mapping[Path, bytes]
    remaining_review: int
    init_batch: str | None

    @property
    def changed(self) -> bool:
        return bool(self.outputs)


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


def _doi_bytes(values: tuple[str, ...] | list[str]) -> bytes:
    return identifier_tokens_bytes(
        IdentifierToken("doi", value) for value in values
    )


def _put_if_changed(
    outputs: dict[Path, bytes],
    path: Path,
    content: bytes,
) -> None:
    if path.exists() and path.read_bytes() == content:
        return
    outputs[path] = content


def _title(message: Mapping[str, Any]) -> str:
    raw = message.get("title")
    if isinstance(raw, list):
        return str(raw[0]) if raw else ""
    return str(raw or "")


def project_relevance_review_dois(config: BibReviewConfig) -> tuple[str, ...]:
    """Return DOI values currently awaiting explicit human relevance review."""
    return _queue_dois(config.paths.review)


def project_relevance_review_cases(
    config: BibReviewConfig,
    *,
    provider: WorkProvider,
    enrichment_lookup: EnrichmentLookup | None = None,
    reporter: Reporter | None = None,
) -> tuple[RelevanceReviewCase, ...]:
    """Refresh provider evidence for all current manual relevance-review DOI values."""
    dois = project_relevance_review_dois(config)
    progress = reporter or Reporter(-1)
    cases: list[RelevanceReviewCase] = []

    for index, doi in enumerate(dois, start=1):
        progress.detail(f"[{index}/{len(dois)}] review evidence {doi}")
        message = provider.work(doi)
        if message is None:
            title = ""
            work_type = ""
            enrichment = Enrichment()
            text = ""
        else:
            title = _title(message)
            work_type = str(message.get("type") or "")
            enrichment = (
                enrichment_lookup(doi, message)
                if enrichment_lookup is not None
                else crossref_enrichment(message, preserve_refused=True)
            )
            if not isinstance(enrichment, Enrichment):
                raise TypeError("relevance review enrichment lookup must return Enrichment")
            text = " ".join(
                part
                for part in (
                    title,
                    enrichment.abstract,
                    " ".join(enrichment.keywords),
                )
                if part
            )

        init_context = project_init_review_context(config, doi)
        cases.append(
            RelevanceReviewCase(
                doi=doi,
                title=title,
                work_type=work_type,
                abstract=enrichment.abstract,
                abstract_source=enrichment.abstract_source,
                keywords=tuple(enrichment.keywords),
                accept_matches=matching_patterns(text, config.relevance.patterns),
                reject_matches=matching_patterns(
                    text,
                    config.relevance.reject_patterns,
                ),
                metadata_available=message is not None,
                init_batch=init_context.batch_id if init_context is not None else None,
                init_attempt=init_context.attempt if init_context is not None else None,
            )
        )

    return tuple(cases)


def _format_doi(doi: str, *, hyperlinks: bool) -> str:
    if not hyperlinks:
        return doi
    url = f"https://doi.org/{quote(doi, safe='/')}"
    return f"\x1b]8;;{url}\x1b\\{doi}\x1b]8;;\x1b\\"


def format_relevance_review_case(
    case: RelevanceReviewCase,
    *,
    index: int | None = None,
    total: int | None = None,
    hyperlinks: bool = False,
) -> str:
    """Render one relevance-review case with the evidence needed by a human."""
    lines: list[str] = []
    if index is not None and total is not None:
        lines.append(f"Relevance review {index}/{total}")
    lines.append(f"DOI: {_format_doi(case.doi, hyperlinks=hyperlinks)}")
    if case.init_batch is not None:
        lines.append(
            f"Initialization: {case.init_batch} (attempt {case.init_attempt})"
        )
    else:
        lines.append("Context: ordinary discovery")

    if not case.metadata_available:
        lines.append("Metadata: unavailable from configured provider")
    else:
        lines.append(f"Title: {case.title or '(untitled)'}")
        lines.append(f"Type: {case.work_type or '(unknown)'}")
        lines.append(f"Abstract: {case.abstract or '(none)'}")
        if case.abstract_source:
            lines.append(f"Abstract source: {case.abstract_source}")
        lines.append(
            "Keywords: "
            + (", ".join(case.keywords) if case.keywords else "(none)")
        )

    lines.append("Current accept-pattern matches:")
    if case.accept_matches:
        lines.extend(f"  - {pattern}" for pattern in case.accept_matches)
    else:
        lines.append("  (none)")
    lines.append("Current reject-pattern matches:")
    if case.reject_matches:
        lines.extend(f"  - {pattern}" for pattern in case.reject_matches)
    else:
        lines.append("  (none)")
    return "\n".join(lines)


def plan_project_relevance_review_decision(
    config: BibReviewConfig,
    *,
    doi: str,
    decision: str,
) -> ProjectRelevanceReviewDecisionPlan:
    """Plan one explicit KEEP/REJECT transition without collecting or merging."""
    normalized = normalize_doi(doi)
    normalized_decision = decision.strip().lower()
    if normalized_decision not in {"keep", "reject"}:
        raise ProjectStateError("relevance review decision must be 'keep' or 'reject'")

    review = list(_queue_dois(config.paths.review))
    if normalized not in review:
        raise ProjectStateError(
            f"{normalized}: DOI does not currently require manual relevance review"
        )

    pending = list(_queue_dois(config.paths.pending))
    rejected = list(_queue_dois(config.paths.rejected))
    if normalized in pending or normalized in rejected:
        raise ProjectStateError(
            f"{normalized}: DOI appears in incompatible relevance queue state"
        )

    review = [value for value in review if value != normalized]
    if normalized_decision == "keep":
        pending.append(normalized)
    else:
        rejected.append(normalized)

    init_transition = plan_project_init_review_transition(
        config,
        doi=normalized,
        decision=normalized_decision,
    )

    outputs: dict[Path, bytes] = {}
    _put_if_changed(outputs, config.paths.pending, _doi_bytes(pending))
    _put_if_changed(outputs, config.paths.review, _doi_bytes(review))
    _put_if_changed(outputs, config.paths.rejected, _doi_bytes(rejected))
    if init_transition is not None:
        outputs.update(init_transition.outputs)

    evidence_before = read_relevance_evidence(config.relevance.evidence)
    evidence_after = record_human_relevance_decision(
        evidence_before,
        doi=normalized,
        decision=normalized_decision,
    )
    if evidence_after != evidence_before:
        _put_if_changed(
            outputs,
            config.relevance.evidence,
            json_bytes(relevance_evidence_data(evidence_after)),
        )

    return ProjectRelevanceReviewDecisionPlan(
        doi=normalized,
        decision=normalized_decision,
        outputs=MappingProxyType(outputs),
        remaining_review=len(review),
        init_batch=(
            init_transition.batch_id if init_transition is not None else None
        ),
    )


def apply_project_relevance_review_decision(
    plan: ProjectRelevanceReviewDecisionPlan,
) -> None:
    """Apply one previously planned explicit human relevance decision."""
    if not isinstance(plan, ProjectRelevanceReviewDecisionPlan):
        raise ProjectStateError(
            "plan must be a ProjectRelevanceReviewDecisionPlan"
        )
    if plan.outputs:
        atomic_write_batch(plan.outputs)
