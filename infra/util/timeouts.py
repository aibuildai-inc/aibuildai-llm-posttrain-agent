"""Timeout constants consumed across the pipeline.

Central table. Any timeout used in more than one place lives here instead of as a scattered ``timeout=60`` / ``timeout=5.0`` magic number, so tuning one value does not require a grep-and-hope pass. Each entry carries the rationale for its specific value.
"""

# ── Host GPU query ───────────────────────────────────────────────────────────

NVIDIA_SMI_TIMEOUT_S: float = 60.0
"""Let nvidia-smi wait behind other users of a busy driver without hanging
forever."""
