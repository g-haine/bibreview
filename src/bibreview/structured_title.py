"""Conservative normalization for structured titles and reference citations.

The normalizers in this module are deliberately stricter than generic HTML
stripping. They decode textual entities iteratively, rescan the resulting
payload, preserve existing TeX, convert only the small MathML subset observed
in the real PHRAISE corpus, and refuse markup whose scholarly semantics would
otherwise have to be inferred.
"""

from __future__ import annotations

from dataclasses import dataclass
import html
import re

from bs4 import BeautifulSoup, Comment, NavigableString


_TAG_RE = re.compile(
    r"<\s*(/?)\s*([A-Za-z][A-Za-z0-9_.:-]*)\b([^<>]*?)(/?)\s*>",
    re.IGNORECASE,
)
_ENTITY_RE = re.compile(
    r"&(?:[A-Za-z][A-Za-z0-9]+|#\d+|#x[0-9A-Fa-f]+);"
)
_CONTROL_RE = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]")
_TEX_PAREN_RE = re.compile(
    r"\\+\(\s*(?P<body>.*?)\s*\\+\)",
    re.DOTALL,
)
_TEX_BRACKET_RE = re.compile(
    r"\\+\[.*?\\+\]",
    re.DOTALL,
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
_SCRIPT_MARKUP_NAMES = frozenset({"inf", "sub", "sup"})
_EMBEDDED_GRAPHIC_NAMES = frozenset(
    {"inline-graphic", "graphic", "img", "image"}
)
_FORMULA_WRAPPERS = frozenset({"inline-formula", "formula"})
_TEX_MARKUP_NAMES = frozenset({"tex-math", "tex"})

_TITLE_INLINE_WRAPPERS = frozenset(
    {
        "b",
        "bold",
        "em",
        "i",
        "italic",
        "scp",
        "small-caps",
        "strong",
        "styled-content",
    }
)
_CITATION_INLINE_WRAPPERS = (
    _TITLE_INLINE_WRAPPERS
    | frozenset({"suffix", "title", "tt"})
)
_CITATION_BLOCK_WRAPPERS = frozenset({"p"})

_MATHML_CONTAINER_NAMES = frozenset({"math", "mrow"})
_MATHML_TOKEN_NAMES = frozenset({"mi", "mn", "mo"})
_MATHML_SCRIPT_NAMES = frozenset({"msub", "msup"})

_MAX_ENTITY_DECODE_PASSES = 4

_SYMBOL_TEX = {
    "∞": r"\infty",
    "ϑ": r"\vartheta",
    "ℋ": r"\mathcal{H}",
    "ℒ": r"\mathcal{L}",
}


@dataclass(frozen=True)
class StructuredTitleNormalization:
    """Result of one conservative title/reference normalization attempt."""

    normalized: str
    deterministic: bool
    changed: bool
    reason: str


def _local_name(name: str | None) -> str:
    """Return a case-folded local tag name without an optional namespace."""
    if not name:
        return ""
    return name.casefold().rsplit(":", 1)[-1]


def _refused(value: str, reason: str) -> StructuredTitleNormalization:
    """Return a non-mutating refusal result."""
    return StructuredTitleNormalization(
        normalized=value,
        deterministic=False,
        changed=False,
        reason=reason,
    )


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


def _decode_entities(value: str) -> tuple[str, bool] | None:
    """Decode nested HTML entities to a stable value within a bounded depth."""
    current = value
    changed = False
    for _ in range(_MAX_ENTITY_DECODE_PASSES):
        if _ENTITY_RE.search(current) is None:
            return current, changed
        decoded = html.unescape(current)
        if decoded == current:
            return current, changed
        current = decoded
        changed = True

    if _ENTITY_RE.search(current) is not None and html.unescape(current) != current:
        return None
    return current, changed


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


def _trusted_tex_payload(tag) -> str | None:
    """Return explicit TeX carried by one TeX element."""
    notation = str(tag.attrs.get("notation", "")).strip().casefold()
    if notation not in {"", "tex", "latex"}:
        return None
    return _canonical_tex_body(tag.get_text())


def _mathml_token_to_tex(tag) -> str | None:
    """Convert one observed MathML token to equivalent TeX conservatively."""
    local = _local_name(tag.name)
    if local not in {"mi", "mn", "mo"}:
        return None
    if tag.find(True, recursive=False) is not None:
        return None

    raw = tag.get_text().strip()
    if not raw:
        return None

    if local == "mn":
        if re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", raw) is None:
            return None
        tex = raw
    elif local == "mo":
        if raw not in {"+", "∞"}:
            return None
        tex = _SYMBOL_TEX.get(raw, raw)
    else:
        if raw in _SYMBOL_TEX:
            tex = _SYMBOL_TEX[raw]
        elif re.fullmatch(r"[A-Za-z]+", raw):
            tex = raw
        else:
            return None

    variant = str(tag.attrs.get("mathvariant", "")).strip().casefold()
    if variant in {"", "italic"}:
        return tex
    if variant == "normal":
        if raw == "∞":
            return tex
        if re.fullmatch(r"[A-Za-z]+", raw):
            return rf"\mathrm{{{raw}}}"
        return None
    if variant == "script":
        if re.fullmatch(r"[A-Z]", raw):
            return rf"\mathcal{{{raw}}}"
        return None
    if variant == "bold-script":
        if re.fullmatch(r"[A-Z]", raw):
            return rf"\boldsymbol{{\mathcal{{{raw}}}}}"
        return None
    return None


def _mathml_to_tex(tag) -> str | None:
    """Convert the corpus-observed semantic MathML subset to TeX."""
    local = _local_name(tag.name)
    if local in {"mi", "mn", "mo"}:
        return _mathml_token_to_tex(tag)

    children = tuple(tag.find_all(True, recursive=False))
    direct_text = "".join(
        str(node)
        for node in tag.children
        if getattr(node, "name", None) is None and str(node).strip()
    )
    if direct_text.strip():
        return None

    if local in {"math", "mrow"}:
        parts = tuple(_mathml_to_tex(child) for child in children)
        if any(part is None for part in parts):
            return None
        return "".join(part for part in parts if part is not None)

    if local in {"msub", "msup"} and len(children) == 2:
        base = _mathml_to_tex(children[0])
        script = _mathml_to_tex(children[1])
        if base is None or script is None:
            return None
        operator = "_" if local == "msub" else "^"
        return f"{base}{operator}{{{script}}}"

    return None


def _provider_error_page(soup) -> bool:
    """Recognize an observed upstream HTTP error page captured as citation text."""
    text = " ".join(soup.stripped_strings).casefold()
    names = {_local_name(tag.name) for tag in soup.find_all(True)}
    return (
        "bad gateway" in text
        and bool(names & {"html", "head", "body", "h1"})
    )


def _normalize(
    value: str,
    *,
    inline_wrappers: frozenset[str],
    block_wrappers: frozenset[str],
) -> StructuredTitleNormalization:
    """Normalize one short bibliographic text under an explicit wrapper policy."""
    if not isinstance(value, str):
        raise TypeError("structured title/citation value must be a string")

    original = value
    if "\uFFFD" in value:
        return _refused(original, "unicode-replacement")
    if "\u00AD" in value:
        return _refused(original, "soft-hyphen")
    if _CONTROL_RE.search(value):
        return _refused(original, "control-character")

    decoded = _decode_entities(value)
    if decoded is None:
        return _refused(original, "entity-decoding-depth")
    value, entities_changed = decoded
    steps: list[str] = []
    if entities_changed:
        steps.append("entity-decoding")

    if _structured_tags_unbalanced(value):
        return _refused(original, "unbalanced-markup")

    if _TAG_RE.search(value) is None and "<!--" not in value:
        return StructuredTitleNormalization(
            normalized=value,
            deterministic=True,
            changed=value != original,
            reason="entity-decoding" if value != original else "already-clean",
        )

    parser_value = value.replace("&", "&amp;")
    soup = BeautifulSoup(parser_value, "html.parser")
    if _provider_error_page(soup):
        return _refused(original, "provider-error-page")
    if any(
        isinstance(text, Comment)
        for text in soup.find_all(string=True)
    ):
        return _refused(original, "xml-comment-review")

    tags = tuple(soup.find_all(True))
    for tag in tags:
        local = _local_name(tag.name)
        if local in _EMBEDDED_GRAPHIC_NAMES:
            return _refused(original, "embedded-graphic")
        if local in _SCRIPT_MARKUP_NAMES:
            return _refused(original, "script-markup")

    for formula in tuple(
        tag
        for tag in soup.find_all(True)
        if _local_name(tag.name) in _FORMULA_WRAPPERS
    ):
        payload = None
        for candidate in formula.find_all(True):
            if _local_name(candidate.name) not in _TEX_MARKUP_NAMES:
                continue
            payload = _trusted_tex_payload(candidate)
            if payload:
                break
        if payload is None:
            return _refused(original, "formula-without-trusted-text")
        formula.replace_with(NavigableString(rf"\({payload}\)"))
        steps.append("embedded-tex")

    for formula in tuple(
        tag
        for tag in soup.find_all(True)
        if _local_name(tag.name) == "math"
    ):
        payload = _mathml_to_tex(formula)
        if payload is None:
            return _refused(original, "unsupported-mathml")
        formula.replace_with(NavigableString(rf"\({payload}\)"))
        steps.append("mathml-to-tex")

    for tex_tag in tuple(
        tag
        for tag in soup.find_all(True)
        if _local_name(tag.name) in _TEX_MARKUP_NAMES
    ):
        payload = _trusted_tex_payload(tex_tag)
        if payload is None:
            return _refused(original, "unsupported-tex")
        tex_tag.replace_with(NavigableString(rf"\({payload}\)"))
        steps.append("embedded-tex")

    for tag in tuple(soup.find_all(True)):
        local = _local_name(tag.name)
        if local in block_wrappers:
            tag.insert_before(NavigableString(" "))
            tag.insert_after(NavigableString(" "))
            tag.unwrap()
            steps.append("structural-unwrapping")
        elif local in inline_wrappers:
            tag.unwrap()
            steps.append("structural-unwrapping")
        else:
            return _refused(original, "unsupported-markup")

    normalized = re.sub(r"\s+", " ", soup.get_text()).strip()
    unique_steps = tuple(dict.fromkeys(steps))
    if entities_changed and "entity-decoding" not in unique_steps:
        unique_steps = ("entity-decoding", *unique_steps)
    reason = "+".join(unique_steps) if unique_steps else "normalized"

    return StructuredTitleNormalization(
        normalized=normalized,
        deterministic=True,
        changed=normalized != original,
        reason=reason if normalized != original else "already-clean",
    )


def normalize_structured_title(value: str) -> StructuredTitleNormalization:
    """Normalize one publication title without editorial inference."""
    return _normalize(
        value,
        inline_wrappers=_TITLE_INLINE_WRAPPERS,
        block_wrappers=frozenset(),
    )


def normalize_structured_citation(value: str) -> StructuredTitleNormalization:
    """Normalize one stored reference citation without parsing citation grammar."""
    return _normalize(
        value,
        inline_wrappers=_CITATION_INLINE_WRAPPERS,
        block_wrappers=_CITATION_BLOCK_WRAPPERS,
    )
