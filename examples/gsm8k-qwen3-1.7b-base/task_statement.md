# Post-train `Qwen/Qwen3-1.7B-Base` for grade-school math

Post-train the base language model in `base_model/` so that it solves grade-school
math word problems, measured by the frozen grader in `grade/`.

## Objective

Maximize the accuracy the grader reports on the GSM8K test split. Store the model
you want measured in a directory named `final_model` inside your working
directory. It must be a fine-tune of `base_model/`.

## What is in this folder

    base_model/         the frozen base weights, Qwen/Qwen3-1.7B-Base. Read-only.
    data/train.jsonl    the GSM8K train split, 7473 problems, each `question` plus a
                        worked `answer` ending in `#### <number>`. Training data.
    grade/evaluate.py   THE grader. Frozen: never modify it, never re-implement it,
                        never substitute your own metric.
    grade/test.jsonl    the GSM8K test split, 1319 problems, the grader's input.

## How the model is scored

    python grade/evaluate.py --model-path <dir> --limit -1 --json-output-file out.json

`--limit -1` grades the whole test split; a positive `--limit` grades that many
problems from the front of it and is the right thing for a development loop. The
JSON holds `accuracy` (higher is better), `n` and `stderr`.

The grader's measurement contract, which is what a model is actually trained
against:

- **Prompt.** Exactly `Question: {question}\nAnswer:`. No chat template, no system
  prompt, no few-shot examples. A base model has no chat behaviour and no answer
  format, so teaching it *this* format is worth more than teaching it arithmetic.
- **Decoding.** Greedy, at most 320 new tokens. An answer that arrives after a long
  preamble may be truncated away.
- **Answer.** The number after the last `####` in the completion, or, when the
  completion has no `####`, the last number in it. `data/train.jsonl` already ends
  every reference answer in that form.
- **Metric.** Fraction of problems whose answer equals the reference.

A full-split grade of this model class takes a few minutes on one modern GPU, so
grade on the full split whenever you would rank or adopt a checkpoint, and keep
small `--limit` values for smoke tests of the serving path. Because you select
among many checkpoints on one split, require a new candidate to beat the incumbent
by more than the `stderr` the grader reports before adopting it.

## Rules

1. **Only this base model.** You may fine-tune only the weights in `base_model/`.
   Downloading or fine-tuning an instruction-tuned variant, or any other model, is
   forbidden.
2. **No external-model distillation.** Do not call a hosted LLM API during this run,
   and do not author training examples yourself. Already-published public datasets
   are allowed no matter how they were built, and using one is not distillation.
3. **Contamination boundary.** Do not train on `grade/test.jsonl`, and do not derive
   training data from its problems. Matching the benchmark's style, format, domain
   and difficulty is expected.
4. **The deliverable loads plainly.** `final_model/` must load with
   `transformers.AutoModelForCausalLM.from_pretrained(...)` in this run's Program
   environment, and it must hold real files: no symlinks, no hard links, and no
   adapter that has not been merged into the weights.
5. **Public data and documentation are fair game.** Finding good training data is
   most of this task.

## Training data

`data/train.jsonl` is the benchmark's own train split. It is legitimate, it is
small, and it is a starting point rather than the data story. Larger public math
and reasoning corpora exist, they are permitted under rule 2 however they were
produced, and choosing one well is usually worth more than any refinement of the
training loop. Augmentations built from the TRAIN split are fine; rule 3 bans
deriving data from the TEST problems, nothing else.

Record which datasets you evaluated with their sizes, which you chose and why, and
which you rejected and on what ground.

## Environment

The run's Program environment is a bare Python 3.11. Install what the work needs
into it, for example `torch`, `transformers`, `trl`, `peft`, `accelerate` and
`datasets`. The grader itself needs only `torch` and `transformers`.

## Hardware

One CUDA GPU. Size batches from tokens times vocabulary rather than from parameter
count: cross-entropy materializes a `[batch x seqlen x vocab]` logits tensor and a
gradient the same size, and this model's vocabulary is large, so the loss, not the
weights, is what runs a device out of memory. Leave room for the grader's own
model when a grade and a training job share a card.
