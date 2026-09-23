"""Read-only canonical metadata hygiene analysis."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import re
from typing import Iterable

from .model import Publication


_FAMILY_ORDER = (
    "legacy-renderer-marker",
    "inline-formula",
    "embedded-graphic",
    "script-markup",
    "mathml",
    "jats",
    "xml-comment",
    "escaped-markup",
    "html-xml-markup",
    "unbalanced-structured-tags",
)

_GENERIC_TAG_RE = re.compile(
    r"<\s*/?\s*[A-Za-z][A-Za-z0-9_.:-]*(?:\s[^<>]*?)?/?>",
    re.IGNORECASE,
)
_TAG_RE = re.compile(
    r"<\s*(/?)\s*([A-Za-z][A-Za-z0-9_.:-]*)\b([^<>]*?)(/?)\s*>",
    re.IGNORECASE,
)
_INLINE_FORMULA_RE = re.compile(
    r"<\s*/?\s*(?:jats:)?inline-formula\b",
    re.IGNORECASE,
)
_EMBEDDED_GRAPHIC_RE = re.compile(
    r"<\s*/?\s*(?:[A-Za-z][A-Za-z0-9_.-]*:)?"
    r"(?:inline-graphic|graphic|img|image)\b",
    re.IGNORECASE,
)
_SCRIPT_MARKUP_RE = re.compile(
    r"<\s*/?\s*(?:[A-Za-z][A-Za-z0-9_.-]*:)?(?:inf|sub|sup)\b",
    re.IGNORECASE,
)
_MATHML_RE = re.compile(
    r"(?:<\s*/?\s*mml:|<\s*/?\s*math\b|xmlns:mml\s*=)",
    re.IGNORECASE,
)
_JATS_RE = re.compile(
    r"(?:<\s*/?\s*jats:|<[^>]*\bjats(?::|\b)[^>]*>)",
    re.IGNORECASE,
)
_TEX_ANNOTATION_RE = re.compile(
    r"<\s*(?:mml:)?annotation\b[^>]*"
    r"encoding\s*=\s*([\"'])application/x-tex\1",
    re.IGNORECASE,
)
_XML_COMMENT_RE = re.compile(r"<!--")
_ESCAPED_MARKUP_RE = re.compile(
    r"&lt;\s*/?\s*(?:p|div|span|inline-formula|math|mml:|jats:)",
    re.IGNORECASE,
)
_LEGACY_MARKER_RE = re.compile(r"\[\[:space:\]\]")

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
class AbstractHygieneFinding:
    """One canonical abstract containing suspicious structured markup."""

    publication_id: str
    doi: str | None
    title: str
    families: tuple[str, ...]
    deterministic_candidate: bool
    normalization_hint: str
    context: str

    def data(self) -> dict[str, object]:
        """Return a JSON-serializable representation."""
        return {
            "publication_id": self.publication_id,
            "doi": self.doi,
            "title": self.title,
            "families": list(self.families),
            "deterministic_candidate": self.deterministic_candidate,
            "normalization_hint": self.normalization_hint,
            "context": self.context,
        }


@dataclass(frozen=True)
class AbstractHygieneReport:
    """Read-only inventory of structured-markup contamination in abstracts."""

    scanned_publications: int
    abstracts_present: int
    findings: tuple[AbstractHygieneFinding, ...]

    @property
    def suspicious_abstracts(self) -> int:
        return len(self.findings)

    @property
    def deterministic_candidates(self) -> int:
        return sum(item.deterministic_candidate for item in self.findings)

    @property
    def review_required(self) -> int:
        return self.suspicious_abstracts - self.deterministic_candidates

    @property
    def family_counts(self) -> dict[str, int]:
        counts: Counter[str] = Counter()
        for finding in self.findings:
            counts.update(finding.families)
        return {
            family: counts[family]
            for family in _FAMILY_ORDER
            if counts[family]
        }

    def summary(self) -> str:
        """Return the compact human-readable inventory summary."""
        lines = [
            "Canonical abstract hygiene",
            f"  Publications scanned     : {self.scanned_publications}",
            f"  Abstracts present        : {self.abstracts_present}",
            f"  Suspicious abstracts     : {self.suspicious_abstracts}",
            f"  Deterministic candidates : {self.deterministic_candidates}",
            f"  Review required          : {self.review_required}",
            "  Families:",
        ]
        if self.family_counts:
            width = max(len(name) for name in self.family_counts)
            lines.extend(
                f"    {name:<{width}} : {count}"
                for name, count in self.family_counts.items()
            )
        else:
            lines.append("    none")
        return "\n".join(lines)

    def data(self) -> dict[str, object]:
        """Return the complete machine-readable report."""
        return {
            "scanned_publications": self.scanned_publications,
            "abstracts_present": self.abstracts_present,
            "suspicious_abstracts": self.suspicious_abstracts,
            "deterministic_candidates": self.deterministic_candidates,
            "review_required": self.review_required,
            "family_counts": self.family_counts,
            "findings": [finding.data() for finding in self.findings],
        }


def _structured_tags_unbalanced(value: str) -> bool:
    """Return whether explicit structured tags are obviously unbalanced."""
    stack: list[str] = []
    saw_tag = False
    for match in _TAG_RE.finditer(value):
        saw_tag = True
        closing, raw_name, attributes, self_closing = match.groups()
        name = raw_name.casefold()
        if self_closing or name in _VOID_HTML_TAGS:
            continue
        if closing:
            if not stack or stack[-1] != name:
                return True
            stack.pop()
        else:
            stack.append(name)
    return saw_tag and bool(stack)


def _families(value: str) -> tuple[str, ...]:
    detected: set[str] = set()
    if _LEGACY_MARKER_RE.search(value):
        detected.add("legacy-renderer-marker")
    if _INLINE_FORMULA_RE.search(value):
        detected.add("inline-formula")
    if _EMBEDDED_GRAPHIC_RE.search(value):
        detected.add("embedded-graphic")
    if _SCRIPT_MARKUP_RE.search(value):
        detected.add("script-markup")
    if _MATHML_RE.search(value):
        detected.add("mathml")
    if _JATS_RE.search(value):
        detected.add("jats")
    if _XML_COMMENT_RE.search(value):
        detected.add("xml-comment")
    if _ESCAPED_MARKUP_RE.search(value):
        detected.add("escaped-markup")
    if _GENERIC_TAG_RE.search(value):
        detected.add("html-xml-markup")
    if _structured_tags_unbalanced(value):
        detected.add("unbalanced-structured-tags")
    return tuple(family for family in _FAMILY_ORDER if family in detected)


def _normalization_assessment(
    value: str,
    families: tuple[str, ...],
) -> tuple[bool, str]:
    family_set = set(families)
    if "unbalanced-structured-tags" in family_set:
        return False, "review-required"
    if "embedded-graphic" in family_set:
        return False, "embedded-graphic-review"
    if "script-markup" in family_set:
        return False, "script-markup-review"
    if {"inline-formula", "mathml"} & family_set:
        if _TEX_ANNOTATION_RE.search(value):
            return True, "embedded-tex-annotation"
        return False, "review-required"
    if "escaped-markup" in family_set:
        return False, "review-required"
    if family_set == {"legacy-renderer-marker"}:
        return True, "legacy-marker-removal"
    if family_set <= {"html-xml-markup", "jats", "xml-comment"}:
        return True, "structural-unwrapping"
    return False, "review-required"


def _context(value: str) -> str:
    collapsed = re.sub(r"\s+", " ", value).strip()
    if not collapsed:
        return ""

    indices: list[int] = []
    for pattern in (
        _LEGACY_MARKER_RE,
        _INLINE_FORMULA_RE,
        _EMBEDDED_GRAPHIC_RE,
        _SCRIPT_MARKUP_RE,
        _MATHML_RE,
        _JATS_RE,
        _XML_COMMENT_RE,
        _ESCAPED_MARKUP_RE,
        _GENERIC_TAG_RE,
    ):
        match = pattern.search(collapsed)
        if match is not None:
            indices.append(match.start())
    center = min(indices) if indices else 0
    start = max(0, center - 70)
    end = min(len(collapsed), center + 170)
    prefix = "…" if start else ""
    suffix = "…" if end < len(collapsed) else ""
    return prefix + collapsed[start:end] + suffix


def scan_abstract_hygiene(
    publications: Iterable[Publication],
) -> AbstractHygieneReport:
    """Inventory suspicious markup in canonical abstracts without mutation."""
    items = tuple(publications)
    findings: list[AbstractHygieneFinding] = []
    abstracts_present = 0

    for publication in items:
        abstract = publication.abstract
        if not abstract.strip():
            continue
        abstracts_present += 1
        families = _families(abstract)
        if not families:
            continue
        deterministic, hint = _normalization_assessment(abstract, families)
        findings.append(
            AbstractHygieneFinding(
                publication_id=publication.id,
                doi=publication.doi,
                title=publication.title,
                families=families,
                deterministic_candidate=deterministic,
                normalization_hint=hint,
                context=_context(abstract),
            )
        )

    return AbstractHygieneReport(
        scanned_publications=len(items),
        abstracts_present=abstracts_present,
        findings=tuple(findings),
    )


def format_abstract_hygiene_report(
    report: AbstractHygieneReport,
    *,
    verbose: bool = False,
) -> str:
    """Format one hygiene report, optionally including each affected record."""
    text = report.summary()
    if not verbose or not report.findings:
        return text

    details = [text, "", "Findings:"]
    for index, finding in enumerate(report.findings, 1):
        identity = finding.doi or finding.publication_id
        details.extend(
            [
                f"[{index}/{len(report.findings)}] {identity}: {finding.title}",
                "  Families: " + ", ".join(finding.families),
                f"  Normalization: {finding.normalization_hint}",
                f"  Context: {finding.context}",
            ]
        )
    return "\n".join(details)
