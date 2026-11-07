#!/usr/bin/env python3
"""Materialize the protein EC x Qwen3-1.7B-Base task folder.

Called by ``setup.sh``, which installs the one package this needs. It downloads
the base weights from the Hugging Face Hub and the protein EC data from the
AIBuildAI repository (MIT), and writes them into the task folder in the shape the
task statement describes.
"""

from __future__ import annotations

import argparse
import csv
import io
import shutil
import urllib.request
from pathlib import Path

MODEL_REPO = "Qwen/Qwen3-1.7B-Base"
# Pinned to one commit so that every build of this example grades the same split.
DATA_URL = (
    "https://raw.githubusercontent.com/aibuildai/AI-Build-AI/"
    "d0cccaf4d7001d4ce90fea46ce92c402fecd47d0/data"
)
TRAIN_URL = f"{DATA_URL}/protein-ec-prediction/train.csv"
TEST_URL = f"{DATA_URL}/protein-ec-prediction/test.csv"
LABELS_URL = f"{DATA_URL}/labels/protein-ec-prediction.csv"
TRAIN_ROWS = 6444
TEST_ROWS = 1313
LABELS = {str(label) for label in range(1, 8)}
FIELDS = ["ID", "Sequence", "label"]
# Weights, tokenizer and configuration only: the framework checkpoints and the
# duplicate formats some repositories carry are dead weight for a fine-tune.
MODEL_PATTERNS = ["*.json", "*.safetensors", "*.txt", "*.model", "*.jinja"]
HERE = Path(__file__).resolve().parent


def fetch_rows(url: str, delimiter: str) -> list[dict]:
    with urllib.request.urlopen(url, timeout=60) as response:
        text = response.read().decode("utf-8")
    # The published files mix \r\n and \n line endings and, despite the .csv
    # name, the two splits are tab-separated.
    text = text.replace("\r", "")
    return list(csv.DictReader(io.StringIO(text), delimiter=delimiter))


def write_csv(rows: list[dict], path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def read_splits() -> tuple[list[dict], list[dict]]:
    # One schema for both splits: the published train split names its columns
    # `Entry`, `EC class`, `Sequence` and the test split `ID`, `Sequences`, with
    # the test labels in a separate file.
    train = [
        {"ID": row["Entry"], "Sequence": row["Sequence"], "label": row["EC class"]}
        for row in fetch_rows(TRAIN_URL, "\t")
    ]
    labels = {row["ID"]: row["label"] for row in fetch_rows(LABELS_URL, ",")}
    test = [
        {"ID": row["ID"], "Sequence": row["Sequences"], "label": labels[row["ID"]]}
        for row in fetch_rows(TEST_URL, "\t")
    ]
    for name, rows, expected in (("train", train, TRAIN_ROWS), ("test", test, TEST_ROWS)):
        if len(rows) != expected:
            raise SystemExit(f"{name}: expected {expected} rows, got {len(rows)}")
        bad = [row["ID"] for row in rows if row["label"] not in LABELS or not row["Sequence"]]
        if bad:
            raise SystemExit(f"{name}: malformed rows, first {bad[:5]}")
    if len(labels) != TEST_ROWS:
        raise SystemExit(f"labels: expected {TEST_ROWS} rows, got {len(labels)}")
    return train, test


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

    print("[2/4] protein EC splits from aibuildai/AI-Build-AI", flush=True)
    train_rows, test_rows = read_splits()
    train = write_csv(train_rows, task_dir / "data" / "train.csv")
    test = write_csv(test_rows, task_dir / "grade" / "test.csv")
    print(f"       train {train} proteins, test {test} proteins", flush=True)

    print("[3/4] task statement and grader", flush=True)
    shutil.copyfile(HERE / "task_statement.md", task_dir / "README.md")
    shutil.copyfile(HERE / "evaluate.py", task_dir / "grade" / "evaluate.py")

    print("[4/4] checking the folder", flush=True)
    required = [
        task_dir / "README.md",
        task_dir / "base_model" / "config.json",
        task_dir / "base_model" / "model.safetensors",
        task_dir / "data" / "train.csv",
        task_dir / "grade" / "evaluate.py",
        task_dir / "grade" / "test.csv",
    ]
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise SystemExit("incomplete task folder, missing: " + ", ".join(missing))
    size_gb = sum(f.stat().st_size for f in task_dir.rglob("*") if f.is_file()) / 1e9
    print(f"       task folder ready: {task_dir} ({size_gb:.1f} GB)", flush=True)


if __name__ == "__main__":
    main()
