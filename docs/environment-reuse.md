# Reusing run environments

New runs automatically reuse Python dependencies from the latest compatible,
Setup-approved run in the **same task output directory**. Compatibility includes
the configured Conda base name, platform, and supported Python version.

Each new run gets its own small writable virtual environment. Installed packages
remain in earlier environments and are loaded without copying them. A child can
install replacement versions without modifying its parent; subsequent children
inherit those replacements too. Python console entry points use the child's
interpreter. Non-Python commands remain available through ancestor `bin` paths.

Setup still executes for every run and verifies that run's imports, data, tests,
and scoring contract. Dependency reuse never restores task readiness, data,
checkpoints, model caches, private graders, or a previous run's result.

To request a clean environment instead:

```yaml
run:
  reuse_environment: false
```

An inherited package cannot be uninstalled from its parent through the child's
package manager. Install a replacement in the child, or request a clean run when
removing an inherited dependency is necessary.

## Ownership and recovery

The manager holds an exclusive writer lease on its environment for its process
lifetime. A new run skips active bases and holds shared leases on all selected
ancestors. Resuming an ancestor while a child depends on it refuses the writer
lease. Existing launch confinement mounts ancestor prefixes read-only; only the
new run's prefix is writable by Setup. No mutable dependency files are hard-linked.

Availability is atomically published after verified Setup, under
`program-environment/available.json`. An overlay's ancestry is stored in
`program-environment/inheritance.json`, so Resume restores the same read paths
and leases. Interrupted overlay creation is rebuilt. Invalid metadata and paths
outside sibling run environment prefixes are rejected. Runtime wheels are cached
under the task output directory's `.cache/program-runtime/`.

Keep ancestor environment directories while descendants reference them; this
component never deletes previous runs or dependencies. Existing runs created
before this feature have no availability record and are not automatically adopted.
Docker image storage and non-Python task environments retain their existing lifecycle.

## Verification

```sh
PYTHONPATH=. python scripts/verify_environment_reuse.py --report .cache/environment-reuse-check.json
```

The check executes second- and third-generation environments, a real local wheel
installation and child-only upgrade, inherited console entry points, separate
process lease contention, invalid private paths, incomplete Setup, interrupted
creation, Resume, and interpreter launch through a directory alias. It measures
the new overlay's allocated bytes and creation time, then deletes its temporary
environments. The measurement uses a small fixture dependency, not model-training
performance.
