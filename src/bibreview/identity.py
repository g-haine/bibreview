"""Stable publication identity and strong external-identifier matching."""

from __future__ import annotations

from collections.abc import Mapping
import re
import uuid

_DOI_PREFIX = re.compile(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", re.IGNORECASE)

# External identifiers are not automatically strong merely because they are
# present in Publication.identifiers. M3 enables exact DOI matching only.
# Future identifiers (for example arXiv or PMID) must be added deliberately
# once their normalization and identity semantics are defined.
STRONG_IDENTIFIER_NAMES = frozenset({"doi"})


class IdentityError(ValueError):
    """Raised when an internal or external identifier is invalid."""


def normalize_doi(value: str) -> str:
    """Return the canonical lower-case DOI value without URL/prefix syntax."""
    if not isinstance(value, str):
        raise IdentityError("DOI must be a string")
    normalized = _DOI_PREFIX.sub("", value.strip()).strip().lower()
    if not normalized or not normalized.startswith("10.") or "/" not in normalized:
        raise IdentityError(f"invalid DOI: {value!r}")
    if any(character.isspace() for character in normalized):
        raise IdentityError(f"invalid DOI containing whitespace: {value!r}")
    return normalized


def new_publication_id() -> str:
    """Generate a new opaque persistent BibReview publication identifier."""
    return str(uuid.uuid4())


def validate_publication_id(value: str) -> str:
    """Validate and canonicalize a persisted publication UUID."""
    if not isinstance(value, str):
        raise IdentityError("publication id must be a string UUID")
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError) as error:
        raise IdentityError(f"invalid publication id: {value!r}") from error
    canonical = str(parsed)
    if value.lower() != canonical:
        raise IdentityError(f"publication id must use canonical UUID form: {canonical}")
    return canonical


def normalize_identifiers(values: Mapping[str, str] | None) -> dict[str, str]:
    """Normalize supported identifiers while preserving extensibility."""
    result: dict[str, str] = {}
    for name, value in (values or {}).items():
        if not isinstance(name, str) or not name.strip():
            raise IdentityError("identifier names must be non-empty strings")
        if not isinstance(value, str) or not value.strip():
            raise IdentityError(f"identifier {name!r} must be a non-empty string")
        key = name.strip().lower()
        result[key] = normalize_doi(value) if key == "doi" else value.strip()
    return result


def strong_identifiers(values: Mapping[str, str] | None) -> tuple[tuple[str, str], ...]:
    """Return normalized identifiers currently approved for exact identity matching."""
    normalized = normalize_identifiers(values)
    return tuple(
        (name, normalized[name])
        for name in sorted(STRONG_IDENTIFIER_NAMES & normalized.keys())
    )


def shared_strong_identifier(
    left: Mapping[str, str] | None, right: Mapping[str, str] | None
) -> tuple[str, str] | None:
    """Return an exact shared approved strong identifier, or ``None``.

    M3 deliberately performs no fuzzy title/author merge and does not treat
    arbitrary metadata identifiers such as ISBN as automatic identity keys.
    """
    right_values = dict(strong_identifiers(right))
    for name, value in strong_identifiers(left):
        if right_values.get(name) == value:
            return name, value
    return None
