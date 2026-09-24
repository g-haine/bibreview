"""Project-level canonical abstract hygiene orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from textwrap import fill
from typing import Any

from .config import BibReviewConfig
from .hygiene import (
    AbstractHygieneReport,
    TitleReferenceHygieneReport,
    scan_abstract_hygiene,
    scan_title_reference_hygiene,
)
from .structured_abstract import normalize_structured_abstract
from .storage import read_bibliography


@dataclass(frozen=True)
class HygieneMigrationProposal:
    """One historical canonical abstract prepared for explicit human review."""

    publication_id: str
    doi: str
    title: str
    families: tuple[str, ...]
    current_value: str
    proposed_value: str
    review_required: bool
    reason: str

    @property
    def key(self) -> str:
        return f"{self.publication_id}:abstract"

    def data(self) -> dict[str, Any]:
        return {
            "publication_id": self.publication_id,
            "doi": self.doi,
            "title": self.title,
            "field": "abstract",
            "families": list(self.families),
            "current_value": self.current_value,
            "proposed_value": self.proposed_value,
            "review_required": self.review_required,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class HygieneMigrationReview:
    """Read-only migration proposals derived from the current canonical bibliography."""

    scanned_publications: int
    suspicious_abstracts: int
    proposals: tuple[HygieneMigrationProposal, ...]

    @property
    def deterministic_proposals(self) -> int:
        return sum(not item.review_required for item in self.proposals)

    @property
    def review_required(self) -> int:
        return sum(item.review_required for item in self.proposals)

    def summary(self) -> str:
        return (
            "Canonical abstract hygiene migration review\n"
            f"  Publications scanned     : {self.scanned_publications}\n"
            f"  Suspicious abstracts     : {self.suspicious_abstracts}\n"
            f"  Deterministic proposals  : {self.deterministic_proposals}\n"
            f"  Review required          : {self.review_required}"
        )

    def data(self) -> dict[str, Any]:
        return {
            "scanned_publications": self.scanned_publications,
            "suspicious_abstracts": self.suspicious_abstracts,
            "deterministic_proposals": self.deterministic_proposals,
            "review_required": self.review_required,
            "proposals": [item.data() for item in self.proposals],
        }


def project_abstract_hygiene(config: BibReviewConfig) -> AbstractHygieneReport:
    """Scan the configured canonical bibliography without writing project state."""
    return scan_abstract_hygiene(read_bibliography(config.paths.bibliography))


def project_title_reference_hygiene(
    config: BibReviewConfig,
) -> TitleReferenceHygieneReport:
    """Scan canonical titles and reference citations without writing project state."""
    return scan_title_reference_hygiene(
        read_bibliography(config.paths.bibliography)
    )


def project_hygiene_migration_review(
    config: BibReviewConfig,
) -> HygieneMigrationReview:
    """Derive migration proposals from the current canonical bibliography only."""
    publications = read_bibliography(config.paths.bibliography)
    report = scan_abstract_hygiene(publications)
    by_id = {publication.id: publication for publication in publications}

    proposals: list[HygieneMigrationProposal] = []
    for finding in report.findings:
        publication = by_id[finding.publication_id]
        result = normalize_structured_abstract(publication.abstract)
        deterministic = result.deterministic and result.changed
        proposals.append(
            HygieneMigrationProposal(
                publication_id=publication.id,
                doi=publication.doi or "",
                title=publication.title,
                families=finding.families,
                current_value=publication.abstract,
                proposed_value=result.normalized if deterministic else "",
                review_required=not deterministic,
                reason=(
                    result.reason
                    if not result.deterministic
                    else (
                        result.reason
                        if result.changed
                        else "no-deterministic-change"
                    )
                ),
            )
        )

    return HygieneMigrationReview(
        scanned_publications=report.scanned_publications,
        suspicious_abstracts=report.suspicious_abstracts,
        proposals=tuple(proposals),
    )


def format_project_hygiene_migration_review(
    review: HygieneMigrationReview,
    *,
    verbose: bool = False,
) -> str:
    """Format migration proposals without mutating project state."""
    lines = [review.summary()]
    if not verbose:
        if review.proposals:
            lines.append(
                "Use -v hygiene --review to inspect current/proposed abstracts."
            )
        return "\n".join(lines)

    for index, proposal in enumerate(review.proposals, 1):
        lines.extend(
            (
                "",
                f"[{index}/{len(review.proposals)}] "
                f"{proposal.doi or proposal.publication_id} — {proposal.title}",
                "  Families: " + ", ".join(proposal.families),
                f"  Normalizer: {proposal.reason}",
                "  Current:",
                fill(
                    proposal.current_value,
                    width=100,
                    initial_indent="    ",
                    subsequent_indent="    ",
                ),
            )
        )
        if proposal.review_required:
            lines.extend(
                (
                    "  Status: REVIEW REQUIRED",
                    "  Proposed: (no safe automatic value)",
                )
            )
        else:
            lines.extend(
                (
                    "  Proposed:",
                    fill(
                        proposal.proposed_value,
                        width=100,
                        initial_indent="    ",
                        subsequent_indent="    ",
                    ),
                )
            )

    return "\n".join(lines)
