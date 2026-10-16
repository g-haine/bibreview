"""Durable bibliographic identifier aliases kept separate from relevance state."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from .config import BibReviewConfig
from .identity import normalize_doi
from .storage import StorageError, json_bytes, read_json


IDENTIFIER_ALIASES_SCHEMA_VERSION = 1
_ALIAS_REASON = "superseded"


@dataclass(frozen=True)
class DoiAlias:
    """One reviewed DOI identity replacement."""

    doi: str
    replacement: str
    reason: str = _ALIAS_REASON

    def data(self) -> dict[str, str]:
        return {
            "doi": self.doi,
            "replacement": self.replacement,
            "reason": self.reason,
        }


def identifier_alias_path(config: BibReviewConfig) -> Path:
    """Return the project-local durable alias ledger path."""

    return config.paths.known.with_name("identifier_aliases.json")


def _validate_aliases(aliases: Mapping[str, DoiAlias]) -> None:
    for source in aliases:
        seen: set[str] = set()
        current = source
        while current in aliases:
            if current in seen:
                raise StorageError(
                    f"DOI alias cycle detected at {current}"
                )
            seen.add(current)
            current = aliases[current].replacement


def read_doi_aliases(config: BibReviewConfig) -> Mapping[str, DoiAlias]:
    """Read and strictly validate the DOI alias ledger."""

    path = identifier_alias_path(config)
    if not path.exists():
        return MappingProxyType({})

    value = read_json(path, dict)
    if value.get("schema_version") != IDENTIFIER_ALIASES_SCHEMA_VERSION:
        raise StorageError(
            "unsupported identifier aliases schema_version: "
            f"{value.get('schema_version')!r}"
        )
    raw_aliases = value.get("doi_aliases")
    if not isinstance(raw_aliases, list):
        raise StorageError("identifier aliases doi_aliases must be a list")

    result: dict[str, DoiAlias] = {}
    for index, raw in enumerate(raw_aliases, 1):
        if not isinstance(raw, Mapping):
            raise StorageError(
                f"identifier alias {index} must be an object"
            )
        unknown = raw.keys() - {"doi", "replacement", "reason"}
        missing = {"doi", "replacement", "reason"} - raw.keys()
        if missing:
            raise StorageError(
                f"identifier alias {index} missing fields: "
                + ", ".join(sorted(missing))
            )
        if unknown:
            raise StorageError(
                f"identifier alias {index} has unknown fields: "
                + ", ".join(sorted(unknown))
            )
        doi = raw["doi"]
        replacement = raw["replacement"]
        reason = raw["reason"]
        if not isinstance(doi, str) or not isinstance(replacement, str):
            raise StorageError(
                f"identifier alias {index} DOI values must be strings"
            )
        if not isinstance(reason, str) or not reason.strip():
            raise StorageError(
                f"identifier alias {index}.reason must be a non-empty string"
            )
        try:
            normalized = normalize_doi(doi)
            normalized_replacement = normalize_doi(replacement)
        except ValueError as error:
            raise StorageError(
                f"identifier alias {index}: {error}"
            ) from error
        if normalized == normalized_replacement:
            raise StorageError(
                f"{normalized}: DOI alias cannot replace itself"
            )
        if normalized in result:
            raise StorageError(
                f"{normalized}: duplicate DOI alias source"
            )
        result[normalized] = DoiAlias(
            doi=normalized,
            replacement=normalized_replacement,
            reason=reason.strip(),
        )

    _validate_aliases(result)
    return MappingProxyType(result)


def resolve_doi_alias(
    aliases: Mapping[str, DoiAlias],
    doi: str,
) -> str:
    """Resolve a DOI through the alias ledger, rejecting cycles."""

    current = normalize_doi(doi)
    seen: set[str] = set()
    while current in aliases:
        if current in seen:
            raise StorageError(f"DOI alias cycle detected at {current}")
        seen.add(current)
        current = aliases[current].replacement
    return current


def record_doi_alias(
    aliases: Mapping[str, DoiAlias],
    *,
    doi: str,
    replacement: str,
) -> Mapping[str, DoiAlias]:
    """Return alias state with one idempotent reviewed replacement recorded."""

    source = normalize_doi(doi)
    target = resolve_doi_alias(aliases, replacement)
    if source == target:
        raise ValueError(f"{source}: DOI alias cannot replace itself")

    existing = aliases.get(source)
    if existing is not None:
        existing_target = resolve_doi_alias(aliases, existing.replacement)
        if existing_target == target:
            return aliases
        raise ValueError(
            f"{source}: DOI alias already points to {existing_target}"
        )

    updated = dict(aliases)
    updated[source] = DoiAlias(
        doi=source,
        replacement=target,
        reason=_ALIAS_REASON,
    )
    _validate_aliases(updated)
    return MappingProxyType(updated)


def doi_aliases_data(
    aliases: Mapping[str, DoiAlias],
) -> dict[str, object]:
    """Return deterministic JSON-ready alias state."""

    return {
        "schema_version": IDENTIFIER_ALIASES_SCHEMA_VERSION,
        "doi_aliases": [
            aliases[doi].data()
            for doi in sorted(aliases)
        ],
    }


def doi_aliases_bytes(
    aliases: Mapping[str, DoiAlias],
) -> bytes:
    """Serialize alias state using BibReview's canonical JSON formatting."""

    return json_bytes(doi_aliases_data(aliases))
