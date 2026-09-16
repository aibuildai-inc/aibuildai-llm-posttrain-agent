"""Check CUDA visibility with the task interpreter."""
from __future__ import annotations

from infra.process.command import run_captured

_CUDA_PROBE_PY = """import sys
try:
    import torch
except Exception as exc:
    sys.stderr.write(repr(exc))
    sys.exit(4)
sys.stderr.write(f"torch {torch.__version__} cuda={torch.version.cuda!r}")
sys.exit(0 if torch.cuda.is_available() else 3)
"""


async def probe_cuda_visibility(
    *, interpreter: str, env: dict, timeout_s: float
) -> tuple[bool, str]:
    """Return whether the task interpreter sees its assigned CUDA devices."""
    result = await run_captured(
        [interpreter, "-c", _CUDA_PROBE_PY], timeout_s, env=env
    )
    stderr = result.stderr.strip()
    if result.returncode == 0:
        return True, ""
    if result.returncode == 3:
        reason = "torch imported but CUDA is unavailable"
    elif result.returncode == 4:
        reason = f"torch import failed: {stderr}"
    else:
        reason = f"probe exited {result.returncode}: {stderr}"
    visible = env.get("CUDA_VISIBLE_DEVICES", "<unset>")
    return False, (
        f"GPU-visibility preflight failed: CUDA_VISIBLE_DEVICES={visible}, but "
        f"{reason}. Refusing to train on CPU. Installed build: "
        f"{stderr or '<no probe stderr>'}. Check the host NVIDIA driver and "
        "device access, and install a driver-compatible CUDA torch build "
        "instead of a CPU-only build."
    )
