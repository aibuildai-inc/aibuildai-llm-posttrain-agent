"""Filesystem-semantics preconditions for the paths aibuildai writes to.

The product relies on POSIX file locking and owner-only (0600) file modes for its run-dir lock and its credential files. A Windows-backed mount surfaced under WSL (`drvfs`, `9p`) silently provides neither. These helpers read `/proc/self/mountinfo` to find the filesystem type actually backing a path and fail loud before anything is written there.
"""
from __future__ import annotations

import os
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path

_MOUNTINFO_PATH = Path("/proc/self/mountinfo")
_UNSUPPORTED_SEMANTIC_FS = frozenset({"drvfs", "9p"})


@dataclass(frozen=True)
class _Mount:
    mount_point: str
    fs_type: str


def _filesystem_type_for_path(path: Path, mountinfo_text: str) -> str:
    normalized = normalize_path(path)
    mounts = _parse_mountinfo(mountinfo_text)
    best: _Mount | None = None
    for mount in mounts:
        if _path_is_on_mount(normalized, mount.mount_point):
            if best is None or len(mount.mount_point) > len(best.mount_point):
                best = mount
    if best is None:
        raise AssertionError(
            f"no mountinfo entry covers path {normalized!r}; "
            f"/proc/self/mountinfo must include the root mount"
        )
    return best.fs_type


def _assert_posix_path_semantics(
    path: Path,
    *,
    purpose: str,
    mountinfo_text: str | None = None,
) -> None:
    display_path = normalize_path(path)
    resolved_path = _resolve_filesystem_path(path)
    if mountinfo_text is None:
        mountinfo_text = _MOUNTINFO_PATH.read_text(encoding="utf-8")
    fs_type = _filesystem_type_for_path(Path(resolved_path), mountinfo_text)
    if fs_type in _UNSUPPORTED_SEMANTIC_FS:
        resolved_suffix = (
            ""
            if resolved_path == display_path
            else f" (resolved path {resolved_path})"
        )
        raise ValueError(
            f"{purpose} path {display_path}{resolved_suffix} is on "
            f"filesystem type {fs_type!r}, "
            "which cannot support the POSIX file locking and owner-only file "
            "mode guarantees aibuildai relies on. Move this path onto a Linux "
            "filesystem such as the WSL ext4 home directory, not /mnt/c."
        )


def _assert_owner_only_file_mode(path: Path, *, purpose: str) -> None:
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode != 0o600:
        raise ValueError(
            f"{purpose} path {path} must have mode 0600, but the filesystem "
            f"reported mode {mode:04o} after chmod. Move it onto a filesystem "
            "that supports POSIX owner-only file modes."
        )


def fsync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_write_owner_only_text(
    target: str | Path, content: str, *, purpose: str
) -> None:
    """Atomically write one 0600 text file on a POSIX filesystem."""
    target = Path(target)
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    _assert_posix_path_semantics(target.parent, purpose=purpose)
    fd, tmp_name = tempfile.mkstemp(
        dir=target.parent,
        prefix=f".{target.name}.",
        suffix=".tmp",
    )
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, 0o600)
        _assert_owner_only_file_mode(tmp, purpose=purpose)
        os.replace(tmp, target)
        _assert_owner_only_file_mode(target, purpose=purpose)
        fsync_directory(target.parent)
    finally:
        tmp.unlink(missing_ok=True)


def _parse_mountinfo(text: str) -> list[_Mount]:
    mounts: list[_Mount] = []
    for line in text.splitlines():
        before, separator, after = line.partition(" - ")
        if separator == "":
            raise ValueError(f"invalid mountinfo line without separator: {line!r}")
        fields = before.split()
        if len(fields) < 5:
            raise ValueError(f"invalid mountinfo line with too few fields: {line!r}")
        post_fields = after.split()
        if not post_fields:
            raise ValueError(f"invalid mountinfo line without filesystem type: {line!r}")
        mounts.append(_Mount(
            mount_point=_decode_mountinfo_path(fields[4]),
            fs_type=post_fields[0],
        ))
    return mounts


def _decode_mountinfo_path(value: str) -> str:
    return (
        value
        .replace("\\040", " ")
        .replace("\\011", "\t")
        .replace("\\012", "\n")
        .replace("\\134", "\\")
    )


def normalize_path(path: "str | Path") -> str:
    """expanduser + absolutize + normpath, WITHOUT following symlinks.

    The no-resolve half is a policy: preserve the user's own path string, so a configured symlink path must never be replaced by its target. This is the one shared spelling of the rule; config path fields apply it through an AfterValidator."""
    expanded = Path(path).expanduser()
    if not expanded.is_absolute():
        expanded = Path.cwd() / expanded
    return os.path.normpath(str(expanded))


def _resolve_filesystem_path(path: Path) -> str:
    return os.path.normpath(str(Path(normalize_path(path)).resolve(strict=False)))


def _path_is_on_mount(path: str, mount_point: str) -> bool:
    return os.path.commonpath([path, mount_point]) == mount_point
