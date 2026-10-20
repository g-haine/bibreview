"""Project-level BibTeX and author-identity hygiene orchestration."""

from __future__ import annotations

from dataclasses import dataclass
import re
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping
from unidecode import unidecode

from .author_distinct import (
    author_distinct_path,
    load_reviewed_distinct_state,
    mark_reviewed_distinct,
    reconcile_reviewed_distinct_state,
    reviewed_distinct_bytes,
    reviewed_distinct_status,
)
from .bibtex_edit import BibtexEditError, bibtex_field_value
from .config import BibReviewConfig
from .identity_hygiene import (
    AuthorHygieneReport,
    merge_author_identities,
    remove_author_variant,
    rename_author_identity,
    scan_author_identity_hygiene,
)
from .model import Author, Editor, Publication
from .storage import (
    atomic_write_batch,
    json_bytes,
    read_bibliography,
    read_json,
)


def _display_contributor(contributor: Author | Editor) -> str:
    if contributor.literal:
        return contributor.literal.strip()
    return " ".join(
        value.strip()
        for value in (contributor.given or "", contributor.family or "")
        if value.strip()
    )


def _canonical_contributor_value(
    contributors: tuple[Author, ...] | tuple[Editor, ...],
) -> str:
    return " and ".join(
        name for contributor in contributors
        if (name := _display_contributor(contributor))
    )


def _normalized_name_key(value: str) -> tuple[str, ...]:
    """Return a conservative order-insensitive token key for one contributor."""
    clean = value.replace("{", "").replace("}", "").strip()
    words = re.findall(r"[a-z0-9]+", unidecode(clean).casefold())
    return tuple(sorted(words))


def _split_bibtex_contributors(
    value: str,
) -> tuple[tuple[tuple[str, ...], ...], bool]:
    """Split a BibTeX contributor list at top-level and separators."""
    segments: list[str] = []
    start = 0
    depth = 0
    index = 0
    while index < len(value):
        char = value[index]
        if char == "{" and (index == 0 or value[index - 1] != "\\"):
            depth += 1
        elif char == "}" and depth > 0 and value[index - 1] != "\\":
            depth -= 1

        if depth == 0:
            match = re.match(r"\s+and\s+", value[index:], flags=re.IGNORECASE)
            if match is not None:
                segments.append(value[start:index])
                index += match.end()
                start = index
                continue
        index += 1
    segments.append(value[start:])

    malformed = any(not segment.strip() for segment in segments)
    keys = tuple(
        _normalized_name_key(segment)
        for segment in segments
        if segment.strip()
    )
    return keys, malformed


def _canonical_contributor_keys(
    contributors: tuple[Author, ...] | tuple[Editor, ...],
) -> tuple[tuple[str, ...], ...]:
    return tuple(
        _normalized_name_key(name)
        for contributor in contributors
        if (name := _display_contributor(contributor))
    )


@dataclass(frozen=True)
class BibtexContributorFinding:
    """One canonical/BibTeX contributor inconsistency."""

    publication_id: str
    doi: str | None
    title: str
    path: str
    field: str
    kind: str
    canonical_value: str
    bibtex_value: str | None
    reason: str

    def data(self) -> dict[str, Any]:
        return {
            "publication_id": self.publication_id,
            "doi": self.doi,
            "title": self.title,
            "path": self.path,
            "field": self.field,
            "kind": self.kind,
            "canonical_value": self.canonical_value,
            "bibtex_value": self.bibtex_value,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class BibtexContributorHygieneReport:
    """Read-only report of contributor inconsistencies in tracked BibTeX."""

    scanned_publications: int
    tracked_bibtex: int
    findings: tuple[BibtexContributorFinding, ...]

    @property
    def missing_files(self) -> int:
        return sum(item.kind == "missing-bibtex" for item in self.findings)

    @property
    def missing_fields(self) -> int:
        return sum(item.kind == "missing-contributor-field" for item in self.findings)

    @property
    def mismatched_fields(self) -> int:
        return sum(item.kind == "contributor-mismatch" for item in self.findings)

    @property
    def malformed_lists(self) -> int:
        return sum(item.kind == "malformed-contributor-list" for item in self.findings)

    @property
    def unreadable_files(self) -> int:
        return sum(item.kind == "invalid-bibtex" for item in self.findings)

    def summary(self) -> str:
        return (
            "Canonical/BibTeX contributor hygiene\n"
            f"  Publications scanned   : {self.scanned_publications}\n"
            f"  Tracked BibTeX files   : {self.tracked_bibtex}\n"
            f"  Missing BibTeX files   : {self.missing_files}\n"
            f"  Missing author/editor  : {self.missing_fields}\n"
            f"  Contributor mismatches : {self.mismatched_fields}\n"
            f"  Malformed contrib. list: {self.malformed_lists}\n"
            f"  Invalid BibTeX files   : {self.unreadable_files}"
        )

    def data(self) -> dict[str, Any]:
        return {
            "scanned_publications": self.scanned_publications,
            "tracked_bibtex_files": self.tracked_bibtex,
            "missing_bibtex_files": self.missing_files,
            "missing_contributor_fields": self.missing_fields,
            "contributor_mismatches": self.mismatched_fields,
            "malformed_contributor_lists": self.malformed_lists,
            "invalid_bibtex_files": self.unreadable_files,
            "findings": [item.data() for item in self.findings],
        }


def project_bibtex_contributor_hygiene(
    config: BibReviewConfig,
) -> BibtexContributorHygieneReport:
    """Compare reviewed canonical contributors with tracked BibTeX artifacts."""
    publications = read_bibliography(config.paths.bibliography)
    findings: list[BibtexContributorFinding] = []
    tracked = 0

    for publication in publications:
        if not publication.permalink:
            continue
        path = config.paths.bibtex / f"{publication.permalink}.bib"
        if not path.exists():
            findings.append(
                BibtexContributorFinding(
                    publication_id=publication.id,
                    doi=publication.doi,
                    title=publication.title,
                    path=str(path),
                    field="bibtex",
                    kind="missing-bibtex",
                    canonical_value="",
                    bibtex_value=None,
                    reason="canonical publication has no tracked BibTeX file",
                )
            )
            continue

        tracked += 1
        try:
            text = path.read_text(encoding="utf-8")
            fields = (
                ("author", publication.authors),
                ("editor", publication.editors),
            )
            for field, contributors in fields:
                canonical_value = _canonical_contributor_value(contributors)
                if not canonical_value:
                    continue
                value = bibtex_field_value(text, field)
                if value is None:
                    findings.append(
                        BibtexContributorFinding(
                            publication_id=publication.id,
                            doi=publication.doi,
                            title=publication.title,
                            path=str(path),
                            field=field,
                            kind="missing-contributor-field",
                            canonical_value=canonical_value,
                            bibtex_value=None,
                            reason=f"canonical {field}s exist but BibTeX has no {field} field",
                        )
                    )
                    continue
                bibtex_keys, malformed = _split_bibtex_contributors(value)
                if malformed:
                    findings.append(
                        BibtexContributorFinding(
                            publication_id=publication.id,
                            doi=publication.doi,
                            title=publication.title,
                            path=str(path),
                            field=field,
                            kind="malformed-contributor-list",
                            canonical_value=canonical_value,
                            bibtex_value=value,
                            reason=(
                                f"BibTeX {field} contains an empty contributor "
                                "segment around an 'and' separator"
                            ),
                        )
                    )
                if bibtex_keys != _canonical_contributor_keys(contributors):
                    findings.append(
                        BibtexContributorFinding(
                            publication_id=publication.id,
                            doi=publication.doi,
                            title=publication.title,
                            path=str(path),
                            field=field,
                            kind="contributor-mismatch",
                            canonical_value=canonical_value,
                            bibtex_value=value,
                            reason=f"BibTeX {field} differs from reviewed canonical contributors",
                        )
                    )
        except (OSError, UnicodeError, BibtexEditError) as error:
            findings.append(
                BibtexContributorFinding(
                    publication_id=publication.id,
                    doi=publication.doi,
                    title=publication.title,
                    path=str(path),
                    field="bibtex",
                    kind="invalid-bibtex",
                    canonical_value="",
                    bibtex_value=None,
                    reason=str(error),
                )
            )

    return BibtexContributorHygieneReport(
        scanned_publications=len(publications),
        tracked_bibtex=tracked,
        findings=tuple(findings),
    )


def project_author_identity_hygiene(
    config: BibReviewConfig,
) -> AuthorHygieneReport:
    """Inspect already-reviewed author identities without mutating state."""
    mapping = (
        read_json(config.paths.author_mappings, dict)
        if config.paths.author_mappings.exists()
        else {}
    )
    raw = scan_author_identity_hygiene(mapping)
    state = load_reviewed_distinct_state(config)
    candidate_pairs = {
        item.slugs
        for item in raw.findings
        if item.kind == "initial-full-name-candidate"
    }
    valid, stale = reviewed_distinct_status(mapping, candidate_pairs, state)
    findings = tuple(
        item
        for item in raw.findings
        if not (
            item.kind == "initial-full-name-candidate"
            and item.slugs in valid
        )
    )
    return AuthorHygieneReport(
        identities=raw.identities,
        name_variants=raw.name_variants,
        findings=findings,
        reviewed_distinct=len(valid),
        stale_reviewed_distinct=stale,
    )


@dataclass(frozen=True)
class ProjectAuthorIdentityMutationPlan:
    """One explicit author identity merge or rename."""

    operation: str
    source_slug: str
    target_slug: str
    outputs: Mapping[Path, bytes]

    @property
    def changed(self) -> bool:
        return bool(self.outputs)

    def summary(self) -> str:
        verb = "Merge" if self.operation == "merge" else "Rename"
        arrow = "into" if self.operation == "merge" else "to"
        return f"{verb} author identity {self.source_slug} {arrow} {self.target_slug}"


def plan_project_author_identity_mutation(
    config: BibReviewConfig,
    *,
    operation: str,
    source_slug: str,
    target_slug: str,
) -> ProjectAuthorIdentityMutationPlan:
    """Plan an explicit human-approved author merge or slug rename."""
    mapping = (
        read_json(config.paths.author_mappings, dict)
        if config.paths.author_mappings.exists()
        else {}
    )
    if operation == "merge":
        updated = merge_author_identities(
            mapping,
            source_slug=source_slug,
            target_slug=target_slug,
        )
    elif operation == "rename":
        updated = rename_author_identity(
            mapping,
            source_slug=source_slug,
            target_slug=target_slug,
        )
    else:
        raise ValueError(f"unsupported author identity operation: {operation}")

    outputs: dict[Path, bytes] = {
        config.paths.author_mappings: json_bytes(updated)
    }
    decision_path = author_distinct_path(config)
    if decision_path.exists():
        state = load_reviewed_distinct_state(config)
        reconciled = reconcile_reviewed_distinct_state(
            state,
            operation=operation,
            source_slug=source_slug,
            target_slug=target_slug,
            updated_mapping=updated,
        )
        outputs[decision_path] = reviewed_distinct_bytes(reconciled)

    return ProjectAuthorIdentityMutationPlan(
        operation=operation,
        source_slug=source_slug,
        target_slug=target_slug,
        outputs=MappingProxyType(outputs),
    )


@dataclass(frozen=True)
class ProjectAuthorVariantRemovalPlan:
    """One explicit removal of an unused author name variant."""

    slug: str
    variant: str
    outputs: Mapping[Path, bytes]

    @property
    def changed(self) -> bool:
        return bool(self.outputs)

    def summary(self) -> str:
        return f"Remove author variant {self.variant!r} from {self.slug}"


def plan_project_author_variant_removal(
    config: BibReviewConfig,
    *,
    slug: str,
    variant: str,
) -> ProjectAuthorVariantRemovalPlan:
    """Plan removal of one exact variant only when canonical data no longer uses it."""
    mapping = (
        read_json(config.paths.author_mappings, dict)
        if config.paths.author_mappings.exists()
        else {}
    )
    publications = read_bibliography(config.paths.bibliography)
    users = [
        publication
        for publication in publications
        if any(
            _display_contributor(author) == variant.strip()
            for author in publication.authors
        )
    ]
    if users:
        raise ValueError(
            f"author variant {variant.strip()!r} is still used by "
            f"{len(users)} canonical publication(s)"
        )
    updated = remove_author_variant(
        mapping,
        slug=slug,
        variant=variant,
    )
    return ProjectAuthorVariantRemovalPlan(
        slug=slug.strip(),
        variant=variant.strip(),
        outputs=MappingProxyType(
            {config.paths.author_mappings: json_bytes(updated)}
        ),
    )


def apply_project_author_variant_removal(
    plan: ProjectAuthorVariantRemovalPlan,
) -> None:
    """Apply one reviewed unused-variant removal."""
    if plan.outputs:
        atomic_write_batch(plan.outputs)


@dataclass(frozen=True)
class ProjectAuthorDistinctPlan:
    """One explicit KEEP-SEPARATE author identity decision."""

    left_slug: str
    right_slug: str
    outputs: Mapping[Path, bytes]

    @property
    def changed(self) -> bool:
        return bool(self.outputs)

    def summary(self) -> str:
        return (
            f"Mark author identities {self.left_slug} and "
            f"{self.right_slug} as reviewed distinct"
        )


def plan_project_author_distinct(
    config: BibReviewConfig,
    *,
    left_slug: str,
    right_slug: str,
) -> ProjectAuthorDistinctPlan:
    """Plan one explicit reviewed-distinct identity decision."""
    mapping = (
        read_json(config.paths.author_mappings, dict)
        if config.paths.author_mappings.exists()
        else {}
    )
    raw = scan_author_identity_hygiene(mapping)
    candidate_pairs = {
        item.slugs
        for item in raw.findings
        if item.kind == "initial-full-name-candidate"
    }
    state = load_reviewed_distinct_state(config)
    updated = mark_reviewed_distinct(
        mapping,
        state,
        left_slug=left_slug,
        right_slug=right_slug,
        candidate_pairs=candidate_pairs,
    )
    pair = tuple(sorted((left_slug.strip(), right_slug.strip())))
    return ProjectAuthorDistinctPlan(
        left_slug=pair[0],
        right_slug=pair[1],
        outputs=MappingProxyType(
            {author_distinct_path(config): reviewed_distinct_bytes(updated)}
        ),
    )


def apply_project_author_distinct(
    plan: ProjectAuthorDistinctPlan,
) -> None:
    """Apply one reviewed KEEP-SEPARATE identity decision."""
    if plan.outputs:
        atomic_write_batch(plan.outputs)


def apply_project_author_identity_mutation(
    plan: ProjectAuthorIdentityMutationPlan,
) -> None:
    """Apply one previously reviewed author identity mutation."""
    if plan.outputs:
        atomic_write_batch(plan.outputs)
