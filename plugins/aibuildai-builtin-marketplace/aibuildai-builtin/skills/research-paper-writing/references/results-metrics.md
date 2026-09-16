# Results and Metrics Writing Guide

## Goal

A strong Results section does not report numbers; it explains them. Every metric must be followed to its root cause: which single design dimension produced it, and why the losing results lost. Treat each analysis item below as a **question that drives toward a cause**, never as a blank to fill with a value.

`Write each result as a question whose answer is a root cause, isolated by comparing results that differ in exactly one dimension.`

## Anti-Checklist Warning

1. "`coder_candidate_7` scored 0.91 PR-AUC" is a value, not a finding. The finding is *why* it scored that.
2. Replace every "report X" instinct with "what caused X, and how do I isolate it?".
3. A root cause is credible only when isolated: compare siblings differing in one dimension.
4. If you cannot isolate a cause, say the cause is unresolved rather than implying one.

## Artifacts You Analyze

Read each graded result's holdout metric, plan, revisions, the executions it names as upstream, failures, and available training logs from the facts in Writer Input and the run's forum posts. Use only files that Writer Input grants. If the readable record does not contain a fact, state that it is absent.

## What to Analyze (each as a root-cause question)

### 1) Headline held-out metric + leaderboard

`Question: how large is the winner's margin over the runner-up, and is it real or within noise?`

1. Rank the results by the blind held-out metric (the trustworthy number).
2. Ask whether the top margin is meaningful or within run-to-run variation.
3. Do not stop at the rank; the next sections must explain the margin.

### 2) Per-result training-side numbers

`Question: does a result's training score corroborate its held-out score, or contradict it?`

1. Pull each result's training-side metric from its recipe/records.
2. A result strong on training but weak on held-out is a generalization-gap signal, not a winner.

### 3) Generalization gap (train vs held-out)

`Question: which results overfit, and which design dimension caused the overfit?`

1. Compute train-minus-held-out per result.
2. A large gap points to capacity/regularization/augmentation as the cause; name which one by comparing recipes.

### 4) Optimization dynamics (loss trend / grad-norm / lr)

`Question: did the winner converge cleanly while a loser diverged or plateaued?`

1. If a result logged per-step records, plot loss trend, grad-norm, and lr (see `references/figures.md`).
2. If no result logged steps, state the absence explicitly and do not infer dynamics you cannot see. Do not fabricate a curve.

### 5) Ablation-style reasoning (isolate the one dimension)

`Question: does the winner's margin come from the model family or from the augmentation? Compare two results that differ only in that dimension.`

1. Use the upstream edges + recipes to find sibling results that differ in exactly one design dimension.
2. The metric delta between them isolates that dimension's effect.
3. If two dimensions changed at once, you cannot attribute the gain; find a cleaner pair or say the attribution is confounded.

### 6) Failure-mechanism analysis

`Question: did the failed results fail for the same reason, and is that reason a design flaw or a resource limit?`

1. Read the failure records; group failures by mechanism (out-of-memory, timeout, crashed encoding, divergent training).
2. Separate "bad design" failures from "the run starved it" failures; they have different implications for the conclusion.
3. A repeated failure mechanism across results is itself a finding about the task.

## Worked Example (leaderboard to root-cause finding)

A 3-row leaderboard plus the matching recipes:

| Result | Held-out PR-AUC | Model family | Augmentation |
| ------------ | --------------- | ------------- | ----------- |
| coder_candidate_3  | 0.91            | grad-boosting | on          |
| coder_candidate_5  | 0.88            | grad-boosting | off         |
| coder_candidate_2  | 0.79            | linear        | off         |

Isolate the dimensions:

1. N3 vs N5 differ only in augmentation -> augmentation contributes +0.03.
2. N5 vs N2 differ only in model family -> the gradient-boosting family contributes +0.09.

Root-cause finding sentence:

> The top result (`coder_candidate_3`, 0.91 PR-AUC) owes most of its margin to the model family rather than to
> augmentation: holding augmentation off, switching from linear to gradient boosting (N2 ->
> N5) raises PR-AUC by 0.09, while adding augmentation on top (N5 -> N3) contributes a further
> 0.03. The model family, not the augmentation, is the dominant driver on this task.

## Quick Quality Checklist

1. Is every reported number followed by its cause?
2. Is each cause isolated by a sibling pair that differs in exactly one dimension?
3. Did you flag generalization gaps instead of crediting raw training scores?
4. Did you plot optimization dynamics only where logs exist, and note their absence otherwise?
5. Did you classify failures by mechanism and separate design flaws from resource limits?
