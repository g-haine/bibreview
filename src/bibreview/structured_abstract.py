"""Deterministic normalization for structured abstract markup.

This module deliberately stays separate from the ordinary text-cleaning path.
It only transforms structured payloads when their scholarly content can be
preserved without inference. Unsafe or unsupported markup is returned unchanged
with an explicit non-deterministic result.
"""

from __future__ import annotations

from dataclasses import dataclass
import re

from bs4 import BeautifulSoup, Comment, NavigableString


_TAG_RE = re.compile(
    r"<\s*(/?)\s*([A-Za-z][A-Za-z0-9_.:-]*)\b([^<>]*?)(/?)\s*>",
    re.IGNORECASE,
)
_STRUCTURED_TAG_RE = re.compile(
    r"<\s*/?\s*[A-Za-z][A-Za-z0-9_.:-]*(?:\s[^<>]*?)?/?>",
    re.IGNORECASE,
)
_ESCAPED_TAG_RE = re.compile(
    r"&lt;\s*/?\s*[A-Za-z][A-Za-z0-9_.:-]*\b",
    re.IGNORECASE,
)
_TEX_PAREN_RE = re.compile(
    r"\\+\(\s*(?P<body>.*?)\s*\\+\)",
    re.DOTALL,
)
_TEX_BRACKET_RE = re.compile(
    r"\\+\[.*?\\+\]",
    re.DOTALL,
)

_EMBEDDED_GRAPHIC_NAMES = frozenset(
    {"inline-graphic", "graphic", "img", "image"}
)
_SCRIPT_MARKUP_NAMES = frozenset({"inf", "sub", "sup"})
_BLOCK_WRAPPERS = frozenset({"p", "div"})
_INLINE_WRAPPERS = frozenset(
    {
        "span",
        "italic",
        "bold",
        "styled-content",
        "em",
        "i",
        "b",
        "strong",
    }
)
_VOID_HTML_TAGS = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)


@dataclass(frozen=True)
class StructuredAbstractNormalization:
    """Result of one conservative structured-abstract normalization attempt."""

    normalized: str
    deterministic: bool
    changed: bool
    reason: str


def _local_name(name: str | None) -> str:
    """Return a case-folded local tag name without an optional namespace."""
    if not name:
        return ""
    return name.casefold().rsplit(":", 1)[-1]


def _structured_tags_unbalanced(value: str) -> bool:
    """Return whether explicit structured tags are obviously unbalanced."""
    stack: list[str] = []
    saw_tag = False
    for match in _TAG_RE.finditer(value):
        saw_tag = True
        closing, raw_name, _attributes, self_closing = match.groups()
        name = raw_name.casefold()
        if self_closing or _local_name(name) in _VOID_HTML_TAGS:
            continue
        if closing:
            if not stack or stack[-1] != name:
                return True
            stack.pop()
        else:
            stack.append(name)
    return saw_tag and bool(stack)


def _canonical_tex_body(value: str) -> str | None:
    """Return a non-empty inline TeX body with outer delimiters removed."""
    text = value.strip()
    if not text:
        return None

    if text.startswith("$$") or text.endswith("$$"):
        return None
    if _TEX_BRACKET_RE.fullmatch(text):
        return None

    if text.startswith("$") and text.endswith("$") and len(text) >= 2:
        text = text[1:-1].strip()
        return text or None

    match = _TEX_PAREN_RE.fullmatch(text)
    if match is not None:
        text = match.group("body").strip()
        return text or None

    return text


def _trusted_tex_payload(formula) -> str | None:
    """Return the preferred trustworthy TeX representation for one formula."""
    for tag in formula.find_all(True):
        if _local_name(tag.name) != "annotation":
            continue
        encoding = str(tag.attrs.get("encoding", "")).strip().casefold()
        if encoding != "application/x-tex":
            continue
        body = _canonical_tex_body(tag.get_text())
        if body:
            return body

    for tag in formula.find_all(True):
        if _local_name(tag.name) != "tex-math":
            continue
        notation = str(tag.attrs.get("notation", "")).strip().casefold()
        if notation != "latex":
            continue
        body = _canonical_tex_body(tag.get_text())
        if body:
            return body

    return None


def _refused(value: str, reason: str) -> StructuredAbstractNormalization:
    """Return a non-mutating refusal result."""
    return StructuredAbstractNormalization(
        normalized=value,
        deterministic=False,
        changed=False,
        reason=reason,
    )


def normalize_structured_abstract(value: str) -> StructuredAbstractNormalization:
    """Normalize structured abstract markup only when conversion is lossless.

    Clean plain text and ordinary TeX are returned unchanged. Embedded graphics,
    subscript/superscript markup, escaped structured payloads, malformed markup,
    formulas without trustworthy textual representations, and unsupported tags
    are refused without modifying the input.
    """
    if not isinstance(value, str):
        raise TypeError("abstract value must be a string")

    if _ESCAPED_TAG_RE.search(value):
        return _refused(value, "escaped-markup")
    if _structured_tags_unbalanced(value):
        return _refused(value, "unbalanced-markup")

    has_structured_content = (
        _STRUCTURED_TAG_RE.search(value) is not None or "<!--" in value
    )
    if not has_structured_content:
        return StructuredAbstractNormalization(
            normalized=value,
            deterministic=True,
            changed=False,
            reason="already-clean",
        )

    soup = BeautifulSoup(value, "html.parser")
    tags = tuple(soup.find_all(True))

    for tag in tags:
        local = _local_name(tag.name)
        if local in _EMBEDDED_GRAPHIC_NAMES:
            return _refused(value, "embedded-graphic")
        if local in _SCRIPT_MARKUP_NAMES:
            return _refused(value, "script-markup")

    formulas = tuple(
        tag for tag in soup.find_all(True)
        if _local_name(tag.name) == "inline-formula"
    )
    replacements: list[tuple[object, str]] = []
    for formula in formulas:
        payload = _trusted_tex_payload(formula)
        if payload is None:
            return _refused(value, "formula-without-trusted-text")
        replacements.append((formula, payload))

    for formula, payload in replacements:
        formula.replace_with(NavigableString(f"\\({payload}\\)"))

    for comment in tuple(
        text for text in soup.find_all(string=True)
        if isinstance(text, Comment)
    ):
        comment.extract()

    for tag in tuple(soup.find_all(True)):
        local = _local_name(tag.name)
        if local in _BLOCK_WRAPPERS:
            tag.insert_before(NavigableString(" "))
            tag.insert_after(NavigableString(" "))
            tag.unwrap()
        elif local in _INLINE_WRAPPERS:
            tag.unwrap()
        else:
            return _refused(value, "unsupported-markup")

    normalized = re.sub(r"\s+", " ", soup.get_text()).strip()
    return StructuredAbstractNormalization(
        normalized=normalized,
        deterministic=True,
        changed=normalized != value,
        reason="normalized" if normalized != value else "already-clean",
    )
