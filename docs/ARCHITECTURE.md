# AIBuildAI

AIBuildAI builds, evaluates, and selects machine-learning solutions. A run may
research a task, construct candidate solutions, execute and score them, and
deliver the selected output together with the recorded work that produced it.

This README is the engineer's orientation map. It explains the stable mental
model, the major owners, and where to continue reading. Exact signatures belong
to the generated API reference, local invariants belong to their code owners,
and installation and operation belong to the [README](../README.md).

## The system in one picture

```text
one Run
└── root Search
    ├── product setup and shared run services
    ├── explore()
    │   ├── child Search
    │   ├── Composite
    │   ├── Agent
    │   └── Program
    ├── grading and result selection
    └── finalization and delivery
```

A **Search** is recursively composable. Rootness is a position, not a different
type: `run_search(search)` places one Search at the root, while
`ctx.spawn(child.run)` places another below an existing Action. Nested Searches
use the same Run, journal, `RunState`, `Clock`, durable execution layer, and
live root services.

AIBuildAI supplies the fixed product lifecycle around a concrete Search. The
product owns setup, grading, selection, finalization, delivery, and the Web
Workspace. The concrete Search owns only the exploration strategy.

## Three independent axes

Understand every execution along three axes: **family**, **lifetime**, and
**authority**. Do not use one axis to guess another.

### Family: what kind of work is this?

| Family | Owns |
|---|---|
| `Search` | An adaptive or recursive exploration strategy and its direct children. |
| `Composite` | One bounded durable orchestration; no physical resources of its own. |
| `Agent` | LLM work whose identity owns a conversation and workspace. |
| `Program` | Code work in a managed process; its identity may own committed state. |

`Agent` and `Program` are the public `WorkUnit` families. `Search` is the
extension point. Use `Composite` when a bounded operation has a business
identity but no search policy. A verifier is not another family: it is an
ordinary Agent or Program used to judge an output.

### Lifetime: how long does it live?

```text
Definition
    ↓ construct with immutable Input
Identity
    ↓ ctx.spawn(identity.action, request, capability=...)
Action
    ↓ first execution or retry
Attempt
```

| Term | Meaning |
|---|---|
| Definition | Concrete class declaring Input, Actions, and family policy. |
| Identity | One durable state owner; reusing the same object addresses it again. |
| Action | One typed method invocation and one DBOS Workflow. |
| Attempt | One physical try; retry creates another Attempt of the same Action. |

Construct a fresh object for independent candidates, judges, or votes. Reuse an
object only when later Actions need the same evolving identity state, such as an
Agent conversation or a Program's committed state.

Input describes the identity. An Action's zero-or-one typed request describes
one call. The Action owns its typed successful Output or `Failure`. Runtime
values such as paths, workflow IDs, timestamps, Handles, and live services do
not belong in Input.

### Authority: where is the truth?

```text
Event journal
    └── historical business facts
        ├── RunState and ExecutionRecord projections
        ├── Web projections
        └── restored execution objects

DBOS
    └── continuation position of unfinished Action Workflows
```

The journal is the business-history authority. `RunState`, records, the Web
Workspace, and restored objects are projections. DBOS remembers where unfinished
workflows continue; it is not a second business-state authority.

A run has one journal, one `RunState`, one `Clock`, one durable type registry,
and one live root environment. Subsystems derive from these authorities rather
than persisting parallel copies.

## Ownership, data flow, and workspace

A durable path records identity ownership:

```text
search_1
search_1/search_1
search_1/search_1/coder_candidate_1
search_1/search_1/coder_candidate_1/coder_1
```

The names come from the operations one package actually runs, so another
search method spells its own path with its own names.

The parent path owns the child identity. The same path projects into the
workspace and resource hierarchy:

```text
<run_home>/workspace/<durable path>
```

Children and descendants are queries over records; the run stores no second
ownership tree. State shared by several Actions stays with the identity. Files
owned by one physical Action Attempt stay below that occurrence.

Action data flow is separate. `upstream=` names exact completed Handles whose
results a new Action consumes; it does not change ownership.

Filesystem authority is separate again, and belongs to the exact Action. A
caller grants it at the spawn site as `read=` and `write=` over `FileRef`
values, which name logical resources rather than host paths and survive
replay unchanged. Agent and Program launch project the recorded grants into
the existing `Confinement`. No relation implies another: an upstream Action
exposes no file, a granted resource creates no dependency, and ownership
grants no descendant visibility. Several Actions may hold `write` on one
resource; ordering and collaboration are the Search's decision, never the
runtime's.

```text
durable path     -> who owns this identity
upstream Handles -> which Action results this Action consumes
read / write     -> which filesystem resources this Action may use
```

An owner settles every direct child Action before it ends. It may join the
child, observe its independent completion, or cancel and settle it.

## Building the durable graph

`ExecutionContext` is the public graph-building surface:

| Operation | Meaning |
|---|---|
| `spawn` | Start one declared Action and return its typed Handle. |
| `wait` | Record an all, any, or at-least-k terminal observation. |
| `cancel` | End an owned child with the caller-supplied reason. |
| `step` | Checkpoint one opaque nondeterministic leaf. |

Durable children start only through `spawn`. A step may perform external or
nondeterministic work, but code inside it does not re-enter the durable graph.
Search and Composite orchestration is ordinary Python around these four
operations; the runtime does not infer business edges, loops, or selection.

Exact public imports, signatures, and docstrings are generated from the facades
under
[`meta-search-design/references/api`](plugins/aibuildai-builtin-marketplace/aibuildai-builtin/skills/meta-search-design/references/api/).
The facade `__all__` declarations are the export authority; this README does
not duplicate them.

## Limits and physical work

Each identity has immutable Input. Each Action of it has a recorded
`ExecutionCapability`, declared by the call that started it: the same identity
may answer one Action with eight cards and the next with none. Capability
declares limits and execution policy; it is not a reservation or a promise that
the host has capacity.

Keep two levels distinct:

```text
Run ceiling  -> resources.run, enforced by the Run cgroup root over everything
Action limit -> what this one invocation declared and receives
```

Nothing between them narrows: a small Action may start a larger one, and the
sum of several Actions may exceed the Run ceiling without a static refusal.

A limit follows the thing it limits. Some fields constrain owned descendants;
others belong only to one identity or Action. Do not copy one field's
propagation rule to another. The field owner and generated reference carry the
exact rule.

Searches and Composites orchestrate. WorkUnits acquire physical resources for
their Actions and Attempts. Runtime owners translate accepted declarations into
GPU placements, cgroups, processes, sandbox bindings, deadlines, and cleanup. A
GPU placement is not exclusive: two Actions may deliberately share one card, so
nothing is claimed or released.

The `Clock` is the business-time authority. Durable budget observations are
checkpointed so replay takes the same control-flow branch. Offline intervals
between process epochs do not become execution time.

## Failure and verification

A `Failure` originates once and keeps its reason. Retry creates another Attempt
of the same Action. A caller uses `capture_failure=True` only when its algorithm
intentionally consumes a child Failure as a value.

A recoverable failure ends the process epoch without inventing a terminal
result; a later compatible Resume continues the unfinished workflow graph. A
terminal failure records the final decision.

Every structured Agent output passes its producer's ordered verifier chain
before becoming the Action result. The producer owns this composition. Each
verifier is a fresh ordinary Agent or Program returning `VerifierOutput`; a
failed verdict returns its reason to the producer conversation for repair.
Candidate invariants needing no external execution belong in Output validation,
not in another verifier.

Two environment switches exist for developers, not for runs: `AIBUILDAI_DEBUG_LOG=1`
restores transport-level detail (message ids, cache and token breakdowns) in the
transcript and the Web workspace, and `AIBUILDAI_TRACEMALLOC=1` records the top
Python allocations of the run process for memory diagnosis.

## Product Search layers

```text
Search
└── AIBuildAISearch          fixed product lifecycle
    └── one package per search.kind
```

`AIBuildAISearch` supplies setup, grading, selection, and delivery. An algorithm can save a result during exploration. Its final returned result takes priority. If it returns no result or reaches the exploration time limit, the run uses its last saved choice. An explicit failure remains a failure. Each concrete algorithm is one package under `engine/builtin/` and owns its topology, control flow, roles, and admission policy. There is no shared candidate layer between them: each package owns the types its roles use.

Configuration is an external construction language. `search.kind` selects the
root Search class, and `search.input` validates against that class's Input
model. Root, hand-written child, and generated child Searches then use the same
invocation model. A child receives business facts through its own Input; it
does not fill gaps from the root config.

Product grading, candidate policy, and delivery stay above the universal
execution layer. Tasks inside an opaque external engine become AIBuildAI
Executions only when code explicitly creates public SDK invocations.

## MetaSearch

```text
MetaSearch
-> MetaAgent writes one generated Python package
-> the package is checked and loaded
-> its Search is constructed and published
-> ctx.spawn(generated_search.run)
```

Generated Search, Composite, Agent, and Program classes join the same durable
graph. Meta creates no second Run, journal, registry, workflow hierarchy, or
execution substrate.

Generated-package activation and recovery live in
`engine/generated_definitions.py`; the live handoff and public Meta values live
in `engine/meta.py`; the product algorithm lives in `engine/search/meta.py`.
The authoring contract, pattern catalog, examples, and exact API live in the
bundled
[`meta-search-design` Skill](plugins/aibuildai-builtin-marketplace/aibuildai-builtin/skills/meta-search-design/SKILL.md).

## Stable architectural invariants

| Invariant | Consequence |
|---|---|
| Rootness is positional. | Do not create separate root and child Search families. |
| Every fact has one authority. | Derive projections and relationships instead of storing copies. |
| Durable work is explicit. | Children enter the graph through declared Actions and `ctx.spawn`. |
| Ownership, dependency and file access differ. | Paths own identities; `upstream` connects Action results; `read` / `write` grant files. |
| Lifecycles are structured. | An owner settles every direct child before it ends. |
| Product policy stays above the substrate. | Grading, candidate policy, and delivery do not leak into the universal runtime. |
| Generated work uses the same runtime. | Meta-generated definitions receive no parallel execution system. |
| Local rules stay local. | Exact fields, edge cases, and mechanics stay with their code owner. |

## Repository ownership map

| Question | Primary owner |
|---|---|
| Identity, Action, Attempt, Handle, `ExecutionContext`, and DBOS integration | `engine/durable_execution.py` |
| Run aggregate, recorded ownership, and execution queries | `engine/run_state.py`, `engine/event/` |
| Capability declarations and budget observations | `engine/capability.py`, `engine/budget.py` |
| Universal Search and the product lifecycle | `engine/search/base.py` |
| Candidate policy and bounded candidate Composites | `engine/search/candidate.py` |
| Concrete search algorithms | `engine/search/*.py` |
| Bounded orchestration without resources | `engine/composite.py` |
| Shared WorkUnit lifecycle and resources | `engine/work_unit/base.py`, `engine/work_unit/resources.py` |
| LLM conversations, role policy, prompts, and verifier composition | `engine/work_unit/agent/` |
| Managed code execution and Program state | `engine/work_unit/program/`, `infra/exec/` |
| Generated definitions and Meta handoff | `engine/generated_definitions.py`, `engine/meta.py` |
| Product assembly and startup/resume composition | `bootstrap.py`, `startup/` |
| Backends, host resources, processes, and filesystem adapters | `infra/` |
| Web Workspace and other output projections | `output/` |
| Memory corpus and summarization | `memory/` |
| Bundled Skills, MCP definitions, examples, and generated API pages | `plugins/` |
| User installation and operation | `README.md` |
| Machine validation | `check.sh`, `scripts/` |

Directory names should tell the truth about ownership. Before adding a file,
identify the domain object or process that owns its behavior and place the file
there. A directory is not an owner merely because its name appears in the code.

## Where to read next

- Product use: the [README](../README.md).
- Search authoring: the bundled
  [`meta-search-design` Skill](plugins/aibuildai-builtin-marketplace/aibuildai-builtin/skills/meta-search-design/SKILL.md)
  and its generated API pages.
- Subsystem changes: start at the owner above, then read its module and class
  docstrings before following callers.
- Validation: run `./check.sh`; add focused direct evidence only when source
  inspection and the machine gate cannot decide the behavior.

## Glossary

| Term | Definition |
|---|---|
| Definition | Concrete class declaring an identity's Input, Actions, and family policy. |
| Identity | One durable state owner with Input, Actions, and family state. |
| Input | Immutable business facts for one identity; never live runtime machinery. |
| Action | One typed method invocation and its DBOS Workflow. |
| Attempt | One physical try inside an Action; retry preserves the Action. |
| Output | Typed result of one Action. |
| Failure | Typed failed Output recorded at its origin. |
| Run | Complete product execution with one journal and root Search. |
| RunState | Event-sourced aggregate projection for one Run. |
| ExecutionRecord | Recorded Identity, its Action records, and family state. |
| Search | Recursively composable orchestration owning an exploration strategy. |
| Root Search | Search placed at the Run root; rootness is positional. |
| Composite | Bounded orchestration over children with no physical resources. |
| WorkUnit | Resource-consuming durable execution family base. |
| Agent | WorkUnit whose Identity owns an LLM conversation and workspace. |
| Program | WorkUnit whose Actions run in managed processes and may share committed state. |
| Capability | One Action's recorded declaration of limits and execution policy. |
| Handle | Typed future for one exact spawned Action. |
| Step | Checkpointed opaque nondeterministic leaf. |
| Ownership | Identity containment encoded by durable path. |
| upstream | Completed Handles whose Action results a new Action consumes. |
| FileRef | Durable reference to one logical file resource an Action may be granted. |
| Verifier | Agent or Program in a producer-owned output-verification chain. |
| Candidate | Scoreable result owned by a candidate-backed Search. |
| MetaSearch | Product Search that runs a Search written by MetaAgent. |
| MetaAgent | Agent that writes a generated Search package and requested report. |
