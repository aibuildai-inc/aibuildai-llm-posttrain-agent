---
name: meta-search-design
description: >-
  Design catalog for MetaAgent: thirty-four workflow and search patterns mapped
  to the AIBuildAI Execution SDK, each with a complete loader-verified example
  package where one exists, and the generated public API reference. Read the
  selection guide, choose a pattern, then write a Search package.
---

# Meta Search Design

This Skill helps you design a Search program before writing it.

## How to use

1. Read the selection guide below to choose a workflow shape for the objective.
2. Read only the pattern references relevant to your choice in `references/examples/`. Before using an entry as code, apply the package rule in [Three source layers](#three-source-layers).
3. Read the public API pages in `references/api/` for exact imports and signatures.
4. Write a short design sketch before writing code.
5. Write the package under `search/`. Export `SEARCH_TYPE` from `search/__init__.py`.

## Selection guide

Choose the simplest sufficient structure for the objective.

### Before writing code

Write a short private design sketch containing:

- Chosen pattern(s)
- Why the objective fits them
- State carried by the Search
- Child Execution types
- Each Program and why all four Program answers are yes
- Fan-out / fan-in structure
- Score or selection rule
- Stopping rule and budget, stated against the live remainder from `await self.budget.snapshot()` when rounds can repeat (pattern 29)
- Whether recursion is used
- How the final `SearchOutput` is produced

### Choose Agent or Program

Start with Agent. Use Program only when all four answers are yes:

1. Are all execution steps settled before launch?
2. Can code classify every terminal Output?
3. Does each typed Action avoid open diagnosis and replanning?
4. Does a separate resource, retry, cancellation, or parallel boundary have real value?

If any answer is no or uncertain, use Agent. "Run one fixed script and read a number" alone does not make the fourth answer yes: the Agent that wrote the script runs it. An authored Agent may declare `task_environment=True` in its `RolePolicy`, which puts the task Conda env's python first on its PATH, and be granted the card with `gpus=1`; it then prepares data, writes and runs training, serves a model, and evaluates, the way the post-training runs that reached their scores did in one session (pattern 37 is that shape). A Program earns its place when the boundary has real value: many candidates trained in parallel with losers cancelled (patterns 18, 20, 21, 28), shards or benchmark pairs fanned out (25, 26), a fixed long worker process. Do not use Agent only to wait for or repeat a settled Program process. Put authored Program classes under `programs/`. Bare `WorkUnit` is not an authoring name.

An Agent may use short Python or shell commands while it investigates. Code use alone does not justify a Program. When a settled long process needs its own boundary, run it as a Program instead of making an Agent wait for it.

### Decision questions

Work through these in order. Stop at the first question where the answer gives you a pattern.

1. **Is one Agent enough?** If the task is a single prompt-and-return, use pattern 01 (Single-shot Agent).

2. **Are the stages fixed and typed?** If the task is a linear pipeline of known steps, use pattern 02 (Sequential Chain).

3. **Are there distinct task categories?** If the input determines which specialist to use, use pattern 03 (Router + Specialists).

4. **Can independent work run in parallel?** If the task splits into independent subproblems, use pattern 05 (Map-Reduce) or pattern 06 (Parallel Best-of-N).

5. **Are subtasks known in advance or discovered dynamically?** If the planner must decide subtasks at runtime, use pattern 09 (Orchestrator-Workers) or pattern 10 (Worker-Iterator).

6. **Is there a useful evaluator and an iterative improvement signal?** If the output can be scored and improved, use pattern 11 (Evaluator-Optimizer / Reflection).

7. **Should proposed designs compete, vote, debate, or be layered?** Use pattern 07 (Tournament), 08 (Committee/Voting), 14 (Mixture-of-Agents), or 15 (Multi-Agent Debate).

8. **Does the problem naturally decompose recursively?** Use pattern 16 (Recursive Divide-and-Conquer) with `MetaAgent` and `load_meta_search`.

9. **Is there a heuristic or value function for frontier search?** Use pattern 17 (Beam Search), 18 (Best-First / A*), or 19 (MCTS).

10. **Is diversity across a population useful?** Use pattern 20 (Evolutionary / Population Search).

11. **Should the round count come from the remaining budget rather than a fixed number?** Wrap the chosen pattern in pattern 29 (Budget-Bounded Improvement): read `await self.budget.snapshot()` between rounds and run another round only while the remainder pays for it. This wrapper composes with any pattern above.

12. **Can runtime evidence invalidate the remaining workflow shape itself?** Answer this after choosing a topology, for every design that runs more than one round. If the Search can absorb a disappointing result with the structure it already has, adapt locally and stay in the chosen pattern. If the strategy or topology itself can become exhausted, add pattern 34 (Runtime Replanning): settle the old work, then pass the redesign request as the message to `await (await self.ctx.spawn(MetaAgent(...).run, message, upstream=...)).result()`. Do not invoke MetaAgent on every failure.

### Applied Agent–Program patterns

After choosing the general topology, use these references when part of the workflow has become a settled mechanical operation with a real boundary: several candidates in parallel, shards, or a bounded matrix.

1. **Are several complete training specifications known before launch?** Use pattern 21 (Batch Training Configurations).

2. **Does an Agent revise code while one fixed test contract repeats?** That is pattern 11 (Evaluator-Optimizer) with the same Agent running the tests itself; no separate pattern.

3. **Can one frozen data policy be materialized mechanically and reviewed afterward?** That is pattern 02 (Sequential Chain): a strategy Agent that writes and runs the filter, then a review Agent.

4. **Can a bounded simulation design be frozen before independent runs?** A cheap simulation is one Agent's own computation (pattern 02); an expensive one with many independent runs is pattern 21's shape.

5. **Do existing model options need fixed benchmark execution followed by semantic error analysis?** Use pattern 25 (Benchmark Evaluation and Error Analysis).

6. **Should one frozen inference policy be applied across stable input shards?** Use pattern 26 (Sharded Batch Inference and Review).

7. **Must an immutable model artifact be exported and checked against a bounded runtime matrix?** Use pattern 27 (Model Export and Compatibility Validation): an export Agent and one fresh validation Agent per environment, spawned together.

8. **Should prior experimental evidence determine the next bounded ablation wave?** Use pattern 28 (Adaptive Ablation Waves).

These applied references specialize earlier topologies. Pattern 26 is a Program-centered Map-Reduce design and pattern 28 a Program-centered Orchestrator-Workers design, because their shards and cells run in parallel; 25 fans out one Program per candidate-benchmark pair for the same reason; 27 is Agent-only.

### Post-training patterns

If the task is to improve a base LLM through fine-tuning, start with pattern 37 (Single-Worker Rounds): one worker Agent with `task_environment=True` and the card does a whole round itself, and the Search only loops on the budget and keeps the best checkpoint. The six patterns below each add one policy on top of that shape; none of them needs a Program, because one card runs one round at a time.

These six are not mutually exclusive, and the first-match rule above does not
apply within this group: read all six questions before choosing, then compose.
Pattern 30 is a checkpoint-selection policy and composes with any of the others
as the way their training stages pick what to keep.

1. **Is training expensive and intermediate checkpoints may score higher than the final one?** Use pattern 30 (Checkpoint Tournament with Model Soup). This is true of nearly every fine-tuning run, so treat it as the selection policy to layer on whatever else you choose, not as the whole design.

2. **Does the task have a cheap verifiable reward signal (exact match, code execution, unit tests)?** Use pattern 31 (SFT into GRPO), with pattern 30 selecting within each stage. If the budget affords a second training stage, prefer this over SFT alone: SFT teaches the output format and GRPO optimises the thing actually being scored. State a reason if you have the reward signal and the budget and still choose one stage.

3. **Is there no suitable open training dataset and a teacher model must generate data?** Use pattern 32 (Teacher Distillation with Iterative Refinement).

4. **Is the model already near its ceiling, with remaining errors few enough to diagnose one by one?** Use pattern 33 (Error-Driven Precision Continuation).

5. **Does the student already answer in the task's format and succeed at least sometimes, so its own samples carry signal?** Use pattern 35 (Guarded On-Policy Rounds). A round that trains on the student's own output can make it worse, so this pattern only fits when every round is scored and a losing round is undone. Choose it over pattern 32 when the student can be sampled, and over pattern 31 when the reward is a verifier rather than a per-token reward model.

6. **Is the metric a rubric or a preference, so a judge model must supply the training signal?** Use pattern 36 (Judge-Calibrated Preference Branch). Never train on a judge this run has not measured against the official grader; the pattern makes that measurement a gate rather than advice.

Every one of these carries the **repair edge** example 31 shows: the Agent that wrote the training script is the Agent that runs it, so a crash is read and fixed inside the same call, and the Search spawns the training call with `capture_failure=True` so a stage that still fails leaves the last good result standing instead of ending the run. It is what turns a bug in a generated script into one more turn instead of a dead run.

### Post-training judgment

Two choices decide more than the pattern you pick, and both are easy to get wrong in a way that only shows up at the end.

**Size the dev eval so it can decide.** A subset score is biased, not merely noisy: every candidate can score lower on the full split than on a small subset, all in the same direction, and the ranking can invert so that the subset leader comes last. The saving is smaller than it looks, because server startup dominates a short eval: a subset several times larger can cost about the same wall clock. Screen larger than feels necessary, and score anything you would actually ship on the full split.

**Size the schedule to the wall clock, not to an epoch count.** A cosine schedule planned for epochs you will not reach never anneals: a run its deadline stops long before the planned last step ships at nearly the peak learning rate. Size the schedule to the time the budget actually buys, so that it anneals. Measure throughput on a smoke run first.

**Read the stop reasons before the score.** A candidate whose evaluation reports most answers cut off at the token cap is a formatting failure, not a reasoning plateau: it has not learned when to stop, usually because training never showed it the prompt shape the grader sends. A checkpoint with most answers truncated can score far below a checkpoint that stops cleanly and sits unused on disk. Never adopt such a candidate as leader or floor; fix the prompt shape instead.

### Hybrids

A hybrid normally combines two or three patterns. Examples:

- Router into specialist-specific evaluator-optimizer loops
- Divide-and-conquer with parallel children and tournament merge
- Orchestrator-workers with best-of-N per subtask and final synthesis
- Beam search with recursive MetaSearch at selected frontier states
- Any multi-round pattern with runtime replanning when its strategy family is exhausted
- Batch training followed by blind benchmark evaluation
- Fixed first-wave ablations followed by adaptive ablation waves

The catalog is not a whitelist. You may invent a composition not listed here, as long as it uses only `spawn`, `wait`, `cancel`, and `step`.

## Common pitfalls

These are framework facts that are easy to guess wrong and expensive to find late. Read them before you write code. They are guidance; the mechanical verifier and the product loader decide what is legal.

- `search/` is the physical directory, not the runtime module name. The product activates the package under a generated name such as `aibuildai_meta_<run>_<path>`. Use relative imports between your own files (`from .search import ...`, `from ..agents import ...`). Never write `from search...` to mean your own package.
- Do not invent your own import smoke test. A package that imports only because you changed `PYTHONPATH` or the working directory proves nothing about the product. The mechanical verifier activates your package with the real loader, under the real name, and builds your `SEARCH_TYPE` from your `input_payload`; rely on that.
- Every module in the package must be import-safe. The isolated verification imports every Python file; live activation stays ordinary and lazy, so a module may also be reached for the first time mid-run. Keep module top level cheap and free of side effects: define types, functions, and constants; do not start work, read mutable runtime state, or do heavy I/O at import time. Put work in Agent execution, a Program's `@action` method, a Composite's `run`, `Search.explore()`, or an explicit `ctx.step()`.
- Generated concrete executions are `Agent`, `Program`, `Search`, or a `Composite`. The generated-package contract puts Programs under `programs/` and Composites under `composites/`. The standard layout puts Agents under `agents/` and the top-level Search in `search.py`. The loader accepts any valid package layout, and `search/__init__.py` must export `SEARCH_TYPE`. Bare `DurableExecution` and bare `WorkUnit` are not importable from any facade -- a bounded sub-orchestration subclasses `Composite[Input]` with one `@action async def run`.
- A Program runs in a fresh managed worker. It does not inherit the Meta Agent's working directory, `sys.path` changes, shell state, or process-local imports. Pass business data through its typed Input and use its own `scratch_dir`, `artifacts_dir`, and capability.
- Durable children start only through `self.ctx.spawn()`. Use `ctx.step()` for nondeterministic leaf I/O whose result must survive replay. Never hide a new durable child inside an ordinary helper or a step.
- An Action is an ordinary bound method: pass `identity.action` itself, plus its zero-or-one typed request, to `ctx.spawn()`; only it owns `upstream=` and `capture_failure=`. Never call the method yourself to invoke it durably, and never put `upstream=` on an identity constructor. `upstream=` names completed Handles whose results the new Action consumes. A Handle names its exact Action. A fan-in lists every source.
- Ask one question before choosing lifetime: does the next operation require the same identity's evolving state? Construct a fresh author-side object when it does not. Reuse the same object across several `spawn()` calls when it does: an Agent's one conversation and workspace, or a Program's committed state. Keep fresh identities for blind judges, independent candidates, and independent votes.
- Failure already propagates. `Failure` is an Output value, not an exception: `Handle.result()` carries the child's `Failure` to the current execution boundary by default and does not return it as a normal value, so never write `except Failure`. Use `capture_failure=True` at spawn only where the parent needs to inspect that Failure; `result()` then returns the Failure instead of raising it, and takes no argument of its own. Do not scatter `isinstance(..., Failure)` checks through normal success paths. A Search that starts several attempts and must select a success after another fails spawns each attempt with `await ctx.spawn(child.run, capture_failure=True)` and inspects each settled Handle with `await handle.result()`. Without capture, the first Failure ends the Search.
- Do not return with open direct children. After `spawn()`, wait for every handle or cancel and settle it before the owning Composite or Search returns.
- Every authored Agent declares class-level `name`, `prompt_template`, and `policy = RolePolicy(...)`. `RolePolicy` says only what a composition site cannot: `tools`, the optional `task_environment=True` and `blocks_long_sleep=True`, and host toolchain access as `system_read=(SystemDir.CONDA_PACKAGES,)` or `system_write=(SystemDir.CONDA_ROOT,)`. It says nothing about files.
- **Filesystem authority belongs to the Action, not the role.** A Search grants it where it starts the child: `await self.ctx.spawn(child.run, request, upstream=(producer,), read=(task_data(), producer.files()), write=(shared,))`. `read=` lets that Action read the logical resources named; `write=` lets it read and write them; no grant means the resource is not there. Every WorkUnit's own scratch and artifacts are always writable and are never declared. Import `task_data` from `engine.builtin.aibuildai` for the run's task data. Get a reference to what an execution wrote from that execution or its Handle: `producer.files()`. Name a resource your own Search owns and shares with `self.files("board")`, and read what its symlinks point at with `self.files("records", links=True)`; resolve it to a real path with `.path()` when your own Python must create or fill it. One reference cannot be written out: a child that starts descendants of its own during its Action reads that live subtree with `read=(own_files(),)`, because the spawn recording the grant is what gives the child its path. `FileRef` and `own_files` are imported from `engine.durable_execution`; a grant is a `FileRef` and never a `str` or a `Path`.
- **A capability belongs to one call, and a limit follows the thing it limits.** Every `spawn` states `capability=ExecutionCapability(...)`. A WorkUnit Action (Agent or Program) must state `wall_clock_seconds`, states `gpus=1` or `gpus=(0, 1)` to be placed and `gpus=0` to run on the CPU, and may state `cost_cap_usd`, `cpu_max_cores`, `memory_max_gb`, and `retry`. A Search Action states only what bounds the orchestration -- its own `wall_clock_seconds`, `cost_cap_usd`, `retry` -- and is refused if it names cards, cores, or memory: a Search opens no process to put them in, and its descendants declare their own. Two Actions may hold the same card; nothing is reserved. `gpu_candidates=(2, 3)` narrows the pool ONE placement chooses from, which is your scheduling choice; the pool must hold at least the number of distinct cards asked for.
- `upstream=`, ownership and file access are three different relations, and none implies another: an upstream Action exposes no file, a read grant creates no dependency, and starting a child does not let it read your tree. Two Actions may both hold `write=` on one resource; whether that is wise is your algorithm's decision, and the runtime neither serializes nor refuses it.
- Every concrete Agent and Program declares a non-empty class-level `name`.
- The example packages' `input_payload.json` paths (`/run/public/data`, `/run/artifacts/...`) are placeholders. Your own payload names the run's real directories from the facts in your prompt; the verifier rejects a payload whose absolute path does not exist on this host.
- Do not add new bare class attributes: annotate a new constant with `ClassVar[...]`, and only override attributes the base already declares, or the class fails to load as a non-annotated pydantic field.
- A shared Agent base class of your own must set `_intermediate: ClassVar[bool] = True`; only concrete leaves declare the full role surface.
- Never give a helper a single-underscore name that already exists on the base you extend (`_run`, `_record`, `_runtime`, ...): those are the kernel's own members and a redefinition shadows them silently. The package verifier refuses such a class and names the member; `_initial_state` and `_intermediate` are the only underscore members you override.
- `Policy(require_gpu_use=True)` is a property of the whole Program identity, and the check fires on EVERY Action of it that was allocated a GPU. An identity that both trains and ships therefore fails the moment it runs its CPU-only Action: a delivery that copies a checkpoint touches no GPU, and the Action is failed with "received a GPU but did not use it" even though it did exactly its job. Keep GPU work and CPU-only work (`ship`/`deliver`/`soup`) on SEPARATE identities, or leave `require_gpu_use` at its default False on any identity that has a CPU-only Action.
- `verifier_invocations` is optional: omit it when every candidate the role's schema accepts is acceptable. To gate candidates, implement `async def verifier_invocations(self, candidate)` and return an ordered tuple of invocations: `(fresh_unit.run,)` for an Action that declares no request, `(fresh_unit.run, request)` for one that does. Each one runs as an ordinary child Action in that order, its Action returns `VerifierOutput(passed, reason)`, and the first `passed=False` sends its `reason` back to the same producer conversation. Check what the candidate says about itself in the Output model instead: a rule that needs no file and no process is not a verifier.
- Declare an execution's Input and Output only through the `input` field annotation and the generic parameters; never assign `input_type` or `output_schema` yourself.
- An agent prompt file must define `{% macro render(input, schema_json) %}` (optionally `{% macro details(input) %}`); any other top-level variable is undefined and fails at render time.
- Import a first-party name only as `from module import name`, where the module is an authoring facade that exports the name in its `__all__`; plain `import engine...` of a first-party module is rejected. This is the one architectural import rule; your code is trusted, not sandboxed.
- A Program declares one or more typed methods with `@action`, sync or async; its `policy` is a `Policy` and it cannot replace `execute()`. Declare several Actions on one Program only when a later operation needs that identity's committed state: the checkpoint set one `train` produced, the artifact one `export` wrote, the corpus that `generate` accumulates across rounds. Inside an Action body, `self.state` is a JSON-serializable `dict[str, object]`; a successful Action that changed it commits it, and the next Action of the same author-side object starts from that snapshot. Each Action runs in a fresh worker, so a process, a served model, or an in-memory object never survives between Actions; only `self.state` and files under the identity's directories do. Actions of one identity may run in parallel, so spawn every `evaluate` of one trainer, then wait. Independent work such as candidates, shards, and configurations stays one fresh identity with one Action each. Before declaring a second Action, ask whether the "committed state" is anything more than a path the Search could pass by Input: in every post-training example it was not, and those packages are now one Agent whose conversation carries the lineage. A rule such as "rank only after calibrating" is written once in the Search's call order (example 36), not enforced by a state key.
- The package must pass `ruff check --select F`, every mechanical check finishes within 30 seconds, and an oversized package or check output is rejected.
- Everything a Program or an Agent writes under its output directory is validated as a regular single-link file tree. Symlinks are rejected and so are hardlinks: `os.link` and `cp -al` leave `st_nlink > 1`, which fails the check even though `islink()` reports False. Copy bytes when you stage a model or a dataset into an output directory.
- The product prepends YOUR interpreter's `bin` to `PATH`, not the `bin` of an interpreter you choose yourself. A subprocess you launch with another environment's python must have that environment's `bin` prepended by you, because a library it loads may exec a bare binary name rather than an absolute path.
- A Program spawn whose Failure a repair could act on must be `capture_failure=True`; otherwise the Failure propagates and ends the run before any repair code runs. Spawn the same target the same way everywhere: a package that guarded `trainer.sft` inside its round loop and left the bootstrap `trainer.sft` unguarded died on the one call that ran first.
- Exercise a Program's OUTPUT READER on the smoke output before the long run, not only the script that writes it. When a reader expects a manifest key its script does not write, the scripts pass their smoke test, the reader never runs, and a long training with good checkpoints is reported `ARTIFACT_MISSING` and never scored. The reader is part of the contract the smoke must cover.
- A Failure that consumed no time is a planning error, not a round. An Action spawned on an identity whose wall clock is already spent fails at once with `used all effective wall-clock time`; a few of those can consume a run's whole `max_rounds` in minutes and ship its worst checkpoint with hours of budget unused. Count a round only when work ran, and give a strategist the instruments to evaluate and adopt checkpoints that already exist on disk, not only to choose the next training.

## Agent and Program

Before choosing Agent or Program, apply the rule in [Choose Agent or Program](#choose-agent-or-program). Put authored Program classes under `programs/` and authored Composites under `composites/`. Every generated execution is an `Agent`, `Program`, `Search`, or a `Composite`; bare `DurableExecution` and bare `WorkUnit` are not importable from any facade.

## Three source layers

| Layer | Path | Authority | Maintenance |
|---|---|---|---|
| Pattern knowledge | `references/examples/*/*.md` | Every entry; an entry with no complete package is a design reference only | Manual |
| Complete example packages | `references/examples/*/search/` + `input_payload.json` | Both package parts sit beside the document and activate through the real generated-definition loader | `check.sh` rejects one that no longer loads |
| Public API reference | `references/api/*.md` | Python facades, signatures, docstrings | Generated by `scripts/gen_reference.py` |

The API pages are authoritative for imports: every importable name appears on exactly one facade page.

## Generated package contract

Your `SEARCH_TYPE` is a plain child Search, not a built-in product Search.

```text
- SEARCH_TYPE subclasses engine.search.Search, not AIBuildAISearch.
- Generated code does not declare a root-selection kind.
- Generated code does not copy product declarations such as agent_types,
  unit_types, or prompt_template.
- Generated code returns the product's own SearchOutput directly:
  from engine.builtin.aibuildai import SearchOutput
  class XSearch(Search[XInput, SearchOutput]):
      async def explore(self) -> SearchOutput | Failure: ...
  SearchOutput has exactly three fields: output_dir, score, and
  components (a tuple of (name, value) pairs, default empty).
- Those three fields are the whole Output. It carries no display name, no
  path naming what produced it, and no sentence about why the Search
  stopped. An execution edge is a live Handle kept at the spawn site, never
  a path stored in an Output for someone to look up later.
- SearchResult is a different, later thing: the run's final product
  result, combining a SearchOutput with the Finalizer's DeliveryRecord.
  Generated code never constructs one.
- It composes work only through the existing public SDK.
```

## Example index

All examples live under `references/examples/`.

| Examples | What they cover |
|---|---|
| 01–16 | Agent-only search topologies (sequential, parallel, routing, voting, debate, recursion). |
| 17–20 | Frontier search algorithms (beam, A*, MCTS, evolutionary); 18 and 20 score candidates with a Program per candidate, 17 and 19 with Agents. |
| 21, 25, 26, 28 | Applied Agent–Program patterns: settled mechanical work fanned out in parallel (configurations, benchmark pairs, shards, ablation cells) as Programs. |
| 27 | Applied Agent-only pattern: an export Agent and one fresh validation Agent per environment. |
| 29–33, 35–37 | Use `budget.snapshot()` to decide whether another round fits. |
| 37 | Post-training baseline: one worker Agent with the task environment and the GPU does a whole round; the Search loops and keeps the best. |
| 30–33, 35, 36 | Post-training policies on top of 37: checkpoint tournament, RL, teacher distillation, error-driven continuation, guarded on-policy rounds, a calibrated preference branch. All Agent-only. |
| 04, 19, 27, 30–33, 35–37 | Give an authored Agent `task_environment=True` and `gpus=1` to train, serve, or evaluate itself. |
| 18, 20, 21, 25, 26, 28 | Include authored Programs, each for a parallel fan-out. |
| 01–33, 35–37 | Ship a complete `search/` code package. |
| 35, 36 | Choose a training method from a number the run measured, and refuse the round when the measurement does not support it. |
| 31 | Repair edge: the Agent that wrote the training script runs it and fixes its own crash; the Search spawns the stage with `capture_failure=True` so a stage that still fails leaves the last good result standing. |
| 34 | Runtime replanning: a running Search returns to `MetaAgent` with a custom first user turn when the remaining plan is invalidated. SDK code fragments in the pattern document, no code package. |
