"""Project-state storage helpers with explicit, atomic file replacement."""

from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
import stat
import tempfile
from typing import Any


class StorageError(ValueError):
    """Raised when persisted project state cannot be read or represented safely."""


def read_json(path: Path | str, expected_type: type) -> Any:
    """Read UTF-8 JSON and validate its root type."""
    source = Path(path)
    try:
        value = json.loads(source.read_text(encoding="utf-8"))
    except OSError:
        raise
    except (ValueError, UnicodeError) as error:
        raise StorageError(f"{source}: {error}") from error
    if not isinstance(value, expected_type):
        raise StorageError(f"{source}: expected {expected_type.__name__}")
    return value


def json_bytes(value: Any) -> bytes:
    """Serialize project JSON deterministically using the established readable format."""
    try:
        text = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)
    except (TypeError, ValueError) as error:
        raise StorageError(f"value is not valid JSON project state: {error}") from error
    return (text + "\n").encode("utf-8")


def atomic_write(path: Path | str, content: bytes) -> None:
    """Replace one file atomically after staging it beside the destination."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=destination.parent,
            prefix=f".{destination.name}.",
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(content)
        mode = stat.S_IMODE(destination.stat().st_mode) if destination.exists() else 0o644
        temporary.chmod(mode)
        os.replace(temporary, destination)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def write_json(path: Path | str, value: Any) -> None:
    """Serialize and atomically replace one JSON project-state file."""
    atomic_write(path, json_bytes(value))


def backup_path(directory: Path | str, prefix: str, suffix: str) -> Path:
    """Return a timestamped, collision-safe backup path without creating it."""
    root = Path(directory)
    stamp = datetime.now().astimezone().strftime("%Y-%m-%dT%H-%M-%S%z")
    candidate = root / f"{prefix}-{stamp}{suffix}"
    count = 0
    while candidate.exists():
        count += 1
        candidate = root / f"{prefix}-{stamp}-{count}{suffix}"
    return candidate
