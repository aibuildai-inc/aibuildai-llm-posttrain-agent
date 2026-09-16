# Data Analysis Writing Guide

## Goal

Treat the dataset as a research object, not a preamble. The point of data analysis is to explain **why a given method won or lost on this task** by tracing the result back to a property of the data. Every observation must end in a modeling consequence; an observation with no consequence does not belong in the paper.

`Each data observation must connect to a modeling consequence: property of the data -> design choice it forces -> why a method won or lost because of it.`

## Anti-Checklist Warning

1. Do not enumerate dataset statistics for their own sake (rows, columns, dtypes) as a list.
2. Every number you report must answer "so which method does this favor or break?".
3. If you cannot name the modeling consequence of an observation, drop the observation.
4. Prefer one observation with a clear consequence over five observations with none.

## Dimensions to Mine (each with its modeling consequence)

Read `train.csv` when present. Read each graded result's plan, output, metric, and the executions it names as upstream, from the facts in Writer Input and the run's forum posts. Use only files that Writer Input grants. The dimension matters only because of the consequence next to it.

### 1) Dataset scale

`Scale decides the model-family ceiling: too few rows starves high-capacity models; many rows unlocks them.`

1. Count rows and columns from `train.csv` (or the recipe if no raw data is shipped).
2. Small data (hundreds to low thousands of rows): favors regularized linear models and gradient-boosted trees; deep nets overfit. If a deep-net design lost here, scale is a possible root cause.
3. Large data with many columns: high-capacity models become viable; a design that stayed with a shallow model may have left accuracy on the table.

### 2) Feature types (numeric / categorical / text)

`Feature type forces the encoding, and the encoding is often the real differentiator between sibling designs.`

1. Classify each column: numeric, low-cardinality categorical, high-cardinality categorical, free text, datetime.
2. High-cardinality categoricals: one-hot explodes dimensionality; target/ordinal encoding or native categorical handling (e.g., gradient-boosting categorical support) usually wins. A design that one-hot-encoded a 10k-cardinality column likely lost on this.
3. Text columns: require tokenization / TF-IDF / embeddings; a design that dropped text threw away signal.

### 3) Label balance

`Imbalance breaks accuracy-style training and metrics; it forces reweighting, threshold tuning, and a PR-style metric.`

1. Compute the class distribution from `train.csv` labels.
2. Heavy imbalance (e.g., 95/5): drives `scale_pos_weight` / class weights, decision-threshold tuning, and a metric that survives imbalance (PR-AUC, F1) over raw accuracy.
3. If the winning design used reweighting and a losing sibling did not, imbalance handling is the likely cause of the margin.

### 4) Missing-value patterns

`Missingness is not noise to fill blindly; its pattern dictates the imputation choice and which models tolerate it natively.`

1. Report per-column missing fraction and whether missingness is concentrated or spread.
2. A column with heavy missingness: simple mean/median imputation can distort it; a missing- indicator flag or a model that handles NaN natively is often better.
3. Tie back: if one design imputed and another used a NaN-native model, missingness handling explains the gap.

### 5) Suspected leakage or ID columns

`A near-perfect single feature is a red flag, not a victory; an ID-like column leaks and must be dropped.`

1. Flag columns that are unique-per-row (IDs), monotonic with row order, or suspiciously predictive of the label.
2. Consequence: such a column must be dropped or it inflates held-out scores that collapse on the real test set.
3. If a result's headline metric is near-perfect, check whether it kept an ID-like column before crediting the model design.

### 6) Train-vs-test distribution shift

`Shift between train and test invalidates an optimistic validation score; it forces a shift-aware split or adversarial validation.`

1. Compare feature distributions of train vs test where both exist (or train vs held-out split).
2. Strong shift: a random split over-reports; time-based or grouped splits and shift-robust models matter.
3. Tie back: a design that validated well but the run's held-out metric dropped is a shift signature, not just bad luck.

## From Observation to Prose (worked example)

Turn raw observations into modeling implications, then into one sentence of paper prose.

Raw observations from `train.csv` and the recipes:

1. 4,200 rows, 38 columns; the positive class is 6%.
2. Column `customer_id` is unique for every row.
3. Column `last_login_days` is missing for 41% of rows.

Modeling implications:

1. 6% positives -> accuracy is uninformative; report PR-AUC and expect reweighting or threshold tuning to drive the margin between results.
2. `customer_id` unique-per-row -> an ID; keeping it leaks, so any design that retained it has a suspect score.
3. 41% missingness in `last_login_days` -> mean imputation distorts the column; a missing- indicator or a NaN-native model should help.

Paper prose:

> The task is strongly imbalanced (6% positive), so we evaluate with PR-AUC rather than
> accuracy; the winning design's gain traces to its class-reweighting, which its losing sibling
> omitted. We drop the unique `customer_id` column to prevent leakage, and handle the 41%
> missingness in `last_login_days` with a missing-indicator flag rather than mean imputation,
> consistent with the design that adopted this choice scoring highest.

## Quick Quality Checklist

1. Does every data observation name a modeling consequence?
2. Is each consequence tied to a concrete design that won or lost because of it?
3. Did you check for ID-like / leaking columns before trusting any near-perfect score?
4. Are imbalance, missingness, and feature-type choices traced to specific recipes?
5. Did you avoid listing statistics that lead nowhere?
