"""Filesystem operations for transcript-owned files."""
from __future__ import annotations

from pathlib import Path

from infra.util.atomic_io import atomic_write_text


def append_transcript(step_dir: str, name: str, content: str) -> None:
    directory = Path(step_dir)
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / name).open("a", encoding="utf-8") as handle:
        handle.write(content)


def write_sidecar(step_dir: str, name: str, content: str) -> None:
    directory = Path(step_dir)
    directory.mkdir(parents=True, exist_ok=True)
    atomic_write_text(directory / name, content)
