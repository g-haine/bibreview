"""Small arXiv feed model, deliberately separate from Publication."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime


@dataclass(frozen=True)
class ArxivEntry:
    """One display-oriented arXiv result."""

    title: str
    summary: str
    url: str
    authors: tuple[str, ...]
    updated: date

    def __post_init__(self) -> None:
        for name in ("title", "summary", "url"):
            if not isinstance(getattr(self, name), str):
                raise ValueError(f"arXiv entry {name} must be a string")
        object.__setattr__(self, "authors", tuple(self.authors))
        if any(not isinstance(author, str) for author in self.authors):
            raise ValueError("arXiv entry authors must contain strings")
        if not isinstance(self.updated, date):
            raise ValueError("arXiv entry updated must be a date")


@dataclass(frozen=True)
class ArxivFeed:
    """Timestamped cache payload for display-oriented arXiv entries."""

    generated_at: datetime
    entries: tuple[ArxivEntry, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.generated_at, datetime):
            raise ValueError("arXiv feed generated_at must be a datetime")
        if self.generated_at.tzinfo is None:
            raise ValueError("arXiv feed generated_at must be timezone-aware")
        object.__setattr__(self, "entries", tuple(self.entries))
        if any(not isinstance(entry, ArxivEntry) for entry in self.entries):
            raise ValueError("arXiv feed entries must contain ArxivEntry objects")
