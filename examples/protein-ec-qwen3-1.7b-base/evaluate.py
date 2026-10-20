#!/usr/bin/env python3
"""The frozen grader: macro F1 on first-level EC class prediction.

The contract this file defines is the whole measurement, so a run reads it and
trains against it rather than re-implementing it:

  prompt      "Sequence: {sequence}\nEC class:"   (no chat template, no system text)
  decoding    greedy, at most --max-new-tokens new tokens
  answer      the first standalone digit 1-7 in the completion; a completion
              with none is a parse failure and is graded as wrong
  metric      macro_f1 = unweighted mean of the seven per-class F1 scores

Usage:

    python evaluate.py --model-path <dir> --limit -1 --json-output-file out.json

``--limit -1`` grades the whole test split; a positive ``--limit`` grades that
many proteins from the front of the split, which is what a development loop
wants. The JSON holds ``macro_f1``, ``accuracy``, ``n``, ``stderr`` (bootstrap
standard error of ``macro_f1``), ``parse_failure_rate`` and ``per_class_f1``.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROMPT = "Sequence: {sequence}\nEC class:"
CLASSES = list(range(1, 8))
_CLASS = re.compile(r"(?<!\d)[1-7](?!\d)")
BOOTSTRAP_SAMPLES = 1000


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Protein EC macro-F1 grader.")
    parser.add_argument("--model-path", required=True, help="model directory or hub id")
    parser.add_argument(
        "--limit",
        type=int,
        default=200,
        help="proteins to grade from the front of the split; -1 grades all of them",
    )
    parser.add_argument("--json-output-file", default=None, help="write metrics here")
    parser.add_argument("--data", default=str(HERE / "test.csv"), help="test split")
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--max-new-tokens", type=int, default=16)
    parser.add_argument(
        "--dtype", default="bfloat16", choices=("bfloat16", "float16", "float32")
    )
    parser.add_argument(
        "--print-samples",
        type=int,
        default=0,
        help="print this many prompt/completion pairs, for debugging a format",
    )
    return parser.parse_args()


def extract_answer(text: str) -> int | None:
    """Return the class the grader reads out of one completion."""
    match = _CLASS.search(text)
    return int(match.group()) if match else None


def load_problems(path: Path, limit: int) -> list[dict]:
    with path.open(newline="") as handle:
        problems = list(csv.DictReader(handle))
    if limit is not None and limit >= 0:
        problems = problems[:limit]
    return problems


def main() -> None:
    args = parse_args()
    import numpy as np
    import torch
    from sklearn.metrics import f1_score
    from transformers import AutoModelForCausalLM, AutoTokenizer

    problems = load_problems(Path(args.data), args.limit)
    if not problems:
        raise SystemExit(f"no proteins in {args.data}")

    tokenizer = AutoTokenizer.from_pretrained(args.model_path)
    tokenizer.padding_side = "left"
    if tokenizer.pad_token_id is None:
        tokenizer.pad_token = tokenizer.eos_token
    device_map = "cuda" if torch.cuda.is_available() else "cpu"
    dtype = getattr(torch, args.dtype)
    try:
        model = AutoModelForCausalLM.from_pretrained(
            args.model_path, dtype=dtype, device_map=device_map
        )
    except TypeError:
        # `dtype` is the current spelling; older transformers know it as
        # `torch_dtype`, and the grader must load on either.
        model = AutoModelForCausalLM.from_pretrained(
            args.model_path, torch_dtype=dtype, device_map=device_map
        )
    model.eval()

    # Grade long prompts beside long prompts: the batch is padded to its own
    # longest member, so length-sorted batches spend their tokens on answers
    # rather than on padding. The reported order is restored afterwards.
    order = sorted(range(len(problems)), key=lambda i: len(problems[i]["Sequence"]))
    completions: dict[int, str] = {}
    started = time.time()
    for start in range(0, len(order), args.batch_size):
        chunk = order[start : start + args.batch_size]
        prompts = [PROMPT.format(sequence=problems[i]["Sequence"]) for i in chunk]
        batch = tokenizer(prompts, return_tensors="pt", padding=True).to(model.device)
        options = dict(
            max_new_tokens=args.max_new_tokens,
            do_sample=False,
            temperature=None,
            top_p=None,
            top_k=None,
            pad_token_id=tokenizer.pad_token_id,
            # An untrained base model never emits an end-of-text token here: it
            # rolls straight on into a sequence it invented. Stopping at that
            # boundary costs nothing and keeps a baseline grade from spending its
            # whole token budget on text no one reads.
            stop_strings=["\nSequence:"],
            tokenizer=tokenizer,
        )
        with torch.inference_mode():
            try:
                generated = model.generate(**batch, **options)
            except (TypeError, ValueError):
                for unsupported in ("stop_strings", "tokenizer"):
                    options.pop(unsupported, None)
                generated = model.generate(**batch, **options)
        new_tokens = generated[:, batch["input_ids"].shape[1] :]
        for index, text in zip(
            chunk, tokenizer.batch_decode(new_tokens, skip_special_tokens=True)
        ):
            # A base model continues past its answer into the next sequence it
            # imagines; the grader reads only the first answer it produced.
            completions[index] = text.split("\nSequence:")[0]
        done = min(start + args.batch_size, len(order))
        print(
            f"  graded {done}/{len(order)} in {time.time() - started:.0f}s",
            flush=True,
        )

    references = np.array([int(problem["label"]) for problem in problems])
    parsed = [extract_answer(completions[index]) for index in range(len(problems))]
    # A parse failure becomes class 0, which is never a reference: it counts as
    # a miss for the true class without becoming an eighth class of its own.
    predictions = np.array([0 if label is None else label for label in parsed])

    def macro_f1(indices: np.ndarray) -> float:
        return float(
            f1_score(
                references[indices],
                predictions[indices],
                labels=CLASSES,
                average="macro",
                zero_division=0,
            )
        )

    for index in range(min(args.print_samples, len(problems))):
        print("-" * 72)
        print(PROMPT.format(sequence=problems[index]["Sequence"]))
        print(completions[index])

    n = len(problems)
    everything = np.arange(n)
    # Macro F1 is not a mean over problems, so its standard error comes from
    # resampling the split; the fixed seed keeps two grades of one model equal.
    rng = np.random.default_rng(0)
    resampled = [macro_f1(rng.integers(0, n, n)) for _ in range(BOOTSTRAP_SAMPLES)]
    per_class = f1_score(
        references, predictions, labels=CLASSES, average=None, zero_division=0
    )
    metrics = {
        "macro_f1": macro_f1(everything),
        "accuracy": float((references == predictions).mean()),
        "n": n,
        "stderr": float(np.std(resampled, ddof=1)),
        "parse_failure_rate": sum(label is None for label in parsed) / n,
        "per_class_f1": {str(c): float(f) for c, f in zip(CLASSES, per_class)},
    }
    print(json.dumps(metrics, indent=2))
    if args.json_output_file:
        os.makedirs(
            os.path.dirname(os.path.abspath(args.json_output_file)), exist_ok=True
        )
        with open(args.json_output_file, "w") as handle:
            json.dump(metrics, handle, indent=2)


if __name__ == "__main__":
    sys.exit(main())
