# Inference Writing Guide

## Goal

A paper that documents an automated AutoML tree-search run must read as a research argument, not a log of numbers. The inferential spine turns flat reporting into the cycle **research question -> hypothesis -> test -> confirmed or refuted**. Every claim must be earned from an artifact the run already left on disk, and every word must be calibrated to the uncertainty the evidence can support.

`Turn flat number-reporting into a research argument: every section advances question -> hypothesis -> test -> confirmed or refuted, and every claim is earned from an artifact on disk.`

## Anti-Checklist Warning

1. "We ran a tree search and the best result scored 0.79" is a log entry, not a paper.
2. Replace every "report the result" instinct with "which question does this result answer?".
3. A mechanism you did not open an artifact to verify is a guess wearing a result's clothes.
4. If the evidence is within noise, the wording must be within noise too.

## Artifacts You Analyze

The run ships its own evidence. Read it from the saved facts, graded results, forum posts, run log, and deliverable files that Writer Input grants. These records can contain pre-result expectations, plans, Judge reasons, metrics, training details, and per-step traces. If a fact is not in the readable record, state that it is absent and do not infer it from a physical path.

## Principles (each as a question -> hypothesis -> test step)

### 1) Commit to a research question

`The Abstract and Introduction must pose a question; the whole paper then runs question -> hypothesis -> test -> result.`

1. Open with a question the held-out result can answer, not a description of activity.
2. Ban purely descriptive openings ("We document an AutoML run that ...") - they assert no claim and so leave nothing to test.
3. Carry the question through every section as one inferential thread, not as scattered tables.

Worked example: rewrite the opening "We document an AutoML run that searched several tabular models" into "Does the model family drive the held-out metric more than feature encoding? We test this on a tree search of 8 model options."

### 2) Mine the run's own pre-registered predictions as hypotheses

`The strongest hypothesis is a prediction the run recorded before the result; the held-out result is its test (confirmed / refuted).`

1. Read the expectations, Judge reason, and plan that the run saved before that result trained, from Writer Input or a forum post.
2. Pair each pre-result prediction with the held-out result that tests it, and label the outcome confirmed or refuted.
3. A prediction that pre-dates its result is genuine evidence; the same words written after seeing the number are only narration.

Worked example: a saved Judge reason predicted that a revision would raise the metric, and a saved pre-result note warned "training to 8000 rounds with no early stopping will overfit"; the held-out score then dropped. Present the drop as CONFIRMING that pre-registered prediction, not as an after-the-fact observation.

### 3) Mechanisms must be earned from artifacts, never asserted

`To claim a mechanism, open the artifact that evidences it and show the fingerprint; with no artifact it is a hypothesis, not a result.`

1. Open the readable record named in Writer Input or a forum post that carries the mechanism's fingerprint: training detail, feature importance, per-result numbers, or a per-step trace.
2. Show the concrete signal (a split count, a loss curve, a metric pair), not the conclusion restated as if it were data.
3. If the artifact is absent or silent on the mechanism, downgrade the claim to a hypothesis explicitly rather than asserting it.

Worked example: claiming "the model memorized group identity" is earned only by opening a readable training record and showing that an ID-like feature has far more splits than informative features; citing the feature without that record is an unearned assertion.

### 4) Quantify uncertainty and calibrate wording

`Report the uncertainty on every metric and calibrate wording to it; ban any word the evidence cannot support.`

1. Attach the metric's uncertainty: for accuracy on n held-out examples the binomial standard error is sqrt(p(1-p)/n).
2. When two scores differ by less than about one standard error, state that the difference is within noise.
3. Ban over-claim words: do not call a two-point trend "monotonic", a sub-SE gap "unambiguous", or a model "dominates"/"beats" a baseline that was never trained.

Worked example: `coder_candidate_1` (0.7909) vs `coder_candidate_3` (0.7800) on ~1800 held-out rows gives SE approx 0.010, so the 0.011 gap is about one SE; report it as "comparable, `coder_candidate_1` nominally ahead", not "`coder_candidate_1` clearly superior".

### 5) Reconcile contradictions and label evidence strength

`A claim contradicted elsewhere in the paper is a desk-reject; tag each claim with its evidence strength, and let the title assert only what the artifacts support.`

1. Scan for self-contradiction: a headline claim the body later undercuts is fatal on its own.
2. Tag each major claim with its evidence strength: tested on the held-out set, a single-result observation, or an untested hypothesis.
3. Scope the title to what was actually tested, never to a comparison the run never ran.

Worked example: a title "Gradient Boosting Dominates Neural Tabular Methods" while the body admits the neural design (`coder_candidate_2`) was never trained is self-contradictory; retitle to what was tested, e.g. "A Tree Search over Tabular Models for X", or scope the claim to the designs that were trained.

### 6) Interrogate the deliverable

`If the run shipped an ensemble or named a winner, infer from the artifacts whether it actually helped, never assert it was "appropriate".`

1. For an ensemble, ask whether the blend actually beats the best single model on the held-out set, and answer it from artifacts.
2. Check the correlation of the blended members: an equal-weight average of two highly-correlated near-twins rarely adds anything.
3. If you cannot show the deliverable helped, say so plainly instead of calling it appropriate.

Worked example: turn "we ensemble the top two results (appropriate)" into "the two ensembled results' predictions correlate 0.955; on the held-out set the blend scores X versus the best single model's Y, so the ensemble helps / does not help, and we therefore ...".

## Quick Quality Checklist

1. Does the Abstract/Introduction pose a question rather than describe activity?
2. Is every hypothesis a prediction that the readable record shows was saved before its result?
3. Is every mechanism backed by an opened artifact, with absent ones marked as hypotheses?
4. Does every metric carry an uncertainty, and is no sub-SE gap described as decisive?
5. Is the paper free of self-contradiction, and does the title assert only what was tested?
6. Is a shipped ensemble or named winner shown to help from artifacts, not merely called "appropriate"?
