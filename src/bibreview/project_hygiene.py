"""Project-level read-only canonical hygiene orchestration."""

from __future__ import annotations

from .config import BibReviewConfig
from .hygiene import AbstractHygieneReport, scan_abstract_hygiene
from .storage import read_bibliography


def project_abstract_hygiene(config: BibReviewConfig) -> AbstractHygieneReport:
    """Scan the configured canonical bibliography without writing project state."""
    return scan_abstract_hygiene(read_bibliography(config.paths.bibliography))
