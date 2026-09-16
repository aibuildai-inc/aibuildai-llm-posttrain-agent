"""Map physical GPU indices to sandbox device nodes through NVML."""
from __future__ import annotations

import glob
import os
import stat
from collections.abc import Sequence

from infra.host_resource.nvml import query_gpu_minors


async def device_minors(
    gpu_indices: Sequence[int], timeout_s: float
) -> list[int]:
    """Translate physical GPU indices to ``/dev/nvidiaN`` device minors."""
    minors = await query_gpu_minors(timeout_s)
    missing = set(gpu_indices).difference(minors)
    if missing:
        raise AssertionError(
            f"GPU indices {sorted(missing)} have no /dev/nvidiaN minor "
            f"mapping from NVML (reported mappings: {sorted(minors)})"
        )
    return [minors[index] for index in gpu_indices]


def enumerate_nvidia_cap_nodes() -> list[str]:
    """Return present MIG capability device nodes."""
    return [
        path
        for path in sorted(glob.glob("/dev/nvidia-caps/nvidia-cap*"))
        if _bindable(path)
    ]


def _bindable(path: str) -> bool:
    try:
        return stat.S_IFMT(os.stat(path).st_mode) in (stat.S_IFCHR, stat.S_IFREG)
    except OSError:
        return False
