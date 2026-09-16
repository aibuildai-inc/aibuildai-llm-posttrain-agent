"""Build local subprocess confinement from resolved resource facts."""
from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from infra.host_resource.confinement import Confinement


def build_subproc_confinement(
    *,
    cgroup: Path,
    device_indices: tuple[int, ...],
    read_paths: Sequence[str],
    write_paths: Sequence[str],
    system_read_paths: Sequence[str],
    system_write_paths: Sequence[str],
    cwd: str,
    apply_bwrap: bool,
    dev_nodes: Sequence[str] = (),
    cwd_writable: bool = True,
) -> Confinement:
    """Build confinement after the caller completes all blocking host reads.

    ``cwd`` is the subprocess working directory. The kernel ALWAYS resolves symlinks on chdir, so this resolves ``cwd`` and (a) binds the resolved form into the sandbox and (b) hands the resolved form back as ``Confinement.cwd`` for the call site to launch with — guaranteeing cwd == its bwrap bind so a symlinked playground cannot drop the process to ``/``.

    ``cgroup`` is the WorkUnit's own cgroup, which the resource tree created and bounded before this call. The unit's limits are ON it; nothing is passed here. There is no longer an axis for whether a unit is capped -- a run is always bounded, and that was never a choice worth offering.

    ``apply_bwrap`` False (from ``resources.sandbox.enable`` off): returns a Confinement with ``apply_bwrap`` off, so ``wrap`` runs the inner command verbatim instead of wrapping it in bwrap. The process still joins its cgroup, so its memory and process limits and its GPU pinning are unaffected; only the bwrap read/write confinement is lost.
    """
    # Fail fast: you cannot chdir into / bwrap-bind a path that does not exist,
    # and a missing cwd surfaces here far more clearly than as a cryptic
    # bwrap/exec error at launch. Raised (not a bare ``assert``) so it survives
    # ``python -O`` in the frozen release binary.
    if not Path(cwd).is_dir():
        raise AssertionError(
            f"build_subproc_confinement: cwd must be an existing directory, got {cwd!r}"
        )
    resolved_cwd = str(Path(cwd).resolve())
    # Escape hatch: skip bwrap entirely (apply_bwrap off). Only the namespace sandbox
    # and the read/write confinement are dropped -- the process still joins its
    # cgroup, so the kernel still bounds it.
    if not apply_bwrap:
        return Confinement(
            cgroup=cgroup,
            apply_bwrap=False,
            cwd=resolved_cwd,
        )
    # The cwd's bind MUST be the resolved path (the kernel resolves the
    # subprocess chdir). Bind it read-write or read-only as requested, and drop
    # any duplicate resolved form from the call site's lists. The de-dup filter
    # matches only the EXACT ``resolved_cwd`` string, not arbitrary unresolved
    # aliases (trailing-slash or symlink variants of the same dir) — a future
    # caller must not assume broader path normalization here.
    rw_binds = list(write_paths)
    if cwd_writable and resolved_cwd not in rw_binds:
        rw_binds.append(resolved_cwd)
    ro_binds = [p for p in read_paths if p != resolved_cwd]
    if not cwd_writable and resolved_cwd not in ro_binds:
        ro_binds.append(resolved_cwd)
    return Confinement(
        cgroup=cgroup,
        apply_bwrap=True,
        cwd=resolved_cwd,
        system_read_paths=tuple(system_read_paths),
        system_write_paths=tuple(system_write_paths),
        hard_ro=tuple(ro_binds),
        rw_paths=tuple(rw_binds),
        dev_nodes=tuple(dev_nodes),
        # The bound cards are numbered from zero inside bwrap.
        pinned_device_count=len(device_indices),
    )
