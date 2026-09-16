"""Read-only access to one run's workspace for the Web member.

The workspace is written by autonomous code, so its contents are untrusted
even though the server is loopback-only. One service owns the whole path
contract: the browser sends workspace-relative POSIX paths, every
component is opened descriptor-relative with ``O_NOFOLLOW`` (a symlink or a
replaced component fails at open, so there is no check-then-reopen race),
only directories are walked, only regular files are read or streamed, and
no absolute host path ever leaves this module. Classification is by
filename only: nothing here deserializes a file.
"""

from __future__ import annotations

import errno
import mimetypes
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Literal

from output.web.models import (
    PreviewKind,
    WorkspaceEntriesResponse,
    WorkspaceEntry,
    WorkspaceStatResponse,
    WorkspaceTextResponse,
)

# The bounded text preview; the full file stays one download away.
TEXT_PREVIEW_BYTES = 2 * 1024 * 1024
# A directory listing above this many entries is cut and says so.
ENTRY_LIMIT = 2000
STREAM_CHUNK = 256 * 1024

# Extension -> Shiki language name the frontend bundles (output/web/frontend/src/shiki.ts).
LANGUAGES = {
    ".py": "python",
    ".sh": "bash",
    ".bash": "bash",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
    ".js": "javascript",
    ".mjs": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".diff": "diff",
    ".patch": "diff",
    ".html": "html",
    ".htm": "html",
    ".svg": "xml",
    ".xml": "xml",
    ".css": "css",
    ".csv": "csv",
    ".ini": "ini",
    ".cfg": "ini",
}
TEXT_EXTENSIONS = {".txt", ".log", ".out", ".err", ".jsonl", ".tsv", ".rst", ".tex", ".bib"}
TEXT_NAMES = {"Dockerfile", "Makefile", "LICENSE", "README", "requirements.txt"}
BINARY_EXTENSIONS = {
    ".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico", ".tiff", ".tif",
    ".mp3", ".mp4", ".wav", ".ogg", ".flac", ".aac", ".m4a", ".webm", ".avi", ".mov", ".mkv",
    ".zip", ".gz", ".bz2", ".xz", ".zst", ".tar", ".7z", ".rar",
    ".whl", ".egg", ".jar", ".war",
    ".pyc", ".pyo", ".so", ".dylib", ".dll", ".exe", ".o", ".a",
    ".bin", ".dat", ".db", ".sqlite", ".sqlite3",
    ".pkl", ".pickle", ".npy", ".npz", ".pt", ".pth", ".safetensors", ".gguf", ".onnx",
    ".h5", ".hdf5", ".parquet", ".arrow", ".feather",
    ".woff", ".woff2", ".ttf", ".otf", ".eot",
    ".DS_Store",
}
# `raw` serves these inline, nothing else: passive documents the browser
# renders itself, never a same-origin active page.
INLINE_TYPES = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}
Kind = Literal["directory", "file", "symlink", "other"]


class WorkspaceError(Exception):
    """A request this service refuses; the app maps it to one HTTP status."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def split_relative(raw: str) -> tuple[str, ...]:
    """The path contract: POSIX, relative, no empty, dot, or backslash component, no NUL. ``""`` is the root."""
    if raw == "":
        return ()
    if "\0" in raw or "\\" in raw or raw.startswith("/"):
        raise WorkspaceError(400, "path must be a relative POSIX path inside the workspace")
    parts = tuple(raw.split("/"))
    if any(part in ("", ".", "..") for part in parts):
        raise WorkspaceError(400, "path must not contain empty, `.`, or `..` components")
    return parts


def _kind(mode: int) -> Kind:
    if stat.S_ISDIR(mode):
        return "directory"
    if stat.S_ISREG(mode):
        return "file"
    if stat.S_ISLNK(mode):
        return "symlink"
    return "other"


def classify(name: str, kind: Kind) -> tuple[PreviewKind, str | None, str | None]:
    """``(preview_kind, language, mime_type)`` from the name alone.

    Unknown extensions default to text preview: most files an agent writes
    are human-readable, and a text preview of a binary is harmless (the
    bounded read shows replacement characters). Only known binary formats
    go straight to download.
    """
    if kind != "file":
        return "unavailable", None, None
    suffix = Path(name).suffix.lower()
    mime = mimetypes.guess_type(name, strict=False)[0]
    if suffix in (".md", ".markdown"):
        return "markdown", "markdown", "text/markdown"
    if suffix in INLINE_TYPES:
        return ("pdf" if suffix == ".pdf" else "image"), None, INLINE_TYPES[suffix]
    if suffix in BINARY_EXTENSIONS:
        return "download_only", None, mime or "application/octet-stream"
    if suffix in LANGUAGES:
        return "code", LANGUAGES[suffix], mime or "text/plain"
    return "text", None, mime or "text/plain"


def _entry(name: str, path: str, st: os.stat_result) -> WorkspaceEntry:
    kind = _kind(st.st_mode)
    preview_kind, language, mime = classify(name, kind)
    return WorkspaceEntry(
        name=name,
        path=path,
        kind=kind,
        size=st.st_size if kind == "file" else None,
        modified_at_unix=st.st_mtime,
        preview_kind=preview_kind,
        language=language,
        mime_type=mime,
    )


def etag_of(st: os.stat_result) -> str:
    return f'"{st.st_ino:x}-{st.st_mtime_ns:x}-{st.st_size:x}"'


@dataclass
class OpenFile:
    """One validated, already open regular file: the descriptor IS the authority for every byte served from it.

    Two callers may reach ``close``: the ``chunks`` generator when the
    stream ends, and the response's background task. Only the FIRST call
    may touch the kernel: after ``os.close`` the number can belong to any
    new connection, so a second ``os.close`` would kill a live socket and
    poison its event-loop transport (the `/api/v1/runs` 500 of issue
    2578). ``close`` therefore retires the descriptor to ``-1`` and a
    later call does nothing.
    """

    fd: int
    stat: os.stat_result
    entry: WorkspaceEntry

    def chunks(self, start: int, end: int) -> Iterator[bytes]:
        """Bytes ``[start, end]`` of the open descriptor, never more than one chunk in memory; closes the descriptor at the end."""
        try:
            offset = start
            while offset <= end:
                chunk = os.pread(self.fd, min(STREAM_CHUNK, end - offset + 1), offset)
                if not chunk:
                    break
                offset += len(chunk)
                yield chunk
        finally:
            self.close()

    def close(self) -> None:
        fd, self.fd = self.fd, -1
        if fd >= 0:
            os.close(fd)


class WorkspaceService:
    """Descriptor-relative, no-follow reads under one workspace root."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def _walk(self, parts: tuple[str, ...]) -> int:
        """Open every directory of ``parts`` in turn, each relative to the previous descriptor, and return the last one."""
        try:
            # O_NOFOLLOW on the root too: a run that replaces its own
            # `workspace` entry with a symlink must not move the whole
            # confinement somewhere else.
            fd = os.open(self._root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        except FileNotFoundError as exc:
            raise WorkspaceError(404, "the workspace directory does not exist yet") from exc
        except OSError as exc:
            raise WorkspaceError(403, "the workspace root is unavailable") from exc
        for part in parts:
            try:
                nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
            except OSError as exc:
                refused = _refused(exc, part, fd)
                os.close(fd)
                raise refused from exc
            os.close(fd)
            fd = nxt
        return fd

    def entries(self, raw: str) -> WorkspaceEntriesResponse:
        parts = split_relative(raw)
        fd = self._walk(parts)
        rows: list[WorkspaceEntry] = []
        truncated = False
        try:
            with os.scandir(fd) as it:
                for dirent in it:
                    if len(rows) == ENTRY_LIMIT:
                        truncated = True
                        break
                    try:
                        st = dirent.stat(follow_symlinks=False)
                    except FileNotFoundError:
                        continue  # removed between readdir and stat: a later poll shows the parent without it
                    rows.append(_entry(dirent.name, "/".join((*parts, dirent.name)), st))
        finally:
            os.close(fd)
        rows.sort(key=lambda e: (e.kind != "directory", e.name))
        return WorkspaceEntriesResponse(path=raw, entries=rows, truncated=truncated)

    def stat(self, raw: str) -> WorkspaceStatResponse:
        parts = split_relative(raw)
        if not parts:
            raise WorkspaceError(400, "the workspace root is a directory, list it with /entries")
        fd = self._walk(parts[:-1])
        try:
            st = os.stat(parts[-1], dir_fd=fd, follow_symlinks=False)
        except OSError as exc:
            raise _refused(exc, parts[-1], fd) from exc
        finally:
            os.close(fd)
        return WorkspaceStatResponse(entry=_entry(parts[-1], raw, st), etag=etag_of(st))

    def open_file(self, raw: str) -> OpenFile:
        """Open one regular file with no-follow on every component; the caller owns the descriptor."""
        parts = split_relative(raw)
        if not parts:
            raise WorkspaceError(400, "the workspace root is a directory, not a file")
        dir_fd = self._walk(parts[:-1])
        try:
            # O_NONBLOCK: opening a FIFO must never block the server; the
            # fstat below then refuses it like every other non-regular entry.
            fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=dir_fd)
        except OSError as exc:
            raise _refused(exc, parts[-1], dir_fd) from exc
        finally:
            os.close(dir_fd)
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            os.close(fd)
            raise WorkspaceError(403, f"{parts[-1]!r} is not a regular file; only regular files are read")
        return OpenFile(fd=fd, stat=st, entry=_entry(parts[-1], raw, st))

    def text(self, raw: str) -> WorkspaceTextResponse:
        opened = self.open_file(raw)
        try:
            data = os.pread(opened.fd, TEXT_PREVIEW_BYTES, 0)
        finally:
            opened.close()
        return WorkspaceTextResponse(
            path=raw,
            text=data.decode("utf-8", errors="replace"),
            size=opened.stat.st_size,
            returned_bytes=len(data),
            truncated=opened.stat.st_size > TEXT_PREVIEW_BYTES,
            language=opened.entry.language,
            encoding="utf-8",
            etag=etag_of(opened.stat),
        )


def _refused(exc: OSError, part: str, dir_fd: int) -> WorkspaceError:
    """The refusal for one component that failed to open under ``dir_fd``; a symlink is named as such whichever errno the no-follow open gave."""
    try:
        is_link = stat.S_ISLNK(os.stat(part, dir_fd=dir_fd, follow_symlinks=False).st_mode)
    except OSError:
        is_link = False
    if is_link or exc.errno == errno.ELOOP:
        return WorkspaceError(403, f"{part!r} is a symlink; symlinks are never followed")
    if exc.errno == errno.ENOTDIR:
        return WorkspaceError(404, f"{part!r} is not a directory")
    if exc.errno == errno.ENOENT:
        return WorkspaceError(404, f"{part!r} does not exist")
    if exc.errno == errno.ENXIO:
        return WorkspaceError(403, f"{part!r} is a special file; only regular files are read")
    return WorkspaceError(403, f"{part!r} cannot be opened ({errno.errorcode.get(exc.errno or 0, exc.errno)})")
