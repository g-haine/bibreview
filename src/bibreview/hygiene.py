"""Read-only canonical metadata hygiene analysis."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from hashlib import sha256
import re
from typing import Iterable

from .model import Publication
from .structured_title import (
    normalize_structured_citation,
    normalize_structured_title,
)


_FAMILY_ORDER = (
    "legacy-renderer-marker",
    "inline-formula",
    "tex-math",
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
_INLINE_FORMULA_BLOCK_RE = re.compile(
    r"<\s*(?:jats:)?inline-formula\b[^>]*>(?P<body>.*?)"
    r"<\s*/\s*(?:jats:)?inline-formula\s*>",
    re.IGNORECASE | re.DOTALL,
)
_TEX_MATH_RE = re.compile(
    r"<\s*/?\s*(?:[A-Za-z][A-Za-z0-9_.-]*:)?tex-math\b",
    re.IGNORECASE,
)
_LATEX_TEX_MATH_BLOCK_RE = re.compile(
    r"<\s*(?:[A-Za-z][A-Za-z0-9_.-]*:)?tex-math\b"
    r"(?=[^>]*\bnotation\s*=\s*[\"']latex[\"'])[^>]*>"
    r"(?P<body>.*?)"
    r"<\s*/\s*(?:[A-Za-z][A-Za-z0-9_.-]*:)?tex-math\s*>",
    re.IGNORECASE | re.DOTALL,
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
    r"<\s*(?:mml:)?annotation\b"
    r"(?=[^>]*\bencoding\s*=\s*[\"']application/x-tex[\"'])[^>]*>"
    r"(?P<body>.*?)"
    r"<\s*/\s*(?:mml:)?annotation\s*>",
    re.IGNORECASE | re.DOTALL,
)
_XML_COMMENT_RE = re.compile(r"<!--")
_ESCAPED_MARKUP_RE = re.compile(
    r"&lt;\s*/?\s*(?:p|div|span|inline-formula|math|mml:|jats:)",
    re.IGNORECASE,
)
_LEGACY_MARKER_RE = re.compile(r"\[\[:space:\]\]")
_SMALL_CAPS_RE = re.compile(
    r"<\s*/?\s*(?:[A-Za-z][A-Za-z0-9_.-]*:)?(?:scp|small-caps)\b",
    re.IGNORECASE,
)
_HTML_ENTITY_RE = re.compile(
    r"&(?:[A-Za-z][A-Za-z0-9]+|#\d+|#x[0-9A-Fa-f]+);"
)
_TEX_FRAGMENT_RE = re.compile(
    r"(?:\\\(|\\\[|(?<!\\)\$(?!\s)[^$\n]+(?<!\s)(?<!\\)\$|"
    r"\\[A-Za-z]+(?:\s*\{[^{}]*\})?)"
)
_CONTROL_CHARACTER_RE = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]")

_TITLE_REFERENCE_FAMILY_ORDER = (
    "legacy-renderer-marker",
    "inline-formula",
    "tex-math",
    "embedded-graphic",
    "script-markup",
    "mathml",
    "jats",
    "xml-comment",
    "escaped-markup",
    "small-caps-markup",
    "html-xml-markup",
    "html-entity",
    "tex-fragment",
    "unicode-replacement",
    "soft-hyphen",
    "control-character",
    "unbalanced-structured-tags",
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


def _all_inline_formulas_have_representation(
    value: str,
    representation_re: re.Pattern[str],
) -> bool:
    """Return whether every inline formula carries a non-empty representation."""
    formulas = tuple(_INLINE_FORMULA_BLOCK_RE.finditer(value))
    if not formulas:
        return False

    for formula in formulas:
        representations = tuple(representation_re.finditer(formula.group("body")))
        if not representations:
            return False
        if not any(match.group("body").strip() for match in representations):
            return False
    return True


def _families(value: str) -> tuple[str, ...]:
    detected: set[str] = set()
    if _LEGACY_MARKER_RE.search(value):
        detected.add("legacy-renderer-marker")
    if _INLINE_FORMULA_RE.search(value):
        detected.add("inline-formula")
    if _TEX_MATH_RE.search(value):
        detected.add("tex-math")
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
        if _all_inline_formulas_have_representation(
            value,
            _LATEX_TEX_MATH_BLOCK_RE,
        ):
            return True, "embedded-tex-math"
        if _all_inline_formulas_have_representation(value, _TEX_ANNOTATION_RE):
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
        _TEX_MATH_RE,
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


@dataclass(frozen=True)
class TitleReferenceHygieneFinding:
    """One publication title or reference citation carrying a hygiene signal."""

    target: str
    publication_id: str
    doi: str | None
    publication_title: str
    value: str
    families: tuple[str, ...]
    deterministic_candidate: bool
    normalization_hint: str
    context: str
    reference_key: str = ""
    reference_doi: str | None = None
    reference_index: int | None = None

    def data(self) -> dict[str, object]:
        """Return a JSON-serializable representation."""
        data: dict[str, object] = {
            "target": self.target,
            "publication_id": self.publication_id,
            "doi": self.doi,
            "publication_title": self.publication_title,
            "value": self.value,
            "families": list(self.families),
            "deterministic_candidate": self.deterministic_candidate,
            "normalization_hint": self.normalization_hint,
            "context": self.context,
        }
        if self.target == "reference-citation":
            data.update(
                {
                    "reference_key": self.reference_key,
                    "reference_doi": self.reference_doi,
                    "reference_index": self.reference_index,
                }
            )
        return data


@dataclass(frozen=True)
class TitleReferenceHygieneReport:
    """Read-only inventory of title and reference-citation hygiene signals."""

    scanned_publications: int
    titles_present: int
    references_scanned: int
    citations_present: int
    title_findings: tuple[TitleReferenceHygieneFinding, ...]
    citation_findings: tuple[TitleReferenceHygieneFinding, ...]

    @property
    def suspicious_titles(self) -> int:
        return len(self.title_findings)

    @property
    def suspicious_citations(self) -> int:
        return len(self.citation_findings)

    @property
    def deterministic_title_candidates(self) -> int:
        return sum(item.deterministic_candidate for item in self.title_findings)

    @property
    def deterministic_citation_candidates(self) -> int:
        return sum(item.deterministic_candidate for item in self.citation_findings)

    @staticmethod
    def _family_counts(
        findings: tuple[TitleReferenceHygieneFinding, ...],
    ) -> dict[str, int]:
        counts: Counter[str] = Counter()
        for finding in findings:
            counts.update(finding.families)
        return {
            family: counts[family]
            for family in _TITLE_REFERENCE_FAMILY_ORDER
            if counts[family]
        }

    @property
    def title_family_counts(self) -> dict[str, int]:
        return self._family_counts(self.title_findings)

    @property
    def citation_family_counts(self) -> dict[str, int]:
        return self._family_counts(self.citation_findings)

    def summary(self) -> str:
        """Return a compact human-readable inventory summary."""
        lines = [
            "Canonical title/reference hygiene",
            f"  Publications scanned          : {self.scanned_publications}",
            f"  Titles present                : {self.titles_present}",
            f"  Titles with hygiene signals   : {self.suspicious_titles}",
            f"  Apparent title candidates     : {self.deterministic_title_candidates}",
            f"  References scanned            : {self.references_scanned}",
            f"  Citations present             : {self.citations_present}",
            f"  Citations with hygiene signals: {self.suspicious_citations}",
            f"  Apparent citation candidates  : {self.deterministic_citation_candidates}",
            "  Title families:",
        ]
        if self.title_family_counts:
            width = max(len(name) for name in self.title_family_counts)
            lines.extend(
                f"    {name:<{width}} : {count}"
                for name, count in self.title_family_counts.items()
            )
        else:
            lines.append("    none")
        lines.append("  Citation families:")
        if self.citation_family_counts:
            width = max(len(name) for name in self.citation_family_counts)
            lines.extend(
                f"    {name:<{width}} : {count}"
                for name, count in self.citation_family_counts.items()
            )
        else:
            lines.append("    none")
        return "\n".join(lines)

    def data(self) -> dict[str, object]:
        """Return the complete machine-readable inventory."""
        return {
            "scanned_publications": self.scanned_publications,
            "titles_present": self.titles_present,
            "titles_with_hygiene_signals": self.suspicious_titles,
            "deterministic_title_candidates": self.deterministic_title_candidates,
            "references_scanned": self.references_scanned,
            "citations_present": self.citations_present,
            "citations_with_hygiene_signals": self.suspicious_citations,
            "deterministic_citation_candidates": self.deterministic_citation_candidates,
            "title_family_counts": self.title_family_counts,
            "citation_family_counts": self.citation_family_counts,
            "title_findings": [item.data() for item in self.title_findings],
            "citation_findings": [item.data() for item in self.citation_findings],
        }


def _title_reference_families(value: str) -> tuple[str, ...]:
    detected = set(_families(value))
    if _SMALL_CAPS_RE.search(value):
        detected.add("small-caps-markup")
    if _HTML_ENTITY_RE.search(value):
        detected.add("html-entity")
    if _TEX_FRAGMENT_RE.search(value):
        detected.add("tex-fragment")
    if "\uFFFD" in value:
        detected.add("unicode-replacement")
    if "\u00AD" in value:
        detected.add("soft-hyphen")
    if _CONTROL_CHARACTER_RE.search(value):
        detected.add("control-character")
    return tuple(
        family for family in _TITLE_REFERENCE_FAMILY_ORDER if family in detected
    )


def _title_reference_assessment(
    value: str,
    families: tuple[str, ...],
    *,
    target: str,
) -> tuple[bool, str]:
    """Assess one hygiene finding using the real conservative normalizer."""
    if target == "title":
        result = normalize_structured_title(value)
    elif target == "reference-citation":
        result = normalize_structured_citation(value)
    else:
        raise ValueError(f"unsupported hygiene target: {target}")

    if not result.deterministic:
        return False, result.reason
    if result.changed:
        return True, result.reason
    if set(families) == {"tex-fragment"}:
        return False, "preserve-tex"
    return False, "already-clean"


def _reference_keys(publication: Publication) -> tuple[str, ...]:
    bases: list[str] = []
    for reference in publication.references:
        doi = reference.identifiers.get("doi")
        if doi:
            bases.append(f"doi:{doi}")
        else:
            digest = sha256(reference.citation.encode("utf-8")).hexdigest()[:16]
            bases.append(f"sha256:{digest}")

    totals = Counter(bases)
    seen: Counter[str] = Counter()
    keys: list[str] = []
    for base in bases:
        seen[base] += 1
        if totals[base] > 1:
            keys.append(f"{base}#{seen[base]}")
        else:
            keys.append(base)
    return tuple(keys)


def scan_title_reference_hygiene(
    publications: Iterable[Publication],
) -> TitleReferenceHygieneReport:
    """Inventory title/reference hygiene signals without mutating canonical data."""
    items = tuple(publications)
    title_findings: list[TitleReferenceHygieneFinding] = []
    citation_findings: list[TitleReferenceHygieneFinding] = []
    titles_present = 0
    references_scanned = 0
    citations_present = 0

    for publication in items:
        title = publication.title
        if title.strip():
            titles_present += 1
            families = _title_reference_families(title)
            if families:
                deterministic, hint = _title_reference_assessment(
                    title,
                    families,
                    target="title",
                )
                title_findings.append(
                    TitleReferenceHygieneFinding(
                        target="title",
                        publication_id=publication.id,
                        doi=publication.doi,
                        publication_title=publication.title,
                        value=title,
                        families=families,
                        deterministic_candidate=deterministic,
                        normalization_hint=hint,
                        context=_context(title),
                    )
                )

        keys = _reference_keys(publication)
        for index, (reference, reference_key) in enumerate(
            zip(publication.references, keys),
            1,
        ):
            references_scanned += 1
            citation = reference.citation
            if not citation.strip():
                continue
            citations_present += 1
            families = _title_reference_families(citation)
            if not families:
                continue
            deterministic, hint = _title_reference_assessment(
                citation,
                families,
                target="reference-citation",
            )
            citation_findings.append(
                TitleReferenceHygieneFinding(
                    target="reference-citation",
                    publication_id=publication.id,
                    doi=publication.doi,
                    publication_title=publication.title,
                    value=citation,
                    families=families,
                    deterministic_candidate=deterministic,
                    normalization_hint=hint,
                    context=_context(citation),
                    reference_key=reference_key,
                    reference_doi=reference.identifiers.get("doi"),
                    reference_index=index,
                )
            )

    return TitleReferenceHygieneReport(
        scanned_publications=len(items),
        titles_present=titles_present,
        references_scanned=references_scanned,
        citations_present=citations_present,
        title_findings=tuple(title_findings),
        citation_findings=tuple(citation_findings),
    )


def format_title_reference_hygiene_report(
    report: TitleReferenceHygieneReport,
    *,
    verbose: bool = False,
) -> str:
    """Format a title/reference hygiene inventory."""
    text = report.summary()
    if not verbose:
        return text

    details = [text]
    if report.title_findings:
        details.extend(("", "Title findings:"))
        for index, finding in enumerate(report.title_findings, 1):
            identity = finding.doi or finding.publication_id
            details.extend(
                (
                    f"[{index}/{len(report.title_findings)}] {identity}",
                    "  Families: " + ", ".join(finding.families),
                    f"  Assessment: {finding.normalization_hint}",
                    f"  Value: {finding.value}",
                )
            )

    if report.citation_findings:
        details.extend(("", "Reference citation findings:"))
        for index, finding in enumerate(report.citation_findings, 1):
            identity = finding.doi or finding.publication_id
            details.extend(
                (
                    f"[{index}/{len(report.citation_findings)}] "
                    f"{identity} — {finding.publication_title}",
                    f"  Reference: {finding.reference_key}",
                    "  Families: " + ", ".join(finding.families),
                    f"  Assessment: {finding.normalization_hint}",
                    f"  Context: {finding.context}",
                )
            )
    return "\n".join(details)


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
