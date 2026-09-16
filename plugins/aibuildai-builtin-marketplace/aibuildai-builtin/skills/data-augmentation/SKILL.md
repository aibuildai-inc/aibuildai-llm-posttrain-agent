---
name: data-augmentation
description: Invariance-based framework for deciding which data augmentations apply to a given task. Every augmentation encodes an implicit symmetry or noise-model assumption; applying one whose assumption the task violates pushes training toward contradictory gradients and silently drops ceiling accuracy. Principles catalogue 16 common augmentations, each with its invariance, examples of tasks that satisfy it, and examples that break it.
---

# Data Augmentation — An Invariance Framework

Every augmentation T encodes an implicit claim: "for this transform T applied to input x, the prediction f should either (a) stay the same, or (b) change in a well-defined way that the data loader mirrors on the label."

If neither (a) nor (b) holds for a task, training on T-augmented data teaches the model to produce the same output on inputs that genuinely have different ground truth. The gradient signal becomes self-contradictory. Training loss keeps decreasing, validation is rarely sensitive enough to catch it, and the damage only shows up on the test set or on edge cases. Ceiling accuracy silently drops.

This playbook is not a list of "good" or "bad" augmentations. It is a catalogue of invariance assumptions. The designer's job is to check, for each possible augmentation, whether the task and data respect its assumption before enabling it.

## Why "just try everything and let CV decide" fails

Most augmentation wins are small (≤1% accuracy). The variance of 5-fold CV on a small dataset routinely swamps 1%. An augmentation that silently hurts by 0.5% will look neutral or even slightly positive in a single CV comparison, and get locked in. Compounded across 6–10 augmentations, this yields a pipeline that loses 2–4 points relative to a correctly chosen subset, and no single ablation looks guilty.

The decision workflow below is cheaper than brute-force A/B ablation and generalizes across tasks.

## Decision workflow

For every possible augmentation T, before enabling it:

1. **Identify the label space.** Class id? Pixel mask? Bounding box? Regression scalar? Per-pixel regression?
2. **Derive how the label must transform under T.**
   - Geometric T + spatial label → label must transform together with the input (flip the mask, rotate the box). Missing this step is a bug, not an augmentation.
   - Photometric T + any label → label usually does not change.
   - Semantically-interpreted T (e.g., "6" rotated 180° = "9") → the mapping on labels is not a bijection; T is ambiguous and unsafe.
3. **Check the data-collection reference frame.** Real datasets have physical or procedural constraints that mathematical symmetries do not respect:
   - **Gravity**: outdoor / indoor photography, standing subjects, sediment cross-sections, ultrasound abdominal, cell-on-slide microscopy.
   - **Light direction**: shadows cast downward from overhead illumination.
   - **Anatomical orientation**: radiology (heart on left in AP view, liver on right), dermoscopy, retinal.
   - **Time direction**: video, time-series, audio.
   - **Depth / stratigraphy axis**: satellite (top = sky), sonar, seismic cross-section, soil core.
   - **Script / reading order**: OCR, handwriting, natural-scene text.
   - **Left/right distinction in the label**: driver-lane classification, laterality, mirror-sensitive medical findings.
4. **Ask whether T matches the data's physical noise model.** Photometric augmentations (Gaussian noise, blur, histogram equalization) implicitly declare a specific test-time noise or calibration model. If the sensor introduces a different physical noise (speckle, salt-and-pepper, Poisson), the augmentation teaches robustness against noise that does not exist at test.
5. **Ask whether absolute magnitude carries label signal.** If it does, any augmentation that rescales magnitude (brightness, gamma, CLAHE, intensity-inversion) destroys task signal.

An augmentation passes only if it survives all five checks. Failing any one means do not enable it.

## Worked examples

**Example 1 — Horizontal flip on ImageNet classification.** Label = class id. A cat mirrored left-right is still a cat. No directional label semantics. No domain frame violated. Passes → safe.

**Example 2 — Horizontal flip on a driver-lane classifier (left vs right lane).** Label = {left, right}. Mirroring the image flips the correct label. If the loader does not flip the label, T injects contradictory gradients. If it does, the remaining label distribution is exactly mirrored noise with no information gain. Fails check 2 → unsafe.

**Example 3 — Vertical flip on outdoor street photos.** Data-collection frame has a gravity prior (sky up). After VFlip: sky below the ground. The model never sees this at test. Fails check 3 → unsafe.

**Example 4 — 180° rotation on MNIST.** Digit 6 rotated 180° looks like 9. The transform is not a bijection over labels. Fails check 2 → unsafe.

**Example 5 — CLAHE on a seismic reflection image.** Pixel values are physical reflection amplitudes encoding subsurface impedance contrast. Local histogram equalization destroys the absolute-amplitude semantics that the label depends on. Fails check 5 → unsafe.

**Example 6 — CLAHE on a chest X-ray classifier.** Task depends on relative structural patterns; absolute pixel value varies between scanners and is usually normalized anyway. Passes check 5 → safe.

The same transform is unsafe in Example 5 and safe in Example 6. The distinction is not "domain" but **whether absolute magnitude carries label signal**.

## Composing augmentations

Two separate questions:

1. **Is each individual augmentation safe?** Decide per-augmentation using the workflow above.
2. **Does the composition over-augment?** Even all-safe augmentations stacked with high individual probabilities can drift the training distribution too far from the test distribution. Prefer `OneOf({A, B, C}, p=0.5)` (mutually exclusive, applied probability 0.5) over three serial `A p=0.5; B p=0.5; C p=0.5` (compound applied probability ≈ 0.875).

## Test-time augmentation (TTA)

TTA is the same invariance check applied at inference: average predictions over T-augmented views iff T is a safe invariance for the task. An augmentation too risky for training is always too risky for TTA. An augmentation safe for training is also safe for TTA provided the inverse of T on the output (un-flip a mask, un-rotate a box) is well-defined and implemented. Unsafe TTA compounds the signal-violation across views, so wrong TTA is usually **more harmful** than the same augmentation used only at training time.

## Principle index

| # | Name | Invariance it assumes | Class | When to consult | File |
|---|------|-----------------------|-------|-----------------|------|
| 01 | Horizontal flip | Left-right symmetry of the label | Geometric discrete | Before enabling horizontal flip in training or in TTA | principles/01.md |
| 02 | Vertical flip | Up-down symmetry (no gravity prior) | Geometric discrete | Before enabling vertical flip in training or in TTA | principles/02.md |
| 03 | 90°/180°/270° rotation (C4 / D4) | Four-fold rotational symmetry | Geometric discrete | Before enabling any 90°-multiple rotation, transpose, or D4 TTA | principles/03.md |
| 04 | Small-angle rotation | Local SO(2) rotational symmetry within ε | Geometric continuous | Setting rotation limit in ShiftScaleRotate or Rotate | principles/04.md |
| 05 | Translation / shift | Translation equivariance of the label | Geometric continuous | Setting shift/translate limits, choosing border-mode | principles/05.md |
| 06 | Isotropic scale / zoom | Scale invariance across axes | Geometric continuous | Setting uniform scale / zoom range | principles/06.md |
| 07 | Anisotropic (per-axis) scale | Each axis scale is independently meaningless | Geometric continuous | Allowing sx ≠ sy scale independence | principles/07.md |
| 08 | Shear | Approximate shear invariance | Geometric continuous | Considering horizontal or vertical shear | principles/08.md |
| 09 | Elastic / grid deformation | Local smooth-diffeomorphism invariance | Geometric continuous | Considering ElasticTransform or GridDistortion | principles/09.md |
| 10 | Brightness / contrast jitter | Absolute-illumination invariance | Photometric | Enabling brightness-shift or contrast-scale | principles/10.md |
| 11 | Gamma correction | Monotone tone-curve invariance | Photometric | Enabling gamma / tone-curve augmentation | principles/11.md |
| 12 | Local histogram equalization (CLAHE) | Local contrast normalization is label-preserving | Photometric | Enabling CLAHE or any local contrast normalization | principles/12.md |
| 13 | Additive Gaussian noise | Additive Gaussian sensor-noise robustness | Photometric | Enabling additive noise as an augmentation | principles/13.md |
| 14 | Blur (Gaussian / motion) | Optical-blur robustness | Photometric | Enabling Gaussian defocus or motion blur | principles/14.md |
| 15 | Cutout / random erasing | Occlusion robustness of the label | Spatial masking | Enabling Cutout, CoarseDropout, Random Erasing, or GridMask | principles/15.md |
| 16 | Mixup / CutMix | Convex combination of (input, label) is valid training signal | Sample-level | Enabling sample-level mixing (Mixup or CutMix) | principles/16.md |

Each principle states its invariance, shows concrete tasks that satisfy it and concrete tasks that break it, and names the silent-failure mode. Use the workflow above to decide for any new task.
