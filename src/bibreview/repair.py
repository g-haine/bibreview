"""Reviewed repairs for structurally incomplete provider metadata."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from .config import BibReviewConfig
from .identity import normalize_doi
from .model import Author, Editor
from .pipeline.collect import ContributorRepair
from .storage import read_json, write_json


REPAIR_SCHEMA_VERSION = 1
REPAIR_PROVENANCE = "human-reviewed"


@dataclass(frozen=True)
class ContributorRepairRecord:
    """One explicit human repair tied to exact provider evidence."""

    doi: str
    evidence_fingerprint: str
    reason: str
    provenance: str = REPAIR_PROVENANCE
    authors: tuple[Author, ...] = ()
    editors: tuple[Editor, ...] = ()

    def data(self) -> dict[str, Any]:
        return {
            "doi": self.doi,
            "evidence_fingerprint": self.evidence_fingerprint,
            "reason": self.reason,
            "provenance": self.provenance,
            "authors": [_contributor_data(item) for item in self.authors],
            "editors": [_contributor_data(item) for item in self.editors],
        }


@dataclass(frozen=True)
class ContributorRepairState:
    """Versioned reviewed structural repairs."""

    repairs: tuple[ContributorRepairRecord, ...] = ()

    def data(self) -> dict[str, Any]:
        return {
            "schema_version": REPAIR_SCHEMA_VERSION,
            "repairs": [item.data() for item in self.repairs],
        }


def repair_path(config: BibReviewConfig) -> Path:
    """Return the project-local reviewed repair sidecar."""
    return config.source.parent / "data" / "repairs.json"


def load_contributor_repair_state(
    config: BibReviewConfig,
) -> ContributorRepairState:
    """Load reviewed repairs, or return an empty state when none exists."""
    path = repair_path(config)
    if not path.exists():
        return ContributorRepairState()
    return contributor_repair_state_from_data(
        read_json(path, dict)
    )


def save_contributor_repair_state(
    config: BibReviewConfig,
    state: ContributorRepairState,
) -> None:
    """Persist reviewed structural repairs atomically."""
    if not isinstance(state, ContributorRepairState):
        raise TypeError("state must be a ContributorRepairState")
    write_json(repair_path(config), state.data())


def contributor_evidence_fingerprint(
    doi: str,
    message: Mapping[str, Any],
) -> str:
    """Fingerprint the exact provider evidence on which a repair depends."""
    normalized_doi = normalize_doi(doi)
    if not isinstance(message, Mapping):
        raise ValueError("repair provider evidence must be a mapping")

    payload = {
        "doi": normalized_doi,
        "message": deepcopy(dict(message)),
    }
    try:
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise ValueError(
            "repair provider evidence must be JSON-serializable"
        ) from error
    return hashlib.sha256(encoded).hexdigest()


def _contributor_data(value: Author | Editor) -> dict[str, Any]:
    return {
        "given": value.given,
        "family": value.family,
        "literal": value.literal,
        "source_fields": deepcopy(dict(value.source_fields)),
    }


def _contributor_from_data(
    value: Any,
    *,
    contributor_type,
    name: str,
) -> Author | Editor:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object")
    expected = {"given", "family", "literal", "source_fields"}
    if set(value) != expected:
        raise ValueError(
            f"{name} must contain given, family, literal, and source_fields"
        )

    for field in ("given", "family", "literal"):
        item = value[field]
        if item is not None and not isinstance(item, str):
            raise ValueError(f"{name}.{field} must be a string or null")

    source_fields = value["source_fields"]
    if not isinstance(source_fields, Mapping):
        raise ValueError(f"{name}.source_fields must be an object")

    return contributor_type(
        given=value["given"],
        family=value["family"],
        literal=value["literal"],
        source_fields=deepcopy(dict(source_fields)),
    )


def contributor_repair_state_from_data(
    value: Any,
) -> ContributorRepairState:
    """Strictly validate persisted reviewed repair state."""
    if not isinstance(value, Mapping):
        raise ValueError("repair state must be an object")
    if set(value) != {"schema_version", "repairs"}:
        raise ValueError(
            "repair state must contain schema_version and repairs"
        )
    if value["schema_version"] != REPAIR_SCHEMA_VERSION:
        raise ValueError(
            "unsupported repair schema_version: "
            f"{value['schema_version']!r}"
        )

    raw_repairs = value["repairs"]
    if not isinstance(raw_repairs, list):
        raise ValueError("repair state repairs must be a list")

    repairs: list[ContributorRepairRecord] = []
    for index, raw in enumerate(raw_repairs, 1):
        name = f"repair[{index}]"
        if not isinstance(raw, Mapping):
            raise ValueError(f"{name} must be an object")
        expected = {
            "doi",
            "evidence_fingerprint",
            "reason",
            "provenance",
            "authors",
            "editors",
        }
        if set(raw) != expected:
            raise ValueError(
                f"{name} contains missing or unsupported fields"
            )

        doi = normalize_doi(raw["doi"])

        fingerprint = raw["evidence_fingerprint"]
        if (
            not isinstance(fingerprint, str)
            or len(fingerprint) != 64
            or any(character not in "0123456789abcdef" for character in fingerprint)
        ):
            raise ValueError(
                f"{name}.evidence_fingerprint must be a SHA-256 string"
            )

        reason = raw["reason"]
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError(f"{name}.reason must be a non-empty string")

        provenance = raw["provenance"]
        if provenance != REPAIR_PROVENANCE:
            raise ValueError(
                f"{name}.provenance must be {REPAIR_PROVENANCE!r}"
            )

        raw_authors = raw["authors"]
        raw_editors = raw["editors"]
        if not isinstance(raw_authors, list):
            raise ValueError(f"{name}.authors must be a list")
        if not isinstance(raw_editors, list):
            raise ValueError(f"{name}.editors must be a list")

        authors = tuple(
            _contributor_from_data(
                item,
                contributor_type=Author,
                name=f"{name}.authors[{position}]",
            )
            for position, item in enumerate(raw_authors, 1)
        )
        editors = tuple(
            _contributor_from_data(
                item,
                contributor_type=Editor,
                name=f"{name}.editors[{position}]",
            )
            for position, item in enumerate(raw_editors, 1)
        )

        if not authors and not editors:
            raise ValueError(
                f"{name} must contain at least one repaired author or editor"
            )

        repairs.append(
            ContributorRepairRecord(
                doi=doi,
                evidence_fingerprint=fingerprint,
                reason=reason.strip(),
                provenance=provenance,
                authors=authors,
                editors=editors,
            )
        )

    dois = [item.doi for item in repairs]
    if len(dois) != len(set(dois)):
        raise ValueError("repair state contains duplicate DOI repairs")

    return ContributorRepairState(repairs=tuple(repairs))


def record_contributor_repair(
    state: ContributorRepairState,
    *,
    doi: str,
    message: Mapping[str, Any],
    reason: str,
    authors: tuple[Author, ...] = (),
    editors: tuple[Editor, ...] = (),
) -> ContributorRepairState:
    """Record or replace one explicit reviewed contributor repair."""
    if not isinstance(state, ContributorRepairState):
        raise TypeError("state must be a ContributorRepairState")
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("repair reason must be a non-empty string")

    normalized_doi = normalize_doi(doi)
    authors = tuple(authors)
    editors = tuple(editors)

    if any(not isinstance(item, Author) for item in authors):
        raise ValueError("repair authors must contain Author objects")
    if any(not isinstance(item, Editor) for item in editors):
        raise ValueError("repair editors must contain Editor objects")
    if not authors and not editors:
        raise ValueError(
            "repair must contain at least one author or editor"
        )

    record = ContributorRepairRecord(
        doi=normalized_doi,
        evidence_fingerprint=contributor_evidence_fingerprint(
            normalized_doi,
            message,
        ),
        reason=reason.strip(),
        authors=authors,
        editors=editors,
    )

    retained = tuple(
        item for item in state.repairs
        if item.doi != normalized_doi
    )
    return ContributorRepairState(repairs=(*retained, record))


def lookup_contributor_repair(
    state: ContributorRepairState,
    *,
    doi: str,
    message: Mapping[str, Any],
) -> ContributorRepair | None:
    """Return a reviewed repair when it still matches current provider evidence."""
    if not isinstance(state, ContributorRepairState):
        raise TypeError("state must be a ContributorRepairState")

    normalized_doi = normalize_doi(doi)
    record = next(
        (
            item
            for item in state.repairs
            if item.doi == normalized_doi
        ),
        None,
    )
    if record is None:
        return None

    current = contributor_evidence_fingerprint(
        normalized_doi,
        message,
    )
    if current != record.evidence_fingerprint:
        # Local import avoids a project <-> repair import cycle later when the
        # project collection layer consumes this module.
        from .project import ProjectStateError

        raise ProjectStateError(
            f"{normalized_doi}: repair evidence is stale; "
            "review the current provider metadata again"
        )

    return ContributorRepair(
        authors=record.authors,
        editors=record.editors,
    )
