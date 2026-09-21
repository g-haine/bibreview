"""Pure offline comparison primitives for BibReview audit workflows.

Provider adapters are intentionally absent from this module. Callers first
normalize canonical publications and provider responses into the small audit
contracts below, then compare them without network access or project mutation.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import asdict, dataclass
import html
import re
from types import MappingProxyType
import unicodedata
from typing import Any

from ..identity import normalize_identifiers, validate_publication_id
from ..model import Author, Editor, Publication


_PAIR_CLASSIFICATIONS = frozenset(
    {
        "equal",
        "formatting-only",
        "canonical-missing",
        "provider-missing",
        "substantive-difference",
        "identity-problem",
    }
)
_PROVIDER_STATUSES = frozenset({"available", "unavailable", "error"})


class AuditError(ValueError):
    """Raised when normalized audit input or comparison state is invalid."""


AuditValue = str | tuple[str, ...]


def _freeze_value(value: AuditValue, *, name: str) -> AuditValue:
    if isinstance(value, str):
        return value
    if isinstance(value, tuple) and all(isinstance(item, str) for item in value):
        return tuple(value)
    raise AuditError(f"{name} must be a string or tuple of strings")


def _freeze_fields(
    values: Mapping[str, AuditValue],
    *,
    name: str,
) -> Mapping[str, AuditValue]:
    if not isinstance(values, Mapping):
        raise AuditError(f"{name} must be a mapping")
    result: dict[str, AuditValue] = {}
    for field, value in values.items():
        if not isinstance(field, str) or not field.strip():
            raise AuditError(f"{name} keys must be non-empty strings")
        key = field.strip()
        if key != field:
            raise AuditError(f"{name} keys must not contain surrounding whitespace")
        result[key] = _freeze_value(value, name=f"{name}.{key}")
    return MappingProxyType(result)


@dataclass(frozen=True)
class AuditRecord:
    """Canonical publication projected into provider-comparable fields."""

    publication_id: str
    identifiers: Mapping[str, str]
    permalink: str
    title: str
    fields: Mapping[str, AuditValue]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "publication_id",
            validate_publication_id(self.publication_id),
        )
        try:
            identifiers = normalize_identifiers(self.identifiers)
        except ValueError as error:
            raise AuditError(f"invalid canonical identifiers: {error}") from error
        object.__setattr__(self, "identifiers", MappingProxyType(identifiers))
        if not isinstance(self.permalink, str) or not isinstance(self.title, str):
            raise AuditError("audit record permalink and title must be strings")
        object.__setattr__(
            self,
            "fields",
            _freeze_fields(self.fields, name="audit record fields"),
        )


@dataclass(frozen=True)
class ProviderEvidence:
    """One provider's normalized evidence for a canonical publication."""

    provider: str
    identifiers: Mapping[str, str] = None
    fields: Mapping[str, AuditValue] = None
    status: str = "available"
    detail: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.provider, str) or not self.provider.strip():
            raise AuditError("provider name must be a non-empty string")
        if self.provider.strip() != self.provider:
            raise AuditError("provider name must not contain surrounding whitespace")
        if self.status not in _PROVIDER_STATUSES:
            allowed = ", ".join(sorted(_PROVIDER_STATUSES))
            raise AuditError(f"provider status must be one of: {allowed}")
        if not isinstance(self.detail, str):
            raise AuditError("provider detail must be a string")

        raw_identifiers = self.identifiers or {}
        try:
            identifiers = normalize_identifiers(raw_identifiers)
        except ValueError as error:
            raise AuditError(
                f"{self.provider}: invalid provider identifiers: {error}"
            ) from error
        fields = _freeze_fields(
            self.fields or {},
            name=f"{self.provider} fields",
        )
        if self.status != "available" and (identifiers or fields):
            raise AuditError(
                f"{self.provider}: unavailable/error evidence must not contain metadata"
            )
        object.__setattr__(self, "identifiers", MappingProxyType(identifiers))
        object.__setattr__(self, "fields", fields)


@dataclass(frozen=True)
class AuditComparison:
    """One provider-to-canonical field comparison."""

    provider: str
    field: str
    classification: str
    canonical_value: AuditValue
    provider_value: AuditValue

    def __post_init__(self) -> None:
        if self.classification not in _PAIR_CLASSIFICATIONS:
            raise AuditError(
                f"invalid audit comparison classification: {self.classification}"
            )


@dataclass(frozen=True)
class AuditProviderIssue:
    """Provider-level unavailable/error evidence, not a canonical defect."""

    provider: str
    classification: str
    detail: str

    def __post_init__(self) -> None:
        if self.classification not in {"unavailable", "error"}:
            raise AuditError("provider issue must be unavailable or error")


@dataclass(frozen=True)
class AuditDisagreement:
    """Conflicting non-empty normalized values supplied by multiple providers."""

    field: str
    provider_values: tuple[tuple[str, AuditValue], ...]

    @property
    def classification(self) -> str:
        return "provider-disagreement"


@dataclass(frozen=True)
class AuditResult:
    """Complete offline comparison result for one canonical publication."""

    publication_id: str
    identifiers: Mapping[str, str]
    permalink: str
    title: str
    comparisons: tuple[AuditComparison, ...]
    provider_issues: tuple[AuditProviderIssue, ...]
    disagreements: tuple[AuditDisagreement, ...]

    @property
    def meaningful_comparisons(self) -> tuple[AuditComparison, ...]:
        """Return pairwise comparisons other than equal/no-op values."""
        return tuple(
            item
            for item in self.comparisons
            if item.classification != "equal"
        )

    def classification_counts(self) -> Mapping[str, int]:
        """Return deterministic counts across comparisons, issues and disagreements."""
        counts = Counter(item.classification for item in self.comparisons)
        counts.update(item.classification for item in self.provider_issues)
        counts.update(item.classification for item in self.disagreements)
        return MappingProxyType(dict(sorted(counts.items())))


def _contributor_name(contributor: Author | Editor) -> str:
    if contributor.literal:
        return contributor.literal
    return " ".join(
        value
        for value in (contributor.given, contributor.family)
        if value
    ).strip()


def publication_audit_record(publication: Publication) -> AuditRecord:
    """Project one canonical Publication into the initial audit field contract."""
    if not isinstance(publication, Publication):
        raise AuditError("publication must be a Publication")
    return AuditRecord(
        publication_id=publication.id,
        identifiers=publication.identifiers,
        permalink=publication.permalink,
        title=publication.title,
        fields={
            "type": publication.type,
            "title": publication.title,
            "authors": tuple(_contributor_name(author) for author in publication.authors),
            "editors": tuple(_contributor_name(editor) for editor in publication.editors),
            "abstract": publication.abstract,
            "container_title": publication.container_title,
            "publication_year": publication.publication_year,
            "volume": publication.volume,
            "issue": publication.issue,
            "pages": publication.pages,
            "publisher": publication.publisher,
            "event": publication.event,
            "keywords": tuple(publication.keywords),
            "created_date": (
                publication.created_date.isoformat()
                if publication.created_date is not None
                else ""
            ),
        },
    )


_TAG = re.compile(r"<[^>]+>")
_DASH = re.compile(r"(?:--+|[‐‑‒–—−])")
_SPACE = re.compile(r"\s+")


def _normalize_text(value: str, *, field: str) -> str:
    text = html.unescape(value)
    text = _TAG.sub("", text)
    text = unicodedata.normalize("NFKC", text)
    text = _DASH.sub("-", text)
    text = _SPACE.sub(" ", text).strip().casefold()
    if field == "pages":
        text = text.replace(" ", "")
    return text.rstrip(".").strip()


def _normalized_value(field: str, value: AuditValue) -> tuple[str, ...]:
    if isinstance(value, str):
        return (_normalize_text(value, field=field),)
    normalized = tuple(_normalize_text(item, field=field) for item in value)
    if field == "keywords":
        return tuple(sorted(set(normalized)))
    return normalized


def _is_empty(value: AuditValue) -> bool:
    if isinstance(value, str):
        return not value.strip()
    return not value or all(not item.strip() for item in value)


def _classify_pair(
    field: str,
    canonical: AuditValue,
    provider: AuditValue,
    *,
    identity: bool = False,
) -> str:
    if _is_empty(canonical) and _is_empty(provider):
        return "equal"
    if _is_empty(canonical):
        return "canonical-missing"
    if _is_empty(provider):
        return "provider-missing"
    if canonical == provider:
        return "equal"
    if _normalized_value(field, canonical) == _normalized_value(field, provider):
        return "formatting-only"
    return "identity-problem" if identity else "substantive-difference"


def _provider_disagreements(
    record: AuditRecord,
    evidences: tuple[ProviderEvidence, ...],
) -> tuple[AuditDisagreement, ...]:
    available = tuple(item for item in evidences if item.status == "available")
    disagreements: list[AuditDisagreement] = []

    field_names = set(record.fields)
    for field in sorted(field_names):
        values = tuple(
            (item.provider, item.fields[field])
            for item in available
            if field in item.fields and not _is_empty(item.fields[field])
        )
        normalized = {
            _normalized_value(field, value)
            for _, value in values
        }
        if len(values) >= 2 and len(normalized) > 1:
            disagreements.append(
                AuditDisagreement(field=field, provider_values=values)
            )

    identifier_names = {
        name
        for item in available
        for name in item.identifiers
    }
    for name in sorted(identifier_names):
        field = f"identifiers.{name}"
        values = tuple(
            (item.provider, item.identifiers[name])
            for item in available
            if name in item.identifiers
        )
        normalized = {
            _normalized_value(field, value)
            for _, value in values
        }
        if len(values) >= 2 and len(normalized) > 1:
            disagreements.append(
                AuditDisagreement(field=field, provider_values=values)
            )

    return tuple(disagreements)


def compare_audit_record(
    record: AuditRecord,
    evidences: tuple[ProviderEvidence, ...],
) -> AuditResult:
    """Compare one canonical audit record with normalized provider evidence."""
    if not isinstance(record, AuditRecord):
        raise AuditError("record must be an AuditRecord")
    if not isinstance(evidences, tuple):
        evidences = tuple(evidences)
    if any(not isinstance(item, ProviderEvidence) for item in evidences):
        raise AuditError("evidences must contain ProviderEvidence objects")

    providers = [item.provider for item in evidences]
    if len(set(providers)) != len(providers):
        raise AuditError("provider evidence names must be unique per publication")

    comparisons: list[AuditComparison] = []
    issues: list[AuditProviderIssue] = []

    for evidence in evidences:
        if evidence.status != "available":
            issues.append(
                AuditProviderIssue(
                    provider=evidence.provider,
                    classification=evidence.status,
                    detail=evidence.detail,
                )
            )
            continue

        for name, provider_value in evidence.identifiers.items():
            field = f"identifiers.{name}"
            canonical_value = record.identifiers.get(name, "")
            comparisons.append(
                AuditComparison(
                    provider=evidence.provider,
                    field=field,
                    classification=_classify_pair(
                        field,
                        canonical_value,
                        provider_value,
                        identity=bool(canonical_value),
                    ),
                    canonical_value=canonical_value,
                    provider_value=provider_value,
                )
            )

        for field, provider_value in evidence.fields.items():
            if field not in record.fields:
                raise AuditError(
                    f"{evidence.provider}: unsupported audit field {field!r}"
                )
            canonical_value = record.fields[field]
            comparisons.append(
                AuditComparison(
                    provider=evidence.provider,
                    field=field,
                    classification=_classify_pair(
                        field,
                        canonical_value,
                        provider_value,
                    ),
                    canonical_value=canonical_value,
                    provider_value=provider_value,
                )
            )

    return AuditResult(
        publication_id=record.publication_id,
        identifiers=record.identifiers,
        permalink=record.permalink,
        title=record.title,
        comparisons=tuple(comparisons),
        provider_issues=tuple(issues),
        disagreements=_provider_disagreements(record, evidences),
    )


def _json_value(value: AuditValue) -> str | list[str]:
    return value if isinstance(value, str) else list(value)


def audit_result_data(result: AuditResult) -> dict[str, Any]:
    """Return one machine-readable, provenance-explicit audit result."""
    if not isinstance(result, AuditResult):
        raise AuditError("result must be an AuditResult")
    return {
        "publication_id": result.publication_id,
        "identifiers": dict(result.identifiers),
        "permalink": result.permalink,
        "title": result.title,
        "comparisons": [
            {
                "provider": item.provider,
                "field": item.field,
                "classification": item.classification,
                "canonical_value": _json_value(item.canonical_value),
                "provider_value": _json_value(item.provider_value),
            }
            for item in result.comparisons
        ],
        "provider_issues": [asdict(item) for item in result.provider_issues],
        "disagreements": [
            {
                "classification": item.classification,
                "field": item.field,
                "provider_values": [
                    {
                        "provider": provider,
                        "value": _json_value(value),
                    }
                    for provider, value in item.provider_values
                ],
            }
            for item in result.disagreements
        ],
        "classification_counts": dict(result.classification_counts()),
    }
