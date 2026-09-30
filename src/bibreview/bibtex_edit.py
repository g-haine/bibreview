"""Conservative parsing, canonicalization, and field editing for BibTeX."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
import re
from typing import Mapping


class BibtexEditError(ValueError):
    """Raised when a tracked BibTeX file cannot be edited safely."""


@dataclass(frozen=True)
class BibtexField:
    """One parsed top-level BibTeX field value span."""

    name: str
    source_name: str
    value_start: int
    value_end: int


@dataclass(frozen=True)
class BibtexEntry:
    """Parsed top-level structure needed for safe BibTeX transformations."""

    entry_type: str
    source_entry_type: str
    key: str
    close_index: int
    fields: tuple[BibtexField, ...]


_ENTRY = re.compile(r"@\s*([A-Za-z]+)\s*([({])", re.MULTILINE)
_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_-]*")
_CANONICAL_DROP_FIELDS = frozenset({"abstract", "month", "url", "pdf"})
_CANONICAL_PROTECTED_FIELDS = frozenset({"title", "booktitle"})


def _escaped(text: str, index: int) -> bool:
    count = 0
    index -= 1
    while index >= 0 and text[index] == "\\":
        count += 1
        index -= 1
    return count % 2 == 1


def _skip_space_and_comments(text: str, index: int, limit: int) -> int:
    while index < limit:
        if text[index].isspace() or text[index] == ",":
            index += 1
            continue
        if text[index] == "%":
            newline = text.find("\n", index, limit)
            return limit if newline < 0 else _skip_space_and_comments(
                text, newline + 1, limit
            )
        break
    return index


def _balanced_end(text: str, start: int, opener: str, closer: str) -> int:
    depth = 0
    quoted = False
    index = start
    while index < len(text):
        char = text[index]
        if char == '"' and not _escaped(text, index):
            quoted = not quoted
        if not quoted:
            if char == opener and not _escaped(text, index):
                depth += 1
            elif char == closer and not _escaped(text, index):
                depth -= 1
                if depth == 0:
                    return index + 1
        index += 1
    raise BibtexEditError("unterminated BibTeX entry or braced value")


def _value_end(text: str, start: int, limit: int) -> int:
    if start >= limit:
        raise BibtexEditError("missing BibTeX field value")
    char = text[start]
    if char == "{":
        end = _balanced_end(text, start, "{", "}")
        if end > limit:
            raise BibtexEditError("BibTeX field value crosses entry boundary")
        return end
    if char == '"':
        index = start + 1
        while index < limit:
            if text[index] == '"' and not _escaped(text, index):
                return index + 1
            index += 1
        raise BibtexEditError("unterminated quoted BibTeX field value")

    index = start
    while index < limit and text[index] not in ",\n\r":
        index += 1
    if index == start:
        raise BibtexEditError("missing BibTeX field value")
    return index


def parse_bibtex_entry(text: str) -> BibtexEntry:
    """Parse one tracked BibTeX entry without interpreting field contents."""
    if not isinstance(text, str) or not text.strip():
        raise BibtexEditError("BibTeX content is empty")
    match = _ENTRY.search(text)
    if match is None:
        raise BibtexEditError("BibTeX entry header is missing")

    opener = match.group(2)
    closer = "}" if opener == "{" else ")"
    open_index = match.end() - 1
    close_index = _balanced_end(text, open_index, opener, closer) - 1
    if text[close_index + 1 :].strip():
        raise BibtexEditError("tracked BibTeX file must contain exactly one entry")

    index = open_index + 1
    key_end = text.find(",", index, close_index)
    if key_end < 0:
        raise BibtexEditError("BibTeX entry key is not followed by fields")
    key = text[index:key_end].strip()
    if not key:
        raise BibtexEditError("BibTeX entry key is empty")
    index = key_end + 1

    fields: list[BibtexField] = []
    seen: set[str] = set()
    while True:
        index = _skip_space_and_comments(text, index, close_index)
        if index >= close_index:
            break
        name_match = _NAME.match(text, index)
        if name_match is None:
            raise BibtexEditError(
                f"cannot parse BibTeX field near offset {index}"
            )
        source_name = name_match.group(0)
        name = source_name.casefold()
        if name in seen:
            raise BibtexEditError(f"duplicate BibTeX field: {name}")
        seen.add(name)
        index = name_match.end()
        while index < close_index and text[index].isspace():
            index += 1
        if index >= close_index or text[index] != "=":
            raise BibtexEditError(f"BibTeX field {name} is missing '='")
        index += 1
        while index < close_index and text[index].isspace():
            index += 1
        value_start = index
        value_end = _value_end(text, value_start, close_index)
        fields.append(
            BibtexField(
                name=name,
                source_name=source_name,
                value_start=value_start,
                value_end=value_end,
            )
        )
        index = value_end

    return BibtexEntry(
        entry_type=match.group(1).casefold(),
        source_entry_type=match.group(1),
        key=key,
        close_index=close_index,
        fields=tuple(fields),
    )


def bibtex_field_names(text: str) -> frozenset[str]:
    """Return parsed top-level field names."""
    return frozenset(field.name for field in parse_bibtex_entry(text).fields)


def _unwrap_value(value: str) -> str:
    stripped = value.strip()
    if stripped.startswith("{") and _balanced_end(stripped, 0, "{", "}") == len(stripped):
        return stripped[1:-1]
    if (
        len(stripped) >= 2
        and stripped.startswith('"')
        and stripped.endswith('"')
        and not _escaped(stripped, len(stripped) - 1)
    ):
        return stripped[1:-1]
    return stripped


def _whole_braced(value: str) -> bool:
    return bool(
        value.startswith("{")
        and _balanced_end(value, 0, "{", "}") == len(value)
    )


def _canonical_value(name: str, value: str) -> str:
    content = _unwrap_value(value).replace("&amp;", r"\&")
    if name == "pages":
        content = content.replace("–", "--")
    if name in _CANONICAL_PROTECTED_FIELDS:
        protected = content if _whole_braced(content) else "{" + content + "}"
        return "{" + protected + "}"
    return "{" + content + "}"


def canonicalize_bibtex(
    text: str,
    *,
    drop_fields: Iterable[str] = _CANONICAL_DROP_FIELDS,
) -> str:
    """Render one BibTeX entry in BibReview's stable tracked-file format.

    The canonical tracked form uses two-space indentation, no spaces around
    equals signs, whole-title brace protection for title and booktitle, and
    omits non-citation payload fields that BibReview stores elsewhere.
    """

    entry = parse_bibtex_entry(text)
    omitted = {str(name).casefold() for name in drop_fields}
    rendered_fields: list[str] = []
    for field in entry.fields:
        if field.name in omitted:
            continue
        value = text[field.value_start : field.value_end]
        rendered_fields.append(
            f"  {field.source_name}={_canonical_value(field.name, value)}"
        )
    if not rendered_fields:
        raise BibtexEditError("BibTeX entry has no canonical fields")

    return (
        f"@{entry.source_entry_type}{{{entry.key},\n"
        + ",\n".join(rendered_fields)
        + "\n}\n"
    )


def update_bibtex_fields(text: str, updates: Mapping[str, str]) -> str:
    """Replace or append top-level fields while preserving unrelated content."""
    if not updates:
        return text
    normalized: dict[str, str] = {}
    for raw_name, value in updates.items():
        name = str(raw_name).casefold()
        if _NAME.fullmatch(name) is None:
            raise BibtexEditError(f"invalid BibTeX field name: {raw_name}")
        if not isinstance(value, str) or not value:
            raise BibtexEditError(f"BibTeX field {name} requires a non-empty value")
        normalized[name] = value

    entry = parse_bibtex_entry(text)
    fields = {field.name: field for field in entry.fields}
    replacements: list[tuple[int, int, str]] = []
    missing: list[tuple[str, str]] = []
    for name, value in normalized.items():
        rendered = "{" + value + "}"
        field = fields.get(name)
        if field is None:
            missing.append((name, rendered))
        else:
            replacements.append((field.value_start, field.value_end, rendered))

    updated = text
    for start, end, rendered in sorted(replacements, reverse=True):
        updated = updated[:start] + rendered + updated[end:]

    if missing:
        reparsed = parse_bibtex_entry(updated)
        close_index = reparsed.close_index
        before = updated[:close_index].rstrip()
        trailing = updated[len(before):close_index]
        separator = "\n" if before.endswith(",") else ",\n"
        additions = ",\n".join(
            f"  {name}={rendered}" for name, rendered in missing
        )
        updated = (
            before
            + separator
            + additions
            + ("\n" if not trailing else trailing)
            + updated[close_index:]
        )

    return updated if updated.endswith("\n") else updated + "\n"
