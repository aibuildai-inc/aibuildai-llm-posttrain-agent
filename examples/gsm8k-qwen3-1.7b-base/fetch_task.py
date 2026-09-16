#!/usr/bin/env python3
"""Materialize the GSM8K x Qwen3-1.7B-Base task folder.

Called by ``setup.sh``, which installs the two packages this needs. It downloads
the base weights and the benchmark splits from the Hugging Face Hub and writes
them into the task folder in the shape the task statement describes.
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

MODEL_REPO = "Qwen/Qwen3-1.7B-Base"
DATASET_REPO = "openai/gsm8k"
DATASET_CONFIG = "main"
# Weights, tokenizer and configuration only: the framework checkpoints and the
# duplicate formats some repositories carry are dead weight for a fine-tune.
MODEL_PATTERNS = ["*.json", "*.safetensors", "*.txt", "*.model", "*.jinja"]
HERE = Path(__file__).resolve().parent


def write_jsonl(rows: list[dict], path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    return len(rows)


def read_split(local_dir: Path, split: str) -> list[dict]:
    import pyarrow.parquet as parquet

    matches = sorted(local_dir.glob(f"{DATASET_CONFIG}/{split}-*.parquet"))
    if not matches:
        raise SystemExit(f"no {split} parquet under {local_dir}")
    rows: list[dict] = []
    for match in matches:
        table = parquet.read_table(match).to_pydict()
        for question, answer in zip(table["question"], table["answer"]):
            # The published answers carry the dataset's calculator annotations,
            # <<48/2=24>>, which are an artifact of how it was collected rather
            # than text a model should learn to emit.
            cleaned = answer
            while "<<" in cleaned and ">>" in cleaned:
                head, rest = cleaned.split("<<", 1)
                cleaned = head + rest.split(">>", 1)[1]
            rows.append({"question": question.strip(), "answer": cleaned.strip()})
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-dir", required=True)
    args = parser.parse_args()

    from huggingface_hub import snapshot_download

    task_dir = Path(args.task_dir).resolve()
    task_dir.mkdir(parents=True, exist_ok=True)

    print(f"[1/4] base model {MODEL_REPO} -> {task_dir / 'base_model'}", flush=True)
    snapshot_download(
        repo_id=MODEL_REPO,
        local_dir=str(task_dir / "base_model"),
        allow_patterns=MODEL_PATTERNS,
    )

    print(f"[2/4] benchmark splits {DATASET_REPO}", flush=True)
    staged = task_dir / ".gsm8k-parquet"
    snapshot_download(
        repo_id=DATASET_REPO,
        repo_type="dataset",
        local_dir=str(staged),
        allow_patterns=[f"{DATASET_CONFIG}/*.parquet"],
    )
    train = write_jsonl(read_split(staged, "train"), task_dir / "data" / "train.jsonl")
    test = write_jsonl(read_split(staged, "test"), task_dir / "grade" / "test.jsonl")
    shutil.rmtree(staged, ignore_errors=True)
    print(f"       train {train} problems, test {test} problems", flush=True)

    print("[3/4] task statement and grader", flush=True)
    shutil.copyfile(HERE / "task_statement.md", task_dir / "README.md")
    shutil.copyfile(HERE / "evaluate.py", task_dir / "grade" / "evaluate.py")

    print("[4/4] checking the folder", flush=True)
    required = [
        task_dir / "README.md",
        task_dir / "base_model" / "config.json",
        task_dir / "base_model" / "model.safetensors",
        task_dir / "data" / "train.jsonl",
        task_dir / "grade" / "evaluate.py",
        task_dir / "grade" / "test.jsonl",
    ]
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise SystemExit("incomplete task folder, missing: " + ", ".join(missing))
    size_gb = sum(f.stat().st_size for f in task_dir.rglob("*") if f.is_file()) / 1e9
    print(f"       task folder ready: {task_dir} ({size_gb:.1f} GB)", flush=True)


if __name__ == "__main__":
    main()
