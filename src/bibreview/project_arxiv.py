"""Project-level orchestration for the optional arXiv cache."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from .arxiv import ArxivFeed, ArxivProvider, arxiv_feed_json_bytes
from .config import BibReviewConfig
from .storage import atomic_write


class ProjectArxivError(ValueError):
    """Raised when the configured arXiv module cannot run safely."""


@dataclass(frozen=True)
class ProjectArxivPlan:
    """Read-only plan for one configured arXiv cache refresh."""

    output: Path
    content: bytes
    entry_count: int
    changed: bool

    def summary(self) -> str:
        return (
            f"arXiv entries: {self.entry_count}; "
            f"cache changed: {'yes' if self.changed else 'no'}"
        )


def build_arxiv_provider(config: BibReviewConfig) -> ArxivProvider:
    """Build the configured generic arXiv provider."""
    if not config.arxiv.enabled:
        raise ProjectArxivError("arXiv module is disabled")
    repository = (
        f"; +{config.project.repository}"
        if config.project.repository
        else ""
    )
    user_agent = (
        f"{config.project.name} via BibReview/{__version__}"
        f" ({repository.lstrip('; ')}"
        f"{'; ' if repository else ''}"
        f"contact: {config.project.contact_email})"
    )
    return ArxivProvider(
        query=config.arxiv.query,
        max_results=config.arxiv.max_results,
        sort_by=config.arxiv.sort_by,
        sort_order=config.arxiv.sort_order,
        user_agent=user_agent,
        contact_email=config.project.contact_email,
    )


def plan_project_arxiv(
    config: BibReviewConfig,
    *,
    provider: ArxivProvider | None = None,
    generated_at: datetime | None = None,
) -> ProjectArxivPlan:
    """Fetch arXiv entries and return a read-only cache update plan."""
    if not config.arxiv.enabled:
        raise ProjectArxivError("arXiv module is disabled")
    if config.arxiv.output is None:
        raise ProjectArxivError("arxiv.output is required")

    provider = provider or build_arxiv_provider(config)
    entries = provider.fetch()
    feed = ArxivFeed(
        generated_at=generated_at or datetime.now(timezone.utc),
        entries=entries,
    )
    content = arxiv_feed_json_bytes(feed)
    output = config.arxiv.output
    current = output.read_bytes() if output.exists() else None
    return ProjectArxivPlan(
        output=output,
        content=content,
        entry_count=len(entries),
        changed=current != content,
    )


def apply_project_arxiv(plan: ProjectArxivPlan) -> None:
    """Apply a previously validated arXiv cache plan."""
    if not isinstance(plan, ProjectArxivPlan):
        raise ProjectArxivError("plan must be a ProjectArxivPlan")
    if plan.changed:
        atomic_write(plan.output, plan.content)
