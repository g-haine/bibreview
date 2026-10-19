"""Read-only hygiene analysis and explicit author-identity normalization."""

from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata
from typing import Any, Mapping

from unidecode import unidecode

from .contributor_hygiene import institution_like_contributor_name
from .pipeline.authors import AuthorMappingError, validate_author_mappings
from .text import safe_component, slugify


def _normalized_words(value: str) -> tuple[str, ...]:
    normalized = unicodedata.normalize("NFKD", value)
    normalized = "".join(
        char for char in normalized if not unicodedata.category(char).startswith(("M", "S"))
    )
    ascii_value = unidecode(normalized).casefold()
    return tuple(re.findall(r"[a-z0-9]+", ascii_value))


def _compact_name(value: str) -> str:
    return "".join(_normalized_words(value))


def _name_parts(value: str) -> tuple[tuple[str, ...], str]:
    words = _normalized_words(value)
    if len(words) < 2:
        return (), words[-1] if words else ""
    return words[:-1], words[-1]


def _initials_compatible(left: str, right: str) -> bool:
    left_given, left_family = _name_parts(left)
    right_given, right_family = _name_parts(right)
    if not left_family or left_family != right_family:
        return False
    if len(left_given) != len(right_given):
        return False

    def compatible(a: str, b: str) -> bool:
        if a == b:
            return True
        if len(a) == 1 and b.startswith(a):
            return True
        if len(b) == 1 and a.startswith(b):
            return True
        return False

    if not all(compatible(a, b) for a, b in zip(left_given, right_given)):
        return False
    return any(
        (len(a) == 1) != (len(b) == 1)
        for a, b in zip(left_given, right_given)
    )


@dataclass(frozen=True)
class AuthorHygieneFinding:
    """One author-identity hygiene finding requiring maintainer attention."""

    kind: str
    slugs: tuple[str, ...]
    names: tuple[str, ...]
    proposed_slug: str | None
    reason: str

    def data(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "slugs": list(self.slugs),
            "names": list(self.names),
            "proposed_slug": self.proposed_slug,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class AuthorHygieneReport:
    """Corpus-wide author identity hygiene report."""

    identities: int
    name_variants: int
    findings: tuple[AuthorHygieneFinding, ...]

    @property
    def weak_slugs(self) -> int:
        return sum(item.kind == "slug-can-be-strengthened" for item in self.findings)

    @property
    def encoding_duplicates(self) -> int:
        return sum(item.kind == "encoding-normalization-duplicate" for item in self.findings)

    @property
    def initial_full_candidates(self) -> int:
        return sum(item.kind == "initial-full-name-candidate" for item in self.findings)

    @property
    def institution_like(self) -> int:
        return sum(item.kind == "institution-like-author" for item in self.findings)

    def summary(self) -> str:
        return (
            "Author identity hygiene\n"
            f"  Identities                    : {self.identities}\n"
            f"  Name variants                 : {self.name_variants}\n"
            f"  Slugs to strengthen           : {self.weak_slugs}\n"
            f"  Encoding duplicate candidates : {self.encoding_duplicates}\n"
            f"  Initial/full-name candidates  : {self.initial_full_candidates}\n"
            f"  Institution-like authors      : {self.institution_like}"
        )

    def data(self) -> dict[str, Any]:
        return {
            "identities": self.identities,
            "name_variants": self.name_variants,
            "slugs_to_strengthen": self.weak_slugs,
            "encoding_duplicate_candidates": self.encoding_duplicates,
            "initial_full_name_candidates": self.initial_full_candidates,
            "institution_like_authors": self.institution_like,
            "findings": [item.data() for item in self.findings],
        }


def scan_author_identity_hygiene(
    mapping: Mapping[str, object],
) -> AuthorHygieneReport:
    """Detect weak slugs and probable duplicate existing author identities."""
    canonical, _ = validate_author_mappings(mapping)
    findings: list[AuthorHygieneFinding] = []

    for slug, names in canonical.items():
        if any(institution_like_contributor_name(name) for name in names):
            findings.append(
                AuthorHygieneFinding(
                    kind="institution-like-author",
                    slugs=(slug,),
                    names=names,
                    proposed_slug=None,
                    reason=(
                        "mapped author name looks like an institution or affiliation; "
                        "manual review is required because collective authors can be legitimate"
                    ),
                )
            )

        stronger = sorted(
            (
                name
                for name in names
                if len(_name_parts(name)[0]) > 0
                and _name_parts(name)[0][0]
                and len(_name_parts(name)[0][0]) > 1
            ),
            key=lambda value: (-len(_compact_name(value)), value.casefold()),
        )
        if not stronger:
            continue
        proposed = slugify(stronger[0])
        first_token = slug.split("-", 1)[0]
        if first_token and len(first_token) == 1 and proposed and proposed != slug:
            findings.append(
                AuthorHygieneFinding(
                    kind="slug-can-be-strengthened",
                    slugs=(slug,),
                    names=names,
                    proposed_slug=proposed,
                    reason="a reviewed full given name is already known for an initial-based slug",
                )
            )

    items = list(canonical.items())
    seen_pairs: set[tuple[str, str]] = set()
    for index, (left_slug, left_names) in enumerate(items):
        for right_slug, right_names in items[index + 1 :]:
            pair = tuple(sorted((left_slug, right_slug)))
            encoding_match = any(
                _compact_name(left) == _compact_name(right)
                for left in left_names
                for right in right_names
            )
            if encoding_match:
                seen_pairs.add(pair)
                findings.append(
                    AuthorHygieneFinding(
                        kind="encoding-normalization-duplicate",
                        slugs=pair,
                        names=tuple((*left_names, *right_names)),
                        proposed_slug=None,
                        reason="distinct identities collapse to the same normalized full name",
                    )
                )
                continue

            initial_match = any(
                _initials_compatible(left, right)
                for left in left_names
                for right in right_names
            )
            if initial_match and pair not in seen_pairs:
                findings.append(
                    AuthorHygieneFinding(
                        kind="initial-full-name-candidate",
                        slugs=pair,
                        names=tuple((*left_names, *right_names)),
                        proposed_slug=None,
                        reason="same surname with compatible initials/full given names",
                    )
                )

    findings.sort(key=lambda item: (item.kind, item.slugs, item.names))
    return AuthorHygieneReport(
        identities=len(canonical),
        name_variants=sum(len(names) for names in canonical.values()),
        findings=tuple(findings),
    )


def merge_author_identities(
    mapping: Mapping[str, object],
    *,
    source_slug: str,
    target_slug: str,
) -> dict[str, list[str]]:
    """Merge one explicitly reviewed existing author identity into another."""
    canonical, _ = validate_author_mappings(mapping)
    source = source_slug.strip()
    target = target_slug.strip()
    if source == target:
        raise AuthorMappingError("source and target author identities must differ")
    if source not in canonical:
        raise AuthorMappingError(f"unknown source author identity {source!r}")
    if target not in canonical:
        raise AuthorMappingError(f"unknown target author identity {target!r}")

    result = {slug: list(names) for slug, names in canonical.items()}
    for name in result[source]:
        if name not in result[target]:
            result[target].append(name)
    del result[source]
    validate_author_mappings(result)
    return result


def rename_author_identity(
    mapping: Mapping[str, object],
    *,
    source_slug: str,
    target_slug: str,
) -> dict[str, list[str]]:
    """Rename one explicitly reviewed author identity without changing its variants."""
    canonical, _ = validate_author_mappings(mapping)
    source = source_slug.strip()
    target = target_slug.strip()
    if source not in canonical:
        raise AuthorMappingError(f"unknown source author identity {source!r}")
    if target in canonical:
        raise AuthorMappingError(
            f"target author identity {target!r} already exists; use a merge instead"
        )
    try:
        safe_component(target)
    except ValueError as error:
        raise AuthorMappingError(str(error)) from error

    result: dict[str, list[str]] = {}
    for slug, names in canonical.items():
        result[target if slug == source else slug] = list(names)
    validate_author_mappings(result)
    return result
