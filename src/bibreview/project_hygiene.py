"""Project-level canonical hygiene inventory and reviewed migration orchestration."""

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
from .structured_title import normalize_structured_title
from .storage import read_bibliography


_MIGRATION_FIELDS = frozenset({"abstract", "title"})


@dataclass(frozen=True)
class HygieneMigrationProposal:
    """One historical canonical field prepared for explicit human review."""

    publication_id: str
    doi: str
    title: str
    families: tuple[str, ...]
    current_value: str
    proposed_value: str
    review_required: bool
    reason: str
    field: str = "abstract"

    @property
    def key(self) -> str:
        return f"{self.publication_id}:{self.field}"

    def data(self) -> dict[str, Any]:
        return {
            "publication_id": self.publication_id,
            "doi": self.doi,
            "title": self.title,
            "field": self.field,
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
    field: str = "abstract"

    @property
    def deterministic_proposals(self) -> int:
        return sum(not item.review_required for item in self.proposals)

    @property
    def review_required(self) -> int:
        return sum(item.review_required for item in self.proposals)

    @property
    def preserved_no_change(self) -> int:
        """Return hygiene findings intentionally excluded from migration."""
        return max(0, self.suspicious_abstracts - len(self.proposals))

    def summary(self) -> str:
        if self.field == "abstract":
            return (
                "Canonical abstract hygiene migration review\n"
                f"  Publications scanned     : {self.scanned_publications}\n"
                f"  Suspicious abstracts     : {self.suspicious_abstracts}\n"
                f"  Deterministic proposals  : {self.deterministic_proposals}\n"
                f"  Review required          : {self.review_required}"
            )
        return (
            "Canonical title hygiene migration review\n"
            f"  Publications scanned     : {self.scanned_publications}\n"
            f"  Titles with signals      : {self.suspicious_abstracts}\n"
            f"  Preserved/no change      : {self.preserved_no_change}\n"
            f"  Deterministic proposals  : {self.deterministic_proposals}\n"
            f"  Review required          : {self.review_required}"
        )

    def data(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "field": self.field,
            "scanned_publications": self.scanned_publications,
            "deterministic_proposals": self.deterministic_proposals,
            "review_required": self.review_required,
            "proposals": [item.data() for item in self.proposals],
        }
        if self.field == "abstract":
            data["suspicious_abstracts"] = self.suspicious_abstracts
        else:
            data["titles_with_hygiene_signals"] = self.suspicious_abstracts
            data["preserved_no_change"] = self.preserved_no_change
        return data


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
    *,
    field: str = "abstract",
) -> HygieneMigrationReview:
    """Derive field migration proposals from the current canonical bibliography."""
    if field not in _MIGRATION_FIELDS:
        raise ValueError(f"unsupported hygiene migration field: {field}")

    publications = read_bibliography(config.paths.bibliography)
    by_id = {publication.id: publication for publication in publications}

    if field == "abstract":
        report = scan_abstract_hygiene(publications)
        findings = report.findings
        suspicious = report.suspicious_abstracts
    else:
        report = scan_title_reference_hygiene(publications)
        findings = report.title_findings
        suspicious = report.suspicious_titles

    proposals: list[HygieneMigrationProposal] = []
    for finding in findings:
        publication = by_id[finding.publication_id]
        current_value = (
            publication.abstract if field == "abstract" else publication.title
        )
        result = (
            normalize_structured_abstract(current_value)
            if field == "abstract"
            else normalize_structured_title(current_value)
        )

        # Existing valid TeX and other deterministic no-op findings are inventory
        # signals, not historical migration decisions.
        if field == "title" and result.deterministic and not result.changed:
            continue

        deterministic = result.deterministic and result.changed
        proposals.append(
            HygieneMigrationProposal(
                publication_id=publication.id,
                doi=publication.doi or "",
                title=publication.title,
                families=finding.families,
                current_value=current_value,
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
                field=field,
            )
        )

    return HygieneMigrationReview(
        scanned_publications=report.scanned_publications,
        suspicious_abstracts=suspicious,
        proposals=tuple(proposals),
        field=field,
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
            command = (
                "hygiene --review"
                if review.field == "abstract"
                else "hygiene --titles --review"
            )
            lines.append(
                f"Use -v {command} to inspect current/proposed {review.field}s."
            )
        return "\n".join(lines)

    for index, proposal in enumerate(review.proposals, 1):
        lines.extend(
            (
                "",
                f"[{index}/{len(review.proposals)}] "
                f"{proposal.doi or proposal.publication_id} — {proposal.title}",
                f"  Field: {proposal.field}",
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
