"""Incrementally read WorkUnit metric series from ``metrics.jsonl``.

The file is append-only. Each ``aibuildai.init()`` starts one segment. Each
metric key becomes one curve. A missing, empty, or invalid file gives no
series. Other errors propagate.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field as dataclass_field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # engine / infra are imported function-locally below; the annotation-only
    # name stays here per the one-placement-per-module rule.
    from engine.status import StatusState


def as_int(v: object) -> int | None:
    """Convert an integer JSON value, or return ``None``."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        return int(v) if v == int(v) else None
    return None


def as_float(v: object) -> float | None:
    """Convert a numeric JSON value, or return ``None``."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    return None


@dataclass(frozen=True)
class RecordedSeries:
    """One metric's current x/y series inside one trace segment, plus its x-axis label and revision. A new revision means the curve must be plotted again."""

    xs: list[float]
    ys: list[float]
    x_label: str
    revision: tuple[int, int]


@dataclass(frozen=True)
class MetricSegment:
    """One segment and its metric keys in first-recorded order."""

    index: int
    fields: list[str]


@dataclass
class _MetricTail:
    """One metric's values and its still-valid x-axis choices."""

    ys: list[float] = dataclass_field(default_factory=list)
    epochs: list[float] | None = dataclass_field(default_factory=list)
    steps: list[float] | None = dataclass_field(default_factory=list)
    indexes: list[float] | None = None
    revision: int = 0

    def append(self, epoch: float | None, step: int | None, value: float) -> None:
        self.ys.append(value)
        if self.epochs is not None:
            if epoch is None or (self.epochs and not self.epochs[-1] < epoch):
                self.epochs = None
            else:
                self.epochs.append(epoch)
        if self.steps is not None:
            if step is None:
                self.steps = None
            else:
                self.steps.append(float(step))
        if self.epochs is None and self.steps is None:
            if self.indexes is None:
                self.indexes = [float(i) for i in range(1, len(self.ys) + 1)]
            else:
                self.indexes.append(float(len(self.ys)))
        self.revision += 1

    def series(self, generation: int) -> RecordedSeries:
        if self.epochs is not None:
            return RecordedSeries(
                self.epochs, self.ys, "epoch", (generation, self.revision)
            )
        if self.steps is not None:
            return RecordedSeries(
                self.steps, self.ys, "step", (generation, self.revision)
            )
        if self.indexes is None:
            raise AssertionError("metric series has no valid x-axis")
        return RecordedSeries(
            self.indexes, self.ys, "step", (generation, self.revision)
        )


class MetricSeriesTail:
    """Follow one metrics file and return its segments and series.

    The reader keeps its fold state and reads only appended bytes. It resets
    when the path, inode, size, or recorded suffix changes.

    Each ``start`` event opens a segment. Events before the first ``start``
    belong to the first segment.
    """

    def __init__(self) -> None:
        self._generation = 0
        self._reset("")

    # The rewrite detector's window: the fold re-checks this many of its last
    # folded bytes against the disk on every call. A trace line carries
    # elapsed_seconds floats, so two different writes agreeing on the final
    # window AND the byte count is not a shape any writer of this file has.
    _FINGERPRINT_BYTES = 64

    def _reset(self, path: str) -> None:
        self._generation += 1
        self._path = path
        self._ino: int | None = None
        self._offset = 0
        self._tail_bytes = b""
        self._remainder = b""
        # One insertion-ordered metric map per segment, INCLUDING data-less
        # segments: a segment's index is its position here, so t-numbering
        # stays stable when earlier segments carry no data.
        self._segments: list[dict[str, _MetricTail]] = []

    def _fold(self, line: str) -> None:
        line = line.strip()
        if not line:
            return
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            return
        if not isinstance(ev, dict):
            return
        if ev.get("event_type") == "start" or not self._segments:
            self._segments.append({})
        m = ev.get("metrics")
        if isinstance(m, dict):
            epoch = as_float(ev.get("epoch"))
            step = as_int(ev.get("step"))
            for name, raw in m.items():
                metric = self._segments[-1].setdefault(name, _MetricTail())
                value = as_float(raw)
                if value is not None:
                    metric.append(epoch, step, value)

    def resolve(
        self,
        attempt_dir: str,
        status: "StatusState",
    ) -> list[MetricSegment]:
        """Path-resolve + guard + incremental follow: ``[]`` when reading is skipped (see ``_guarded_jsonl_path``)."""
        path = _guarded_jsonl_path(attempt_dir, status)
        if path is None:
            return []
        return self._current(str(path))

    def _current(self, path: str) -> list[MetricSegment]:
        if path != self._path:
            self._reset(path)
        try:
            fd = os.open(path, os.O_RDONLY)
        except OSError:
            self._reset(path)
            return []
        try:
            st = os.fstat(fd)
            suffix = (
                os.pread(
                    fd, len(self._tail_bytes), self._offset - len(self._tail_bytes)
                )
                if self._tail_bytes
                else b""
            )
            # Refold from scratch when the file is not the one the fold
            # followed: another inode, a shrink, or folded bytes that no
            # longer match the disk (any rewrite, whatever its size).
            if (
                st.st_ino != self._ino
                or st.st_size < self._offset
                or suffix != self._tail_bytes
            ):
                self._reset(path)
                self._ino = st.st_ino
            if st.st_size > self._offset:
                chunk = os.pread(fd, st.st_size - self._offset, self._offset)
                self._offset += len(chunk)
                self._tail_bytes = (self._tail_bytes + chunk)[
                    -self._FINGERPRINT_BYTES :
                ]
                lines = (self._remainder + chunk).split(b"\n")
                # The piece after the last newline is a line still being
                # written; hold it AS BYTES and complete it with the next
                # chunk — the read boundary is a byte offset, so decoding a
                # torn piece would corrupt a multi-byte character forever.
                # Only complete lines are decoded, so their bytes are whole.
                self._remainder = lines.pop()
                for line in lines:
                    self._fold(line.decode("utf-8"))
        finally:
            os.close(fd)
        return [
            MetricSegment(index + 1, list(keys))
            for index, keys in enumerate(self._segments)
            if keys
        ]

    def series(self, segment: int, field: str) -> RecordedSeries:
        """The accumulated series for one metric in one 1-based segment."""
        if segment < 1:
            raise ValueError(f"segment must be at least 1, got {segment}")
        if segment > len(self._segments):
            return RecordedSeries([], [], "epoch", (self._generation, 0))
        metric = self._segments[segment - 1].get(field)
        if metric is None or not metric.ys:
            return RecordedSeries([], [], "epoch", (self._generation, 0))
        return metric.series(self._generation)


def _guarded_jsonl_path(
    attempt_dir: str,
    status: "StatusState",
) -> "Path | None":
    """Return the attempt's metrics path when the WorkUnit can have one."""
    from engine.status import StatusState

    if not attempt_dir or status == StatusState.PENDING:
        return None
    return Path(attempt_dir) / "metrics.jsonl"
