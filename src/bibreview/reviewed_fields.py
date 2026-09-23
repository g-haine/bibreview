"""Shared reviewed field-application helpers for staged metadata corrections."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
import re
from typing import Mapping

from .model import Author, Editor, Publication
from .pipeline.audit import AuditValue
from .project import ProjectStateError


def _display_contributor(contributor: Author | Editor) -> str:
    if contributor.literal:
        return contributor.literal
    return " ".join(
        value for value in (contributor.given, contributor.family) if value
    ).strip()


def _replace_contributors(
    existing: tuple[Author, ...] | tuple[Editor, ...],
    names: tuple[str, ...],
    contributor_type: type[Author] | type[Editor],
) -> tuple[Author, ...] | tuple[Editor, ...]:
    """Preserve exact existing contributors and never infer name components."""
    used: set[int] = set()
    result: list[Author | Editor | None] = [None] * len(names)

    for position, name in enumerate(names):
        for index, contributor in enumerate(existing):
            if index in used:
                continue
            if _display_contributor(contributor) == name:
                used.add(index)
                result[position] = contributor
                break

    remaining = [
        (index, contributor)
        for index, contributor in enumerate(existing)
        if index not in used
    ]
    remaining_index = 0
    for position, name in enumerate(names):
        if result[position] is not None:
            continue
        source_fields: Mapping[str, object] = {}
        if remaining_index < len(remaining):
            _, previous = remaining[remaining_index]
            remaining_index += 1
            source_fields = previous.source_fields
        result[position] = contributor_type(
            literal=name,
            source_fields=source_fields,
        )

    return tuple(item for item in result if item is not None)


def apply_reviewed_field(
    publication: Publication,
    field: str,
    value: AuditValue,
) -> Publication:
    """Apply one reviewed field value without inventing metadata."""
    if field == "authors":
        if not isinstance(value, tuple):
            raise ProjectStateError("authors resolution must be a tuple")
        return replace(
            publication,
            authors=_replace_contributors(publication.authors, value, Author),
        )
    if field == "editors":
        if not isinstance(value, tuple):
            raise ProjectStateError("editors resolution must be a tuple")
        return replace(
            publication,
            editors=_replace_contributors(publication.editors, value, Editor),
        )
    if field == "keywords":
        if not isinstance(value, tuple):
            raise ProjectStateError("keywords resolution must be a tuple")
        return replace(publication, keywords=value)
    if field == "created_date":
        if not isinstance(value, str):
            raise ProjectStateError("created_date resolution must be a string")
        try:
            parsed = date.fromisoformat(value) if value else None
        except ValueError as error:
            raise ProjectStateError(
                f"invalid resolved created_date: {value!r}"
            ) from error
        return replace(publication, created_date=parsed)

    scalar_fields = {
        "type",
        "title",
        "abstract",
        "container_title",
        "publication_year",
        "volume",
        "issue",
        "pages",
        "publisher",
        "event",
    }
    if field not in scalar_fields:
        raise ProjectStateError(f"unsupported reviewed field: {field}")
    if not isinstance(value, str):
        raise ProjectStateError(f"{field} resolution must be a string")
    return replace(publication, **{field: value})


def bibtex_field_for(
    field: str,
    publication: Publication,
    existing_fields: frozenset[str],
) -> str | None:
    """Map one reviewed canonical field to a tracked BibTeX field when safe."""
    direct = {
        "title": "title",
        "authors": "author",
        "editors": "editor",
        "publication_year": "year",
        "volume": "volume",
        "pages": "pages",
        "publisher": "publisher",
    }
    if field in direct:
        return direct[field]
    if field == "issue":
        return "issue" if "issue" in existing_fields and "number" not in existing_fields else "number"
    if field == "container_title":
        if "journal" in existing_fields:
            return "journal"
        if "booktitle" in existing_fields:
            return "booktitle"
        if publication.type == "journal-article":
            return "journal"
        if publication.type in {"proceedings-article", "book-chapter"}:
            return "booktitle"
        if "series" in existing_fields:
            return "series"
        return None
    if field == "abstract":
        return "abstract" if "abstract" in existing_fields else None
    if field == "keywords":
        return "keywords" if "keywords" in existing_fields else None
    if field == "event":
        return "eventtitle" if "eventtitle" in existing_fields else None
    return None


def _bibtex_escape(value: str) -> str:
    value = re.sub(r"(?<!\\)&", r"\\&", value)
    return " ".join(value.splitlines()).strip()


def bibtex_value(field: str, value: AuditValue) -> str:
    """Render one reviewed canonical value for conservative BibTeX editing."""
    if isinstance(value, tuple):
        if field in {"authors", "editors"}:
            return " and ".join(_bibtex_escape(item) for item in value)
        return ", ".join(_bibtex_escape(item) for item in value)
    rendered = _bibtex_escape(value)
    return "{" + rendered + "}" if field == "title" else rendered
