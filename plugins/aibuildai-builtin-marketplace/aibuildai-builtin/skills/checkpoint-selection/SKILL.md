---
name: checkpoint-selection
description: >-
  Catalog of strategies for selecting (or aggregating) checkpoints during
  training and across attempts within one design. Twelve principles
  organized into three layers: within-run weight-space averaging
  (best-val baseline, last-N average, top-K average, SWA, EMA, snapshot
  ensemble), within-run prediction-space averaging (top-K prediction
  average, multi-strategy combo), cross-fold consistency (fold-consistent
  selection rule), and across-attempt aggregation (top-K attempt average,
  Caruana ensemble selection). Plus a pitfalls file. Each principle is
  task-agnostic and self-contained: precise definition, mechanism, pros,
  cons, when to use, common misconception, primary-source quote.
  Definitions verified against PyTorch swa_utils, timm avg_checkpoints,
  ChemProp checkpoint flags, the SWA paper (arXiv:1803.05407), the
  snapshot-ensemble paper (arXiv:1704.00109), and the Caruana 2004
  ensemble selection paper.
---

# Checkpoint Selection Playbook

> **Pipeline contract (read first).** In this pipeline each design trains **exactly one model on the full training set** in **one attempt** — there is NO k-fold / cross-validation, and NO multi-attempt search within a design. So only the *within-run* layers below apply: choose how to select/average checkpoints across the steps of that single training run. The cross-fold (Principle 09) and across-attempt / OOF-aggregation principles (10, 11) and any "per fold" wording are NOT used here — design-to-design variance comes from the Designer/Reviser exploring different designs, and combining across designs is the Aggregator's job (see the ensemble skill). Ignore fold/OOF/attempt framing in the principles when implementing.

A training run produces a sequence of parameter vectors over its steps. Which point in that sequence do you deploy?

The naive answer — pick the single step with the best validation score — is also the answer most prone to cherry-picking bias. Every selection over noisy validation introduces a positive bias of order σ·√(2 log N), where σ is the validation noise and N is the number of choices made.

This playbook catalogues the alternatives that mature competition pipelines actually use. They share one property: they aggregate instead of selecting a single point. Aggregation can be done in weight space (average parameter tensors into one model) or prediction space (predict with each checkpoint and average outputs). Both compose with downstream ensembling across designs.

## The two within-run decision layers (the ones that apply here)

- **Layer 1 — within-run, weight space.** During the single training run, deploy a parameter average of multiple checkpoints rather than the single best-validation point. Inference cost stays at one forward pass.
- **Layer 2 — within-run, prediction space.** Keep K separate checkpoints from the one training run and average their predictions at inference. Inference cost scales linearly with K; gains are typically larger than weight-averaging when the K checkpoints come from different basins (e.g., snapshot ensemble cycles).

The two layers are orthogonal and compose: e.g. SWA within the run (Layer 1), then a multi-strategy prediction average across SWA+EMA+best (Layer 2). (A third "across attempts / across folds" layer exists in the principle files but does NOT apply in this single-model, single-attempt pipeline.)

## When to consult this skill

- **Designer / Reviser** — when planning how a design's training will save and select checkpoints. Name an explicit checkpoint-selection rule in the plan (e.g., "best-val + EMA, predictions averaged at inference"). Leaving this implicit defaults to "max-spike cherry-pick", which is the unsafe choice on noisy validation.
- **Coder** — when implementing the chosen rule inside `train.py` for the single training run. Use Layer 1 / Layer 2 within-run rules only; there are no folds and no multiple attempts to be consistent across.
- **Aggregator** — for ensembling across designs, combine each design's `raw_test` outputs (averaging / weighted blend / rank / voting — see the ensemble skill). There is no OOF or attempt aggregation step inside one design.

## Principle index

| # | Method | Layer | When to consult | File |
|---|--------|-------|-----------------|------|
| 01 | Best validation checkpoint with patience and min_delta | Layer 1 baseline | Considering "best-val" as the selection rule for a fold | principles/01.md |
| 02 | Last-N checkpoint weight averaging | Layer 1 weight-space | Considering averaging the final N saved checkpoints into one deployable model | principles/02.md |
| 03 | Top-K checkpoint weight averaging | Layer 1 weight-space | Considering averaging the K checkpoints with the best validation scores | principles/03.md |
| 04 | Stochastic Weight Averaging (SWA) | Layer 1 weight-space | Considering a modified LR schedule whose tail iterates are averaged | principles/04.md |
| 05 | Exponential Moving Average of weights (EMA) | Layer 1 weight-space | Considering an EMA shadow maintained alongside the optimizer | principles/05.md |
| 06 | Snapshot Ensemble (cyclic LR) | Layer 1 weight-space (output) | Considering cosine-annealing-with-warm-restarts to harvest multiple optima from one training run | principles/06.md |
| 07 | Top-K checkpoint prediction averaging | Layer 2 prediction-space | Combining outputs of K best-validation checkpoints (not weights) | principles/07.md |
| 08 | Multi-strategy combo (best + SWA + EMA) | Layer 2 prediction-space | Saving best-val + SWA + EMA, predicting with each, averaging predictions | principles/08.md |
| 09 | Fold-consistent selection rule | Cross-fold consistency | Choosing a selection rule that applies identically to every fold of one design | principles/09.md |
| 10 | Top-K attempt aggregation across the search dimension | Layer 3 across-attempt | Aggregating multiple attempts of the same design before exporting OOF/test | principles/10.md |
| 11 | Caruana ensemble selection (forward search) | Layer 3 across-attempt advanced | Choosing a data-driven subset of attempts when N ≥ 20 | principles/11.md |
| 12 | Pitfalls in checkpoint selection | Cross-cutting | Always consult before committing a checkpoint-selection plan | principles/12.md |

## How to use

Read only the principles whose "When to consult" condition matches your current decision. Each principle is self-contained — pick one and you can use it as a design-level decision without reading the rest. Always read principle 12 (pitfalls) before committing to a plan.

## Default stack for our scenarios

For small-to-medium datasets with noisy validation curves, the recommended default for the single training run is:

```
within-run:    best-val + EMA, predict with both, average (Principle 08)
```

This is the lowest-cherry-pick configuration achievable without modifying training infrastructure. Stronger configurations (SWA with cyclic LR + snapshot ensemble) are available via Principles 04 and 06 when training compute permits. (There is no across-attempt layer in this pipeline — one attempt per design.)

## References (primary sources, verified)

- Izmailov, Podoprikhin, Garipov, Vetrov, Wilson 2018, "Averaging Weights Leads to Wider Optima and Better Generalization", arXiv:1803.05407 (SWA).
- PyTorch `torch.optim.swa_utils` documentation (SWA, EMA, batch-norm re-estimation).
- timm `avg_checkpoints.py` — production implementations of last-N and top-K weight averaging.
- ChemProp documentation, checkpoint averaging options (`best`, `SWA`, `avg5`, `avg_many`).
- Caruana, Niculescu-Mizil, Crew, Ksikes 2004, "Ensemble Selection from Libraries of Models", ICML.
- Huang, Li, Pleiss, Liu, Hopcroft, Weinberger 2017, "Snapshot Ensembles: Train 1, get M for free", arXiv:1704.00109.
- Kaggle ASL Fingerspelling 9th place writeup (last-N weight averaging in production).
- Kaggle TGS Salt 1st place writeup (snapshot ensemble × fold ensemble × architecture ensemble).

## Cross-references

The ensemble playbook (`aibuildai-builtin:ensemble`) covers SWA, EMA, last-K averaging, and snapshot ensemble as ensemble *techniques* (Principles 1–4 of that skill). This skill reframes them as *checkpoint-selection rules* — same mechanism, different decision context. Consult the ensemble principle when you need to understand the underlying averaging mathematics; consult this skill's principle when you need to choose which rule a design's training script should follow.
