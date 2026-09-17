"""Small human-readable progress reporter shared by BibReview workflows."""

from __future__ import annotations

from dataclasses import dataclass
import sys
from typing import TextIO


@dataclass
class Reporter:
    """Write progress messages according to a simple verbosity level."""

    verbosity: int = 0
    stream: TextIO | None = None

    @property
    def _stream(self) -> TextIO:
        return self.stream or sys.stderr

    def step(self, message: str) -> None:
        if self.verbosity >= 0:
            print(f"[*] {message}", file=self._stream)

    def detail(self, message: str) -> None:
        if self.verbosity >= 1:
            print(f"    {message}", file=self._stream)

    def debug(self, message: str) -> None:
        if self.verbosity >= 2:
            print(f"      {message}", file=self._stream)

    def warning(self, message: str) -> None:
        print(f"bibreview: warning: {message}", file=self._stream)
