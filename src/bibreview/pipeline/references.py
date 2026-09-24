"""Pure reconstruction and comparison helpers for reference refresh.

This module deliberately separates provider-reference reconstruction from
project-state orchestration. It never mutates canonical publications. Automatic
safety is intentionally strict: a reference-list change is considered safe only
when it is exactly explainable by the structured-citation normalizer while list
length, ordering, and identifiers remain unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from types import MappingProxyType
from typing import Any, Mapping

from ..identity import IdentityError, normalize_doi
from ..model import Publication, Reference
from ..structured_title import normalize_structured_citation


REFERENCE_REFRESH_CLASSIFICATIONS = frozenset(
    {"unchanged", "safe-update", "review-required", "unavailable"}
)


@dataclass(frozen=True)
class ProviderReferenceCandidate:
    """One reference reconstructed from a provider parent-work record."""

    index: int
    reference: Reference
    raw_citation: str
    deterministic: bool
    reason: str

    def data(self) -> dict[str, object]:
        return {
            "index": self.index,
            "reference": reference_data(self.reference),
            "raw_citation": self.raw_citation,
            "deterministic": self.deterministic,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class ReferenceReconstruction:
    """Provider reconstruction of one publication's complete reference list."""

    available: bool
    reason: str
    candidates: tuple[ProviderReferenceCandidate, ...] = ()

    @property
    def references(self) -> tuple[Reference, ...]:
        return tuple(item.reference for item in self.candidates)


@dataclass(frozen=True)
class ReferenceRefreshResult:
    """Comparison between canonical and provider-reconstructed references."""

    publication_id: str
    doi: str | None
    title: str
    classification: str
    reason: str
    current_count: int
    provider_count: int
    changed_indices: tuple[int, ...]
    current_fingerprint: str
    proposed_fingerprint: str
    proposed_references: tuple[Reference, ...]
    provider_refusals: tuple[tuple[int, str], ...] = ()

    def __post_init__(self) -> None:
        if self.classification not in REFERENCE_REFRESH_CLASSIFICATIONS:
            raise ValueError(
                f"unsupported reference refresh classification: {self.classification}"
            )

    @property
    def actionable(self) -> bool:
        return self.classification in {"safe-update", "review-required"}

    def data(self) -> dict[str, object]:
        return {
            "publication_id": self.publication_id,
            "doi": self.doi,
            "title": self.title,
            "classification": self.classification,
            "reason": self.reason,
            "current_count": self.current_count,
            "provider_count": self.provider_count,
            "changed_indices": list(self.changed_indices),
            "current_fingerprint": self.current_fingerprint,
            "proposed_fingerprint": self.proposed_fingerprint,
            "proposed_references": [
                reference_data(reference)
                for reference in self.proposed_references
            ],
            "provider_refusals": [
                {"index": index, "reason": reason}
                for index, reason in self.provider_refusals
            ],
        }


def reference_data(reference: Reference) -> dict[str, object]:
    """Return one reference as deterministic JSON-compatible data."""
    return {
        "identifiers": dict(reference.identifiers),
        "citation": reference.citation,
    }


def reference_from_data(value: Mapping[str, Any]) -> Reference:
    """Strictly decode one persisted reference object."""
    if not isinstance(value, Mapping) or set(value) != {"identifiers", "citation"}:
        raise ValueError("reference must contain identifiers and citation")
    identifiers = value["identifiers"]
    citation = value["citation"]
    if (
        not isinstance(identifiers, Mapping)
        or any(
            not isinstance(key, str) or not isinstance(item, str)
            for key, item in identifiers.items()
        )
    ):
        raise ValueError("reference identifiers must map strings to strings")
    if not isinstance(citation, str):
        raise ValueError("reference citation must be a string")
    return Reference(
        identifiers=MappingProxyType(dict(identifiers)),
        citation=citation,
    )


def reference_refresh_result_from_data(
    value: Mapping[str, Any],
) -> ReferenceRefreshResult:
    """Strictly decode one persisted reference-refresh result."""
    if not isinstance(value, Mapping):
        raise ValueError("reference refresh result must be an object")
    required = {
        "publication_id",
        "doi",
        "title",
        "classification",
        "reason",
        "current_count",
        "provider_count",
        "changed_indices",
        "current_fingerprint",
        "proposed_fingerprint",
        "proposed_references",
        "provider_refusals",
    }
    if set(value) != required:
        raise ValueError("invalid reference refresh result fields")

    publication_id = value["publication_id"]
    doi = value["doi"]
    title = value["title"]
    classification = value["classification"]
    reason = value["reason"]
    current_count = value["current_count"]
    provider_count = value["provider_count"]
    changed_indices = value["changed_indices"]
    current_fingerprint = value["current_fingerprint"]
    proposed_fingerprint = value["proposed_fingerprint"]
    proposed_references = value["proposed_references"]
    provider_refusals = value["provider_refusals"]

    if not isinstance(publication_id, str) or not publication_id:
        raise ValueError("publication_id must be a non-empty string")
    if doi is not None and not isinstance(doi, str):
        raise ValueError("doi must be a string or null")
    if not isinstance(title, str):
        raise ValueError("title must be a string")
    if classification not in REFERENCE_REFRESH_CLASSIFICATIONS:
        raise ValueError("invalid reference refresh classification")
    if not isinstance(reason, str):
        raise ValueError("reason must be a string")
    for name, item in (
        ("current_count", current_count),
        ("provider_count", provider_count),
    ):
        if not isinstance(item, int) or isinstance(item, bool) or item < 0:
            raise ValueError(f"{name} must be a non-negative integer")
    if (
        not isinstance(changed_indices, list)
        or any(
            not isinstance(item, int) or isinstance(item, bool) or item <= 0
            for item in changed_indices
        )
    ):
        raise ValueError("changed_indices must contain positive integers")
    if (
        not isinstance(current_fingerprint, str)
        or not current_fingerprint
        or not isinstance(proposed_fingerprint, str)
        or not proposed_fingerprint
    ):
        raise ValueError("reference fingerprints must be non-empty strings")
    if not isinstance(proposed_references, list):
        raise ValueError("proposed_references must be a list")
    references = tuple(reference_from_data(item) for item in proposed_references)
    if not isinstance(provider_refusals, list):
        raise ValueError("provider_refusals must be a list")
    refusals: list[tuple[int, str]] = []
    for item in provider_refusals:
        if (
            not isinstance(item, Mapping)
            or set(item) != {"index", "reason"}
            or not isinstance(item["index"], int)
            or isinstance(item["index"], bool)
            or item["index"] <= 0
            or not isinstance(item["reason"], str)
        ):
            raise ValueError("provider_refusals entries must contain index and reason")
        refusals.append((item["index"], item["reason"]))

    return ReferenceRefreshResult(
        publication_id=publication_id,
        doi=doi,
        title=title,
        classification=classification,
        reason=reason,
        current_count=current_count,
        provider_count=provider_count,
        changed_indices=tuple(changed_indices),
        current_fingerprint=current_fingerprint,
        proposed_fingerprint=proposed_fingerprint,
        proposed_references=references,
        provider_refusals=tuple(refusals),
    )


def references_fingerprint(references: tuple[Reference, ...]) -> str:
    """Return a stable fingerprint of one exact ordered reference list."""
    payload = [
        {
            "identifiers": dict(sorted(reference.identifiers.items())),
            "citation": reference.citation,
        }
        for reference in references
    ]
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def _string(value: Any) -> str:
    return "" if value is None else str(value)


def _reference_citation(reference: Mapping[str, Any]) -> str:
    """Build one provider citation without DOI-level citation lookup."""
    unstructured = _string(reference.get("unstructured")).strip()
    if unstructured:
        return unstructured

    values = [
        f"{reference['author']}," if reference.get("author") else "",
        f"{reference['article-title']}." if reference.get("article-title") else "",
        reference.get("journal-title"),
        reference.get("volume-title"),
        f"({reference['year']})" if reference.get("year") else "",
    ]
    return " ".join(str(value) for value in values if value).strip()


def reconstruct_provider_references(
    message: Mapping[str, Any],
) -> ReferenceReconstruction:
    """Reconstruct one provider parent-work reference list conservatively."""
    if not isinstance(message, Mapping):
        raise TypeError("provider work message must be a mapping")
    if "reference" not in message:
        return ReferenceReconstruction(
            available=False,
            reason="provider-references-missing",
        )

    raw_references = message["reference"]
    if not isinstance(raw_references, list):
        return ReferenceReconstruction(
            available=False,
            reason="provider-references-invalid",
        )

    candidates: list[ProviderReferenceCandidate] = []
    for index, item in enumerate(raw_references, 1):
        if not isinstance(item, Mapping):
            return ReferenceReconstruction(
                available=False,
                reason=f"provider-reference-{index}-invalid",
            )

        identifiers: dict[str, str] = {}
        raw_doi = item.get("DOI")
        if raw_doi not in (None, ""):
            try:
                identifiers["doi"] = normalize_doi(str(raw_doi))
            except IdentityError:
                pass

        raw_citation = _reference_citation(item)
        normalized = normalize_structured_citation(raw_citation)
        citation = (
            normalized.normalized
            if normalized.deterministic
            else raw_citation
        )
        candidates.append(
            ProviderReferenceCandidate(
                index=index,
                reference=Reference(
                    identifiers=MappingProxyType(identifiers),
                    citation=citation,
                ),
                raw_citation=raw_citation,
                deterministic=normalized.deterministic,
                reason=normalized.reason,
            )
        )

    return ReferenceReconstruction(
        available=True,
        reason="provider-references-available",
        candidates=tuple(candidates),
    )


def _with_canonical_doi_fallback(
    publication: Publication,
    reconstruction: ReferenceReconstruction,
) -> ReferenceReconstruction:
    """Fill citation-poor provider DOI entries from the exact canonical position.

    Parent CrossRef records frequently expose only a cited DOI. Historical
    BibReview collection resolved those DOI values through the DOI citation
    formatter, so replacing a rich canonical citation with an empty provider
    citation would create artificial drift.

    Fallback is intentionally narrow: it is allowed only when the provider
    citation is empty and the provider DOI exactly equals the canonical DOI at
    the same 1-based position. The canonical citation is then passed through the
    current conservative citation normalizer. Non-DOI entries and identifier
    drift never use this fallback.
    """
    if not reconstruction.available:
        return reconstruction

    current = publication.references
    candidates: list[ProviderReferenceCandidate] = []
    for candidate in reconstruction.candidates:
        if candidate.raw_citation.strip():
            candidates.append(candidate)
            continue

        position = candidate.index - 1
        if position >= len(current):
            candidates.append(candidate)
            continue

        provider_doi = candidate.reference.identifiers.get("doi")
        canonical = current[position]
        canonical_doi = canonical.identifiers.get("doi")
        if not provider_doi or provider_doi != canonical_doi:
            candidates.append(candidate)
            continue

        normalized = normalize_structured_citation(canonical.citation)
        citation = (
            normalized.normalized
            if normalized.deterministic
            else canonical.citation
        )
        candidates.append(
            ProviderReferenceCandidate(
                index=candidate.index,
                reference=Reference(
                    identifiers=candidate.reference.identifiers,
                    citation=citation,
                ),
                raw_citation=canonical.citation,
                deterministic=normalized.deterministic,
                reason=(
                    f"canonical-doi-fallback:{normalized.reason}"
                ),
            )
        )

    return ReferenceReconstruction(
        available=True,
        reason=reconstruction.reason,
        candidates=tuple(candidates),
    )


def _changed_indices(
    current: tuple[Reference, ...],
    proposed: tuple[Reference, ...],
) -> tuple[int, ...]:
    limit = max(len(current), len(proposed))
    result = []
    for index in range(limit):
        left = current[index] if index < len(current) else None
        right = proposed[index] if index < len(proposed) else None
        if left != right:
            result.append(index + 1)
    return tuple(result)


def _result(
    publication: Publication,
    reconstruction: ReferenceReconstruction,
    *,
    classification: str,
    reason: str,
    proposed: tuple[Reference, ...],
    changed_indices: tuple[int, ...],
    persist_proposed: bool = True,
) -> ReferenceRefreshResult:
    refusals = tuple(
        (item.index, item.reason)
        for item in reconstruction.candidates
        if not item.deterministic
    )
    return ReferenceRefreshResult(
        publication_id=publication.id,
        doi=publication.doi,
        title=publication.title,
        classification=classification,
        reason=reason,
        current_count=len(publication.references),
        provider_count=len(proposed),
        changed_indices=changed_indices,
        current_fingerprint=references_fingerprint(publication.references),
        proposed_fingerprint=references_fingerprint(proposed),
        proposed_references=proposed if persist_proposed else (),
        provider_refusals=refusals,
    )


def compare_reference_reconstruction(
    publication: Publication,
    reconstruction: ReferenceReconstruction,
) -> ReferenceRefreshResult:
    """Classify one provider reconstruction against canonical references.

    A safe update is intentionally narrow: every changed citation must equal the
    deterministic T2 normalization of the current canonical citation, and the
    ordered identifier list must remain exactly unchanged.
    """
    if not isinstance(publication, Publication):
        raise TypeError("publication must be a Publication")
    if not isinstance(reconstruction, ReferenceReconstruction):
        raise TypeError("reconstruction must be a ReferenceReconstruction")

    current = publication.references
    effective = _with_canonical_doi_fallback(publication, reconstruction)
    proposed = effective.references

    if not reconstruction.available:
        return _result(
            publication,
            effective,
            classification="unavailable",
            reason=reconstruction.reason,
            proposed=(),
            changed_indices=(),
            persist_proposed=False,
        )

    changed = _changed_indices(current, proposed)
    if not changed:
        return _result(
            publication,
            effective,
            classification="unchanged",
            reason="provider-reference-list-unchanged",
            proposed=proposed,
            changed_indices=(),
            persist_proposed=False,
        )

    if len(current) != len(proposed):
        return _result(
            publication,
            effective,
            classification="review-required",
            reason="reference-count-changed",
            proposed=proposed,
            changed_indices=changed,
        )

    for index, (left, right) in enumerate(zip(current, proposed, strict=True), 1):
        if dict(left.identifiers) != dict(right.identifiers):
            return _result(
                publication,
                effective,
                classification="review-required",
                reason=f"reference-identifiers-changed:{index}",
                proposed=proposed,
                changed_indices=changed,
            )

    for candidate, left, right in zip(
        effective.candidates,
        current,
        proposed,
        strict=True,
    ):
        if left.citation == right.citation:
            continue
        if not candidate.deterministic:
            return _result(
                publication,
                effective,
                classification="review-required",
                reason=(
                    f"provider-citation-refused:{candidate.index}:"
                    f"{candidate.reason}"
                ),
                proposed=proposed,
                changed_indices=changed,
            )
        normalized_current = normalize_structured_citation(left.citation)
        if (
            not normalized_current.deterministic
            or normalized_current.normalized != right.citation
        ):
            return _result(
                publication,
                effective,
                classification="review-required",
                reason=f"reference-citation-drift:{candidate.index}",
                proposed=proposed,
                changed_indices=changed,
            )

    return _result(
        publication,
        effective,
        classification="safe-update",
        reason="deterministic-citation-normalization",
        proposed=proposed,
        changed_indices=changed,
    )
