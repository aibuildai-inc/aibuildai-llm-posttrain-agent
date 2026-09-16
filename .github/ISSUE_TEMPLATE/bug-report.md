---
name: Bug report
about: Report defective existing product behavior that can be corrected within the current design.
title: ""
labels: ["framework-bug"]
---

## Summary

<One sentence in a real user's voice. Should answer "what did you see that was wrong" in three seconds of reading. Quote the smallest concrete fact (a number, a label, an error string) if possible.>

## Environment

- **Command**: `aibuildai run <config>`
- **Run id**: `<YYYYMMDDTHHMMSSZ_NNNN>`
- **Terminal**: `<columns>x<rows>` (tmux)
- **Model**: `<model id>`

## Steps to reproduce

1. <Action a real user would do, not an agent-only one>
2. ...

## Expected behavior

### What I expected

<One paragraph: the concrete user-visible behavior the user expected to see.>

### Source of that expectation

<Where did the user form this expectation? Quote the README sentence, the dashboard label, the prior turn's output, or the prompt-displayed promise that set up the expectation.>

## Actual behavior

### What the user saw

<One paragraph in product language. Quote captured screen text or artifact values verbatim in fenced code blocks below this section, not in this paragraph.>

### Why it is wrong

<One paragraph in product language explaining the gap between expected and actual. No code-level reasoning, no source-tree references.>

## Evidence

### Screenshot  <!-- delete this subsection if no PNG was rendered -->

<!-- Drag-drop the screenshot here; GitHub hosts it automatically.
     Filing headless (no browser)? Embed a hosted image instead: ![caption](image-url) -->

### Captured screen text

```
<tmux capture-pane -p output — always include this, even when a PNG is also embedded above, so the text stays grep-able>
```

### Artifacts referenced  <!-- delete this subsection if there are no on-disk artifacts to cite -->

- `<absolute path>:<line-or-byte-range>` — `<exact value>`

## Triage markers

- **Severity**: `blocker | major | minor`
- **Axis**: `within-modality | cross-modality | artifact-sanity`
- **Calibration**: `high-confidence | low-confidence`
- **Trust impact**: `yes | no` — does this make the user doubt the product works?
- **Likely real bug**: `yes | no` — highlight impossible values, struggle loops
- **Hits stated non-goal**: `yes | no` — does the README explicitly disclaim this case?
- **Model-produced artifact**: `yes | no` — did a model write or choose the thing that is wrong? If yes, say how many runs have shown it, and name the prompt line or the code check that should have caught it
