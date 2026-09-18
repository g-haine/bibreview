"""Safe planning and persistence of rendered static-site artifacts.

This layer is intentionally mechanical. It does not render bibliographic data,
fetch metadata, acquire BibTeX, or know about any particular project. Callers
declare complete ownership of one or more generated subtrees and receive a
read-only plan before any filesystem mutation occurs.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Iterable, Mapping

from ..storage import atomic_write_batch
from .render import RenderedArtifact


class SitePersistenceError(ValueError):
    """Raised when rendered artifacts cannot be persisted safely."""


@dataclass(frozen=True)
class SitePersistencePlan:
    """Read-only reconciliation plan for one generated static-site surface."""

    root: Path
    writes: Mapping[Path, bytes]
    deletes: tuple[Path, ...]
    expected_count: int
    unchanged_count: int

    @property
    def changed(self) -> bool:
        """Whether applying this plan would mutate the site tree."""
        return bool(self.writes or self.deletes)

    def summary(self) -> str:
        """Return a compact human-readable persistence summary."""
        return (
            f"expected: {self.expected_count}; "
            f"write: {len(self.writes)}; "
            f"delete: {len(self.deletes)}; "
            f"unchanged: {self.unchanged_count}"
        )


def _relative_path(value: str, *, name: str) -> PurePosixPath:
    if not isinstance(value, str) or not value:
        raise SitePersistenceError(f"{name} must be a non-empty relative path")
    if "\\" in value:
        raise SitePersistenceError(f"{name} must use POSIX separators: {value!r}")
    path = PurePosixPath(value)
    if path.is_absolute():
        raise SitePersistenceError(f"{name} must be relative: {value!r}")
    if value.endswith("/") or any(part in {"", ".", ".."} for part in path.parts):
        raise SitePersistenceError(f"unsafe {name}: {value!r}")
    return path


def _managed_root(value: str) -> PurePosixPath:
    return _relative_path(value, name="managed root")


def _assert_no_symlink_parent(root: Path, relative: PurePosixPath) -> None:
    """Reject existing symlinked directory components below the site root."""
    current = root
    for part in relative.parts[:-1]:
        current = current / part
        if current.is_symlink():
            raise SitePersistenceError(
                f"generated path traverses symlinked directory: {current}"
            )


def _owned(relative: PurePosixPath, managed: tuple[PurePosixPath, ...]) -> bool:
    return any(
        relative.parts[: len(root.parts)] == root.parts
        for root in managed
    )


def _overlap(first: PurePosixPath, second: PurePosixPath) -> bool:
    shorter, longer = sorted((first, second), key=lambda item: len(item.parts))
    return longer.parts[: len(shorter.parts)] == shorter.parts


def _assert_no_symlink_directory_chain(
    root: Path,
    relative: PurePosixPath,
) -> None:
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise SitePersistenceError(
                f"managed root traverses symlinked directory: {current}"
            )


def plan_rendered_artifacts(
    root: Path | str,
    artifacts: Iterable[RenderedArtifact],
    *,
    managed_roots: Iterable[str],
) -> SitePersistencePlan:
    """Plan deterministic writes/deletions without mutating the filesystem.

    managed_roots declares complete ownership of generated directories such as
    _posts, authors, years, or a dedicated nested subtree such as
    _data/bibreview. Existing files below those roots that are absent from
    artifacts are planned for deletion. Overlapping roots and artifacts outside
    managed roots are rejected.

    The site root itself is never created or modified while planning.
    """
    site_root = Path(root)
    resolved_root = site_root.resolve(strict=False)
    if site_root.exists() and site_root.is_symlink():
        raise SitePersistenceError(f"site root must not be a symlink: {site_root}")

    managed = tuple(_managed_root(value) for value in managed_roots)
    if not managed:
        raise SitePersistenceError("managed_roots must not be empty")
    if len(set(managed)) != len(managed):
        raise SitePersistenceError("managed_roots contains duplicates")
    for index, first in enumerate(managed):
        for second in managed[index + 1 :]:
            if _overlap(first, second):
                raise SitePersistenceError(
                    "managed_roots must not overlap: "
                    f"{first.as_posix()!r} and {second.as_posix()!r}"
                )

    for managed_root in managed:
        _assert_no_symlink_directory_chain(site_root, managed_root)
        directory = site_root.joinpath(*managed_root.parts)
        if directory.exists() and not directory.is_dir():
            raise SitePersistenceError(
                f"managed root is not a directory: {directory}"
            )

    expected: dict[PurePosixPath, bytes] = {}
    for artifact in artifacts:
        if not isinstance(artifact, RenderedArtifact):
            raise SitePersistenceError("artifacts must contain RenderedArtifact objects")
        relative = _relative_path(artifact.path, name="artifact path")
        if not _owned(relative, managed):
            raise SitePersistenceError(
                f"artifact path is outside managed roots: {artifact.path!r}"
            )
        if relative in expected:
            raise SitePersistenceError(f"duplicate artifact path: {artifact.path!r}")
        _assert_no_symlink_parent(site_root, relative)
        expected[relative] = artifact.content.encode("utf-8")

    writes: dict[Path, bytes] = {}
    unchanged = 0
    expected_destinations: set[Path] = set()
    for relative, content in expected.items():
        destination = site_root.joinpath(*relative.parts)
        try:
            destination.resolve(strict=False).relative_to(resolved_root)
        except ValueError as error:
            raise SitePersistenceError(
                f"artifact path escapes site root: {relative.as_posix()!r}"
            ) from error
        expected_destinations.add(destination)
        if destination.exists() and destination.is_dir():
            raise SitePersistenceError(
                f"artifact destination is an existing directory: {destination}"
            )
        if destination.exists() and not destination.is_symlink():
            if destination.read_bytes() == content:
                unchanged += 1
                continue
        writes[destination] = content

    deletes: list[Path] = []
    for managed_root in managed:
        _assert_no_symlink_directory_chain(site_root, managed_root)
        directory = site_root.joinpath(*managed_root.parts)
        if not directory.exists():
            continue
        if not directory.is_dir():
            raise SitePersistenceError(
                f"managed root is not a directory: {directory}"
            )
        for existing in sorted(directory.rglob("*")):
            if existing.is_symlink():
                if existing not in expected_destinations:
                    deletes.append(existing)
                continue
            if existing.is_file() and existing not in expected_destinations:
                deletes.append(existing)

    return SitePersistencePlan(
        root=site_root,
        writes=MappingProxyType(writes),
        deletes=tuple(deletes),
        expected_count=len(expected),
        unchanged_count=unchanged,
    )


def apply_rendered_artifacts(plan: SitePersistencePlan) -> None:
    """Apply a previously prepared site persistence plan.

    All changed files are staged before replacement through BibReview's existing
    atomic batch writer. Obsolete files are removed only after all writes have
    been staged/replaced successfully.
    """
    if not isinstance(plan, SitePersistencePlan):
        raise SitePersistenceError("plan must be a SitePersistencePlan")
    if plan.writes:
        atomic_write_batch(plan.writes)
    for path in plan.deletes:
        if path.is_dir() and not path.is_symlink():
            raise SitePersistenceError(f"refusing to delete directory: {path}")
        path.unlink(missing_ok=True)
