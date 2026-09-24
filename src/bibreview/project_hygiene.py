"""Project-level canonical abstract hygiene orchestration and reviewed migration."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from textwrap import fill
from types import MappingProxyType
from typing import Any, Mapping

from .config import BibReviewConfig
from .hygiene import (
    AbstractHygieneReport,
    scan_abstract_hygiene,
)
from .model import Publication
from .project import ProjectStateError
from .storage import (
    atomic_write_batch,
    json_bytes,
    read_bibliography,
    read_json,
)
from .structured_abstract import normalize_structured_abstract


HYGIENE_REVIEW_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class HygieneProposal:
    """One canonical abstract awaiting explicit historical hygiene review."""

    publication_id: str
    doi: str
    title: str
    current_abstract: str
    proposed_abstract: str
    review_required: bool
    reason: str
    families: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.publication_id:
            raise ValueError("hygiene proposal publication_id must not be empty")
        if not self.current_abstract:
            raise ValueError("hygiene proposal current_abstract must not be empty")
        if not self.reason:
            raise ValueError("hygiene proposal reason must not be empty")
        families = tuple(self.families)
        if any(not isinstance(item, str) or not item for item in families):
            raise TypeError("hygiene proposal families must contain non-empty strings")
        if self.review_required:
            if self.proposed_abstract:
                raise ValueError(
                    "review-required hygiene proposal must not define proposed_abstract"
                )
        else:
            if not self.proposed_abstract:
                raise ValueError(
                    "deterministic hygiene proposal requires proposed_abstract"
                )
            if self.proposed_abstract == self.current_abstract:
                raise ValueError(
                    "deterministic hygiene proposal must change the abstract"
                )
        object.__setattr__(self, "families", families)

    @property
    def key(self) -> str:
        return f"{self.publication_id}:abstract"

    def data(self) -> dict[str, Any]:
        return {
            "publication_id": self.publication_id,
            "doi": self.doi,
            "title": self.title,
            "current_abstract": self.current_abstract,
            "proposed_abstract": self.proposed_abstract,
            "review_required": self.review_required,
            "reason": self.reason,
            "families": list(self.families),
        }


@dataclass(frozen=True)
class HygieneReview:
    """Persisted historical abstract-cleanup proposals awaiting human decisions."""

    scanned_publications: int
    abstracts_present: int
    proposals: tuple[HygieneProposal, ...]

    @property
    def suspicious_abstracts(self) -> int:
        return len(self.proposals)

    @property
    def deterministic_proposals(self) -> int:
        return sum(not item.review_required for item in self.proposals)

    @property
    def review_required(self) -> int:
        return sum(item.review_required for item in self.proposals)

    def data(self) -> dict[str, Any]:
        return {
            "schema_version": HYGIENE_REVIEW_SCHEMA_VERSION,
            "scanned_publications": self.scanned_publications,
            "abstracts_present": self.abstracts_present,
            "proposals": [item.data() for item in self.proposals],
        }

    def summary(self) -> str:
        return (
            "Canonical abstract hygiene migration\n"
            f"  Publications scanned     : {self.scanned_publications}\n"
            f"  Abstracts present        : {self.abstracts_present}\n"
            f"  Suspicious abstracts     : {self.suspicious_abstracts}\n"
            f"  Deterministic proposals  : {self.deterministic_proposals}\n"
            f"  Review required          : {self.review_required}"
        )


@dataclass(frozen=True)
class ProjectHygieneProposalPlan:
    """Plan for persisting one exact canonical hygiene proposal set."""

    review: HygieneReview
    outputs: Mapping[Path, bytes]

    @property
    def changed(self) -> bool:
        return bool(self.outputs)

    def summary(self) -> str:
        return self.review.summary()


def hygiene_review_path(config: BibReviewConfig) -> Path:
    """Return historical hygiene proposal state beside audit files."""
    path = config.audit.report.with_name("hygiene.json")
    if path in {config.audit.report, config.audit.campaign}:
        raise ProjectStateError("hygiene review path collides with audit state")
    return path


def hygiene_review_fingerprint(review: HygieneReview) -> str:
    """Fingerprint the exact current/proposed abstract set."""
    encoded = json.dumps(
        review.data(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def hygiene_review_from_data(value: Any) -> HygieneReview:
    """Strictly load persisted canonical hygiene proposals."""
    if not isinstance(value, Mapping):
        raise ProjectStateError("hygiene review must be an object")
    if value.get("schema_version") != HYGIENE_REVIEW_SCHEMA_VERSION:
        raise ProjectStateError(
            "unsupported hygiene review schema_version: "
            f"{value.get('schema_version')!r}"
        )

    scanned = value.get("scanned_publications")
    abstracts = value.get("abstracts_present")
    if not isinstance(scanned, int) or isinstance(scanned, bool) or scanned < 0:
        raise ProjectStateError(
            "hygiene review scanned_publications must be non-negative"
        )
    if not isinstance(abstracts, int) or isinstance(abstracts, bool) or abstracts < 0:
        raise ProjectStateError(
            "hygiene review abstracts_present must be non-negative"
        )

    raw_proposals = value.get("proposals")
    if not isinstance(raw_proposals, list):
        raise ProjectStateError("hygiene review proposals must be a list")

    proposals: list[HygieneProposal] = []
    for index, raw in enumerate(raw_proposals, 1):
        if not isinstance(raw, Mapping):
            raise ProjectStateError(
                f"hygiene proposal {index} must be an object"
            )

        strings: dict[str, str] = {}
        for name in (
            "publication_id",
            "doi",
            "title",
            "current_abstract",
            "proposed_abstract",
            "reason",
        ):
            item = raw.get(name)
            if not isinstance(item, str):
                raise ProjectStateError(
                    f"hygiene proposal {index}.{name} must be a string"
                )
            if name in {"publication_id", "current_abstract", "reason"} and not item:
                raise ProjectStateError(
                    f"hygiene proposal {index}.{name} must not be empty"
                )
            strings[name] = item

        review_required = raw.get("review_required")
        if not isinstance(review_required, bool):
            raise ProjectStateError(
                f"hygiene proposal {index}.review_required must be a boolean"
            )
        raw_families = raw.get("families")
        if (
            not isinstance(raw_families, list)
            or any(not isinstance(item, str) or not item for item in raw_families)
        ):
            raise ProjectStateError(
                f"hygiene proposal {index}.families must be a list of non-empty strings"
            )

        try:
            proposals.append(
                HygieneProposal(
                    publication_id=strings["publication_id"],
                    doi=strings["doi"],
                    title=strings["title"],
                    current_abstract=strings["current_abstract"],
                    proposed_abstract=strings["proposed_abstract"],
                    review_required=review_required,
                    reason=strings["reason"],
                    families=tuple(raw_families),
                )
            )
        except (TypeError, ValueError) as error:
            raise ProjectStateError(
                f"invalid hygiene proposal {index}: {error}"
            ) from error

    keys = [item.key for item in proposals]
    if len(keys) != len(set(keys)):
        raise ProjectStateError("hygiene review contains duplicate proposal keys")

    return HygieneReview(
        scanned_publications=scanned,
        abstracts_present=abstracts,
        proposals=tuple(proposals),
    )


def load_project_hygiene_review(config: BibReviewConfig) -> HygieneReview:
    """Load one persisted historical hygiene proposal set."""
    path = hygiene_review_path(config)
    if not path.exists():
        raise ProjectStateError(
            f"{path}: hygiene proposal state is missing; run hygiene --propose first"
        )
    return hygiene_review_from_data(read_json(path, dict))


def build_hygiene_review(
    publications: tuple[Publication, ...],
) -> HygieneReview:
    """Build proposals from the current canon without mutating it."""
    report = scan_abstract_hygiene(publications)
    by_id = {publication.id: publication for publication in publications}
    proposals: list[HygieneProposal] = []

    for finding in report.findings:
        publication = by_id[finding.publication_id]
        current = publication.abstract
        normalized = normalize_structured_abstract(current)
        safe_proposal = (
            normalized.deterministic
            and normalized.changed
            and bool(normalized.normalized.strip())
        )
        proposals.append(
            HygieneProposal(
                publication_id=publication.id,
                doi=publication.doi or "",
                title=publication.title,
                current_abstract=current,
                proposed_abstract=normalized.normalized if safe_proposal else "",
                review_required=not safe_proposal,
                reason=normalized.reason,
                families=finding.families,
            )
        )

    return HygieneReview(
        scanned_publications=report.scanned_publications,
        abstracts_present=report.abstracts_present,
        proposals=tuple(proposals),
    )


def plan_project_hygiene_proposals(
    config: BibReviewConfig,
) -> ProjectHygieneProposalPlan:
    """Plan persisted historical abstract-cleanup proposals."""
    publications = read_bibliography(config.paths.bibliography)
    review = build_hygiene_review(publications)
    outputs = {
        hygiene_review_path(config): json_bytes(review.data())
    }
    return ProjectHygieneProposalPlan(
        review=review,
        outputs=MappingProxyType(outputs),
    )


def apply_project_hygiene_proposals(
    plan: ProjectHygieneProposalPlan,
) -> None:
    """Persist a previously prepared hygiene proposal set."""
    if not isinstance(plan, ProjectHygieneProposalPlan):
        raise ProjectStateError("plan must be a ProjectHygieneProposalPlan")
    if plan.outputs:
        atomic_write_batch(plan.outputs)


def project_abstract_hygiene(config: BibReviewConfig) -> AbstractHygieneReport:
    """Scan the configured canonical bibliography without writing project state."""
    return scan_abstract_hygiene(read_bibliography(config.paths.bibliography))


def format_project_hygiene_review(
    review: HygieneReview,
    *,
    verbose: bool = False,
) -> str:
    """Format persisted migration proposals for offline human inspection."""
    lines = [review.summary()]
    if not verbose:
        return "\n".join(lines)

    for proposal in review.proposals:
        identity = proposal.doi or proposal.publication_id
        lines.extend((
            "",
            f"{identity} — {proposal.title}",
            "  Families: " + ", ".join(proposal.families),
            f"  Normalizer: {proposal.reason}",
            "  Current:",
            fill(
                proposal.current_abstract,
                width=100,
                initial_indent="    ",
                subsequent_indent="    ",
            ),
        ))
        if proposal.review_required:
            lines.extend((
                "  Proposed: (none — review required)",
            ))
        else:
            lines.extend((
                "  Proposed:",
                fill(
                    proposal.proposed_abstract,
                    width=100,
                    initial_indent="    ",
                    subsequent_indent="    ",
                ),
            ))
    return "\n".join(lines)
