"""Atomic write helper. POSIX same-fs rename atomicity is the load-bearing guarantee."""
from __future__ import annotations

import contextlib
import os
import secrets
import stat
from pathlib import Path


def _open_temp_file(target: Path) -> tuple[int, Path]:
    """Open a same-dir temp file with normal new-file permissions."""
    while True:
        tmp = target.parent / f".{target.name}.{secrets.token_hex(8)}.tmp"
        try:
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
        except FileExistsError:
            continue
        return fd, tmp


def atomic_write_text(target: str | Path, content: str) -> None:
    """Atomic write via temp + fsync + os.replace. Readers see prior or new full version, never partial. A new target follows the process umask. A replaced target keeps its mode. POSIX only — same-fs rename atomicity is the load-bearing guarantee. The target parent must already exist."""
    target = Path(target)
    fd, tmp = _open_temp_file(target)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            try:
                target_mode = stat.S_IMODE(target.stat().st_mode)
            except FileNotFoundError:
                target_mode = None
            if target_mode is not None:
                os.fchmod(f.fileno(), target_mode)
            os.fsync(f.fileno())
        os.replace(tmp, target)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
