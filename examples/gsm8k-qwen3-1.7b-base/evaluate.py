#!/usr/bin/env python3
"""The frozen grader: exact-match accuracy on GSM8K.

The contract this file defines is the whole measurement, so a run reads it and
trains against it rather than re-implementing it:

  prompt      "Question: {question}\nAnswer:"     (no chat template, no system text)
  decoding    greedy, at most --max-new-tokens new tokens
  answer      the number after the LAST "####" in the completion; when the
              completion carries no "####", the last number in it
  metric      accuracy = fraction of problems whose answer matches the reference

Usage:

    python evaluate.py --model-path <dir> --limit -1 --json-output-file out.json

``--limit -1`` grades the whole test split; a positive ``--limit`` grades that
many problems from the front of the split, which is what a development loop
wants. The JSON holds ``accuracy``, ``n`` and ``stderr``.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROMPT = "Question: {question}\nAnswer:"
_NUMBER = re.compile(r"-?\d[\d,]*\.?\d*")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="GSM8K exact-match grader.")
    parser.add_argument("--model-path", required=True, help="model directory or hub id")
    parser.add_argument(
        "--limit",
        type=int,
        default=200,
        help="problems to grade from the front of the split; -1 grades all of them",
    )
    parser.add_argument("--json-output-file", default=None, help="write metrics here")
    parser.add_argument("--data", default=str(HERE / "test.jsonl"), help="test split")
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--max-new-tokens", type=int, default=320)
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


def normalized_number(raw: str) -> str | None:
    """Return a comparable form of one number, or None when it is not one."""
    cleaned = raw.replace(",", "").rstrip(".")
    try:
        value = float(cleaned)
    except ValueError:
        return None
    if value == int(value):
        return str(int(value))
    return repr(value)


def extract_answer(text: str) -> str | None:
    """Return the answer the grader reads out of one completion."""
    segment = text.rsplit("####", 1)[-1] if "####" in text else text
    matches = _NUMBER.findall(segment)
    if not matches and segment is not text:
        matches = _NUMBER.findall(text)
    for candidate in reversed(matches):
        normalized = normalized_number(candidate)
        if normalized is not None:
            return normalized
    return None


def load_problems(path: Path, limit: int) -> list[dict]:
    problems = []
    with path.open() as handle:
        for line in handle:
            line = line.strip()
            if line:
                problems.append(json.loads(line))
    if limit is not None and limit >= 0:
        problems = problems[:limit]
    return problems


def main() -> None:
    args = parse_args()
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    problems = load_problems(Path(args.data), args.limit)
    if not problems:
        raise SystemExit(f"no problems in {args.data}")

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
    order = sorted(range(len(problems)), key=lambda i: len(problems[i]["question"]))
    completions: dict[int, str] = {}
    started = time.time()
    for start in range(0, len(order), args.batch_size):
        chunk = order[start : start + args.batch_size]
        prompts = [PROMPT.format(question=problems[i]["question"]) for i in chunk]
        batch = tokenizer(prompts, return_tensors="pt", padding=True).to(model.device)
        options = dict(
            max_new_tokens=args.max_new_tokens,
            do_sample=False,
            temperature=None,
            top_p=None,
            top_k=None,
            pad_token_id=tokenizer.pad_token_id,
            # An untrained base model never emits an end-of-text token here: it
            # rolls straight on into a question it invented. Stopping at that
            # boundary costs nothing and keeps a baseline grade from spending its
            # whole token budget on text no one reads.
            stop_strings=["\nQuestion:"],
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
            # A base model continues past its answer into the next question it
            # imagines; the grader reads only the first answer it produced.
            completions[index] = text.split("\nQuestion:")[0]
        done = min(start + args.batch_size, len(order))
        print(
            f"  graded {done}/{len(order)} in {time.time() - started:.0f}s",
            flush=True,
        )

    correct = 0
    for index, problem in enumerate(problems):
        reference = extract_answer(problem["answer"])
        predicted = extract_answer(completions[index])
        if reference is not None and predicted == reference:
            correct += 1

    for index in range(min(args.print_samples, len(problems))):
        print("-" * 72)
        print(PROMPT.format(question=problems[index]["question"]))
        print(completions[index])

    n = len(problems)
    accuracy = correct / n
    stderr = (accuracy * (1 - accuracy) / n) ** 0.5
    metrics = {"accuracy": accuracy, "n": n, "stderr": stderr}
    print(json.dumps(metrics, indent=2))
    if args.json_output_file:
        os.makedirs(
            os.path.dirname(os.path.abspath(args.json_output_file)), exist_ok=True
        )
        with open(args.json_output_file, "w") as handle:
            json.dump(metrics, handle, indent=2)


if __name__ == "__main__":
    sys.exit(main())
