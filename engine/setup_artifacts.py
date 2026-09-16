"""Read the files that Setup freezes for one run.

Setup writes the score program, README, and public dir after it reads the task folder. The rest of the run reads those same files. This module owns that file I/O.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

from engine.paths import RunPaths
from infra.util.paths import canonical_path, is_within

if TYPE_CHECKING:
    from config import AgentConfig


# The task statement for a role whose directory grant is the raw task folder:
# no frozen README stands between such a role and the user's own files.
_SETUP_README = (
    "Your task is described inside the task folder {folder}. Read the folder to "
    "learn what this run must produce and what counts as a good result; no one "
    "has summarized it for you."
)


def read_public_dir(public_dir: str, task_folder: str) -> tuple[str, ...]:
    """Prove every SETUP-authored symlink in the public dir is safe, and answer with the real paths they read.

    This is the ONE shared public dir reader. The SETUP gate calls it and catches the ``ValueError`` below, so the agent repairs the link in its own session; after that gate every link in the tree resolves inside the task folder, and the launch boundary binds those resolved targets as the public resource's own link view. It raises ``ValueError`` -- a builtin both callers name trivially.

    For every symlink at any depth of the tree -- the walk never descends THROUGH a link, so nested links inside the user's own material are neither seen, guarded, nor bound; in a sandbox they dangle, fail-closed -- the link's real target must exist and resolve inside the task folder. ``is_within`` resolves BOTH sides through symlinks and compares whole path COMPONENTS, so a sibling-escape (a link to ``/data/taskA-answers`` while the task is ``/data/taskA`` -- ``str.startswith`` would wrongly call it inside) and a chain that redirects back out (``is_within`` resolves the whole chain) are both rejected. The paths returned are the fully RESOLVED real targets (``canonical_path``), deduplicated in a stable order because two links may point at one directory, so the sandbox binds exactly what the links truly read and the bind list is the same across a resume.
    """
    root = Path(public_dir)
    # A task that hands the run no data at all still gets a real, empty dir
    # to read, so DATA_DIR always names something that is there.
    root.mkdir(parents=True, exist_ok=True)
    targets: list[str] = []
    for parent, dir_names, file_names in os.walk(root, followlinks=False):
        for name in sorted(list(dir_names) + file_names):
            entry = Path(parent, name)
            if not entry.is_symlink():
                continue
            rel = str(entry.relative_to(root))
            target = canonical_path(entry)
            if not is_within(target, task_folder):
                raise ValueError(
                    f"the public link {rel!r} in {public_dir} points at "
                    f"{target}, which is outside the task "
                    f"folder {task_folder}. The run reads only material "
                    f"inside the task folder; a link resolving anywhere else "
                    f"-- a sibling like a '-answers' directory, or a link "
                    f"that itself redirects out -- would hand it something "
                    f"it must not see. Point the link inside the task "
                    f"folder, or drop it."
                )
            if not target.exists():
                raise ValueError(
                    f"the public link {rel!r} in {public_dir} points at "
                    f"{target}, but nothing exists there. The work would "
                    f"see the link name but could not read data through it. "
                    f"Point the link at material that exists inside the task "
                    f"folder, or drop it."
                )
            if Path(os.readlink(entry)) != target:
                entry.unlink()
                entry.symlink_to(target)
            targets.append(str(target))
        # Never descend THROUGH a symlinked directory: its contents are the
        # user's own material, not the SETUP-authored tree, so its interior
        # links are out of this guard's scope by construction.
        dir_names[:] = [
            name for name in dir_names if not Path(parent, name).is_symlink()
        ]
    return tuple(dict.fromkeys(targets))


def task_readme(config: "AgentConfig", run_paths: RunPaths, *, public: bool) -> str:
    """The task statement rendered into one role's system prompt.

    ``public`` is the role's task data source as its directory grants declare it, and nothing else decides it. A role granted the public data dir reads the frozen ``README.md`` — the user's own words, copied out of the task folder by SETUP and never rewritten. A role granted the raw task folder instead is pointed at that folder, whenever it runs: Setup runs before the file exists, the per-design Router runs long after, and both read the folder because that is their grant. A public role reaching this before the file exists is a real ordering bug, and says so.
    """
    if not public:
        return _SETUP_README.format(folder=config.task_folder)
    path = Path(run_paths.readme_path)
    if not path.is_file():
        raise AssertionError(
            f"a role granted the public data dir needs the frozen README, but "
            f"{path} does not exist; SETUP writes it, and only roles granted the "
            "raw task folder may run before then"
        )
    return path.read_text()
