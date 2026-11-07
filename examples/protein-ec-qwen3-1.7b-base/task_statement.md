# Post-train `Qwen/Qwen3-1.7B-Base` for protein EC class prediction

Post-train the base language model in `base_model/` so that, given a protein's
amino-acid sequence, it names the protein's first-level Enzyme Commission (EC)
class, measured by the frozen grader in `grade/`.

## Objective

Maximize the macro F1 the grader reports on the test split. Store the model you
want measured in a directory named `final_model` inside your working directory.
It must be a fine-tune of `base_model/`.

The seven classes are 1 Oxidoreductases, 2 Transferases, 3 Hydrolases, 4 Lyases,
5 Isomerases, 6 Ligases and 7 Translocases.

## What is in this folder

    base_model/         the frozen base weights, Qwen/Qwen3-1.7B-Base. Read-only.
    data/train.csv      the train split, 6444 proteins, columns `ID`, `Sequence`
                        (one-letter amino-acid codes) and `label` (1-7). Training data.
    grade/evaluate.py   THE grader. Frozen: never modify it, never re-implement it,
                        never substitute your own metric.
    grade/test.csv      the test split, 1313 proteins in the same columns, the
                        grader's input.

## How the model is scored

    python grade/evaluate.py --model-path <dir> --limit -1 --json-output-file out.json

`--limit -1` grades the whole test split; a positive `--limit` grades that many
proteins from the front of it and is the right thing for a development loop. The
JSON holds `macro_f1` (the score; higher is better), `n` and `stderr` (the
bootstrap standard error of `macro_f1`), plus `accuracy`, `parse_failure_rate`
and `per_class_f1` for diagnosis.

The grader's measurement contract, which is what a model is actually trained
against:

- **Prompt.** Exactly `Sequence: {sequence}\nEC class:`. No chat template, no
  system prompt, no few-shot examples. A base model has no answer format, so
  teaching it *this* format is the first thing that moves the score.
- **Decoding.** Greedy, at most 16 new tokens.
- **Answer.** The first standalone digit 1-7 in the completion. A completion
  without one is a parse failure and counts as wrong for its true class.
- **Metric.** Macro F1 over the seven classes: every class weighs the same,
  however few proteins it has.

A full-split grade of this model class takes a few minutes on one modern GPU, so
grade on the full split whenever you would rank or adopt a checkpoint, and keep
small `--limit` values for smoke tests of the serving path. Because you select
among many checkpoints on one split, require a new candidate to beat the incumbent
by more than the `stderr` the grader reports before adopting it.

## What makes this task hard

- **Long inputs.** Sequences are a median of about 400 residues; 90% are under
  825 and the longest is 1022. As the grader's prompt, that is a median of about
  230 tokens, 460 at the 90th percentile and 577 at most, so a training
  `max_length` that holds every prompt plus its answer truncates nothing.
  The grader never truncates; a model trained only on truncated sequences is
  graded on lengths it has not seen. Memory scales with these lengths, see
  Hardware.
- **Heavy class imbalance.** In the train split the largest class (2) has 2544
  proteins and the smallest (7) has 148; the test split is skewed the same way.
  The metric is macro F1, so predicting the majority class everywhere scores only
  about 0.080, and the small classes count as much as the large ones.

## Rules

1. **Only this base model.** You may fine-tune only the weights in `base_model/`.
   Downloading or fine-tuning an instruction-tuned variant, any other model, or a
   protein language model is forbidden.
2. **No external-model distillation.** Do not call a hosted LLM API during this run,
   and do not author training examples yourself. Already-published public datasets
   are allowed no matter how they were built, and using one is not distillation.
3. **Contamination boundary.** Do not train on `grade/test.csv`, and do not derive
   training data from its proteins. Public protein databases contain the test
   proteins too: any external data must have every protein whose `ID` or
   `Sequence` appears in `grade/test.csv` removed before it is trained on.
4. **The deliverable loads plainly.** `final_model/` must load with
   `transformers.AutoModelForCausalLM.from_pretrained(...)` in this run's Program
   environment, and it must hold real files: no symlinks, no hard links, and no
   adapter that has not been merged into the weights.
5. **Public data and documentation are fair game.**

## Training data

`data/train.csv` is the benchmark's own train split. It is legitimate and it is
small. Larger public sources of EC-annotated sequences exist (UniProt/Swiss-Prot
among them), they are permitted under rule 2, and rule 3 says how to use them.
Augmentations built from the TRAIN split are fine; rule 3 bans deriving data from
the TEST proteins, nothing else.

Record which datasets you evaluated with their sizes, which you chose and why, and
which you rejected and on what ground.

## Environment

The run's Program environment is a bare Python 3.11. Install what the work needs
into it, for example `torch`, `transformers`, `trl`, `peft`, `accelerate` and
`datasets`. The grader itself needs `torch`, `transformers`, `accelerate` and
`scikit-learn`.

## Hardware

One CUDA GPU. Size batches from tokens times vocabulary rather than from parameter
count: cross-entropy materializes a `[batch x seqlen x vocab]` logits tensor and a
gradient the same size, and this model's vocabulary is large, so the loss, not the
weights, is what runs a device out of memory. With sequences this long that
happens at small batch sizes. Leave room for the grader's own model when a grade
and a training job share a card.
