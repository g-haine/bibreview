"""Pure offline comparison primitives for BibReview audit workflows.

Provider adapters are intentionally absent from this module. Callers first
normalize canonical publications and provider responses into the small audit
contracts below, then compare them without network access or project mutation.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from difflib import SequenceMatcher
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
    identifiers: Mapping[str, str] = field(default_factory=dict)
    fields: Mapping[str, AuditValue] = field(default_factory=dict)
    status: str = "available"
    detail: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.provider, str) or not self.provider.strip():
            raise AuditError("provider name must be a non-empty string")
        if self.provider.strip() != self.provider:
            raise AuditError("provider name must not contain surrounding whitespace")
        if not isinstance(self.status, str) or self.status not in _PROVIDER_STATUSES:
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
class AuditReviewFinding:
    """Derived human-review finding without changing stored provider evidence."""

    field: str
    classification: str
    providers: tuple[str, ...]
    canonical_value: AuditValue
    provider_values: tuple[tuple[str, AuditValue], ...]
    actionable: bool = True
    detail: str = ""


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
_ABSTRACT_PREFIX = re.compile(r"^(?:abstract|summary)\s*[:.\-]?\s*", re.IGNORECASE)
_TEX_COMMAND = re.compile(r"\\([A-Za-z]+)")
_TEX_MATH_DELIMITER = re.compile(r"(?:\$\$?|\\\(|\\\)|\\\[|\\\])")
_NAME_PUNCTUATION = re.compile(r"[^a-z0-9 ]+")
_FAMILY_PARTICLES = frozenset({
    "da", "de", "del", "della", "den", "der", "di", "du",
    "la", "le", "van", "von",
})


_TEX_SYMBOLS = {
    "le": "≤",
    "leq": "≤",
    "ge": "≥",
    "geq": "≥",
    "theta": "θ",
    "alpha": "α",
    "beta": "β",
    "gamma": "γ",
    "delta": "δ",
    "lambda": "λ",
    "mu": "μ",
    "phi": "φ",
    "psi": "ψ",
    "omega": "ω",
}


def _strip_diacritics(value: str) -> str:
    return "".join(
        char
        for char in unicodedata.normalize("NFKD", value)
        if not unicodedata.combining(char)
    )


def _normalize_tex(value: str) -> str:
    text = _TEX_MATH_DELIMITER.sub("", value)

    def replace_command(match: re.Match[str]) -> str:
        command = match.group(1)
        return _TEX_SYMBOLS.get(command.casefold(), "")

    text = _TEX_COMMAND.sub(replace_command, text)
    return text.replace("{", "").replace("}", "")


def _normalize_text(value: str, *, field: str) -> str:
    text = html.unescape(value)
    text = _TAG.sub("", text)
    if field in {"title", "abstract"}:
        text = _normalize_tex(text)
    text = unicodedata.normalize("NFKC", text)
    text = _DASH.sub("-", text)
    if field == "abstract":
        text = _ABSTRACT_PREFIX.sub("", text)
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


def _name_tokens(value: str) -> tuple[str, ...]:
    text = _strip_diacritics(value).casefold()
    text = _DASH.sub("", text).replace("-", "")
    text = text.replace("'", "").replace("’", "")
    text = _NAME_PUNCTUATION.sub(" ", text)
    return tuple(
        item
        for item in _SPACE.sub(" ", text).strip().split(" ")
        if item
    )


def _name_parts(value: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    if "," in value:
        family_text, given_text = value.split(",", 1)
        family = _name_tokens(family_text)
        given = _name_tokens(given_text)
        if family:
            return given, family

    tokens = _name_tokens(value)
    if not tokens:
        return (), ()

    family_start = len(tokens) - 1
    while family_start > 0 and tokens[family_start - 1] in _FAMILY_PARTICLES:
        family_start -= 1
    return tokens[:family_start], tokens[family_start:]


def _compatible_name_token(left: str, right: str) -> bool:
    return (
        left == right
        or (len(left) == 1 and right.startswith(left))
        or (len(right) == 1 and left.startswith(right))
    )


def _compatible_given_names(
    left: tuple[str, ...],
    right: tuple[str, ...],
) -> bool:
    if left == right:
        return True
    if not left or not right:
        return False

    left_joined = "".join(left)
    right_joined = "".join(right)
    if left_joined == right_joined:
        return True

    if len(left) == len(right) and all(
        _compatible_name_token(a, b)
        for a, b in zip(left, right, strict=True)
    ):
        return True

    left_all_initials = all(len(token) == 1 for token in left)
    right_all_initials = all(len(token) == 1 for token in right)
    if left_all_initials and right_all_initials:
        return left == right

    if left_all_initials:
        return left[0] == right[0][0]
    if right_all_initials:
        return right[0] == left[0][0]

    if left[0] == right[0]:
        left_extra = left[1:]
        right_extra = right[1:]
        if not left_extra and all(len(token) == 1 for token in right_extra):
            return True
        if not right_extra and all(len(token) == 1 for token in left_extra):
            return True

    return False


def _compatible_name(left: str, right: str) -> bool:
    if _normalize_text(left, field="authors") == _normalize_text(right, field="authors"):
        return True
    left_given, left_family = _name_parts(left)
    right_given, right_family = _name_parts(right)
    return bool(
        left_family
        and left_family == right_family
        and _compatible_given_names(left_given, right_given)
    )


def _compatible_contributors(
    canonical: AuditValue,
    provider: AuditValue,
) -> bool:
    if not isinstance(canonical, tuple) or not isinstance(provider, tuple):
        return False
    if len(canonical) != len(provider):
        return False
    return all(
        _compatible_name(left, right)
        for left, right in zip(canonical, provider, strict=True)
    )


def _compatible_contributor_set(
    canonical: AuditValue,
    provider: AuditValue,
) -> bool:
    if not isinstance(canonical, tuple) or not isinstance(provider, tuple):
        return False
    if len(canonical) != len(provider):
        return False

    remaining = list(provider)
    for canonical_name in canonical:
        match = next(
            (
                index
                for index, provider_name in enumerate(remaining)
                if _compatible_name(canonical_name, provider_name)
            ),
            None,
        )
        if match is None:
            return False
        remaining.pop(match)
    return True


def _near_equal_abstract(canonical: AuditValue, provider: AuditValue) -> bool:
    if not isinstance(canonical, str) or not isinstance(provider, str):
        return False
    left = _normalize_text(canonical, field="abstract")
    right = _normalize_text(provider, field="abstract")
    if not left or not right:
        return False
    compact_left = re.sub(r"[^\\w]+", "", left, flags=re.UNICODE)
    compact_right = re.sub(r"[^\\w]+", "", right, flags=re.UNICODE)
    if compact_left == compact_right:
        return True
    shorter = min(len(left), len(right))
    longer = max(len(left), len(right))
    if longer == 0 or shorter / longer < 0.95:
        return False
    return SequenceMatcher(None, left, right).ratio() >= 0.98


def _equivalent_values(
    field: str,
    left: AuditValue,
    right: AuditValue,
) -> bool:
    if left == right:
        return True
    if _normalized_value(field, left) == _normalized_value(field, right):
        return True
    if field in {"authors", "editors"}:
        return _compatible_contributors(left, right)
    if field == "abstract":
        return _near_equal_abstract(left, right)
    return False


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
    if _equivalent_values(field, canonical, provider):
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
        representatives: list[AuditValue] = []
        for _, value in values:
            if not any(
                _equivalent_values(field, value, existing)
                for existing in representatives
            ):
                representatives.append(value)
        if len(values) >= 2 and len(representatives) > 1:
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
        representatives: list[AuditValue] = []
        for _, value in values:
            if not any(
                _equivalent_values(field, value, existing)
                for existing in representatives
            ):
                representatives.append(value)
        if len(values) >= 2 and len(representatives) > 1:
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


def _disagreements_from_comparisons(
    comparisons: tuple[AuditComparison, ...],
) -> tuple[AuditDisagreement, ...]:
    grouped: dict[str, list[tuple[str, AuditValue]]] = {}
    for item in comparisons:
        if _is_empty(item.provider_value):
            continue
        grouped.setdefault(item.field, []).append(
            (item.provider, item.provider_value)
        )

    disagreements: list[AuditDisagreement] = []
    for field in sorted(grouped):
        values = tuple(grouped[field])
        normalized = {
            _normalized_value(field, value)
            for _, value in values
        }
        if len(values) >= 2 and len(normalized) > 1:
            disagreements.append(
                AuditDisagreement(field=field, provider_values=values)
            )
    return tuple(disagreements)


def reclassify_audit_result(result: AuditResult) -> AuditResult:
    """Reapply current offline comparison rules to persisted raw values."""
    if not isinstance(result, AuditResult):
        raise AuditError("result must be an AuditResult")
    comparisons = tuple(
        AuditComparison(
            provider=item.provider,
            field=item.field,
            classification=_classify_pair(
                item.field,
                item.canonical_value,
                item.provider_value,
                identity=item.field.startswith("identifiers.")
                and not _is_empty(item.canonical_value),
            ),
            canonical_value=item.canonical_value,
            provider_value=item.provider_value,
        )
        for item in result.comparisons
    )
    return AuditResult(
        publication_id=result.publication_id,
        identifiers=result.identifiers,
        permalink=result.permalink,
        title=result.title,
        comparisons=comparisons,
        provider_issues=result.provider_issues,
        disagreements=_disagreements_from_comparisons(comparisons),
    )


def _canonical_field_value(
    comparisons: tuple[AuditComparison, ...],
    field: str,
) -> AuditValue:
    values = [
        item.canonical_value
        for item in comparisons
        if item.field == field and not _is_empty(item.canonical_value)
    ]
    if not values:
        return ""
    return values[0]


def _merge_review_findings(
    findings: list[AuditReviewFinding],
) -> tuple[AuditReviewFinding, ...]:
    merged: list[AuditReviewFinding] = []
    for finding in findings:
        representative = (
            finding.provider_values[0][1]
            if finding.provider_values
            else ""
        )
        match_index: int | None = None
        for index, existing in enumerate(merged):
            if (
                existing.field == finding.field
                and existing.classification == finding.classification
                and existing.actionable == finding.actionable
                and existing.detail == finding.detail
                and _equivalent_values(
                    finding.field,
                    existing.canonical_value,
                    finding.canonical_value,
                )
                and existing.provider_values
                and _equivalent_values(
                    finding.field,
                    existing.provider_values[0][1],
                    representative,
                )
            ):
                match_index = index
                break

        if match_index is None:
            merged.append(finding)
            continue

        existing = merged[match_index]
        merged[match_index] = AuditReviewFinding(
            field=existing.field,
            classification=existing.classification,
            providers=tuple(dict.fromkeys(existing.providers + finding.providers)),
            canonical_value=existing.canonical_value,
            provider_values=existing.provider_values + finding.provider_values,
            actionable=existing.actionable,
            detail=existing.detail,
        )
    return tuple(merged)


def audit_review_findings(result: AuditResult) -> tuple[AuditReviewFinding, ...]:
    """Derive concise human-review findings from a full raw audit result."""
    if not isinstance(result, AuditResult):
        raise AuditError("result must be an AuditResult")

    comparisons = result.comparisons
    canonical_editors = _canonical_field_value(comparisons, "editors")
    equal_fields = {
        item.field
        for item in comparisons
        if item.classification == "equal"
    }
    findings: list[AuditReviewFinding] = []
    handled_role_pairs: set[tuple[str, str]] = set()

    for item in comparisons:
        if item.classification in {"equal", "formatting-only", "provider-missing"}:
            continue

        if (
            item.field == "authors"
            and item.classification == "canonical-missing"
            and not _is_empty(canonical_editors)
        ):
            key = (item.provider, item.field)
            if key not in handled_role_pairs:
                matches_editors = _compatible_contributor_set(
                    canonical_editors,
                    item.provider_value,
                )
                findings.append(
                    AuditReviewFinding(
                        field="contributors",
                        classification="role-disagreement",
                        providers=(item.provider,),
                        canonical_value=canonical_editors,
                        provider_values=((item.provider, item.provider_value),),
                        actionable=False,
                        detail=(
                            "provider authors match canonical editors"
                            if matches_editors
                            else "provider reports authors for canonical editor-only record"
                        ),
                    )
                )
                handled_role_pairs.add(key)
            continue

        if (
            item.classification == "substantive-difference"
            and item.field in {"publication_year", "container_title"}
            and item.field in equal_fields
        ):
            continue

        findings.append(
            AuditReviewFinding(
                field=item.field,
                classification=item.classification,
                providers=(item.provider,),
                canonical_value=item.canonical_value,
                provider_values=((item.provider, item.provider_value),),
                actionable=True,
            )
        )

    existing_fields = {
        (finding.field, finding.classification)
        for finding in findings
    }
    for disagreement in result.disagreements:
        if (
            disagreement.field in {"publication_year", "container_title"}
            and disagreement.field in equal_fields
        ):
            continue
        if (disagreement.field, "substantive-difference") in existing_fields:
            continue
        findings.append(
            AuditReviewFinding(
                field=disagreement.field,
                classification="provider-disagreement",
                providers=tuple(
                    provider for provider, _ in disagreement.provider_values
                ),
                canonical_value=_canonical_field_value(
                    comparisons,
                    disagreement.field,
                ),
                provider_values=disagreement.provider_values,
                actionable=False,
            )
        )

    return _merge_review_findings(findings)


def _json_value(value: AuditValue) -> str | list[str]:
    return value if isinstance(value, str) else list(value)



def _audit_value_from_json(value: Any, *, name: str) -> AuditValue:
    if isinstance(value, str):
        return value
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return tuple(value)
    raise AuditError(f"{name} must be a string or list of strings")


def audit_result_from_data(value: Mapping[str, Any]) -> AuditResult:
    """Reconstruct and validate one persisted machine-readable audit result."""
    if not isinstance(value, Mapping):
        raise AuditError("audit result must be an object")
    required = {
        "publication_id",
        "identifiers",
        "permalink",
        "title",
        "comparisons",
        "provider_issues",
        "disagreements",
        "classification_counts",
    }
    missing = required - value.keys()
    unknown = value.keys() - required
    if missing:
        raise AuditError(
            "audit result missing fields: " + ", ".join(sorted(missing))
        )
    if unknown:
        raise AuditError(
            "audit result has unknown fields: " + ", ".join(sorted(unknown))
        )

    publication_id = validate_publication_id(value["publication_id"])
    raw_identifiers = value["identifiers"]
    if not isinstance(raw_identifiers, Mapping):
        raise AuditError("audit result identifiers must be an object")
    try:
        identifiers = normalize_identifiers(raw_identifiers)
    except (TypeError, ValueError) as error:
        raise AuditError(f"invalid audit result identifiers: {error}") from error

    permalink = value["permalink"]
    title = value["title"]
    if not isinstance(permalink, str) or not isinstance(title, str):
        raise AuditError("audit result permalink and title must be strings")

    raw_comparisons = value["comparisons"]
    if not isinstance(raw_comparisons, list):
        raise AuditError("audit result comparisons must be a list")
    comparisons: list[AuditComparison] = []
    for index, raw in enumerate(raw_comparisons, 1):
        if not isinstance(raw, Mapping):
            raise AuditError(f"audit result comparisons[{index}] must be an object")
        required_comparison = {
            "provider",
            "field",
            "classification",
            "canonical_value",
            "provider_value",
        }
        if set(raw) != required_comparison:
            raise AuditError(
                f"audit result comparisons[{index}] must contain "
                + ", ".join(sorted(required_comparison))
            )
        provider = raw["provider"]
        field_name = raw["field"]
        classification = raw["classification"]
        if not isinstance(classification, str):
            raise AuditError(
                f"audit result comparisons[{index}].classification must be a string"
            )
        if not isinstance(provider, str) or not provider.strip():
            raise AuditError(
                f"audit result comparisons[{index}].provider must be a non-empty string"
            )
        if not isinstance(field_name, str) or not field_name.strip():
            raise AuditError(
                f"audit result comparisons[{index}].field must be a non-empty string"
            )
        comparisons.append(
            AuditComparison(
                provider=provider,
                field=field_name,
                classification=classification,
                canonical_value=_audit_value_from_json(
                    raw["canonical_value"],
                    name=f"audit result comparisons[{index}].canonical_value",
                ),
                provider_value=_audit_value_from_json(
                    raw["provider_value"],
                    name=f"audit result comparisons[{index}].provider_value",
                ),
            )
        )

    raw_issues = value["provider_issues"]
    if not isinstance(raw_issues, list):
        raise AuditError("audit result provider_issues must be a list")
    provider_issues: list[AuditProviderIssue] = []
    for index, raw in enumerate(raw_issues, 1):
        if not isinstance(raw, Mapping) or set(raw) != {
            "provider",
            "classification",
            "detail",
        }:
            raise AuditError(
                f"audit result provider_issues[{index}] must contain "
                "provider, classification, and detail"
            )
        if (
            not isinstance(raw["provider"], str)
            or not raw["provider"].strip()
            or not isinstance(raw["detail"], str)
        ):
            raise AuditError(
                f"audit result provider_issues[{index}] has invalid text fields"
            )
        provider_issues.append(
            AuditProviderIssue(
                provider=raw["provider"],
                classification=raw["classification"],
                detail=raw["detail"],
            )
        )

    raw_disagreements = value["disagreements"]
    if not isinstance(raw_disagreements, list):
        raise AuditError("audit result disagreements must be a list")
    disagreements: list[AuditDisagreement] = []
    for index, raw in enumerate(raw_disagreements, 1):
        if not isinstance(raw, Mapping) or set(raw) != {
            "classification",
            "field",
            "provider_values",
        }:
            raise AuditError(
                f"audit result disagreements[{index}] must contain "
                "classification, field, and provider_values"
            )
        if raw["classification"] != "provider-disagreement":
            raise AuditError(
                f"audit result disagreements[{index}].classification is invalid"
            )
        field_name = raw["field"]
        raw_provider_values = raw["provider_values"]
        if not isinstance(field_name, str) or not field_name.strip():
            raise AuditError(
                f"audit result disagreements[{index}].field must be a non-empty string"
            )
        if not isinstance(raw_provider_values, list):
            raise AuditError(
                f"audit result disagreements[{index}].provider_values must be a list"
            )
        provider_values: list[tuple[str, AuditValue]] = []
        for pair_index, pair in enumerate(raw_provider_values, 1):
            if not isinstance(pair, Mapping) or set(pair) != {"provider", "value"}:
                raise AuditError(
                    f"audit result disagreements[{index}].provider_values"
                    f"[{pair_index}] must contain provider and value"
                )
            provider = pair["provider"]
            if not isinstance(provider, str) or not provider.strip():
                raise AuditError(
                    f"audit result disagreements[{index}].provider_values"
                    f"[{pair_index}].provider must be a non-empty string"
                )
            provider_values.append(
                (
                    provider,
                    _audit_value_from_json(
                        pair["value"],
                        name=(
                            f"audit result disagreements[{index}].provider_values"
                            f"[{pair_index}].value"
                        ),
                    ),
                )
            )
        disagreements.append(
            AuditDisagreement(
                field=field_name,
                provider_values=tuple(provider_values),
            )
        )

    result = AuditResult(
        publication_id=publication_id,
        identifiers=MappingProxyType(dict(identifiers)),
        permalink=permalink,
        title=title,
        comparisons=tuple(comparisons),
        provider_issues=tuple(provider_issues),
        disagreements=tuple(disagreements),
    )

    counts = value["classification_counts"]
    if not isinstance(counts, Mapping):
        raise AuditError("audit result classification_counts must be an object")
    normalized_counts: dict[str, int] = {}
    for name, count in counts.items():
        if (
            not isinstance(name, str)
            or not isinstance(count, int)
            or isinstance(count, bool)
            or count < 0
        ):
            raise AuditError(
                "audit result classification_counts must map strings to "
                "non-negative integers"
            )
        normalized_counts[name] = count
    if normalized_counts != dict(result.classification_counts()):
        raise AuditError("audit result classification_counts are inconsistent")

    return result

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
