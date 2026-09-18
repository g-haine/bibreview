"""Serialization helpers for the optional arXiv cache."""

from __future__ import annotations

from datetime import timezone

from ..storage import json_bytes
from .model import ArxivFeed


def arxiv_feed_data(feed: ArxivFeed) -> dict[str, object]:
    """Return the stable JSON-compatible cache representation."""
    generated_at = (
        feed.generated_at.astimezone(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )
    return {
        "generated_at": generated_at,
        "papers": [
            {
                "title": entry.title,
                "summary": entry.summary,
                "url": entry.url,
                "authors": list(entry.authors),
                "updated": entry.updated.isoformat(),
            }
            for entry in feed.entries
        ],
    }


def arxiv_feed_json_bytes(feed: ArxivFeed) -> bytes:
    """Serialize an arXiv cache with BibReview's deterministic JSON style."""
    return json_bytes(arxiv_feed_data(feed))
