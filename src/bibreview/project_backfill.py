"""Project-state orchestration for reviewed missing-field backfills."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from .config import BibReviewConfig
from .pipeline.backfill import BackfillResult, backfill
from .pipeline.collect import EnrichmentLookup, WorkProvider
from .project import ProjectStateError
from .reporting import Reporter
from .storage import (
    atomic_write_batch,
    bibliography_document_data,
    json_bytes,
    read_bibliography,
)


@dataclass(frozen=True)
class ProjectBackfillPlan:
    """Read-only description of one missing-field backfill staging operation."""

    result: BackfillResult
    outputs: Mapping[Path, bytes]

    @property
    def changed(self) -> bool:
        return bool(self.outputs)

    def summary(self) -> str:
        return (
            f"scanned: {self.result.scanned_count}; "
            f"eligible: {self.result.eligible_count}; "
            f"filled: {len(self.result.items)}; "
            f"no-value: {len(self.result.no_value)}; "
            f"unavailable: {len(self.result.unavailable)}"
        )


def plan_project_backfill(
    config: BibReviewConfig,
    *,
    provider: WorkProvider,
    fields: tuple[str, ...],
    types: tuple[str, ...] = (),
    enrichment_lookup: EnrichmentLookup | None = None,
    reporter: Reporter | None = None,
) -> ProjectBackfillPlan:
    """Stage conservative fills of selected missing canonical fields."""
    paths = config.paths
    existing = (
        read_bibliography(paths.bibliography)
        if paths.bibliography.exists()
        else ()
    )
    staged = (
        read_bibliography(paths.collected)
        if paths.collected.exists()
        else ()
    )
    if staged:
        raise ProjectStateError(
            f"{paths.collected}: contains {len(staged)} staged publication(s); "
            "merge the existing batch before backfilling"
        )

    result = backfill(
        existing,
        provider=provider,
        fields=fields,
        types=types,
        enrichment_lookup=enrichment_lookup,
        reporter=reporter,
    )

    outputs: dict[Path, bytes] = {}
    if result.items:
        outputs[paths.collected] = json_bytes(
            bibliography_document_data(
                tuple(item.publication for item in result.items)
            )
        )

    return ProjectBackfillPlan(
        result=result,
        outputs=MappingProxyType(outputs),
    )


def apply_project_backfill(plan: ProjectBackfillPlan) -> None:
    """Apply one prepared backfill plan to canonical staging."""
    if not isinstance(plan, ProjectBackfillPlan):
        raise ProjectStateError("plan must be a ProjectBackfillPlan")
    if plan.outputs:
        atomic_write_batch(plan.outputs)
