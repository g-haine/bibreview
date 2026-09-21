"""Core BibReview publication model, independent of providers and site rendering."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import date
from types import MappingProxyType
from typing import Mapping

from .identity import normalize_identifiers, validate_publication_id


def _validate_contributor(
    role: str,
    given: str | None,
    family: str | None,
    literal: str | None,
    source_fields: Mapping[str, object],
) -> MappingProxyType:
    """Validate one source-provided bibliographic contributor."""
    if given is not None and not isinstance(given, str):
        raise ValueError(f"{role} given name must be a string or None")
    if family is not None and not isinstance(family, str):
        raise ValueError(f"{role} family name must be a string or None")
    if literal is not None and not isinstance(literal, str):
        raise ValueError(f"{role} literal name must be a string or None")
    if not (
        (given or "").strip()
        or (family or "").strip()
        or (literal or "").strip()
    ):
        raise ValueError(f"{role} must contain at least one non-empty name component")
    if not isinstance(source_fields, Mapping):
        raise ValueError(f"{role} source_fields must be a mapping")
    extras = deepcopy(dict(source_fields))
    if any(not isinstance(key, str) or not key for key in extras):
        raise ValueError(f"{role} source_fields keys must be non-empty strings")
    if {"given", "family", "literal"} & extras.keys():
        raise ValueError(
            f"{role} source_fields must not duplicate canonical name fields"
        )
    return MappingProxyType(extras)


@dataclass(frozen=True)
class Author:
    """A source-provided author name, not an inferred canonical person identity."""

    given: str | None = None
    family: str | None = None
    literal: str | None = None
    source_fields: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "source_fields",
            _validate_contributor(
                "author",
                self.given,
                self.family,
                self.literal,
                self.source_fields,
            ),
        )


@dataclass(frozen=True)
class Editor:
    """A source-provided editor name, distinct from authorship."""

    given: str | None = None
    family: str | None = None
    literal: str | None = None
    source_fields: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "source_fields",
            _validate_contributor(
                "editor",
                self.given,
                self.family,
                self.literal,
                self.source_fields,
            ),
        )


@dataclass(frozen=True)
class Reference:
    """A bibliographic reference that may or may not carry a DOI."""

    identifiers: Mapping[str, str] = field(default_factory=dict)
    citation: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "identifiers",
            MappingProxyType(normalize_identifiers(self.identifiers)),
        )
        if not isinstance(self.citation, str):
            raise ValueError("reference citation must be a string")


@dataclass(frozen=True)
class Publication:
    """Canonical in-memory publication representation for BibReview."""

    id: str
    identifiers: Mapping[str, str] = field(default_factory=dict)
    type: str = ""
    title: str = ""
    authors: tuple[Author, ...] = ()
    editors: tuple[Editor, ...] = ()
    abstract: str = ""
    container_title: str = ""
    publication_year: str = ""
    volume: str = ""
    issue: str = ""
    pages: str = ""
    publisher: str = ""
    event: str = ""
    keywords: tuple[str, ...] = ()
    created_date: date | None = None
    permalink: str = ""
    references: tuple[Reference, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", validate_publication_id(self.id))
        object.__setattr__(
            self,
            "identifiers",
            MappingProxyType(normalize_identifiers(self.identifiers)),
        )
        object.__setattr__(self, "authors", tuple(self.authors))
        object.__setattr__(self, "editors", tuple(self.editors))
        object.__setattr__(self, "keywords", tuple(self.keywords))
        object.__setattr__(self, "references", tuple(self.references))
        for name in (
            "type", "title", "abstract", "container_title", "publication_year",
            "volume", "issue", "pages", "publisher", "event", "permalink",
        ):
            if not isinstance(getattr(self, name), str):
                raise ValueError(f"publication {name} must be a string")
        if self.created_date is not None and not isinstance(self.created_date, date):
            raise ValueError("publication created_date must be a date or None")
        if any(not isinstance(author, Author) for author in self.authors):
            raise ValueError("publication authors must contain Author objects")
        if any(not isinstance(editor, Editor) for editor in self.editors):
            raise ValueError("publication editors must contain Editor objects")
        if not self.authors and not self.editors:
            raise ValueError("publication must contain at least one author or editor")
        if any(not isinstance(keyword, str) for keyword in self.keywords):
            raise ValueError("publication keywords must contain strings")
        if any(not isinstance(reference, Reference) for reference in self.references):
            raise ValueError("publication references must contain Reference objects")

    @property
    def doi(self) -> str | None:
        """Return the normalized DOI when one exists."""
        return self.identifiers.get("doi")
