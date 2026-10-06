"""Author-name mapping analysis independent of project storage and site rendering."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from types import MappingProxyType
from typing import Iterable, Mapping
from urllib.parse import quote

from unidecode import unidecode

from ..model import Author, Publication
from ..text import safe_component, slugify


class AuthorMappingError(ValueError):
    """Raised when author mapping state is invalid or ambiguous."""


@dataclass(frozen=True)
class AuthorReviewItem:
    """One author identity proposal that requires human judgment."""

    slug: str
    names: tuple[str, ...]
    occurrences: int
    reasons: tuple[str, ...]
    possible_matches: Mapping[str, tuple[str, ...]]


@dataclass(frozen=True)
class AuthorMappingPlan:
    """Read-only result of author mapping analysis."""

    known_names: int
    unknown_names: int
    safe: Mapping[str, tuple[str, ...]]
    review: tuple[AuthorReviewItem, ...]


@dataclass(frozen=True)
class AuthorReviewOccurrence:
    """Source evidence for one unresolved author name in one publication."""

    publication_id: str
    doi: str | None
    title: str
    orcid: str | None
    affiliations: tuple[str, ...]


@dataclass(frozen=True)
class AuthorReviewCase:
    """One exact unresolved display name with evidence for human review."""

    name: str
    slug: str
    reasons: tuple[str, ...]
    possible_matches: Mapping[str, tuple[str, ...]]
    occurrences: tuple[AuthorReviewOccurrence, ...]
    possible_match_occurrences: Mapping[str, tuple[AuthorReviewOccurrence, ...]]


def author_name(author: Author) -> str:
    """Return the source-provided display name without inventing missing parts."""
    if not isinstance(author, Author):
        raise AuthorMappingError("author must be an Author")
    literal = (author.literal or "").strip()
    if literal:
        return literal
    parts = [part.strip() for part in (author.given or "", author.family or "") if part.strip()]
    if not parts:
        raise AuthorMappingError("author has no displayable name")
    return " ".join(parts)


def publication_author_names(publications: Iterable[Publication]) -> tuple[str, ...]:
    """Return source author names in publication order, including repetitions."""
    names: list[str] = []
    for publication in publications:
        if not isinstance(publication, Publication):
            raise AuthorMappingError("publications must contain Publication objects")
        names.extend(author_name(author) for author in publication.authors)
    return tuple(names)


def validate_author_mappings(mapping: Mapping[str, object]) -> tuple[dict[str, tuple[str, ...]], dict[str, str]]:
    """Validate ``slug -> name variants`` state and build its reverse lookup."""
    if not isinstance(mapping, Mapping):
        raise AuthorMappingError("author mappings must be an object")
    canonical: dict[str, tuple[str, ...]] = {}
    reverse: dict[str, str] = {}
    for slug, raw_names in mapping.items():
        if not isinstance(slug, str):
            raise AuthorMappingError("author mapping slugs must be strings")
        try:
            safe_component(slug)
        except ValueError as error:
            raise AuthorMappingError(str(error)) from error
        if not isinstance(raw_names, (list, tuple)) or not raw_names:
            raise AuthorMappingError(f"{slug}: expected a non-empty list of author names")
        names: list[str] = []
        for value in raw_names:
            if not isinstance(value, str) or not value.strip():
                raise AuthorMappingError(f"{slug}: author names must be non-empty strings")
            name = value.strip()
            if name in reverse and reverse[name] != slug:
                raise AuthorMappingError(f"ambiguous author name {name!r}")
            reverse[name] = slug
            names.append(name)
        canonical[slug] = tuple(names)
    return canonical, reverse


def _name_signature(name: str) -> tuple[str, str]:
    words = slugify(name).split("-")
    return (words[-1], words[0][:1]) if words else ("", "")


def plan_author_mappings(
    publications: Iterable[Publication],
    mapping: Mapping[str, object],
) -> AuthorMappingPlan:
    """Separate unambiguous new author identities from names needing review."""
    canonical, reverse = validate_author_mappings(mapping)
    occurrences = Counter(publication_author_names(publications))
    unknown = sorted(
        set(occurrences) - reverse.keys(),
        key=lambda name: (unidecode(name.split()[-1]), name),
    )

    grouped: dict[str, list[str]] = defaultdict(list)
    for name in unknown:
        slug = slugify(name)
        if not slug:
            raise AuthorMappingError(f"cannot generate an author slug for {name!r}")
        grouped[slug].append(name)

    safe: dict[str, tuple[str, ...]] = {}
    review: list[AuthorReviewItem] = []
    for slug, names in grouped.items():
        signature = _name_signature(names[0])
        possible = sorted(
            {
                known_slug
                for known_name, known_slug in reverse.items()
                if _name_signature(known_name) == signature
            }
        )
        if slug not in canonical and len(names) == 1 and not possible:
            safe[slug] = tuple(names)
            continue

        reasons: list[str] = []
        if slug in canonical:
            reasons.append("proposed slug already exists")
        if len(names) > 1:
            reasons.append("several unknown names produce the same slug")
        if possible:
            reasons.append("a known author has the same surname and first initial")
        review.append(
            AuthorReviewItem(
                slug=slug,
                names=tuple(names),
                occurrences=sum(occurrences[name] for name in names),
                reasons=tuple(reasons),
                possible_matches=MappingProxyType(
                    {candidate: canonical[candidate] for candidate in possible}
                ),
            )
        )

    return AuthorMappingPlan(
        known_names=len(reverse),
        unknown_names=len(unknown),
        safe=MappingProxyType(safe),
        review=tuple(review),
    )


def _author_affiliations(author: Author) -> tuple[str, ...]:
    """Return human-readable affiliations preserved in source fields."""
    raw = author.source_fields.get("affiliation", ())
    if not isinstance(raw, (list, tuple)):
        return ()
    values: list[str] = []
    for item in raw:
        if isinstance(item, Mapping):
            name = item.get("name")
            if isinstance(name, str) and name.strip():
                values.append(name.strip())
        elif isinstance(item, str) and item.strip():
            values.append(item.strip())
    return tuple(values)


def _author_orcid(author: Author) -> str | None:
    """Return a preserved ORCID source field when present."""
    for key in ("ORCID", "orcid"):
        value = author.source_fields.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def author_review_cases(
    publications: Iterable[Publication],
    mapping: Mapping[str, object],
) -> tuple[AuthorReviewCase, ...]:
    """Return exact unresolved names plus local publication evidence."""
    items = tuple(publications)
    plan = plan_author_mappings(items, mapping)
    cases: list[AuthorReviewCase] = []
    for review in plan.review:
        for name in review.names:
            occurrences: list[AuthorReviewOccurrence] = []
            for publication in items:
                for author in publication.authors:
                    if author_name(author) != name:
                        continue
                    occurrences.append(
                        AuthorReviewOccurrence(
                            publication_id=publication.id,
                            doi=publication.doi,
                            title=publication.title,
                            orcid=_author_orcid(author),
                            affiliations=_author_affiliations(author),
                        )
                    )
            possible_match_occurrences: dict[str, tuple[AuthorReviewOccurrence, ...]] = {}
            for candidate_slug, candidate_names in review.possible_matches.items():
                candidate_occurrences: list[AuthorReviewOccurrence] = []
                candidate_name_set = set(candidate_names)
                for publication in items:
                    for author in publication.authors:
                        if author_name(author) not in candidate_name_set:
                            continue
                        candidate_occurrences.append(
                            AuthorReviewOccurrence(
                                publication_id=publication.id,
                                doi=publication.doi,
                                title=publication.title,
                                orcid=_author_orcid(author),
                                affiliations=_author_affiliations(author),
                            )
                        )
                possible_match_occurrences[candidate_slug] = tuple(candidate_occurrences)

            cases.append(
                AuthorReviewCase(
                    name=name,
                    slug=slugify(name),
                    reasons=review.reasons,
                    possible_matches=review.possible_matches,
                    occurrences=tuple(occurrences),
                    possible_match_occurrences=MappingProxyType(
                        possible_match_occurrences
                    ),
                )
            )
    return tuple(cases)


def assign_author_mapping(
    mapping: Mapping[str, object],
    *,
    name: str,
    slug: str,
    create_new: bool,
) -> dict[str, list[str]]:
    """Apply one explicit human identity decision to author mapping data."""
    canonical, reverse = validate_author_mappings(mapping)
    exact_name = name.strip()
    if not exact_name:
        raise AuthorMappingError("author name must not be empty")
    exact_slug = slug.strip()
    if not exact_slug:
        raise AuthorMappingError("author slug must not be empty")
    try:
        safe_component(exact_slug)
    except ValueError as error:
        raise AuthorMappingError(str(error)) from error

    if exact_name in reverse:
        raise AuthorMappingError(
            f"author name {exact_name!r} is already assigned to {reverse[exact_name]!r}"
        )
    if create_new:
        if exact_slug in canonical:
            raise AuthorMappingError(
                f"author identity {exact_slug!r} already exists; map to it explicitly instead"
            )
    elif exact_slug not in canonical:
        raise AuthorMappingError(f"unknown author identity {exact_slug!r}")

    result = {known_slug: list(names) for known_slug, names in canonical.items()}
    if create_new:
        result[exact_slug] = [exact_name]
    else:
        result[exact_slug].append(exact_name)
    validate_author_mappings(result)
    return result


def _format_review_doi(doi: str, *, hyperlinks: bool) -> str:
    """Format one DOI, optionally as an OSC 8 terminal hyperlink."""
    if not hyperlinks:
        return doi
    url = f"https://doi.org/{quote(doi, safe='/')}"
    return f"\x1b]8;;{url}\x1b\\{doi}\x1b]8;;\x1b\\"


def format_author_review_case(
    case: AuthorReviewCase,
    *,
    index: int | None = None,
    total: int | None = None,
    hyperlinks: bool = False,
) -> str:
    """Render evidence for one explicit human author identity decision."""
    lines: list[str] = []
    if index is not None and total is not None:
        lines.append(f"Author review {index}/{total}")
    lines.append(f"Source name: {case.name}")
    lines.append(f"Proposed slug: {case.slug}")
    if case.reasons:
        lines.append(f"Reason: {'; '.join(case.reasons)}")
    if case.possible_matches:
        lines.append("Possible existing identities:")
        for number, (slug, names) in enumerate(case.possible_matches.items(), start=1):
            lines.append(f"  {number}. {slug}: {', '.join(names)}")
            candidate_occurrences = case.possible_match_occurrences.get(slug, ())
            if candidate_occurrences:
                lines.append("     Canonical publications:")
                for occurrence in candidate_occurrences:
                    identifier = (
                        f"DOI {_format_review_doi(occurrence.doi, hyperlinks=hyperlinks)}"
                        if occurrence.doi
                        else occurrence.publication_id
                    )
                    lines.append(
                        f"       - {identifier}: {occurrence.title or '(untitled)'}"
                    )
                    if occurrence.orcid:
                        lines.append(f"         ORCID: {occurrence.orcid}")
                    for affiliation in occurrence.affiliations:
                        lines.append(f"         Affiliation: {affiliation}")
            else:
                lines.append("     Canonical publications: none found")
    else:
        lines.append("Possible existing identities: none")
    lines.append("Unresolved-name publications:")
    for occurrence in case.occurrences:
        identifier = f"DOI {occurrence.doi}" if occurrence.doi else occurrence.publication_id
        lines.append(f"  - {identifier}: {occurrence.title or '(untitled)'}")
        if occurrence.orcid:
            lines.append(f"    ORCID: {occurrence.orcid}")
        for affiliation in occurrence.affiliations:
            lines.append(f"    Affiliation: {affiliation}")
    return "\n".join(lines)


def apply_safe_author_mappings(
    mapping: Mapping[str, object],
    plan: AuthorMappingPlan,
) -> dict[str, list[str]]:
    """Return updated mapping data containing only safe proposals from ``plan``."""
    canonical, _ = validate_author_mappings(mapping)
    result = {slug: list(names) for slug, names in canonical.items()}
    for slug, names in plan.safe.items():
        if slug in result:
            raise AuthorMappingError(f"safe proposal unexpectedly collides with {slug!r}")
        result[slug] = list(names)
    return result


def author_mapping_plan_data(plan: AuthorMappingPlan) -> dict[str, object]:
    """Return a JSON-serializable representation of an author mapping plan."""
    return {
        "known_names": plan.known_names,
        "unknown_names": plan.unknown_names,
        "safe": {slug: list(names) for slug, names in plan.safe.items()},
        "review": [
            {
                "slug": item.slug,
                "names": list(item.names),
                "occurrences": item.occurrences,
                "reason": "; ".join(item.reasons),
                "possible_matches": [
                    {"slug": slug, "names": list(names)}
                    for slug, names in item.possible_matches.items()
                ],
            }
            for item in plan.review
        ],
    }


def format_author_mapping_plan(
    plan: AuthorMappingPlan,
    *,
    applied: int = 0,
    dry_run: bool = False,
) -> str:
    """Render a concise actionable report without hiding ambiguous identities."""
    lines: list[str] = []
    if applied:
        lines.append(
            f"{'Would apply' if dry_run else 'Applied'} {applied} safe author mapping(s)."
        )
    lines.append(f"Known name variants: {plan.known_names}")
    lines.append(f"Unknown author names: {plan.unknown_names}")
    if plan.safe:
        lines.append("Safe proposals:")
        lines.extend(f"  + {slug}: {', '.join(names)}" for slug, names in plan.safe.items())
    if plan.review:
        lines.append("Manual review required:")
        for item in plan.review:
            lines.append(f"  ? {item.slug}: {', '.join(item.names)}")
            lines.append(f"    Reason: {'; '.join(item.reasons)}")
            for slug, names in item.possible_matches.items():
                lines.append(f"    Possible match: {slug} ({', '.join(names)})")
    if not plan.unknown_names:
        lines.append("All publication authors are mapped.")
    elif plan.safe:
        lines.append("Run again with --apply-safe to add the unambiguous proposals.")
    return "\n".join(lines)
