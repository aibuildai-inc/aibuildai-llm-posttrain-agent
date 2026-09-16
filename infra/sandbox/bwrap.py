"""Pure bwrap arg-list emitter — a neutral leaf with no product coupling.

Extracted from the role bind translator (engine/work_unit/agent/policy.py) so any layer (engine, backends, infra) can import build_bwrap_argv without pulling in engine.work_unit.agent.policy or Agent class declarations.

Layer: infra leaf (infra.sandbox). No imports from engine or backends, or output, and none from the rest of infra. May only import stdlib.
"""

from __future__ import annotations

from collections.abc import Sequence


_GPU_CONTROL_NODES: tuple[str, ...] = (
    "nvidiactl",
    "nvidia-uvm",
    "nvidia-uvm-tools",
)


def device_dev_nodes(minors: Sequence[int]) -> list[str]:
    """The GPU device-node set for a K-card claim: one per-card node ``/dev/nvidia{minor}`` for each DEVICE MINOR in ``minors`` followed by the three shared control nodes (nvidiactl / nvidia-uvm / nvidia-uvm-tools, bound ONCE regardless of K). The sandbox sees the K cards renumbered to in-sandbox cuda:0..cuda:K-1.

    ``minors`` are ``/dev/nvidiaN`` device minors, NOT nvidia-smi indices — the two numberings differ on real hosts. Callers translate a claim's nvidia-smi indices with ``infra.sandbox.gpu_nodes.device_minors`` first.

    Kept PURE (no filesystem access) — it merely formats names. A caller hands the result to build_bwrap_argv via ``dev_nodes`` to bind exactly these cards."""
    return [
        *(f"/dev/nvidia{minor}" for minor in minors),
        *(f"/dev/{name}" for name in _GPU_CONTROL_NODES),
    ]


# Fixed namespace structure. Host paths are supplied by Confinement.
_FIXED_NAMESPACE: tuple[str, ...] = (
    "--symlink",
    "usr/lib",
    "/lib",
    "--symlink",
    "usr/lib64",
    "/lib64",
    "--symlink",
    "usr/bin",
    "/bin",
    "--symlink",
    "usr/sbin",
    "/sbin",
    "--proc",
    "/proc",
    "--dev",
    "/dev",
    "--tmpfs",
    "/tmp",
)


def build_bwrap_argv(
    ro_paths: Sequence[str],
    rw_paths: Sequence[str],
    *,
    hard_ro: Sequence[str] = (),
    dev_nodes: Sequence[str] = (),
    mask_files: Sequence[str] = (),
    mask_dirs: Sequence[str] = (),
) -> list[str]:
    """Emit the bwrap bind arg list (pure: no config / filesystem reads).

    Order:
      1. fixed namespace structure (proc, dev, private /tmp, and system links),
      2. role ro-binds via --ro-bind-try (resolved add-dir paths), then hard_ro via hard --ro-bind,
      3. MASKS that hide a sub-path INSIDE an include bind — emitted AFTER all ro-binds so the later bind wins (bwrap's own --tmpfs /tmp / --dev /dev use the same later-wins mechanism): each ``mask_files`` entry via ``--ro-bind /dev/null <path>`` (a file shadow), each ``mask_dirs`` entry via ``--tmpfs <path>`` (an empty-dir shadow). Used for the framework's own launcher dir, which sits inside a bind the role needs; task data is kept out of a role by not binding it, never by masking it.
      4. rw-binds (every path in ``rw_paths``) AFTER all ro-binds so a writable subdir overrides a read-only parent,
      5. GPU dev-binds: each path in ``dev_nodes`` via HARD --dev-bind.

    Hard vs try bind by source:
      - ro_paths (role-policy reads): --ro-bind-try (may legitimately be absent, e.g. val_inputs_smoke before smoke setup; bwrap skips an absent source at exec time without a Python precheck — no silent drop, no TOCTOU gap),
      - hard_ro: HARD --ro-bind (system and input paths that MUST exist),
      - rw_paths: HARD --bind,
      - dev_nodes: HARD --dev-bind (caller-supplied GPU device-node paths that MUST exist).

    hard_ro holds input and system paths that must exist. The write side has no soft form. A caller must pass every write path in ``rw_paths``. A missing required path fails before this call. build_bwrap_argv binds every path it receives.

    dev_nodes is the single seam for GPU device binds: the caller decides WHICH device nodes to bind. A unit with a GPU claim passes ``device_dev_nodes(device_minors(claim))`` — the claim's cards translated to device minors (infra.sandbox.gpu_nodes) — and its call site ALSO sets CUDA_VISIBLE_DEVICES=0,..,K-1 (in-sandbox CUDA renumbers the accessible cards from 0). A unit without a claim passes NOTHING — no claim means the unit requested no GPU, never the run's visible set. build_bwrap_argv binds whatever set it is handed; HOW that set is chosen is the caller's concern.
    """
    # Host /tmp is NEVER a bind target (ro OR rw) — it is provided only as the
    # tmpfs. Guard every source that actually gets emitted as a bind, including
    # all caller-owned path lists, so a caller cannot re-bind host /tmp over the
    # private tmpfs and defeat isolation.
    if "/tmp" in (*ro_paths, *hard_ro, *rw_paths):
        raise AssertionError(
            "host /tmp must never be bound (it is provided as a tmpfs)"
        )
    argv: list[str] = list(_FIXED_NAMESPACE)
    # ro_paths are ROLE-POLICY reads that may LEGITIMATELY be absent at bind
    # time (e.g. {pipeline}/val_inputs_smoke does not exist until smoke setup
    # runs). --ro-bind-try makes bwrap bind the path if present and skip it at
    # EXEC time if absent — the decision happens at the moment of use, inside
    # bwrap, so there is no Python existence precheck (no silent drop) and no
    # TOCTOU gap. hard_ro paths MUST exist, so they stay HARD
    # --ro-bind and fail loud when absent.
    for path in ro_paths:
        argv += ["--ro-bind-try", path, path]

    for path in hard_ro:
        argv += ["--ro-bind", path, path]

    # Masks: hide a sub-path that sits INSIDE one of the include binds above
    # (the agent launcher dir). Emitted
    # AFTER the ro-binds so the later bind wins, and BEFORE rw/dev (which target
    # disjoint paths — the role's scratch writes, /dev/nvidia* — so no collision). A
    # file is shadowed by /dev/null; a dir by an empty tmpfs. The caller
    # Confinement stats each real path to pick the right bucket.
    for path in mask_files:
        argv += ["--ro-bind", "/dev/null", path]

    for path in mask_dirs:
        argv += ["--tmpfs", path]

    for path in rw_paths:
        argv += ["--bind", path, path]

    for dev in dev_nodes:
        argv += ["--dev-bind", dev, dev]

    return argv
