"""Curator for the BUILT-IN corpus: the cross-task pattern memories.

A source-only maintenance tool, and the built-in corpus under ``memory/`` is its only subject. One way to produce a pattern document, and one way to ask for it: every pattern is rebuilt from the current task memories of its category. The incremental updater beside it was a second way to write the same file from a different input, so a document could disagree with the tasks it claims to summarize depending on which path last touched it. A user's own task memory is not built here: one Agent writes it from the runs' saved evidence (``memory.memorize``), so nothing in this file is on the local memorize path.

Usage:

```
python -m memory.curate                                    # rebuild every pattern memory
python -m memory.curate --model claude-haiku-4-5-20251001  # override model
```
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from infra.backends.one_shot import agent_turn
from infra.backends.spec import AgentSpec
from memory.store import MemoryStore

# The curator has no run config to read a model from, so it names the one it
# writes the built-in corpus with. ``--model`` overrides it.
PATTERN_MODEL = "claude-haiku-4-5-20251001"

# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

PATTERN_SYSTEM = (
    "You maintain cross-task ML pattern documents. Be specific and actionable. "
    "Cite task names and scores as evidence. "
    "Output ONLY the markdown document, no preamble."
)

PATTERN_REBUILD_PROMPT = """Create a cross-task pattern document for the "{category}" category from these task memories.

Rules:
- Focus on patterns across 2+ tasks (not one-off observations)
- Be specific: cite task names and scores as evidence
- Include anti-patterns (things that consistently fail)
- Keep under 80 lines

Output ONLY markdown:

# Pattern: {category}

## When this applies
- <task characteristics>

## Proven patterns
- bullet (with evidence)

## Common pitfalls
- bullet

## Model selection guide
- concise decision tree

## Feature engineering patterns
- bullet

## Tuning patterns
- bullet

---
TASK MEMORIES:

{task_memories}
"""


async def rebuild_pattern(category: str, model: str, store: MemoryStore) -> Path | None:
    """Rebuild a pattern memory from all task memories in that category."""
    tasks_by_category = store.tasks_by_category()
    tasks = tasks_by_category.get(category, [])
    task_memories = []
    for t in tasks:
        tf = store.tasks_dir / f"{t}.md"
        if tf.exists():
            task_memories.append(f"## {t}\n\n{tf.read_text()}")
    if not task_memories:
        print(f"[skip] no task memories for pattern {category}")
        return None

    all_memories = "\n\n---\n\n".join(task_memories)
    if len(all_memories) > 150_000:
        all_memories = all_memories[:150_000] + "\n...[truncated]"

    prompt = PATTERN_REBUILD_PROMPT.format(category=category, task_memories=all_memories)
    answer = await agent_turn(
        spec=AgentSpec(
            name="pattern-curator",
            instructions=PATTERN_SYSTEM,
            model=model,
            tools=(),
            # A maintainer's foreground command, with the same unbounded
            # patience the rest of this tool has always had.
            cwd=str(store.root),
        ),
        prompt=prompt,
    )
    if not isinstance(answer, str):
        raise AssertionError("the pattern curator returned a structured result, not a document")
    result = answer.strip()
    if not result:
        raise RuntimeError(f"empty LLM result for pattern {category}")
    if "\n# " in result:
        result = "# " + result.split("\n# ", 1)[1]

    out_path = store.patterns_dir / f"{category}.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(result + "\n")
    print(f"[ok] rebuilt {out_path}")
    return out_path


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main_async(args: argparse.Namespace) -> int:
    store = MemoryStore.builtin()
    model = args.model or PATTERN_MODEL
    for category in store.tasks_by_category():
        await rebuild_pattern(category, model=model, store=store)
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Rebuild the built-in pattern memories")
    p.add_argument("--model", help=f"model override (default: {PATTERN_MODEL})")
    args = p.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
