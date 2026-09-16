# Budget-Bounded Improvement

## Intent

Use Budget-Bounded Improvement when the number of rounds should come from the live exploration budget, not from a fixed count. The whole exploration -- Meta authoring, its review, and your Search -- shares one wall-clock budget and, when configured, one cost budget. Authoring and review spend from it before your Search starts, so a fixed round count either wastes the remainder or runs past it.

The shape is:

```text
snapshot the budget
→ does the remainder pay for one more round?
→ yes: run the round (any inner pattern), then snapshot again
→ no: return the best or last result
```

The primitive is one replay-safe call:

```python
budget = await self.budget.snapshot()
```

It returns `wall_clock_limit_s`, `wall_clock_used_s`, `wall_clock_remaining_s`, and the cost twins `cost_limit_usd` (None means no limit), `cost_used_usd`, `cost_remaining_usd`. The observation is journaled, so a replay sees the same values and takes the same branch.

The same call answers a second question when you hand it work this Search ran or spawned. Hand it the Handle to ask about that one call; hand it the identity to ask about every call that identity has answered:

```python
worker = WorkerAgent(...)
handle = await self.ctx.spawn(worker.run, "Start the assigned work.")
output = await handle.result()
spent = await self.budget.snapshot(handle)   # this one Action
lifetime = await self.budget.snapshot(worker)  # every Action of this worker
```

Both fill the same shape: `wall_clock_local_limit_s` and `wall_clock_local_used_s` (the cap the scope you named carries, and the Local Time it really ran), `wall_clock_local_remaining_s`, `wall_clock_effective_remaining_s` (after the exploration window and every ancestor limit), the cost three -- `cost_local_cap_usd`, `cost_local_used_usd`, `cost_local_remaining_usd`, all three about THIS Agent's own provider bill and none of them about what it started -- and `cost_charges_exploration_budget`, which says whether that bill also counts toward the one global exploration budget. `None` means that dimension has no finite limit. A capability belongs to one call, so a round's own grant is read through that round's Handle. Use it to price the NEXT round from what the last one actually used, instead of from what you guessed. You may ask only about work this Search started, and only after it is running.

It is appropriate when:

- work divides into rounds whose size you can state (a round's local wall clock and expected cost);
- more rounds keep helping, so unused budget is wasted opportunity;
- the stop rule is "the remainder no longer pays for a round", not a quality gate.

## Structure

The loop body may be any inner pattern: a single Agent (this example), a produce-evaluate pair (pattern 11), or a parallel wave (patterns 05-06). The snapshot decides only whether another body runs.

Compare each round's declared `wall_clock_seconds` against `wall_clock_remaining_s` before constructing the round: a declaration the remainder cannot cover is refused at launch, so checking first turns a hard failure into a clean stop.

## Notes

- Snapshot between rounds, not once at the start: parallel siblings and evaluator turns also spend from the budget.
- Keep the round price honest. A round that routinely overruns its declared wall clock starves the check.
- The example package under `search/` is the minimal single-Agent form. It also queries the round it just ran, and stops when a round used its whole grant, because the next one of that size would be cut off too.
