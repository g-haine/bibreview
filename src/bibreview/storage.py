"""Project-state storage helpers with explicit, atomic file replacement."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime
import json
import os
from pathlib import Path
import stat
import tempfile
from typing import Any, Iterable, Mapping

from .model import Author, Editor, Publication, Reference


class StorageError(ValueError):
    """Raised when persisted project state cannot be read or represented safely."""


BIBLIOGRAPHY_SCHEMA_VERSION = 2
_SUPPORTED_BIBLIOGRAPHY_SCHEMA_VERSIONS = frozenset({1, 2})


@dataclass(frozen=True)
class BibliographyMetadata:
    """Global metadata attached to one persisted bibliography document."""

    schema_version: int = BIBLIOGRAPHY_SCHEMA_VERSION
    last_update: date | None = None


@dataclass(frozen=True)
class BibliographyDocument:
    """Canonical bibliography document with global metadata and publications."""

    metadata: BibliographyMetadata
    publications: tuple[Publication, ...]


def read_json(path: Path | str, expected_type: type) -> Any:
    """Read UTF-8 JSON and validate its root type."""
    source = Path(path)
    try:
        value = json.loads(source.read_text(encoding="utf-8"))
    except OSError:
        raise
    except (ValueError, UnicodeError) as error:
        raise StorageError(f"{source}: {error}") from error
    if not isinstance(value, expected_type):
        raise StorageError(f"{source}: expected {expected_type.__name__}")
    return value


def json_bytes(value: Any) -> bytes:
    """Serialize project JSON deterministically using the established readable format."""
    try:
        text = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)
    except (TypeError, ValueError) as error:
        raise StorageError(f"value is not valid JSON project state: {error}") from error
    return (text + "\n").encode("utf-8")


def atomic_write(path: Path | str, content: bytes) -> None:
    """Replace one file atomically after staging it beside the destination."""
    atomic_write_batch({Path(path): content})


def atomic_write_batch(outputs: Mapping[Path | str, bytes]) -> None:
    """Stage every output before atomically replacing each destination.

    Staging is all-or-nothing: if any temporary file cannot be prepared, no
    destination is replaced. Replacement itself is atomic per file, not a
    filesystem transaction across the complete batch; concurrent writers are
    therefore unsupported.
    """
    prepared: list[tuple[Path, bytes]] = []
    resolved: set[Path] = set()
    for raw_path, content in outputs.items():
        destination = Path(raw_path)
        if not isinstance(content, bytes):
            raise StorageError(f"{destination}: batch content must be bytes")
        key = destination.resolve()
        if key in resolved:
            raise StorageError(f"duplicate output path: {destination}")
        resolved.add(key)
        prepared.append((destination, content))

    staged: list[tuple[Path, Path]] = []
    try:
        for destination, content in prepared:
            destination.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                dir=destination.parent,
                prefix=f".{destination.name}.",
                delete=False,
            ) as stream:
                temporary = Path(stream.name)
                stream.write(content)
            mode = stat.S_IMODE(destination.stat().st_mode) if destination.exists() else 0o644
            temporary.chmod(mode)
            staged.append((temporary, destination))
        for temporary, destination in staged:
            os.replace(temporary, destination)
    finally:
        for temporary, _ in staged:
            temporary.unlink(missing_ok=True)


def write_json(path: Path | str, value: Any) -> None:
    """Serialize and atomically replace one JSON project-state file."""
    atomic_write(path, json_bytes(value))


def backup_path(directory: Path | str, prefix: str, suffix: str) -> Path:
    """Return a timestamped, collision-safe backup path without creating it."""
    root = Path(directory)
    stamp = datetime.now().astimezone().strftime("%Y-%m-%dT%H-%M-%S%z")
    candidate = root / f"{prefix}-{stamp}{suffix}"
    count = 0
    while candidate.exists():
        count += 1
        candidate = root / f"{prefix}-{stamp}-{count}{suffix}"
    return candidate


def publication_data(publication: Publication) -> dict[str, Any]:
    """Convert one canonical publication to the persisted BibReview JSON shape."""
    if not isinstance(publication, Publication):
        raise StorageError("bibliography entries must be Publication objects")
    return {
        "id": publication.id,
        "identifiers": dict(publication.identifiers),
        "type": publication.type,
        "title": publication.title,
        "authors": [
            {
                "given": author.given,
                "family": author.family,
                "literal": author.literal,
                "source_fields": deepcopy(dict(author.source_fields)),
            }
            for author in publication.authors
        ],
        "editors": [
            {
                "given": editor.given,
                "family": editor.family,
                "literal": editor.literal,
                "source_fields": deepcopy(dict(editor.source_fields)),
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
        "created_date": publication.created_date.isoformat() if publication.created_date else None,
        "permalink": publication.permalink,
        "references": [
            {
                "identifiers": dict(reference.identifiers),
                "citation": reference.citation,
            }
            for reference in publication.references
        ],
    }


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise StorageError(f"{name} must be an object")
    return value


def _string(value: Any, name: str, *, nullable: bool = False) -> str | None:
    if nullable and value is None:
        return None
    if not isinstance(value, str):
        raise StorageError(f"{name} must be a string" + (" or null" if nullable else ""))
    return value


def publication_from_data(
    value: Mapping[str, Any],
    *,
    schema_version: int = BIBLIOGRAPHY_SCHEMA_VERSION,
) -> Publication:
    """Build and validate one Publication from canonical persisted JSON data."""
    if schema_version not in _SUPPORTED_BIBLIOGRAPHY_SCHEMA_VERSIONS:
        raise StorageError(f"unsupported bibliography schema version: {schema_version}")
    record = _mapping(value, "publication")
    required = {
        "id", "identifiers", "type", "title", "authors", "abstract",
        "container_title", "publication_year", "volume", "issue", "pages",
        "publisher", "event", "keywords", "created_date", "permalink", "references",
    }
    if schema_version >= 2:
        required.add("editors")
    missing = required - record.keys()
    unknown = record.keys() - required
    if missing:
        raise StorageError(f"publication missing fields: {', '.join(sorted(missing))}")
    if unknown:
        raise StorageError(f"publication has unknown fields: {', '.join(sorted(unknown))}")

    identifiers = _mapping(record["identifiers"], "publication.identifiers")
    if any(not isinstance(key, str) or not isinstance(item, str) for key, item in identifiers.items()):
        raise StorageError("publication.identifiers must map strings to strings")

    raw_authors = record["authors"]
    if not isinstance(raw_authors, list):
        raise StorageError("publication.authors must be a list")
    authors: list[Author] = []
    for index, raw_author in enumerate(raw_authors, 1):
        author = _mapping(raw_author, f"publication.authors[{index}]")
        if set(author) != {"given", "family", "literal", "source_fields"}:
            raise StorageError(
                f"publication.authors[{index}] must contain given, family, literal, and source_fields"
            )
        source_fields = _mapping(author["source_fields"], f"publication.authors[{index}].source_fields")
        authors.append(
            Author(
                given=_string(author["given"], f"publication.authors[{index}].given", nullable=True),
                family=_string(author["family"], f"publication.authors[{index}].family", nullable=True),
                literal=_string(author["literal"], f"publication.authors[{index}].literal", nullable=True),
                source_fields=deepcopy(dict(source_fields)),
            )
        )

    raw_editors = record["editors"] if schema_version >= 2 else []
    if not isinstance(raw_editors, list):
        raise StorageError("publication.editors must be a list")
    editors: list[Editor] = []
    for index, raw_editor in enumerate(raw_editors, 1):
        editor = _mapping(raw_editor, f"publication.editors[{index}]")
        if set(editor) != {"given", "family", "literal", "source_fields"}:
            raise StorageError(
                f"publication.editors[{index}] must contain given, family, literal, and source_fields"
            )
        source_fields = _mapping(
            editor["source_fields"],
            f"publication.editors[{index}].source_fields",
        )
        editors.append(
            Editor(
                given=_string(
                    editor["given"],
                    f"publication.editors[{index}].given",
                    nullable=True,
                ),
                family=_string(
                    editor["family"],
                    f"publication.editors[{index}].family",
                    nullable=True,
                ),
                literal=_string(
                    editor["literal"],
                    f"publication.editors[{index}].literal",
                    nullable=True,
                ),
                source_fields=deepcopy(dict(source_fields)),
            )
        )

    raw_keywords = record["keywords"]
    if not isinstance(raw_keywords, list) or any(not isinstance(item, str) for item in raw_keywords):
        raise StorageError("publication.keywords must be a list of strings")

    raw_references = record["references"]
    if not isinstance(raw_references, list):
        raise StorageError("publication.references must be a list")
    references: list[Reference] = []
    for index, raw_reference in enumerate(raw_references, 1):
        reference = _mapping(raw_reference, f"publication.references[{index}]")
        if set(reference) != {"identifiers", "citation"}:
            raise StorageError(
                f"publication.references[{index}] must contain identifiers and citation"
            )
        ref_identifiers = _mapping(
            reference["identifiers"], f"publication.references[{index}].identifiers"
        )
        if any(not isinstance(key, str) or not isinstance(item, str)
               for key, item in ref_identifiers.items()):
            raise StorageError(
                f"publication.references[{index}].identifiers must map strings to strings"
            )
        references.append(
            Reference(
                identifiers=dict(ref_identifiers),
                citation=_string(reference["citation"], f"publication.references[{index}].citation"),
            )
        )

    raw_date = record["created_date"]
    if raw_date is None:
        created_date = None
    elif isinstance(raw_date, str):
        try:
            created_date = date.fromisoformat(raw_date)
        except ValueError as error:
            raise StorageError("publication.created_date must be an ISO date or null") from error
    else:
        raise StorageError("publication.created_date must be an ISO date or null")

    try:
        return Publication(
            id=_string(record["id"], "publication.id"),
            identifiers=dict(identifiers),
            type=_string(record["type"], "publication.type"),
            title=_string(record["title"], "publication.title"),
            authors=tuple(authors),
            editors=tuple(editors),
            abstract=_string(record["abstract"], "publication.abstract"),
            container_title=_string(record["container_title"], "publication.container_title"),
            publication_year=_string(record["publication_year"], "publication.publication_year"),
            volume=_string(record["volume"], "publication.volume"),
            issue=_string(record["issue"], "publication.issue"),
            pages=_string(record["pages"], "publication.pages"),
            publisher=_string(record["publisher"], "publication.publisher"),
            event=_string(record["event"], "publication.event"),
            keywords=tuple(raw_keywords),
            created_date=created_date,
            permalink=_string(record["permalink"], "publication.permalink"),
            references=tuple(references),
        )
    except ValueError as error:
        raise StorageError(f"invalid publication: {error}") from error


def bibliography_data(publications: Iterable[Publication]) -> list[dict[str, Any]]:
    """Convert canonical publications to deterministic persisted JSON data."""
    return [publication_data(publication) for publication in publications]


def bibliography_metadata_data(metadata: BibliographyMetadata) -> dict[str, Any]:
    """Convert bibliography metadata to persisted JSON data."""
    if not isinstance(metadata, BibliographyMetadata):
        raise StorageError("metadata must be BibliographyMetadata")
    if metadata.schema_version != BIBLIOGRAPHY_SCHEMA_VERSION:
        raise StorageError(
            "unsupported bibliography schema version: "
            f"{metadata.schema_version}"
        )
    return {
        "schema_version": metadata.schema_version,
        "last_update": (
            metadata.last_update.isoformat()
            if metadata.last_update is not None
            else None
        ),
    }


def bibliography_document_data(
    publications: Iterable[Publication],
    *,
    metadata: BibliographyMetadata | None = None,
) -> dict[str, Any]:
    """Convert a canonical bibliography document to persisted JSON data."""
    metadata = metadata or BibliographyMetadata()
    return {
        "metadata": bibliography_metadata_data(metadata),
        "publications": bibliography_data(publications),
    }


def _bibliography_metadata_from_data(value: Any) -> BibliographyMetadata:
    metadata = _mapping(value, "bibliography.metadata")
    required = {"schema_version", "last_update"}
    missing = required - metadata.keys()
    unknown = metadata.keys() - required
    if missing:
        raise StorageError(
            "bibliography.metadata missing fields: "
            + ", ".join(sorted(missing))
        )
    if unknown:
        raise StorageError(
            "bibliography.metadata has unknown fields: "
            + ", ".join(sorted(unknown))
        )

    schema_version = metadata["schema_version"]
    if (
        not isinstance(schema_version, int)
        or isinstance(schema_version, bool)
        or schema_version not in _SUPPORTED_BIBLIOGRAPHY_SCHEMA_VERSIONS
    ):
        supported = ", ".join(
            str(value)
            for value in sorted(_SUPPORTED_BIBLIOGRAPHY_SCHEMA_VERSIONS)
        )
        raise StorageError(
            "bibliography.metadata.schema_version must be one of "
            f"{supported}"
        )

    raw_last_update = metadata["last_update"]
    if raw_last_update is None:
        last_update = None
    elif isinstance(raw_last_update, str):
        try:
            last_update = date.fromisoformat(raw_last_update)
        except ValueError as error:
            raise StorageError(
                "bibliography.metadata.last_update must be an ISO date or null"
            ) from error
    else:
        raise StorageError(
            "bibliography.metadata.last_update must be an ISO date or null"
        )

    return BibliographyMetadata(
        schema_version=schema_version,
        last_update=last_update,
    )


def _publications_from_records(
    records: Any,
    *,
    path: Path | str,
    schema_version: int,
) -> tuple[Publication, ...]:
    if not isinstance(records, list):
        raise StorageError(f"{path}: bibliography.publications must be a list")
    result: list[Publication] = []
    for index, record in enumerate(records, 1):
        try:
            result.append(
                publication_from_data(record, schema_version=schema_version)
            )
        except StorageError as error:
            raise StorageError(f"{path}: record {index}: {error}") from error
    return tuple(result)


def read_bibliography_document(path: Path | str) -> BibliographyDocument:
    """Read a canonical bibliography document.

    The earlier bare-list representation remains readable for backward data
    compatibility and is interpreted as a document with empty metadata. Writers
    always emit the document representation.
    """
    source = Path(path)
    try:
        value = json.loads(source.read_text(encoding="utf-8"))
    except OSError:
        raise
    except (ValueError, UnicodeError) as error:
        raise StorageError(f"{source}: {error}") from error

    if isinstance(value, list):
        return BibliographyDocument(
            metadata=BibliographyMetadata(),
            publications=_publications_from_records(
                value,
                path=source,
                schema_version=1,
            ),
        )
    if not isinstance(value, Mapping):
        raise StorageError(f"{source}: expected bibliography document object")
    required = {"metadata", "publications"}
    missing = required - value.keys()
    unknown = value.keys() - required
    if missing:
        raise StorageError(
            f"{source}: bibliography document missing fields: "
            + ", ".join(sorted(missing))
        )
    if unknown:
        raise StorageError(
            f"{source}: bibliography document has unknown fields: "
            + ", ".join(sorted(unknown))
        )
    source_metadata = _bibliography_metadata_from_data(value["metadata"])
    publications = _publications_from_records(
        value["publications"],
        path=source,
        schema_version=source_metadata.schema_version,
    )
    return BibliographyDocument(
        metadata=BibliographyMetadata(
            schema_version=BIBLIOGRAPHY_SCHEMA_VERSION,
            last_update=source_metadata.last_update,
        ),
        publications=publications,
    )


def read_bibliography(path: Path | str) -> tuple[Publication, ...]:
    """Read canonical publications, ignoring document-level metadata."""
    return read_bibliography_document(path).publications


def write_bibliography(
    path: Path | str,
    publications: Iterable[Publication],
    *,
    metadata: BibliographyMetadata | None = None,
) -> None:
    """Atomically write a canonical bibliography document."""
    write_json(
        path,
        bibliography_document_data(publications, metadata=metadata),
    )


def write_bibliography_document(
    path: Path | str,
    document: BibliographyDocument,
) -> None:
    """Atomically write one complete canonical bibliography document."""
    if not isinstance(document, BibliographyDocument):
        raise StorageError("document must be BibliographyDocument")
    write_bibliography(
        path,
        document.publications,
        metadata=document.metadata,
    )
