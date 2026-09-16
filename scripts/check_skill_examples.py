"""Verify every meta-search-design example package with the real loader.

Each example folder that ships a ``search/`` package must also ship the
``input_payload.json`` that builds its ``SEARCH_TYPE``. The package runs
through the same mechanical verifier a generated Meta package faces, so a
stale example fails this gate instead of misleading the MetaAgent that
reads it.

All examples are verified in this one process. The verifier's import is the
whole cost of a check (about 4.5 s per process, against under 0.1 s of
verification per package), so one process per example made this gate take
minutes; each example loads under its own module name, so the packages
cannot shadow each other, and a package that fails leaves the next one's
verdict untouched. A contract failure prints its reason; any other error
while checking one example prints its traceback and counts as that
example's failure, the same two outcomes the verifier's own process exit
gave (its contract exit and its infrastructure exit). What the examples do
share in one process is the Search kind namespace: a Search that declares
a ``kind`` already declared by an earlier example or by the product is
refused at class formation, exactly as it would be when that package runs
in a product process, so such a refusal is a defect in the example, not in
this gate. No shipped example declares a kind.
"""

from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
EXAMPLES_ROOT = (
    REPO_ROOT
    / "plugins/aibuildai-builtin-marketplace/aibuildai-builtin"
    / "skills/meta-search-design/references/examples"
)


def main() -> int:
    # The verifier process ran with PYTHONDONTWRITEBYTECODE; the same rule
    # here keeps bytecode out of the shipped example trees on any host.
    sys.dont_write_bytecode = True
    from engine.builtin.meta.agents.meta.package_verifier import (
        PackageContractError,
        verify,
    )

    packaged = sorted(
        example
        for example in EXAMPLES_ROOT.iterdir()
        if (example / "search").is_dir()
    )
    if not packaged:
        print(f"no example packages under {EXAMPLES_ROOT}", file=sys.stderr)
        return 1
    failures = 0
    for example in packaged:
        payload = example / "input_payload.json"
        if not payload.is_file():
            print(f"{example.name}: missing input_payload.json", file=sys.stderr)
            failures += 1
            continue
        module_name = f"aibuildai_meta_example_{example.name.replace('-', '_')}"
        try:
            verify(
                example,
                package_relpath="search",
                module_name=module_name,
                input_payload=json.loads(payload.read_text(encoding="utf-8")),
            )
        except PackageContractError as exc:
            print(f"{example.name}: FAILED", file=sys.stderr)
            print(f"PackageContractError: {exc}", file=sys.stderr)
            failures += 1
        except (Exception, SystemExit):
            # SystemExit too: a package that exits at import was one failed
            # verifier process before, and stays one failed example now. A
            # KeyboardInterrupt still stops the gate, as it stopped the loop.
            print(f"{example.name}: FAILED", file=sys.stderr)
            traceback.print_exc()
            failures += 1
        else:
            print(f"{example.name}: ok")
    if failures:
        print(f"{failures} example package(s) failed verification", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
