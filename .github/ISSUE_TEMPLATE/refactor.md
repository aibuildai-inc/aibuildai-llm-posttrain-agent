---
name: Refactor finding
about: Report work whose primary deliverable changes structural ownership, authority, abstraction boundaries, or state derivation.
title: ""
labels: ["refactor"]
---

## Summary

<One sentence naming the structural promise the code breaks — e.g. "The Designer's docstring says it emits one frozen spec, but the code emits several and a real run confirms it." The work is a refactor only when establishing or replacing that structure is itself the primary deliverable. A visible symptom does not force the issue into framework-bug.>

## The broken promise

<Quote what the code, a docstring, a name, or a recorded decision promises, versus what the source or a real run actually does. The finding is *defined* by this mismatch. If you cannot name a promise and point at what defeats it, this is a hunch, not a refactor finding — close it.>

## Static evidence

- `<file:line>` — <what this location is, and how it defeats the promise: the call site that bypasses the abstraction, the same concept named two ways across modules, the operation the domain calls atomic that the code performs as a catchable sequence, the silent default that hides a precondition.>

## Runtime symptom  <!-- delete this subsection if the smell is purely static with no runtime manifestation yet (e.g. a finding surfaced by a proactive polish read) -->

- **Run id**: `<YYYYMMDDTHHMMSSZ_NNNN>`
- <What an observer of the running system can point at — the handoff that does not connect, the artifact two writers fought over so the reader's result flips with write order, the doc that disagrees with what the run actually wrote on disk.>

## What the design should be

<One paragraph: the shape the code should have so the promise holds. The contributor who takes this proves the fix by re-running and confirming the named observable flipped — a behavioral before/after, not a cleaner diff. Migrate every call site and delete the old shape; a half-migration reintroduces the muddle.>
