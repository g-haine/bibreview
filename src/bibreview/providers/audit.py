"""Provider-specific normalization into generic audit evidence."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from ..identity import IdentityError, normalize_doi
from ..pipeline.audit import ProviderEvidence
from ..text import clean_metadata, normalize_provider_abstract
from .crossref import CrossRefProvider, crossref_page_locator
from .openalex import OpenAlexProvider, openalex_abstract
from .semantic_scholar import SemanticScholarProvider


class AuditEvidenceSource(Protocol):
    """One DOI-backed source of normalized audit evidence."""

    name: str

    def evidence(self, doi: str) -> ProviderEvidence:
        """Return normalized evidence, or raise a sanitized provider error."""


def _string(value: object) -> str:
    return "" if value is None else str(value).strip()


def _audit_abstract(value: object) -> str:
    """Normalize only lossless provider abstract markup; retain refusals verbatim."""
    result = normalize_provider_abstract(_string(value))
    return result.normalized


def _first(value: object) -> str:
    if isinstance(value, list) and value:
        return _string(value[0])
    return ""


def _crossref_names(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    names: list[str] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        literal = _string(item.get("name"))
        if literal:
            names.append(literal)
            continue
        name = " ".join(
            part
            for part in (
                _string(item.get("given")),
                _string(item.get("family")),
            )
            if part
        ).strip()
        if name:
            names.append(name)
    return tuple(names)


def _date_parts_year(value: object) -> str:
    if not isinstance(value, Mapping):
        return ""
    parts = value.get("date-parts")
    first = parts[0] if isinstance(parts, list) and parts else None
    if not isinstance(first, list) or not first:
        return ""
    try:
        return str(int(first[0]))
    except (TypeError, ValueError):
        return ""


def _crossref_year(message: Mapping[str, object]) -> str:
    for name in ("published-print", "published-online", "published"):
        year = _date_parts_year(message.get(name))
        if year:
            return year
    return ""


def _crossref_created_date(message: Mapping[str, object]) -> str:
    created = message.get("created")
    if not isinstance(created, Mapping):
        return ""
    parts = created.get("date-parts")
    first = parts[0] if isinstance(parts, list) and parts else None
    if not isinstance(first, list) or len(first) < 3:
        return ""
    try:
        year, month, day = (int(first[0]), int(first[1]), int(first[2]))
    except (TypeError, ValueError):
        return ""
    try:
        from datetime import date

        return date(year, month, day).isoformat()
    except ValueError:
        return ""


def _crossref_event(value: object) -> str:
    if isinstance(value, Mapping):
        name = value.get("name")
        if isinstance(name, str):
            return name.strip()
    return ""


def _crossref_identifiers(
    message: Mapping[str, object],
) -> dict[str, str]:
    identifiers: dict[str, str] = {}
    raw_doi = message.get("DOI")
    if isinstance(raw_doi, str) and raw_doi.strip():
        try:
            identifiers["doi"] = normalize_doi(raw_doi)
        except IdentityError:
            pass

    isbn_types = message.get("isbn-type")
    if isinstance(isbn_types, list):
        for item in isbn_types:
            if not isinstance(item, Mapping):
                continue
            value = _string(item.get("value"))
            if value:
                identifiers["isbn"] = value
                break
    return identifiers


class CrossRefAuditSource:
    """Normalize CrossRef work metadata into audit evidence."""

    name = "crossref"
    batch_size = CrossRefProvider.BATCH_SIZE

    def __init__(self, provider: CrossRefProvider) -> None:
        self.provider = provider

    def _from_message(self, message: Mapping[str, object]) -> ProviderEvidence:
        subjects = message.get("subject")
        keywords = (
            tuple(
                _string(value)
                for value in subjects
                if _string(value)
            )
            if isinstance(subjects, list)
            else ()
        )
        return ProviderEvidence(
            provider=self.name,
            identifiers=_crossref_identifiers(message),
            fields={
                "type": _string(message.get("type")),
                "title": _first(message.get("title")),
                "authors": _crossref_names(message.get("author")),
                "editors": _crossref_names(message.get("editor")),
                "abstract": _audit_abstract(message.get("abstract")),
                "container_title": _first(message.get("container-title")),
                "publication_year": _crossref_year(message),
                "volume": _string(message.get("volume")),
                "issue": _string(message.get("issue")),
                "pages": crossref_page_locator(message),
                "publisher": _string(message.get("publisher")),
                "event": _crossref_event(message.get("event")),
                "keywords": keywords,
                "created_date": _crossref_created_date(message),
            },
        )

    def evidence(self, doi: str) -> ProviderEvidence:
        normalized = normalize_doi(doi)
        message = self.provider.work(normalized)
        if message is None:
            return ProviderEvidence(
                provider=self.name,
                status="unavailable",
                detail="record not found",
            )
        return self._from_message(message)

    def evidence_many(
        self,
        dois: tuple[str, ...],
    ) -> Mapping[str, ProviderEvidence]:
        normalized = tuple(dict.fromkeys(normalize_doi(doi) for doi in dois))
        records = self.provider.works(normalized)
        return {
            doi: (
                self._from_message(records[doi])
                if doi in records
                else ProviderEvidence(
                    provider=self.name,
                    status="unavailable",
                    detail="record not found",
                )
            )
            for doi in normalized
        }


def _openalex_authors(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    names: list[str] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        raw = _string(item.get("raw_author_name"))
        if raw:
            names.append(raw)
            continue
        author = item.get("author")
        if isinstance(author, Mapping):
            name = _string(author.get("display_name"))
            if name:
                names.append(name)
    return tuple(names)


def _openalex_container(value: object) -> str:
    if not isinstance(value, Mapping):
        return ""
    source = value.get("source")
    if not isinstance(source, Mapping):
        return ""
    return _string(source.get("display_name"))


def _openalex_pages(value: object) -> tuple[str, str, str]:
    if not isinstance(value, Mapping):
        return "", "", ""
    volume = _string(value.get("volume"))
    issue = _string(value.get("issue"))
    first = _string(value.get("first_page"))
    last = _string(value.get("last_page"))
    if first and last and first != last:
        pages = f"{first}-{last}"
    else:
        pages = first or last
    return volume, issue, pages


class OpenAlexAuditSource:
    """Normalize OpenAlex work metadata into audit evidence."""

    name = "openalex"
    batch_size = OpenAlexProvider.BATCH_SIZE

    def __init__(self, provider: OpenAlexProvider) -> None:
        self.provider = provider

    def _from_data(self, data: Mapping[str, object]) -> ProviderEvidence:
        identifiers: dict[str, str] = {}
        raw_doi = data.get("doi")
        if isinstance(raw_doi, str) and raw_doi.strip():
            try:
                identifiers["doi"] = normalize_doi(raw_doi)
            except IdentityError:
                pass

        raw_year = data.get("publication_year")
        year = (
            str(raw_year)
            if isinstance(raw_year, int) and not isinstance(raw_year, bool)
            else ""
        )
        volume, issue, pages = _openalex_pages(data.get("biblio"))
        return ProviderEvidence(
            provider=self.name,
            identifiers=identifiers,
            fields={
                "title": _string(data.get("title")),
                "authors": _openalex_authors(data.get("authorships")),
                "abstract": _audit_abstract(
                    openalex_abstract(data.get("abstract_inverted_index"))
                ),
                "container_title": _openalex_container(data.get("primary_location")),
                "publication_year": year,
                "volume": volume,
                "issue": issue,
                "pages": pages,
            },
        )

    def evidence(self, doi: str) -> ProviderEvidence:
        data = self.provider.work(doi)
        if data is None:
            return ProviderEvidence(
                provider=self.name,
                status="unavailable",
                detail="record not found",
            )
        return self._from_data(data)

    def evidence_many(
        self,
        dois: tuple[str, ...],
    ) -> Mapping[str, ProviderEvidence]:
        normalized = tuple(dict.fromkeys(normalize_doi(doi) for doi in dois))
        records = self.provider.works(normalized)
        return {
            doi: (
                self._from_data(records[doi])
                if doi in records
                else ProviderEvidence(
                    provider=self.name,
                    status="unavailable",
                    detail="record not found",
                )
            )
            for doi in normalized
        }


def _semantic_authors(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    names: list[str] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        name = _string(item.get("name"))
        if name:
            names.append(name)
    return tuple(names)


class SemanticScholarAuditSource:
    """Normalize Semantic Scholar paper metadata into audit evidence."""

    name = "semantic_scholar"
    batch_size = SemanticScholarProvider.BATCH_SIZE

    def __init__(self, provider: SemanticScholarProvider) -> None:
        self.provider = provider

    def _from_data(self, data: Mapping[str, object]) -> ProviderEvidence:
        identifiers: dict[str, str] = {}
        external_ids = data.get("externalIds")
        if isinstance(external_ids, Mapping):
            raw_doi = external_ids.get("DOI")
            if isinstance(raw_doi, str) and raw_doi.strip():
                try:
                    identifiers["doi"] = normalize_doi(raw_doi)
                except IdentityError:
                    pass

        raw_year = data.get("year")
        year = (
            str(raw_year)
            if isinstance(raw_year, int) and not isinstance(raw_year, bool)
            else ""
        )
        return ProviderEvidence(
            provider=self.name,
            identifiers=identifiers,
            fields={
                "title": _string(data.get("title")),
                "authors": _semantic_authors(data.get("authors")),
                "abstract": _audit_abstract(data.get("abstract")),
                "container_title": _string(data.get("venue")),
                "publication_year": year,
            },
        )

    def evidence(self, doi: str) -> ProviderEvidence:
        data = self.provider.paper(doi)
        if data is None:
            return ProviderEvidence(
                provider=self.name,
                status="unavailable",
                detail="record not found",
            )
        return self._from_data(data)

    def evidence_many(
        self,
        dois: tuple[str, ...],
    ) -> Mapping[str, ProviderEvidence]:
        normalized = tuple(dict.fromkeys(normalize_doi(doi) for doi in dois))
        records = self.provider.papers(normalized)
        return {
            doi: (
                self._from_data(records[doi])
                if doi in records
                else ProviderEvidence(
                    provider=self.name,
                    status="unavailable",
                    detail="record not found",
                )
            )
            for doi in normalized
        }

