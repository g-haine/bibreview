"""Persisted relevance evidence and deterministic offline rule analysis."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, replace
import html
import math
from pathlib import Path
import re
import unicodedata
from typing import Any, Iterable, Mapping

from .identity import normalize_doi
from .storage import StorageError, read_json


RELEVANCE_EVIDENCE_SCHEMA_VERSION = 1
_SCREENING_OUTCOMES = frozenset({"queued", "review", "rejected", "unknown"})
_HUMAN_DECISIONS = frozenset({"", "keep", "reject"})
_EVIDENCE_SOURCES = frozenset({"screening", "backfill", "canonical-backfill"})

_STOPWORDS = frozenset(
    """
    a an and are as at be been being by for from has have in into is it its
    of on or that the their this to using via was were with within without
    we our can may
    """.split()
)
_WORD = re.compile(r"[^\W_]+(?:[-'][^\W_]+)*", re.UNICODE)


@dataclass(frozen=True)
class RelevanceEvidence:
    """One retained title/abstract/keyword screening snapshot for a DOI."""

    doi: str
    title: str
    abstract: str
    keywords: tuple[str, ...]
    work_type: str
    screening_outcome: str = "unknown"
    accept_matches: tuple[str, ...] = ()
    reject_matches: tuple[str, ...] = ()
    source: str = "screening"
    batch_id: str = ""
    attempt: int = 0
    human_decision: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "doi", normalize_doi(self.doi))
        object.__setattr__(self, "keywords", tuple(self.keywords))
        object.__setattr__(self, "accept_matches", tuple(self.accept_matches))
        object.__setattr__(self, "reject_matches", tuple(self.reject_matches))
        for name in ("title", "abstract", "work_type", "source", "batch_id", "human_decision"):
            if not isinstance(getattr(self, name), str):
                raise ValueError(f"relevance evidence {name} must be a string")
        if any(not isinstance(item, str) for item in self.keywords):
            raise ValueError("relevance evidence keywords must contain strings")
        if any(not isinstance(item, str) for item in self.accept_matches):
            raise ValueError("relevance evidence accept_matches must contain strings")
        if any(not isinstance(item, str) for item in self.reject_matches):
            raise ValueError("relevance evidence reject_matches must contain strings")
        if self.screening_outcome not in _SCREENING_OUTCOMES:
            raise ValueError(
                f"unsupported relevance screening outcome: {self.screening_outcome!r}"
            )
        if self.human_decision not in _HUMAN_DECISIONS:
            raise ValueError(
                f"unsupported relevance human decision: {self.human_decision!r}"
            )
        if self.source not in _EVIDENCE_SOURCES:
            raise ValueError(f"unsupported relevance evidence source: {self.source!r}")
        if not isinstance(self.attempt, int) or isinstance(self.attempt, bool) or self.attempt < 0:
            raise ValueError("relevance evidence attempt must be a non-negative integer")


def relevance_evidence_data(entries: Iterable[RelevanceEvidence]) -> dict[str, Any]:
    """Return deterministic JSON-ready relevance evidence."""

    return {
        "schema_version": RELEVANCE_EVIDENCE_SCHEMA_VERSION,
        "entries": [
            {
                "doi": entry.doi,
                "title": entry.title,
                "abstract": entry.abstract,
                "keywords": list(entry.keywords),
                "work_type": entry.work_type,
                "screening_outcome": entry.screening_outcome,
                "accept_matches": list(entry.accept_matches),
                "reject_matches": list(entry.reject_matches),
                "source": entry.source,
                "batch_id": entry.batch_id,
                "attempt": entry.attempt,
                "human_decision": entry.human_decision,
            }
            for entry in entries
        ],
    }


def relevance_evidence_from_data(value: Any) -> tuple[RelevanceEvidence, ...]:
    """Strictly load one relevance-evidence document."""

    if not isinstance(value, Mapping):
        raise StorageError("relevance evidence must be an object")
    if value.get("schema_version") != RELEVANCE_EVIDENCE_SCHEMA_VERSION:
        raise StorageError(
            "unsupported relevance evidence schema_version: "
            f"{value.get('schema_version')!r}"
        )
    raw_entries = value.get("entries")
    if not isinstance(raw_entries, list):
        raise StorageError("relevance evidence entries must be a list")

    result: list[RelevanceEvidence] = []
    for index, raw in enumerate(raw_entries, 1):
        if not isinstance(raw, Mapping):
            raise StorageError(f"relevance evidence entry {index} must be an object")
        required = {
            "doi",
            "title",
            "abstract",
            "keywords",
            "work_type",
            "screening_outcome",
            "accept_matches",
            "reject_matches",
            "source",
            "batch_id",
            "attempt",
            "human_decision",
        }
        missing = required - raw.keys()
        unknown = raw.keys() - required
        if missing:
            raise StorageError(
                f"relevance evidence entry {index} missing fields: "
                + ", ".join(sorted(missing))
            )
        if unknown:
            raise StorageError(
                f"relevance evidence entry {index} has unknown fields: "
                + ", ".join(sorted(unknown))
            )

        keywords = raw["keywords"]
        accept_matches = raw["accept_matches"]
        reject_matches = raw["reject_matches"]
        if not isinstance(keywords, list) or any(not isinstance(item, str) for item in keywords):
            raise StorageError(
                f"relevance evidence entry {index}.keywords must be a list of strings"
            )
        if not isinstance(accept_matches, list) or any(
            not isinstance(item, str) for item in accept_matches
        ):
            raise StorageError(
                f"relevance evidence entry {index}.accept_matches must be a list of strings"
            )
        if not isinstance(reject_matches, list) or any(
            not isinstance(item, str) for item in reject_matches
        ):
            raise StorageError(
                f"relevance evidence entry {index}.reject_matches must be a list of strings"
            )
        try:
            entry = RelevanceEvidence(
                doi=raw["doi"],
                title=raw["title"],
                abstract=raw["abstract"],
                keywords=tuple(keywords),
                work_type=raw["work_type"],
                screening_outcome=raw["screening_outcome"],
                accept_matches=tuple(accept_matches),
                reject_matches=tuple(reject_matches),
                source=raw["source"],
                batch_id=raw["batch_id"],
                attempt=raw["attempt"],
                human_decision=raw["human_decision"],
            )
        except (TypeError, ValueError) as error:
            raise StorageError(
                f"relevance evidence entry {index}: {error}"
            ) from error
        result.append(entry)

    if len({entry.doi for entry in result}) != len(result):
        raise StorageError("relevance evidence contains duplicate DOI entries")
    return tuple(result)


def read_relevance_evidence(path: Path | str) -> tuple[RelevanceEvidence, ...]:
    """Read persisted evidence, treating a missing file as an empty ledger."""

    source = Path(path)
    if not source.exists():
        return ()
    return relevance_evidence_from_data(read_json(source, dict))


def merge_relevance_evidence(
    existing: Iterable[RelevanceEvidence],
    additions: Iterable[RelevanceEvidence],
) -> tuple[RelevanceEvidence, ...]:
    """Replace DOI snapshots while preserving explicit human decisions/context."""

    result = list(existing)
    positions = {entry.doi: index for index, entry in enumerate(result)}
    for addition in additions:
        current_index = positions.get(addition.doi)
        if current_index is None:
            positions[addition.doi] = len(result)
            result.append(addition)
            continue

        current = result[current_index]
        replacement = addition
        if not replacement.human_decision and current.human_decision:
            replacement = replace(
                replacement,
                human_decision=current.human_decision,
            )
        if not replacement.batch_id and current.batch_id:
            replacement = replace(
                replacement,
                batch_id=current.batch_id,
                attempt=current.attempt,
            )
        result[current_index] = replacement
    return tuple(result)


def with_relevance_context(
    entry: RelevanceEvidence,
    *,
    batch_id: str,
    attempt: int,
) -> RelevanceEvidence:
    """Attach initialization batch context to one screening snapshot."""

    return replace(entry, batch_id=batch_id, attempt=attempt)


def record_human_relevance_decision(
    entries: Iterable[RelevanceEvidence],
    *,
    doi: str,
    decision: str,
) -> tuple[RelevanceEvidence, ...]:
    """Annotate retained evidence without making review depend on ledger presence."""

    normalized = normalize_doi(doi)
    normalized_decision = decision.strip().lower()
    if normalized_decision not in {"keep", "reject"}:
        raise ValueError("relevance human decision must be 'keep' or 'reject'")

    result = list(entries)
    for index, entry in enumerate(result):
        if entry.doi == normalized:
            result[index] = replace(entry, human_decision=normalized_decision)
            break
    return tuple(result)


def relevance_text(entry: RelevanceEvidence) -> str:
    """Return the exact conceptual relevance surface used for offline analysis."""

    return " ".join(
        part
        for part in (
            entry.title,
            entry.abstract,
            " ".join(entry.keywords),
        )
        if part
    )


def _normalized_text(value: str) -> str:
    return "".join(
        "-" if unicodedata.category(character) == "Pd" else character
        for character in value
    )


def _matches(value: str, patterns: Iterable[str]) -> tuple[str, ...]:
    text = _normalized_text(value)
    return tuple(
        pattern
        for pattern in patterns
        if re.search(pattern, text, re.IGNORECASE) is not None
    )


def _replay_outcome(
    entry: RelevanceEvidence,
    *,
    accept_patterns: tuple[str, ...],
    reject_patterns: tuple[str, ...],
    unmatched: str,
) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
    text = relevance_text(entry)
    accept = _matches(text, accept_patterns)
    reject = _matches(text, reject_patterns)
    if accept and reject:
        return "review", accept, reject
    if accept:
        return "accept", accept, reject
    if reject:
        return "reject", accept, reject
    return ("reject" if unmatched == "reject" else "review"), accept, reject


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _wilson_lower(successes: int, total: int, *, z: float = 1.96) -> float:
    if total <= 0:
        return 0.0
    p = successes / total
    z2 = z * z
    denominator = 1.0 + z2 / total
    center = p + z2 / (2.0 * total)
    margin = z * math.sqrt((p * (1.0 - p) + z2 / (4.0 * total)) / total)
    return max(0.0, (center - margin) / denominator)


def _feature_text(entry: RelevanceEvidence) -> str:
    """Return a markup-neutral text surface used only for statistical features."""

    text = relevance_text(entry)
    for _ in range(2):
        decoded = html.unescape(text)
        if decoded == text:
            break
        text = decoded
    text = _COMMENT.sub(" ", text)
    text = _TAG_LIKE.sub(" ", text)
    return _normalized_text(text).casefold()


def _feature_set(entry: RelevanceEvidence) -> set[str]:
    text = _feature_text(entry)
    tokens = [
        token
        for token in _WORD.findall(text)
        if len(token) >= 2 and token not in _STOPWORDS
    ]
    result: set[str] = set()
    for width in (1, 2, 3):
        for index in range(len(tokens) - width + 1):
            result.add(" ".join(tokens[index : index + width]))
    return result


def _rule_diagnostics(
    labeled: tuple[tuple[RelevanceEvidence, str, str], ...],
    patterns: tuple[str, ...],
    opposite_patterns: tuple[str, ...],
) -> list[dict[str, Any]]:
    matches_by_pattern: list[set[str]] = []
    opposite_by_doi = {
        entry.doi: bool(_matches(relevance_text(entry), opposite_patterns))
        for entry, _, _ in labeled
    }
    for pattern in patterns:
        matches_by_pattern.append(
            {
                entry.doi
                for entry, _, _ in labeled
                if _matches(relevance_text(entry), (pattern,))
            }
        )

    result: list[dict[str, Any]] = []
    for index, pattern in enumerate(patterns):
        dois = matches_by_pattern[index]
        labels = {
            entry.doi: label
            for entry, label, _ in labeled
            if entry.doi in dois
        }
        keep = sum(label == "keep" for label in labels.values())
        reject = sum(label == "reject" for label in labels.values())
        others = set().union(
            *(matches for pos, matches in enumerate(matches_by_pattern) if pos != index)
        ) if len(matches_by_pattern) > 1 else set()
        unique = len(dois - others)
        conflicts = sum(opposite_by_doi.get(doi, False) for doi in dois)
        result.append(
            {
                "pattern": pattern,
                "support": len(dois),
                "keep": keep,
                "reject": reject,
                "keep_precision": _ratio(keep, len(dois)),
                "reject_precision": _ratio(reject, len(dois)),
                "unique_coverage": unique,
                "opposite_conflicts": conflicts,
            }
        )
    return result


def _replay_metrics(
    labeled: tuple[tuple[RelevanceEvidence, str, str], ...],
    *,
    accept_patterns: tuple[str, ...],
    reject_patterns: tuple[str, ...],
    unmatched: str,
) -> dict[str, Any]:
    auto_accept: list[str] = []
    auto_reject: list[str] = []
    review: list[str] = []
    false_accept: list[str] = []
    false_reject: list[str] = []
    conflicts: list[str] = []

    for entry, label, _ in labeled:
        outcome, accept, reject = _replay_outcome(
            entry,
            accept_patterns=accept_patterns,
            reject_patterns=reject_patterns,
            unmatched=unmatched,
        )
        if accept and reject:
            conflicts.append(entry.doi)
        if outcome == "accept":
            auto_accept.append(entry.doi)
            if label == "reject":
                false_accept.append(entry.doi)
        elif outcome == "reject":
            auto_reject.append(entry.doi)
            if label == "keep":
                false_reject.append(entry.doi)
        else:
            review.append(entry.doi)

    correct_accept = len(auto_accept) - len(false_accept)
    correct_reject = len(auto_reject) - len(false_reject)
    return {
        "auto_accept": len(auto_accept),
        "auto_reject": len(auto_reject),
        "manual_review": len(review),
        "conflicts": len(conflicts),
        "accept_false_positives": len(false_accept),
        "reject_false_negatives": len(false_reject),
        "accept_precision": _ratio(correct_accept, len(auto_accept)),
        "reject_precision": _ratio(correct_reject, len(auto_reject)),
        "automatic_coverage": _ratio(
            len(auto_accept) + len(auto_reject),
            len(labeled),
        ),
        "false_accept_dois": false_accept,
        "false_reject_dois": false_reject,
        "conflict_dois": conflicts,
    }


def _signal_candidates(
    labeled: tuple[tuple[RelevanceEvidence, str, str], ...],
    *,
    accept_patterns: tuple[str, ...],
    reject_patterns: tuple[str, ...],
    unmatched: str,
    limit: int = 20,
) -> dict[str, list[dict[str, Any]]]:
    """Rank signals by their ability to resolve the current manual-review gap."""

    counts: dict[str, Counter[str]] = defaultdict(Counter)
    review_counts: dict[str, Counter[str]] = defaultdict(Counter)
    dois: dict[str, dict[str, list[str]]] = defaultdict(
        lambda: {"keep": [], "reject": []}
    )
    review_dois: dict[str, dict[str, list[str]]] = defaultdict(
        lambda: {"keep": [], "reject": []}
    )
    human_counts: dict[str, Counter[str]] = defaultdict(Counter)
    batches: dict[str, set[str]] = defaultdict(set)
    review_batches: dict[str, set[str]] = defaultdict(set)

    keep_total = sum(label == "keep" for _, label, _ in labeled)
    reject_total = sum(label == "reject" for _, label, _ in labeled)
    total = len(labeled)
    base = {
        "keep": keep_total / total if total else 0.0,
        "reject": reject_total / total if total else 0.0,
    }

    review_items: list[tuple[RelevanceEvidence, str, str]] = []
    for entry, label, source in labeled:
        replay, _, _ = _replay_outcome(
            entry,
            accept_patterns=accept_patterns,
            reject_patterns=reject_patterns,
            unmatched=unmatched,
        )
        if replay == "review":
            review_items.append((entry, label, source))

        for feature in _feature_set(entry):
            counts[feature][label] += 1
            if len(dois[feature][label]) < 5:
                dois[feature][label].append(entry.doi)
            if source == "human":
                human_counts[feature][label] += 1
            if entry.batch_id:
                batches[feature].add(entry.batch_id)

            if replay == "review":
                review_counts[feature][label] += 1
                if len(review_dois[feature][label]) < 5:
                    review_dois[feature][label].append(entry.doi)
                if entry.batch_id:
                    review_batches[feature].add(entry.batch_id)

    review_total = len(review_items)
    review_base = {
        "keep": (
            sum(label == "keep" for _, label, _ in review_items) / review_total
            if review_total
            else 0.0
        ),
        "reject": (
            sum(label == "reject" for _, label, _ in review_items) / review_total
            if review_total
            else 0.0
        ),
    }

    candidates: dict[str, list[dict[str, Any]]] = {"accept": [], "reject": []}
    for feature, counter in counts.items():
        support = counter["keep"] + counter["reject"]
        if support < 2:
            continue

        gap_counter = review_counts[feature]
        review_support = gap_counter["keep"] + gap_counter["reject"]
        if review_support < 2:
            continue

        for direction, label in (("accept", "keep"), ("reject", "reject")):
            successes = counter[label]
            precision = successes / support
            gap_successes = gap_counter[label]
            review_precision = gap_successes / review_support
            if review_precision <= review_base[label]:
                continue

            lower = _wilson_lower(successes, support)
            review_lower = _wilson_lower(gap_successes, review_support)
            lift = precision / base[label] if base[label] else None
            review_lift = (
                review_precision / review_base[label]
                if review_base[label]
                else None
            )
            candidates[direction].append(
                {
                    "phrase": feature,
                    "support": support,
                    "keep": counter["keep"],
                    "reject": counter["reject"],
                    "precision": precision,
                    "wilson_lower": lower,
                    "lift": lift,
                    "review_support": review_support,
                    "review_keep": gap_counter["keep"],
                    "review_reject": gap_counter["reject"],
                    "review_precision": review_precision,
                    "review_wilson_lower": review_lower,
                    "review_lift": review_lift,
                    "human_keep": human_counts[feature]["keep"],
                    "human_reject": human_counts[feature]["reject"],
                    "batch_count": len(batches[feature]),
                    "review_batch_count": len(review_batches[feature]),
                    "supporting_dois": dois[feature][label],
                    "contradicting_dois": dois[feature][
                        "reject" if label == "keep" else "keep"
                    ],
                    "review_supporting_dois": review_dois[feature][label],
                    "review_contradicting_dois": review_dois[feature][
                        "reject" if label == "keep" else "keep"
                    ],
                }
            )

    for direction in candidates:
        candidates[direction].sort(
            key=lambda item: (
                item["review_wilson_lower"],
                item["review_support"],
                item["review_precision"],
                item["wilson_lower"],
                item["support"],
                item["phrase"],
            ),
            reverse=True,
        )
        candidates[direction] = candidates[direction][:limit]
    return candidates

def analyze_relevance(
    entries: Iterable[RelevanceEvidence],
    labels: Mapping[str, tuple[str, str]],
    *,
    accept_patterns: Iterable[str] = (),
    reject_patterns: Iterable[str] = (),
    unmatched: str = "manual-review",
) -> dict[str, Any]:
    """Analyze labeled evidence without provider access or project mutation."""

    evidence = tuple(entries)
    if unmatched not in {"manual-review", "reject"}:
        raise ValueError("relevance unmatched policy must be 'manual-review' or 'reject'")
    accept = tuple(accept_patterns)
    reject = tuple(reject_patterns)

    labeled_list: list[tuple[RelevanceEvidence, str, str]] = []
    source_counts: Counter[str] = Counter()
    for entry in evidence:
        label_item = labels.get(entry.doi)
        if label_item is None:
            continue
        label, source = label_item
        if label not in {"keep", "reject"}:
            raise ValueError(f"{entry.doi}: unsupported relevance label {label!r}")
        labeled_list.append((entry, label, source))
        source_counts[source] += 1
    labeled = tuple(labeled_list)
    human = tuple(item for item in labeled if item[2] == "human")

    summary = {
        "evidence": len(evidence),
        "labeled": len(labeled),
        "keep": sum(label == "keep" for _, label, _ in labeled),
        "reject": sum(label == "reject" for _, label, _ in labeled),
        "human_labeled": len(human),
        "unresolved_evidence": len(evidence) - len(labeled),
        "batches": len({entry.batch_id for entry in evidence if entry.batch_id}),
        "label_sources": dict(sorted(source_counts.items())),
    }

    return {
        "summary": summary,
        "current_rules": _replay_metrics(
            labeled,
            accept_patterns=accept,
            reject_patterns=reject,
            unmatched=unmatched,
        ),
        "human_reviewed_rules": _replay_metrics(
            human,
            accept_patterns=accept,
            reject_patterns=reject,
            unmatched=unmatched,
        ),
        "accept_patterns": _rule_diagnostics(labeled, accept, reject),
        "reject_patterns": _rule_diagnostics(labeled, reject, accept),
        "signals": _signal_candidates(
            labeled,
            accept_patterns=accept,
            reject_patterns=reject,
            unmatched=unmatched,
        ),
    }


def _format_ratio(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.3f}"


def format_relevance_analysis(analysis: Mapping[str, Any]) -> str:
    """Render a compact human-facing offline relevance report."""

    summary = analysis["summary"]
    current = analysis["current_rules"]
    human = analysis["human_reviewed_rules"]
    provenance = ", ".join(
        f"{name}={count}"
        for name, count in summary["label_sources"].items()
    ) or "none"
    lines = [
        "Offline relevance analysis",
        f"  Evidence snapshots : {summary['evidence']}",
        f"  Labeled             : {summary['labeled']}",
        f"  KEEP                : {summary['keep']}",
        f"  REJECT              : {summary['reject']}",
        f"  Human-reviewed      : {summary['human_labeled']}",
        f"  Label provenance    : {provenance}",
        f"  Unresolved evidence : {summary['unresolved_evidence']}",
        f"  Batches represented : {summary['batches']}",
        "",
        "Current-rule replay",
        f"  Auto accept         : {current['auto_accept']}",
        f"  Auto reject         : {current['auto_reject']}",
        f"  Manual review       : {current['manual_review']}",
        f"  Accept precision    : {_format_ratio(current['accept_precision'])}",
        f"  Reject precision    : {_format_ratio(current['reject_precision'])}",
        f"  Automatic coverage  : {_format_ratio(current['automatic_coverage'])}",
        f"  Accept false pos.   : {current['accept_false_positives']}",
        f"  Reject false neg.   : {current['reject_false_negatives']}",
    ]
    if current["false_accept_dois"] or current["false_reject_dois"]:
        lines.extend(["", "Current-rule errors"])
        for doi in current["false_accept_dois"]:
            lines.append(f"  false accept: {doi}")
        for doi in current["false_reject_dois"]:
            lines.append(f"  false reject: {doi}")

    if summary["human_labeled"] > 0:
        lines.extend(
            [
                "",
                "Human-reviewed subset",
                f"  Labeled             : {summary['human_labeled']}",
                f"  Accept precision    : {_format_ratio(human['accept_precision'])}",
                f"  Reject precision    : {_format_ratio(human['reject_precision'])}",
                f"  Automatic coverage  : {_format_ratio(human['automatic_coverage'])}",
            ]
        )

    for heading, key in (
        ("Candidate accept signals for current review gap", "accept"),
        ("Candidate reject signals for current review gap", "reject"),
    ):
        lines.extend(["", heading])
        signals = analysis["signals"][key]
        if not signals:
            lines.append("  (insufficient discriminative evidence)")
            continue
        for item in signals[:10]:
            lines.append(
                "  "
                + repr(item["phrase"])
                + f": review support {item['review_support']}; "
                + f"review precision {item['review_precision']:.3f}; "
                + f"review Wilson lower {item['review_wilson_lower']:.3f}; "
                + f"overall {item['support']} @ {item['precision']:.3f}; "
                + f"review batches {item['review_batch_count']}"
            )

    return "\n".join(lines)
