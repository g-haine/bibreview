"""Typed identifier tokens for line-oriented BibReview project state."""

from __future__ import annotations

from collections.abc import Collection, Iterable
from dataclasses import dataclass
from pathlib import Path
import re

from .identity import IdentityError, normalize_doi, validate_publication_id


_IDENTIFIER_KIND = re.compile(r"^[A-Za-z][A-Za-z0-9._-]*$")
CANONICAL_ID_KIND = "id"
DOI_KIND = "doi"


class IdentifierStateError(ValueError):
    """Raised when a typed project-state identifier is invalid."""


def _normalize_kind(value: str) -> str:
    if not isinstance(value, str):
        raise IdentifierStateError("identifier kind must be a string")
    normalized = value.strip().lower()
    if not normalized:
        raise IdentifierStateError("identifier kind must not be empty")
    if not _IDENTIFIER_KIND.fullmatch(normalized):
        raise IdentifierStateError(
            "identifier kind must start with a letter and contain only "
            "letters, digits, '.', '_' or '-'"
        )
    return normalized


def _normalize_allowed_kinds(
    allowed_kinds: Collection[str] | None,
) -> frozenset[str] | None:
    if allowed_kinds is None:
        return None
    return frozenset(_normalize_kind(kind) for kind in allowed_kinds)


def _normalize_value(kind: str, value: str) -> str:
    if not isinstance(value, str):
        raise IdentifierStateError("identifier value must be a string")
    normalized = value.strip()
    if not normalized:
        raise IdentifierStateError("identifier value must not be empty")

    try:
        if kind == DOI_KIND:
            return normalize_doi(normalized)
        if kind == CANONICAL_ID_KIND:
            return validate_publication_id(normalized)
    except IdentityError as error:
        raise IdentifierStateError(str(error)) from error

    return normalized


@dataclass(frozen=True)
class IdentifierToken:
    """One canonical typed project-state identifier token."""

    kind: str
    value: str

    def __post_init__(self) -> None:
        kind = _normalize_kind(self.kind)
        value = _normalize_value(kind, self.value)
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "value", value)

    def __str__(self) -> str:
        return f"{self.kind}:{self.value}"


def _require_allowed_kind(
    token: IdentifierToken,
    allowed_kinds: Collection[str] | None,
) -> IdentifierToken:
    allowed = _normalize_allowed_kinds(allowed_kinds)
    if allowed is None or token.kind in allowed:
        return token
    expected = ", ".join(sorted(allowed)) if allowed else "<none>"
    raise IdentifierStateError(
        f"identifier kind {token.kind!r} is not allowed; expected: {expected}"
    )


def parse_identifier_token(
    value: str,
    *,
    allowed_kinds: Collection[str] | None = None,
    allow_legacy_doi: bool = False,
) -> IdentifierToken:
    """Parse one typed identifier token.

    When allow_legacy_doi is true, historical untyped DOI values, including
    DOI URLs/prefixes accepted by normalize_doi(), are accepted and
    canonicalized to a typed DOI token. Untyped non-DOI values are never
    guessed.
    """

    if not isinstance(value, str):
        raise IdentifierStateError("identifier token must be a string")
    raw = value.strip()
    if not raw:
        raise IdentifierStateError("identifier token must not be empty")

    if allow_legacy_doi:
        try:
            legacy_doi = normalize_doi(raw)
        except IdentityError:
            pass
        else:
            return _require_allowed_kind(
                IdentifierToken(DOI_KIND, legacy_doi),
                allowed_kinds,
            )

    if ":" not in raw:
        raise IdentifierStateError(
            "identifier token must use '<kind>:<value>' syntax"
        )

    kind, token_value = raw.split(":", 1)
    return _require_allowed_kind(
        IdentifierToken(kind, token_value),
        allowed_kinds,
    )


def read_identifier_tokens(
    path: Path | str,
    *,
    allowed_kinds: Collection[str] | None = None,
    allow_legacy_doi: bool = False,
) -> tuple[IdentifierToken, ...]:
    """Read a line-oriented identifier state file.

    Blank lines and full-line comments are ignored. Duplicate canonical tokens
    are collapsed while preserving first occurrence order, matching BibReview's
    historical DOI-state behavior.
    """

    source = Path(path)
    if not source.exists():
        return ()

    lines = source.read_text(encoding="utf-8").splitlines()
    result: list[IdentifierToken] = []
    seen: set[IdentifierToken] = set()

    for number, raw in enumerate(lines, 1):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        try:
            token = parse_identifier_token(
                stripped,
                allowed_kinds=allowed_kinds,
                allow_legacy_doi=allow_legacy_doi,
            )
        except IdentifierStateError as error:
            raise IdentifierStateError(
                f"{source}: line {number}: {error}"
            ) from error
        if token not in seen:
            seen.add(token)
            result.append(token)

    return tuple(result)


def identifier_tokens_bytes(tokens: Iterable[IdentifierToken]) -> bytes:
    """Serialize canonical identifier tokens as one UTF-8 line each."""

    lines: list[bytes] = []
    for token in tokens:
        if not isinstance(token, IdentifierToken):
            raise IdentifierStateError(
                "identifier state output must contain IdentifierToken objects"
            )
        lines.append(str(token).encode("utf-8") + b"\n")
    return b"".join(lines)
