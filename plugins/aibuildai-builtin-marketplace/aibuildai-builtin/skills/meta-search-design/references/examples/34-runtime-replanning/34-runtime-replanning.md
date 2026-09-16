# Runtime Replanning

## Intent

Replace the remaining workflow only when new evidence makes its current strategy invalid. Keep local repair inside the current Search.

## Structure

```text
current Search
  -> settle or cancel its open children
  -> call one MetaAgent Action with the redesign request
  -> publish and load the generated Search
  -> call the generated Search
```

The message is an Action argument. It is not part of `MetaAgentInput`. Stable machine facts stay in the identity Input. The evidence stays in the run workspace, where MetaAgent can read it.

## Complete handoff

The Search declares the re-entry grant as a field of its own Input -- `replan: MetaAgentInput` -- and the submitted payload fills it verbatim. Every field is safe to copy: the grant states no fact belonging to one particular Search. The payload adds the one field the copied view leaves out, `reviewer_capability`, the grant the next generation's own review may spend: a MetaAgent whose Input carries none dies with `constructed without reviewer_capability` the moment its review starts, after its whole authoring turn has been paid for. The grant is never a `MetaAgent(...)` constructor argument; the constructor refuses unknown keywords. A Search reads its own frozen Input and never the run it is part of.

```python
from engine.capability import ExecutionCapability
from engine.builtin.meta.authoring import MetaAgent, MetaAgentInput, load_meta_search

message = (
    "Three tuning rounds stopped improving. Read this Search's workspace "
    "and design a continuation that uses a different strategy."
)
meta = MetaAgent(
    input=self.input.replan,  # carries reviewer_capability; the payload put it there
)
handle = await self.ctx.spawn(
    meta.run,
    message,
    upstream=tuple(round_handles),
    capability=ExecutionCapability(wall_clock_seconds=1800, gpus=0),
)
meta_output = await handle.result()
child = load_meta_search(meta, meta_output)
child_handle = await self.ctx.spawn(child.run, upstream=(handle,))
return await child_handle.result()
```

`upstream=` names the completed Actions whose results support the request. Do not copy those results into the message. A Handle is the exact Action reference.

## Failure-driven replanning

Capture a Failure only when the Search must inspect it:

```python
trainer_handle = await self.ctx.spawn(trainer.run, capture_failure=True)
outcome = await trainer_handle.result()
if not isinstance(outcome, Failure):
    return outcome
if outcome.kind is not FailureKind.MEMORY_CAP:
    smaller_handle = await self.ctx.spawn(smaller_attempt.run)
    return await smaller_handle.result()

meta = MetaAgent(
    input=self.input.replan,  # carries reviewer_capability; the payload put it there
)
handle = await self.ctx.spawn(
    meta.run,
    "The current model family cannot fit the memory ceiling. Design a new plan.",
    upstream=(trainer_handle,),
    capability=ExecutionCapability(wall_clock_seconds=1800, gpus=0),
)
meta_output = await handle.result()
child = load_meta_search(meta, meta_output)
child_handle = await self.ctx.spawn(child.run, upstream=(handle,))
return await child_handle.result()
```

## Parallel wave

When one completed cell invalidates a wave, cancel and settle every pending Handle before replanning. Name only the completed Action that supports the redesign request. Cancelled work produced no result for the request.

## Repeated replanning

Repeated Meta Actions are valid. Do not add a depth counter. The run budget and each Search stopping rule bound the chain. Check that enough time remains for both authoring and useful follow-up work.

## Smaller use

Use the Action message for a short situational focus on any Agent:

```python
reviewer = CodeReviewAgent(
    input=CodeReviewInput(diff_path=diff_path, budget_s=600),
)
reviewer_handle = await self.ctx.spawn(
    reviewer.run,
    "Two earlier rounds regressed at the tokenizer boundary. Look there first.",
    capability=ExecutionCapability(wall_clock_seconds=900, gpus=0),
)
result = await reviewer_handle.result()
```

## Do not

- Do not invoke MetaAgent for one ordinary failed Attempt.
- Do not return while a direct child is open.
- Do not put `upstream` on a constructor.
- Do not keep a second recursion counter or request-state file.
- Do not resume the abandoned plan after the generated Search starts.
