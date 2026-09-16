Fixes #<N>  <!-- when the PR closes an issue -->

## TL;DR

- **The problem**: <one sentence naming the broken behavior, missing capability, structural problem, developer-tool problem, or resolved uncertainty>
- **What this PR does**: <one sentence naming the completed change>
- **Does it work**: <one sentence summarizing direct evidence and required gates>

## What the user sees now  <!-- delete this section when the PR makes no product behavior claim -->

### Before this PR

<one paragraph in product language. The exact user-visible symptom. No code-name jargon. Quote the exact log line or error output in a fenced code block if applicable.>

### After this PR

<one paragraph in product language. What the user sees now. Quote the exact new error message or output in a fenced code block if applicable.>

### What still works  <!-- delete this entire subsection if the change does not need to call out preserved behavior -->

<one paragraph. What the fix did NOT break — for example "resume after crash still works", "errors on the orphan path still surface".>

## Structural finding  <!-- delete this entire section if this is a straightforward bug fix and not a polish-style structural root -->

### The shape of the bug (in product terms)

<one paragraph. What structural pattern the old code had. Lead with product language; do not open with a code citation.>

### Evidence

<file:line citation, annotated with what that location is in the product. Optionally a short code snippet showing the bug shape.>

### What this PR replaces

<one paragraph. What the new code does differently, and what behavior is preserved versus replaced.>

## Capability proof  <!-- enhancement PRs adding new harness machinery only; delete otherwise -->

### Capability change

<What new harness machinery this PR adds — a new agent role, a new pipeline stage, or a cross-run memory mechanism — and the specific field-consumer gap it closes. Name the downstream agent or stage that consumes the new output and the decision that consumption changes.>

### Ruling out fake success (the common ways)

<Check the common ways a capability can look successful without being real, listed below. This is not an exhaustive list — a feature can fake success in a way not listed, so stay alert.>

- **NO-OP** (decoration): <cheap, one run. Show the consumption chain end to end — your new component wrote field X, a named downstream stage actually read X, and the artifact it produced was demonstrably different because of X. A role that writes a file nobody reads is decorative.>
- **SUPPRESSION**: <a medium-sized config, OFF-vs-ON. Show the surviving work got *better*, not merely that more executions were marked failed. A gate that only changes which work counts is not capability.>
- **SCALE-ARTIFACT**: <the same medium-sized config, OFF-vs-ON. Show the delta is structural and exceeds run-to-run noise; a small config cannot separate a real gain from noise, so a small-scale "before/after" proves nothing.>

### Structural before/after (never a score)

<The OFF-vs-ON artifact comparison as a change in *what the agents did*, not a leaderboard number — for example: fold count moved from 1 to a real value and out-of-fold predictions now exist; training actually ran on GPU where it had silently fallen back to CPU; the implemented model now matches the design; a leaking split became sound. One noisy run cannot prove a score gain, and asserting one breaks the no-estimated-performance rule.>

### Disable flag

<Name the clean switch that turns the feature fully off, used for the OFF-vs-ON comparison. It is the proof mechanism, not a back-compat fallback — and a feature you cannot switch off cleanly is telling you its boundaries are tangled.>

## Root cause

<Paragraph 1 — in product language only: how the buggy flow plays out from the user's command to the wrong output.>

<Paragraph 2 — cite file:line as evidence, with prose annotation tying it back to paragraph 1. A reviewer should be able to verify the trace by reading the cited code, not by trusting your prose.>

## What changed

- **`<filename>`** (`<role of this file in the product>`) — <what changed in this file, 1-2 sentences in product language>
- **`<filename>`** (`<role of this file in the product>`) — <what changed in this file, 1-2 sentences in product language>

## Verification

- [x] `./check.sh` clean (pyright, ruff, semgrep, Agent public members, lock files)
- [x] The real product was run, and the change was observed doing what it claims — <inline GitHub evidence a reviewer can read without access to the author's machine>. Use a matched pair: the unchanged behavior from the base branch, and the changed behavior from this branch, with the triggering condition actually induced. A run that would have looked the same with the bug still present proves nothing.
- [x] Someone who is not the author read this diff — <who, and what they said>

<!-- This project has no test suite. Do not add one or offer a test as evidence. What this PR promises must be shown in the real product or shown to be structurally impossible to get wrong. -->

## Reviewer notes  <!-- delete this entire section if there is nothing for a reviewer to glance at -->

- <Things a reviewer should glance at. If you picked a UX option from a multiple-choice in the issue, name the option AND the reasoning in product language.>

## Deferred by the user  <!-- delete this entire section unless the USER asked for the deferral -->

<!-- Nothing goes here on your own judgment. A leftover you noticed is fixed in THIS PR,
     whatever its age and whoever wrote it: name the statement this change makes true, and
     if the leftover can make that statement false, it is this PR's work. Describing it
     well is not deferring it, and the independent audit rules on every classification you
     make - not you.

     This section holds ONE thing: a deferral the USER asked for. Each entry then carries
     both halves or it is not a deferral. -->

- <the user's own words authorizing the deferral, quoted> — now owned by #<N>
