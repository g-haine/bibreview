"""Reviewed manual import workflow for DOI-less publications."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path
import re
from types import MappingProxyType

import yaml

from .config import BibReviewConfig
from .identity import (
    IdentityError,
    new_publication_id,
    normalize_identifiers,
    validate_publication_id,
)
from .model import Author, Editor, Publication, Reference
from .storage import (
    StorageError,
    atomic_write,
    atomic_write_batch,
    bibliography_document_data,
    json_bytes,
    read_bibliography,
)
from .text import safe_component, slugify


IMPORT_SCHEMA_VERSION = 1
IMPORT_PROVENANCE_KINDS = frozenset({
    "manual",
    "official-import",
    "provider",
})


class ProjectImportError(ValueError):
    """Raised when a reviewed manual import cannot be staged safely."""


@dataclass(frozen=True)
class ImportProvenance:
    """Human-reviewed provenance retained with one manual import."""

    kind: str
    source: str = ""
    note: str = ""


@dataclass(frozen=True)
class ImportManifest:
    """Validated manual-import contract."""

    schema_version: int
    publication: Publication
    provenance: ImportProvenance
    citation: str
    bibtex: str


@dataclass(frozen=True)
class ProjectImportPlan:
    """Read-only description of one manual import staging operation."""

    manifest: ImportManifest
    source: Path
    evidence_path: Path
    bibtex_path: Path
    warnings: tuple[str, ...]
    outputs: Mapping[Path, bytes]

    @property
    def changed(self) -> bool:
        return bool(self.outputs)

    def summary(self) -> str:
        return (
            f"publication: {self.manifest.publication.id}; "
            f"permalink: {self.manifest.publication.permalink}; "
            f"provenance: {self.manifest.provenance.kind}; "
            f"warnings: {len(self.warnings)}"
        )


def _mapping(value, name: str) -> dict:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ProjectImportError(f"{name} must be a mapping")
    return value


def _string(value, name: str, *, required: bool = False) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise ProjectImportError(f"{name} must be a string")
    result = value.strip()
    if required and not result:
        raise ProjectImportError(f"{name} must not be empty")
    return result


def _string_list(value, name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ProjectImportError(f"{name} must be a list of strings")
    return tuple(item.strip() for item in value if item.strip())


def _contributor(value, name: str, contributor_type):
    item = _mapping(value, name)
    allowed = {"given", "family", "literal", "source_fields"}
    unknown = set(item) - allowed
    if unknown:
        raise ProjectImportError(
            f"{name} contains unsupported field(s): {', '.join(sorted(unknown))}"
        )
    source_fields = _mapping(item.get("source_fields"), f"{name}.source_fields")
    try:
        return contributor_type(
            given=_string(item.get("given"), f"{name}.given") or None,
            family=_string(item.get("family"), f"{name}.family") or None,
            literal=_string(item.get("literal"), f"{name}.literal") or None,
            source_fields=source_fields,
        )
    except ValueError as error:
        raise ProjectImportError(f"{name}: {error}") from error


def _contributors(value, name: str, contributor_type):
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ProjectImportError(f"{name} must be a list")
    return tuple(
        _contributor(item, f"{name}[{index}]", contributor_type)
        for index, item in enumerate(value, 1)
    )


def _created_date(value) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        raise ProjectImportError(
            "publication.created_date must be an ISO date or null"
        )
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise ProjectImportError(
            "publication.created_date must be an ISO date or null"
        ) from error


def _references(value) -> tuple[Reference, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ProjectImportError("publication.references must be a list")
    result: list[Reference] = []
    for index, raw in enumerate(value, 1):
        name = f"publication.references[{index}]"
        item = _mapping(raw, name)
        unknown = set(item) - {"identifiers", "citation"}
        if unknown:
            raise ProjectImportError(
                f"{name} contains unsupported field(s): "
                + ", ".join(sorted(unknown))
            )
        identifiers = _mapping(item.get("identifiers"), f"{name}.identifiers")
        citation = _string(item.get("citation"), f"{name}.citation")
        try:
            result.append(
                Reference(
                    identifiers=identifiers,
                    citation=citation,
                )
            )
        except (IdentityError, ValueError) as error:
            raise ProjectImportError(f"{name}: {error}") from error
    return tuple(result)


def _publication(
    raw,
    *,
    publication_id: str,
) -> Publication:
    value = _mapping(raw, "publication")
    allowed = {
        "identifiers",
        "type",
        "title",
        "authors",
        "editors",
        "abstract",
        "container_title",
        "publication_year",
        "volume",
        "issue",
        "pages",
        "publisher",
        "event",
        "keywords",
        "created_date",
        "permalink",
        "references",
    }
    unknown = set(value) - allowed
    if unknown:
        raise ProjectImportError(
            "publication contains unsupported field(s): "
            + ", ".join(sorted(unknown))
        )

    raw_identifiers = _mapping(value.get("identifiers"), "publication.identifiers")
    try:
        identifiers = normalize_identifiers(raw_identifiers)
    except IdentityError as error:
        raise ProjectImportError(f"publication.identifiers: {error}") from error
    if "doi" in identifiers:
        raise ProjectImportError(
            "manual import cannot contain a DOI; DOI is BibReview's sole "
            "strong/automatable identifier and must use the discover/collect "
            "workflow instead"
        )

    title = _string(value.get("title"), "publication.title", required=True)
    permalink = _string(value.get("permalink"), "publication.permalink")
    if not permalink:
        permalink = slugify(title)
    try:
        safe_component(permalink)
    except ValueError as error:
        raise ProjectImportError(f"publication.permalink: {error}") from error

    authors = _contributors(value.get("authors"), "publication.authors", Author)
    editors = _contributors(value.get("editors"), "publication.editors", Editor)

    try:
        return Publication(
            id=publication_id,
            identifiers=identifiers,
            type=_string(value.get("type"), "publication.type", required=True),
            title=title,
            authors=authors,
            editors=editors,
            abstract=_string(value.get("abstract"), "publication.abstract"),
            container_title=_string(
                value.get("container_title"),
                "publication.container_title",
            ),
            publication_year=_string(
                value.get("publication_year"),
                "publication.publication_year",
                required=True,
            ),
            volume=_string(value.get("volume"), "publication.volume"),
            issue=_string(value.get("issue"), "publication.issue"),
            pages=_string(value.get("pages"), "publication.pages"),
            publisher=_string(value.get("publisher"), "publication.publisher"),
            event=_string(value.get("event"), "publication.event"),
            keywords=_string_list(value.get("keywords"), "publication.keywords"),
            created_date=_created_date(value.get("created_date")),
            permalink=permalink,
            references=_references(value.get("references")),
        )
    except (IdentityError, ValueError) as error:
        raise ProjectImportError(f"publication: {error}") from error


def _provenance(raw) -> ImportProvenance:
    value = _mapping(raw, "provenance")
    unknown = set(value) - {"kind", "source", "note"}
    if unknown:
        raise ProjectImportError(
            "provenance contains unsupported field(s): "
            + ", ".join(sorted(unknown))
        )
    kind = _string(value.get("kind"), "provenance.kind", required=True)
    if kind not in IMPORT_PROVENANCE_KINDS:
        expected = ", ".join(sorted(IMPORT_PROVENANCE_KINDS))
        raise ProjectImportError(
            f"provenance.kind must be one of: {expected}"
        )
    source = _string(value.get("source"), "provenance.source")
    note = _string(value.get("note"), "provenance.note")
    if kind != "manual" and not source:
        raise ProjectImportError(
            f"provenance.source is required for {kind!r} imports"
        )
    return ImportProvenance(kind=kind, source=source, note=note)


def _bibtex(value) -> str:
    text = _string(value, "bibtex", required=True)
    if re.search(r"(?m)^\s*@", text) is None:
        raise ProjectImportError("bibtex must contain at least one BibTeX entry")
    return text.rstrip() + "\n"


def load_import_manifest(path: Path | str) -> ImportManifest:
    """Read and validate one reviewed manual-import YAML contract."""

    source = Path(path)
    try:
        raw = yaml.safe_load(source.read_text(encoding="utf-8"))
    except OSError:
        raise
    except (UnicodeError, yaml.YAMLError) as error:
        raise ProjectImportError(f"{source}: {error}") from error

    root = _mapping(raw, "import manifest")
    allowed = {
        "schema_version",
        "id",
        "provenance",
        "citation",
        "publication",
        "bibtex",
    }
    unknown = set(root) - allowed
    if unknown:
        raise ProjectImportError(
            "import manifest contains unsupported field(s): "
            + ", ".join(sorted(unknown))
        )
    missing = {"schema_version", "id", "provenance", "publication", "bibtex"} - set(root)
    if missing:
        raise ProjectImportError(
            "import manifest missing field(s): "
            + ", ".join(sorted(missing))
        )

    schema_version = root["schema_version"]
    if schema_version != IMPORT_SCHEMA_VERSION:
        raise ProjectImportError(
            f"schema_version must be {IMPORT_SCHEMA_VERSION}"
        )

    try:
        publication_id = validate_publication_id(root["id"])
    except IdentityError as error:
        raise ProjectImportError(f"id: {error}") from error

    publication = _publication(
        root["publication"],
        publication_id=publication_id,
    )
    return ImportManifest(
        schema_version=schema_version,
        publication=publication,
        provenance=_provenance(root["provenance"]),
        citation=_string(root.get("citation"), "citation"),
        bibtex=_bibtex(root["bibtex"]),
    )


def import_manifest_data(manifest: ImportManifest) -> dict:
    """Return the normalized durable YAML representation of an import."""

    publication = manifest.publication
    result = {
        "schema_version": manifest.schema_version,
        "id": publication.id,
        "provenance": {
            "kind": manifest.provenance.kind,
            "source": manifest.provenance.source,
            "note": manifest.provenance.note,
        },
        "citation": manifest.citation,
        "publication": {
            "identifiers": dict(publication.identifiers),
            "type": publication.type,
            "title": publication.title,
            "authors": [
                {
                    "given": author.given,
                    "family": author.family,
                    "literal": author.literal,
                    "source_fields": dict(author.source_fields),
                }
                for author in publication.authors
            ],
            "editors": [
                {
                    "given": editor.given,
                    "family": editor.family,
                    "literal": editor.literal,
                    "source_fields": dict(editor.source_fields),
                }
                for editor in publication.editors
            ],
            "abstract": publication.abstract,
            "container_title": publication.container_title,
            "publication_year": publication.publication_year,
            "volume": publication.volume,
            "issue": publication.issue,
            "pages": publication.pages,
            "publisher": publication.publisher,
            "event": publication.event,
            "keywords": list(publication.keywords),
            "created_date": (
                publication.created_date.isoformat()
                if publication.created_date is not None
                else None
            ),
            "permalink": publication.permalink,
            "references": [
                {
                    "identifiers": dict(reference.identifiers),
                    "citation": reference.citation,
                }
                for reference in publication.references
            ],
        },
        "bibtex": manifest.bibtex,
    }
    return result


def import_manifest_bytes(manifest: ImportManifest) -> bytes:
    """Serialize one validated import contract deterministically."""

    text = yaml.safe_dump(
        import_manifest_data(manifest),
        sort_keys=False,
        allow_unicode=True,
        width=100,
    )
    return text.encode("utf-8")


def initial_import_manifest_bytes(publication_id: str) -> bytes:
    """Return a human-editable import skeleton with one persistent UUID."""

    try:
        canonical_id = validate_publication_id(publication_id)
    except IdentityError as error:
        raise ProjectImportError(str(error)) from error

    value = {
        "schema_version": IMPORT_SCHEMA_VERSION,
        "id": canonical_id,
        "provenance": {
            "kind": "manual",
            "source": "",
            "note": "",
        },
        "citation": "",
        "publication": {
            "identifiers": {},
            "type": "",
            "title": "",
            "authors": [],
            "editors": [],
            "abstract": "",
            "container_title": "",
            "publication_year": "",
            "volume": "",
            "issue": "",
            "pages": "",
            "publisher": "",
            "event": "",
            "keywords": [],
            "created_date": None,
            "permalink": "",
            "references": [],
        },
        "bibtex": "",
    }
    return yaml.safe_dump(
        value,
        sort_keys=False,
        allow_unicode=True,
        width=100,
    ).encode("utf-8")


def initialize_import_manifest(path: Path | str) -> str:
    """Create a new reviewed import manifest with a persistent UUID."""

    destination = Path(path)
    if destination.exists():
        raise ProjectImportError(
            f"{destination}: refusing to overwrite an existing import manifest"
        )
    publication_id = new_publication_id()
    atomic_write(destination, initial_import_manifest_bytes(publication_id))
    return publication_id


def _optional_bibliography(path: Path) -> tuple[Publication, ...]:
    if not path.exists():
        return ()
    return read_bibliography(path)


def _duplicate_warnings(
    candidate: Publication,
    existing: tuple[Publication, ...],
) -> tuple[str, ...]:
    warnings: list[str] = []
    candidate_title = slugify(candidate.title)
    auxiliary = dict(candidate.identifiers)

    for publication in existing:
        if publication.id == candidate.id:
            raise ProjectImportError(
                f"publication UUID {candidate.id} already exists in canonical bibliography"
            )
        if publication.permalink == candidate.permalink:
            raise ProjectImportError(
                f"publication permalink {candidate.permalink!r} already belongs to "
                f"canonical publication {publication.id}"
            )

        shared = sorted(
            (name, value)
            for name, value in auxiliary.items()
            if publication.identifiers.get(name) == value
        )
        for name, value in shared:
            warnings.append(
                f"possible duplicate: auxiliary identifier {name}:{value} "
                f"matches canonical publication {publication.id}"
            )

        if (
            candidate_title
            and slugify(publication.title) == candidate_title
            and publication.publication_year == candidate.publication_year
        ):
            warnings.append(
                "possible duplicate: normalized title/year matches canonical "
                f"publication {publication.id}"
            )

    return tuple(dict.fromkeys(warnings))


def _put_if_changed(outputs: dict[Path, bytes], path: Path, content: bytes) -> None:
    if path.exists() and path.read_bytes() == content:
        return
    outputs[path] = content


def plan_project_import(
    config: BibReviewConfig,
    manifest_path: Path | str,
) -> ProjectImportPlan:
    """Plan one reviewed DOI-less import without mutating project state."""

    source = Path(manifest_path)
    manifest = load_import_manifest(source)
    paths = config.paths

    staged = _optional_bibliography(paths.collected)
    if staged:
        raise ProjectImportError(
            f"{paths.collected}: contains {len(staged)} staged publication(s); "
            "merge the existing batch before importing another publication"
        )

    existing = _optional_bibliography(paths.bibliography)
    warnings = _duplicate_warnings(manifest.publication, existing)

    evidence_path = paths.imports / f"{manifest.publication.id}.yml"
    evidence = import_manifest_bytes(manifest)
    if evidence_path.exists() and evidence_path.read_bytes() != evidence:
        raise ProjectImportError(
            f"{evidence_path}: existing import evidence differs for the same UUID"
        )

    bibtex_path = paths.bibtex / f"{manifest.publication.permalink}.bib"
    bibtex = manifest.bibtex.encode("utf-8")
    if bibtex_path.exists() and bibtex_path.read_bytes() != bibtex:
        raise ProjectImportError(
            f"{bibtex_path}: refusing to overwrite different tracked BibTeX"
        )

    outputs: dict[Path, bytes] = {
        paths.collected: json_bytes(
            bibliography_document_data((manifest.publication,))
        )
    }
    _put_if_changed(outputs, evidence_path, evidence)
    _put_if_changed(outputs, bibtex_path, bibtex)

    return ProjectImportPlan(
        manifest=manifest,
        source=source,
        evidence_path=evidence_path,
        bibtex_path=bibtex_path,
        warnings=warnings,
        outputs=MappingProxyType(outputs),
    )


def apply_project_import(plan: ProjectImportPlan) -> None:
    """Apply one previously validated manual import plan."""

    if not isinstance(plan, ProjectImportPlan):
        raise ProjectImportError("plan must be a ProjectImportPlan")
    if plan.outputs:
        atomic_write_batch(plan.outputs)
