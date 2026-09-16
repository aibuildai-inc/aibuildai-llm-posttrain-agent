---
name: ensemble
description: >-
  Catalog of ensemble techniques in ML/DL. Sixteen methods grouped into four
  orthogonal spaces: weight-space (parameter averaging into one model),
  prediction-space aggregation (combining outputs of N models), data / pipeline
  (input or fold variation), and model-space / meta (heterogeneous combinations).
  Each principle file covers one method or concept: precise definition, mechanism,
  pros, cons, when to use, common misconceptions. Task-agnostic — applies across
  classification, segmentation, detection, regression, and tabular problems.
  Definitions verified against primary sources (original papers, sklearn / PyTorch
  documentation, foundational references).
---

# Ensemble Techniques Playbook

> **Pipeline contract (read first).** In this pipeline each design trains **exactly one model on the full training set** — there is NO k-fold inside one design and NO per-design out-of-fold (OOF) predictions. The Aggregator ensembles **across designs** by combining each design's `raw_test` predictions (probability averaging, weighted blend, rank averaging, or voting — prediction-space methods 5–8). Methods that assume folds inside one design or an OOF matrix — **K-Fold CV Ensemble (12)** and **OOF-trained stacking / blending (15, OOF-driven weights in 7)** — do NOT apply here; OOF does not exist, so blend weights are tuned on the manager's global holdout or set uniformly, never on per-design OOF. Within-run weight-space methods (snapshot, SWA, EMA, last-K) remain available to the Coder for the single training run.

Ensemble methods improve generalization by combining multiple views of a model (its weights at different iterates), multiple models (their predictions), or multiple training pipelines (different data, different architectures). The gains come from **error decorrelation**, not from model count — combining N models that make the same mistakes gives you no improvement.

This playbook catalogs sixteen techniques. They fall naturally into four spaces based on *where* averaging happens. The spaces are orthogonal, so a single pipeline can stack techniques from several spaces multiplicatively.

## The Four Spaces

- **Weight-space** — average parameters across training iterates to produce a *single* deployable model. Inference cost stays at one forward pass. Mechanism: SGD explores a region around a minimum, and the average parameter vector sits in a flatter, more generalizable minimum than any single iterate.
- **Prediction-space aggregation** — keep N already-trained models and combine their outputs at inference time. Inference cost scales linearly with N. The choice of combining rule (mean, vote, weighted, rank) depends on whether probabilities are calibrated and what the metric rewards.
- **Data / pipeline** — generate multiple views of each input (augmented, rescaled, cropped) and average predictions across views (TTA / multi-scale / multi-crop). Training cost is either zero or a by-product of inference. (The "train multiple fold models" variant — K-Fold CV ensemble — does not apply in this single-model-per-design pipeline; combining across designs is the Aggregator's job.)
- **Model-space / meta** — combine learners that differ in training data, fitting procedure, or architecture. The theoretical grounding is the Krogh-Vedelsby ambiguity decomposition: ensemble error equals average member error minus member disagreement, so diversity is the load-bearing property.

## Principle Index

| # | Method | Space | When to consult | File |
|---|--------|-------|-----------------|------|
| 1 | Snapshot Ensembling | Weight-space | Considering cyclic-LR restarts and saving per-cycle checkpoints | principles/01.md |
| 2 | Stochastic Weight Averaging (SWA) | Weight-space | Considering parameter averaging over a late-training plateau | principles/02.md |
| 3 | Last-K Checkpoint Averaging | Weight-space | Considering averaging the last K checkpoints as a cheap weight-space ensemble | principles/03.md |
| 4 | EMA (Exponential Moving Average of Weights) | Weight-space | Considering an EMA "shadow" teacher alongside the optimizer | principles/04.md |
| 5 | Probability Averaging (Soft Voting) | Prediction-space | Combining probability / soft-mask outputs across models, folds, or TTA views | principles/05.md |
| 6 | Majority Voting (Hard Voting) | Prediction-space | Combining already-thresholded discrete predictions | principles/06.md |
| 7 | Weighted Averaging | Prediction-space | Assigning non-equal weights to ensemble members (e.g., OOF-driven) | principles/07.md |
| 8 | Rank Averaging | Prediction-space | Combining uncalibrated or scale-mismatched member outputs | principles/08.md |
| 9 | Test-Time Augmentation (TTA) | Data / pipeline | Choosing which inference-time augmentation views to average | principles/09.md |
| 10 | Multi-Scale Ensemble | Data / pipeline | Running inference at multiple input resolutions and averaging | principles/10.md |
| 11 | Multi-Crop Ensemble | Data / pipeline | Averaging over multiple spatial crops at inference | principles/11.md |
| 12 | K-Fold CV Ensemble | Data / pipeline | Combining fold models vs. retraining on all data | principles/12.md |
| 13 | Bagging (Bootstrap Aggregating) | Model-space | Considering bootstrap resampling for ensemble diversity | principles/13.md |
| 14 | Boosting | Model-space | Considering sequential error-driven ensembling (GBM / XGBoost / LightGBM) | principles/14.md |
| 15 | Stacking and Blending | Model-space | Training a meta-learner on base-model predictions | principles/15.md |
| 16 | Cross-Architecture (Heterogeneous) Ensemble | Model-space | Combining models from different architectural families | principles/16.md |
| — | Pitfalls That Silently Destroy Ensemble Gains | Cross-cutting | **Always** consult before committing an ensemble design | principles/17.md |
| — | How to Combine Methods Across Spaces | Cross-cutting | Stacking techniques from multiple spaces into one pipeline | principles/18.md |

## How to Use

Read only the principles whose "When to consult" condition matches your current decision. The absolute paths of each principle file are listed in the fetch rule that follows. Always read principle 17 (pitfalls) before committing to an ensemble design — most of the value of this playbook comes from avoiding the listed failure modes.

The method principles are self-contained paragraphs. Pick any one and you can use it as a design-level decision without reading the rest.

## References (primary sources, verified)

Weight-space: Huang et al. 2017 (Snapshot Ensembles, arXiv:1704.00109); Izmailov et al. 2018 (SWA, arXiv:1803.05407); PyTorch `torch.optim.swa_utils` docs; timm model-EMA docs.

Prediction-space: scikit-learn Ensemble docs (`VotingClassifier`, `VotingRegressor`); Caruana et al. 2004 (Ensemble Selection, ICML).

Data / pipeline: Simonyan & Zisserman 2014 (VGG, arXiv:1409.1556, multi-scale evaluation); He et al. 2015 (ResNet, arXiv:1512.03385, 10-crop testing); albumentations, ttach, MMEngine TTA docs; scikit-learn cross-validation docs.

Model-space: Breiman 1996 (Bagging Predictors); Freund & Schapire 1997 (AdaBoost); Wolpert 1992 (Stacked Generalization); Krogh & Vedelsby 1995 (ambiguity decomposition, NIPS 7); scikit-learn `BaggingClassifier`, `StackingClassifier`, `GradientBoostingClassifier` docs.
