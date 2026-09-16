---
name: Feature request
about: Report a capability the product does not have, that a user wished it had. Not a bug — the product never promised this.
title: ""
labels: ["enhancement"]
---

## Summary

<One sentence — what gap I observed. "I wanted X, the product does not have it." NOT a bug (the product never promised this); this is a missing capability the user wishes existed.>

## Environment

- **Command**: `aibuildai run <config>`
- **Run id**: `<YYYYMMDDTHHMMSSZ_NNNN>`
- **Terminal**: `<columns>x<rows>` (tmux)
- **Model**: `<model id>`

## What I tried to do

<One paragraph: the goal the user had. "I wanted to see X / I wanted to do Y / I wanted to understand Z." Written as user intent, not as a code request.>

## What the product offered

<One paragraph: what surfaces and capabilities the product DID offer when the user reached for the goal above. Quote any UI text or output verbatim if applicable.>

## What is missing

<One paragraph: what specifically wasn't there. The gap, in user language. "There is no way to ..." / "The dashboard shows X but not Y" / "The output does not tell me Z.">

## What I wished it had

<One paragraph: concrete proposal in product terms. NOT a code change request — describe the new user-visible capability. "I would want the dashboard to also show ..." or "I would want a CLI flag to ..." or "I would want the output to include ...".>

## Why this matters

<One paragraph: who would benefit, how often, what frustration exists today without it. Is this an every-run need, an occasional need, or an edge-case improvement?>

## Evidence  <!-- delete this entire section if there is no concrete artifact showing the gap -->

### Screenshot

<!-- Drag-drop the screenshot here; GitHub hosts it automatically.
     Filing headless (no browser)? Embed a hosted image instead: ![caption](image-url) -->

### Captured screen text

```
<tmux capture-pane -p output showing the current state where the wished-for capability would have appeared>
```

## Triage markers

- **Severity**: `blocker | major | minor` — does the absence block the user, or is it just inconvenient?
- **User frequency**: `every-run | occasional | edge-case` — how often would a user actually hit this gap?
- **Calibration**: `high-confidence | low-confidence`
- **Workaround available**: `yes | no` — can the user achieve the goal through another path today?
- **Hits stated non-goal**: `yes | no` — does the README explicitly disclaim this scope?
