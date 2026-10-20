"""Stage one reviewed publication-scoped contributor name correction."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from .bibtex_edit import BibtexEditError, update_bibtex_fields
from .config import BibReviewConfig
from .identity import normalize_doi
from .model import Author, Editor, Publication
from .pipeline.authors import author_name
from .project import ProjectStateError
from .reviewed_fields import bibtex_value
from .storage import (
    atomic_write_batch,
    backup_path,
    bibliography_document_data,
    json_bytes,
    read_bibliography,
)


@dataclass(frozen=True)
class ProjectContributorCorrectionPlan:
    """Read-only plan for one reviewed contributor correction."""

    publication_id: str
    doi: str
    title: str
    role: str
    before: str
    after: str
    position: int
    outputs: Mapping[Path, bytes]
    bibtex_backup: Path

    def summary(self) -> str:
        return (
            "Publication contributor correction\n"
            f"  DOI         : {self.doi}\n"
            f"  Role        : {self.role}\n"
            f"  Position    : {self.position}\n"
            f"  Current     : {self.before}\n"
            f"  Replacement : {self.after}"
        )

    def data(self) -> dict[str, Any]:
        return {
            "publication_id": self.publication_id,
            "doi": self.doi,
            "title": self.title,
            "role": self.role,
            "position": self.position,
            "before": self.before,
            "after": self.after,
            "bibtex_backup": str(self.bibtex_backup),
        }


def _contributor_name(contributor: Author | Editor) -> str:
    if isinstance(contributor, Author):
        return author_name(contributor)
    literal = (contributor.literal or "").strip()
    if literal:
        return literal
    parts = [
        part.strip()
        for part in (contributor.given or "", contributor.family or "")
        if part.strip()
    ]
    if not parts:
        raise ProjectStateError("editor has no displayable name")
    return " ".join(parts)


def plan_project_contributor_correction(
    config: BibReviewConfig,
    *,
    doi: str,
    role: str,
    source: str,
    replacement: str,
) -> ProjectContributorCorrectionPlan:
    """Plan one exact contributor-name correction into staging plus BibTeX."""
    if role not in {"author", "editor"}:
        raise ProjectStateError("contributor role must be author or editor")
    source = source.strip()
    replacement = replacement.strip()
    if not source or not replacement:
        raise ProjectStateError("contributor source and replacement names must be non-empty")
    if source == replacement:
        raise ProjectStateError("contributor replacement must differ from the current name")

    staged = read_bibliography(config.paths.collected) if config.paths.collected.exists() else ()
    if staged:
        raise ProjectStateError(
            f"{config.paths.collected}: contains {len(staged)} staged publication(s); "
            "merge the existing batch before correcting a contributor"
        )

    normalized = normalize_doi(doi)
    canonical = read_bibliography(config.paths.bibliography)
    matches = [publication for publication in canonical if publication.doi == normalized]
    if len(matches) != 1:
        raise ProjectStateError(
            f"{normalized}: expected exactly one canonical publication, found {len(matches)}"
        )
    publication = matches[0]
    contributors = publication.authors if role == "author" else publication.editors
    names = tuple(_contributor_name(item) for item in contributors)
    positions = [index for index, name in enumerate(names) if name == source]
    if not positions:
        raise ProjectStateError(
            f"{normalized}: {role} {source!r} is not present in the canonical publication"
        )
    if len(positions) != 1:
        raise ProjectStateError(
            f"{normalized}: {role} {source!r} occurs {len(positions)} times; "
            "the target occurrence is ambiguous"
        )

    index = positions[0]
    contributor_type = Author if role == "author" else Editor
    revised_contributors = list(contributors)
    revised_contributors[index] = contributor_type(
        literal=replacement,
        source_fields=contributors[index].source_fields,
    )
    revised = replace(
        publication,
        **{("authors" if role == "author" else "editors"): tuple(revised_contributors)},
    )

    if not publication.permalink:
        raise ProjectStateError(f"{normalized}: publication has no permalink for BibTeX update")
    bibtex_path = config.paths.bibtex / f"{publication.permalink}.bib"
    try:
        text = bibtex_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise ProjectStateError(
            f"{bibtex_path}: tracked BibTeX is required for contributor correction: {error}"
        ) from error

    field = "author" if role == "author" else "editor"
    rendered_names = tuple(_contributor_name(item) for item in revised_contributors)
    try:
        revised_bibtex = update_bibtex_fields(
            text,
            {field: bibtex_value(field + "s", rendered_names)},
        )
    except BibtexEditError as error:
        raise ProjectStateError(
            f"{bibtex_path}: cannot synchronize contributor correction safely: {error}"
        ) from error

    archive = backup_path(config.paths.archive, publication.permalink, ".bib")
    outputs = {
        config.paths.collected: json_bytes(
            bibliography_document_data((revised,))
        ),
        archive: text.encode("utf-8"),
        bibtex_path: revised_bibtex.encode("utf-8"),
    }
    return ProjectContributorCorrectionPlan(
        publication_id=publication.id,
        doi=normalized,
        title=publication.title,
        role=role,
        before=source,
        after=replacement,
        position=index + 1,
        outputs=MappingProxyType(outputs),
        bibtex_backup=archive,
    )


def apply_project_contributor_correction(
    plan: ProjectContributorCorrectionPlan,
) -> None:
    """Apply one previously prepared contributor correction plan."""
    if not isinstance(plan, ProjectContributorCorrectionPlan):
        raise ProjectStateError("plan must be a ProjectContributorCorrectionPlan")
    atomic_write_batch(plan.outputs)
