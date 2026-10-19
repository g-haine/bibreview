"""Conservative contributor hygiene signals shared by collection and migration."""

from __future__ import annotations

import re
from typing import Any, Mapping

from unidecode import unidecode

from .model import Author, Editor, Publication


_INSTITUTION_LIKE_RE = re.compile(
    r"\b(?:department|university|universite|institute|institution|laboratory|"
    r"laboratoire|centre|center|academy|faculty|school|hospital|research\s+"
    r"(?:centre|center)|national\s+laboratory)\b",
    re.IGNORECASE,
)


def contributor_display_name(contributor: Author | Editor) -> str:
    """Return the exact source-visible contributor name."""
    if contributor.literal:
        return contributor.literal.strip()
    return " ".join(
        part.strip()
        for part in (contributor.given or "", contributor.family or "")
        if part.strip()
    )


def institution_like_contributor_name(value: str) -> bool:
    """Return whether a contributor name looks institution/affiliation-like."""
    normalized = unidecode(str(value))
    if re.match(r"^\s*\d+\s+", normalized):
        return True
    return _INSTITUTION_LIKE_RE.search(normalized) is not None


def provider_contributor_name(item: Mapping[str, Any]) -> str:
    """Return one provider contributor display name without inference."""
    literal = str(item.get("name") or "").strip()
    if literal:
        return literal
    return " ".join(
        str(item.get(field) or "").strip()
        for field in ("given", "family")
        if str(item.get(field) or "").strip()
    )


def provider_suspicious_contributors(
    message: Mapping[str, Any],
) -> tuple[tuple[str, int, str], ...]:
    """Return suspicious mixed person/institution provider contributors.

    Purely institutional lists are not rejected because collective authorship can
    be legitimate. A review signal is raised only when institution-like entries
    are mixed with ordinary person-like contributors in the same role.
    """
    findings: list[tuple[str, int, str]] = []
    for role in ("author", "editor"):
        raw = message.get(role)
        if not isinstance(raw, list):
            continue
        names: list[tuple[int, str]] = []
        for index, item in enumerate(raw):
            if not isinstance(item, Mapping):
                continue
            name = provider_contributor_name(item)
            if name:
                names.append((index, name))
        if not names:
            continue
        suspicious = [
            (index, name)
            for index, name in names
            if institution_like_contributor_name(name)
        ]
        ordinary = [
            (index, name)
            for index, name in names
            if not institution_like_contributor_name(name)
        ]
        if suspicious and ordinary:
            findings.extend((role, index, name) for index, name in suspicious)
    return tuple(findings)


def publication_suspicious_contributors(
    publication: Publication,
) -> tuple[tuple[str, int, str, Mapping[str, object]], ...]:
    """Return institution-like canonical contributors requiring human review."""
    result: list[tuple[str, int, str, Mapping[str, object]]] = []
    for role, contributors in (
        ("author", publication.authors),
        ("editor", publication.editors),
    ):
        for index, contributor in enumerate(contributors):
            name = contributor_display_name(contributor)
            if name and institution_like_contributor_name(name):
                result.append((role, index, name, contributor.source_fields))
    return tuple(result)
