"""Merge canonical publications without coupling to project-specific file formats."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Iterable

from ..identity import strong_identifiers
from ..model import Publication


class MergeError(ValueError):
    """Raised when publication identity is ambiguous or inconsistent."""


@dataclass(frozen=True)
class MergeResult:
    """Deterministic result of merging incoming publications into existing state."""

    publications: tuple[Publication, ...]
    added_ids: tuple[str, ...]
    updated_ids: tuple[str, ...]
    unchanged_ids: tuple[str, ...]


def _validate_publications(values: Iterable[Publication], name: str) -> list[Publication]:
    result = list(values)
    if any(not isinstance(publication, Publication) for publication in result):
        raise MergeError(f"{name} must contain Publication objects")
    return result


def _build_indexes(publications: list[Publication]) -> tuple[dict[str, int], dict[tuple[str, str], int]]:
    ids: dict[str, int] = {}
    strong: dict[tuple[str, str], int] = {}
    for index, publication in enumerate(publications):
        if publication.id in ids:
            raise MergeError(f"duplicate publication id: {publication.id}")
        ids[publication.id] = index
        for identifier in strong_identifiers(publication.identifiers):
            if identifier in strong:
                name, value = identifier
                raise MergeError(f"duplicate strong identifier {name}:{value}")
            strong[identifier] = index
    return ids, strong


def merge_publications(
    existing: Iterable[Publication],
    incoming: Iterable[Publication],
) -> MergeResult:
    """Merge publications by internal UUID, then exact approved strong identifiers.

    Existing order is preserved; new publications are appended in incoming order.
    If an incoming publication matches an existing record only by DOI, the
    existing UUID is retained. This protects persistent identity while allowing
    metadata refreshes. DOI-less publications remain distinct unless they share
    the same internal UUID.
    """
    merged = _validate_publications(existing, "existing")
    additions = _validate_publications(incoming, "incoming")
    ids, strong = _build_indexes(merged)

    added_ids: list[str] = []
    updated_ids: list[str] = []
    unchanged_ids: list[str] = []

    for publication in additions:
        matches: set[int] = set()
        if publication.id in ids:
            matches.add(ids[publication.id])
        for identifier in strong_identifiers(publication.identifiers):
            if identifier in strong:
                matches.add(strong[identifier])

        if len(matches) > 1:
            details = ", ".join(
                f"{name}:{value}" for name, value in strong_identifiers(publication.identifiers)
            ) or publication.id
            raise MergeError(
                f"incoming publication identity conflicts across existing records: {details}"
            )

        if not matches:
            index = len(merged)
            if publication.id in ids:
                raise MergeError(f"duplicate publication id: {publication.id}")
            for identifier in strong_identifiers(publication.identifiers):
                if identifier in strong:
                    name, value = identifier
                    raise MergeError(f"duplicate strong identifier {name}:{value}")
            merged.append(publication)
            ids[publication.id] = index
            for identifier in strong_identifiers(publication.identifiers):
                strong[identifier] = index
            added_ids.append(publication.id)
            continue

        index = next(iter(matches))
        previous = merged[index]
        replacement = publication if publication.id == previous.id else replace(publication, id=previous.id)

        for identifier in strong_identifiers(previous.identifiers):
            if strong.get(identifier) == index:
                del strong[identifier]
        if previous.id != replacement.id and ids.get(previous.id) == index:
            del ids[previous.id]

        conflicting_indexes = {
            strong[identifier]
            for identifier in strong_identifiers(replacement.identifiers)
            if identifier in strong and strong[identifier] != index
        }
        if conflicting_indexes:
            name, value = next(
                identifier
                for identifier in strong_identifiers(replacement.identifiers)
                if identifier in strong and strong[identifier] != index
            )
            raise MergeError(f"replacement strong identifier conflicts with another record: {name}:{value}")

        merged[index] = replacement
        ids[replacement.id] = index
        for identifier in strong_identifiers(replacement.identifiers):
            strong[identifier] = index

        if replacement == previous:
            unchanged_ids.append(previous.id)
        else:
            updated_ids.append(previous.id)

    return MergeResult(
        publications=tuple(merged),
        added_ids=tuple(added_ids),
        updated_ids=tuple(updated_ids),
        unchanged_ids=tuple(unchanged_ids),
    )
