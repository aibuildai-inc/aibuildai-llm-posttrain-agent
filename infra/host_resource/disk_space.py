"""Free space on the host, the one capacity fact the product reads.

cgroup v2 has no capacity dimension. ``io.max`` caps bandwidth and IOPS; there
is no file, in any controller, that says "this directory may hold at most N
bytes". The product therefore bounds nothing per directory. It reads the free
space of the filesystem the run writes into and uses it for ADMISSION: below
the floor, stop launching new work.
"""

from __future__ import annotations

import os
from pathlib import Path


def free_bytes(path: Path) -> int:
    """Free space on the filesystem holding ``path``. One syscall.

    This is the host-wide floor. It protects the MACHINE, and on a shared host it protects other people's data. It drives ADMISSION -- below the floor, stop launching new work -- not a kill. A training run three hours in is not the right thing to shoot because the disk got tight; stop the bleeding before amputating.
    """
    st = os.statvfs(path)
    return st.f_bavail * st.f_frsize
