# AIBuildAI LLM Post-Train Agent

[![Slack](https://img.shields.io/badge/Slack-AIBuildAI%20Community-4A154B?logo=slack)](https://join.slack.com/t/aibuildaicommunity/shared_invite/zt-4a40v9kus-bQr~NIAZSKJkTwYxj5Elww)

Source code of the **AIBuildAI post-train agent**, the line built for autonomous post-training of language models. Given a task folder, the data plus a description of what to solve, it builds, evaluates, and selects solutions and delivers the selected output with the recorded work that produced it. It inherits the whole-experiment tree search of the science line and adds two components. A **knowledge system** for post-training: a remote retrieval service (the Kb) serving skill documents on the workflow of a post-training run and on methods, datasets, and frameworks, which any agent queries over MCP when it faces a design decision. And **meta search**: instead of executing a fixed search, a meta agent investigates the task, consults the knowledge system, and writes the *search program* the run then executes, so the search topology is task-specific. Every run also gets a web workspace and a durable, resumable execution graph backed by a private PostgreSQL journal.

This repository is the source code of the post-train agent, for running from source, reading, and modifying. The packaged AIBuildAI product (pre-built binary and installer) is released from [aibuildai-inc/AI-Build-AI](https://github.com/aibuildai-inc/AI-Build-AI). Report problems with the code on this repository's issue tracker.

## Requirements

- Linux x86_64 with a systemd user session, Python 3.11 to 3.13
- A C toolchain (`gcc`, `make`) with zlib and readline headers: the first run builds a private PostgreSQL from the official source tarball
- `bwrap` (package `bubblewrap`) with unprivileged user namespaces enabled, for the sandbox around agents and training subprocesses
- Network access to GitHub releases and ftp.postgresql.org: the run fetches pinned copies of the tools it owns (ruff, tectonic, micromamba, caddy, PostgreSQL) into `~/.cache/aibuildai` and never uses the host's copies
- Access to a model: for Claude, a Claude Code login or an Anthropic API key; for any other model, an Anthropic-compatible endpoint and its key (DeepSeek and OpenRouter endpoints are built in)
- A CUDA GPU is recommended; the agents detect and use available hardware

No conda is needed: Program environments are created with the owned micromamba. A run is bounded by one cgroup-v2 tree, so your systemd user manager must delegate the `memory` and `pids` controllers (stock Ubuntu does); Check with:

```bash
U=$(id -u); cat /sys/fs/cgroup/user.slice/user-$U.slice/user@$U.service/cgroup.subtree_control
# want at least: memory pids
```

If either is missing, an administrator adds `Delegate=pids memory cpu` to a drop-in under `/etc/systemd/system/user@$U.service.d/`, reloads systemd, and you log in again.

## Installation

```bash
sudo apt-get install build-essential zlib1g-dev libreadline-dev bubblewrap   # Debian/Ubuntu
git clone https://github.com/aibuildai-inc/aibuildai-llm-posttrain-agent.git && cd aibuildai-llm-posttrain-agent
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.lock && pip install -e . --no-deps   # add -r requirements-dev.lock for the check.sh gate
aibuildai setup     # optional: fetch and build the owned tools now instead of at the first run
```

`pip install -e .` provides the `aibuildai` command. Install from the lock file: the pinned `claude-agent-sdk` is the one this line was verified with.

## Configuration

```bash
claude auth login                       # or
export AIBUILDAI_API_KEY=sk-ant-...
```

| Environment variable | Purpose |
|---|---|
| `AIBUILDAI_API_KEY` | API key for the configured model endpoint when there is no Claude Code login |
| `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_API_KEY` | Read, in this order, when `AIBUILDAI_API_KEY` is unset |
| `AIBUILDAI_BASE_URL` | Anthropic-compatible endpoint to use instead of Anthropic, for example `https://openrouter.ai/api` |
| `AIBUILDAI_SMALL_FAST_MODEL` | Model for the agents' small quick calls when the endpoint needs an explicit one |
| `AIBUILDAI_HAIKU_MODEL`, `AIBUILDAI_SONNET_MODEL`, `AIBUILDAI_OPUS_MODEL` | The model the endpoint serves for each of the three Claude model tiers, when it names them differently |
| `AIBUILDAI_SUBAGENT_MODEL` | Model for sub-agents, when it differs from the role's model |
| `AIBUILDAI_KB_BASE_URL` | Base URL of the knowledge base MCP service used when a config enables `mcps: kb`; defaults to the public AIBuildAI Kb, `https://32.194.230.84/open`, a fixed snapshot of the corpus |
| `AIBUILDAI_KB_TOKEN` | Bearer token sent to the knowledge base, when it requires one |
| `AIBUILDAI_WEB_PORT` | TCP port of the local workspace service, when the default is taken |
| `AIBUILDAI_WEB_NAME` | Name of a second workspace service on this host; requires `AIBUILDAI_WEB_PORT` |

A bare `deepseek-*` model id targets DeepSeek's Anthropic-compatible endpoint automatically. OpenRouter ids carry a provider prefix and need `AIBUILDAI_BASE_URL` and `AIBUILDAI_SMALL_FAST_MODEL`. Any other model needs an Anthropic-compatible endpoint that serves it, named in `AIBUILDAI_BASE_URL`, with its key in `AIBUILDAI_API_KEY`.

## Usage

Every run is described by one YAML file. `aibuildai config` prints a starter with every field, its default, and its documentation; `run:`, `llm:`, `work_units:`, `search:`, and `resources:` are required and the starter fills them all in.

```bash
aibuildai config > task.yaml
aibuildai run task.yaml
```

The run prints a workspace URL such as `http://127.0.0.1:<port>/run/<run-id>`; open it to follow the run live. Without a browser the run proceeds the same way.

### Example: protein EC number prediction

The example task predicts the enzyme class (EC number, 1-7) of a protein from its amino acid sequence ([Yu et al., *Science* 2023](https://www.science.org/doi/10.1126/science.adf2465)). The dataset and a scoring script ship with the product repository, [aibuildai-inc/AI-Build-AI](https://github.com/aibuildai-inc/AI-Build-AI), and this repository ships a ready config under `examples/`:

```bash
BASE=https://raw.githubusercontent.com/aibuildai-inc/AI-Build-AI/main
mkdir -p protein-ec/data && cd protein-ec
for f in train.csv test.csv sample_submission.csv; do
    curl -fsSL -o data/$f $BASE/data/protein-ec-prediction/$f
done
curl -fsSL -o data/README.md $BASE/tasks/protein-ec-prediction.md      # the task statement lives in the task folder
sed "s#/path/to/protein-ec#$PWD#" <repo>/examples/protein-ec-prediction.yaml > task.yaml
```

The config sets `claude-opus-5` and `search.kind: meta`: a meta agent first investigates the task and writes the search program, then the run executes that program as a child search and delivers the winner. It mounts the knowledge base for every role (`mcps: kb: { type: reference }`; set `AIBUILDAI_KB_BASE_URL` to use another Kb service) and, through `llm.system_instructions`, tells Setup and the meta agent to consult it and to pass that instruction on to the agents the meta agent writes, keeps the design report off (`search.input.report: false`), and uses short budgets, a 75-minute exploration wall clock with 10-30-minute agent budgets, so a run finishes in under two hours; raise them for a serious run. Run it:

```bash
aibuildai run task.yaml
```

The run home is printed at the end. Because the task's `test.csv` ships without labels, Setup carves a stratified held-out slice out of `train.csv` (the run's `public/test.csv`, 1,313 proteins), writes the answer key and a score program under `private/`, and every candidate is graded on that slice. The `deliverable/` directory holds what the task statement asks for, here a `submission.csv` for that slice; score it with the run's own program in the run's Program environment (macro F1):

```bash
H=playground/protein-ec-prediction/<run-home>
$H/program-environment/env/bin/python -c "import sys; sys.path.insert(0, '$H/private'); import score; print(score.score('$H/deliverable'))"
```

Verified runs of this config take 40-45 minutes and about $20 on one A100: Setup 2 min, the meta agent 5-10 min, the generated search 20-30 min, delivery 1 min. In the run with the knowledge base mounted, Setup, the meta agent, and every generated agent consulted it (`mcp__kb__search_skills` 15 times in total), the meta agent wrote a search program that trains three ESM-2 candidates in parallel and blends the winner, and the deliverable scored 0.800 (the baseline attempt scores 0.08).

### Your own task

Copy `examples/protein-ec-prediction.yaml` (or start from `aibuildai config`, which prints every field with its documentation) and point `run.data_root` at your task folder: everything the run gets, holding what the task asks for in any readable form (a README, a task statement, a paper) plus every data file. Setup reads the folder whole, freezes the statement as the run's README, and writes the run's score program from it.

### Key fields

| YAML path | Default | Meaning |
|---|---|---|
| `llm.default.model` | required | Base model; `effort` and `max_thinking_tokens` tune reasoning |
| `llm.by_role` / `llm.auto` | | Pin a model per role, or let the router choose per role |
| `search.kind` | `tree` (`meta` in the example) | Search method: `meta` (a meta agent writes the search program), or a fixed one: `nb_tree` (whole-experiment tree search), `nb`, `tree`, `linear`, `parallel` |
| `search.input` | per kind | The selected method's own parameters: `meta` takes `report`; the fixed methods take `parallel` and `early_stopping` plus their own |
| `run.budget.wall_clock_minutes` | 1440 | Time after which no new exploration starts; in-flight work and delivery still finish |
| `run.budget.cost_usd` | none | Soft run-level spend limit |
| `run.setup_from_scratch` / `run.task_prompt` | false | Let Setup obtain the data and evaluation itself from a task statement, instead of a task folder |
| `work_units[*].budget` | starter values | Time calculation per unit kind: `fixed` minutes, `depth`-scaled, or `expected` with slack |
| `resources.work_unit.cpu_max_cores` / `memory_max_gb` | starter values | Hard limits for each work unit |
| `memory.enable` | false | Inject the local memory built by `memorize` into every agent |
| `mcps` | `{}` | MCP servers: `kb` (knowledge base), `kaggle` (competition submission, with `submission.max_submissions`), or your own |
| `writer.enable` | false | Draft a paper about the run after aggregation |
| `disallowed_tools` | `[]` | Built-in tools no role may use, for example `['WebSearch', 'WebFetch']` |

### Other commands

- `aibuildai memorize task.yaml` folds the task's past runs into an editable memory document.
- `aibuildai write-paper <run-dir>` drafts a paper from a finished run and compiles it to PDF.
- `aibuildai setup` fetches and builds the owned tools ahead of a run.

## Outputs

```
{playground_root}/<task-name>/<timestamp>_<run-id>/    # the run home
  run_config.json                    # final config and run id, used by resume
  public/                            # what candidates read: the frozen README plus the data Setup prepared
  private/                           # score program, metric manifest, baseline attempt, winner record
  deliverable/                       # runnable model package, or the task-defined result
  workspace/                         # per-search working directories: attempts, artifacts, scratch
  program-environment/               # the run's Program environment
  resource_samples.jsonl             # host resource samples over the run
```

Every event of the run is journaled in the private PostgreSQL cluster under `~/.aibuildai/postgresql`, which is what makes a run resumable and replayable in the workspace.

## Method

**The task and the inherited search.** Post-training is expensive to evaluate, one score means training a model through one or more stages, so only tens of pipelines fit in a budget, and the decisions interact: the right algorithm depends on the data mix, the mix on the base model. The starting point is the whole-experiment tree search of the science line (`search.kind: nb_tree`): a Designer seeds the run with dissimilar starting plans, a Worker (the *experimenter*) conducts each experiment end to end in one session and returns proposals that become child experiments, and the task's own score program grades every finished node after its session has closed. Two components adapt it to post-training.

**Knowledge system.** The Kb is a remote retrieval service that serves a corpus of *skill documents* over MCP; this repository holds the client (`mcps: kb`, tools `mcp__kb__search_skills`, `load_skill`, `read_reference`); the service and its corpus are the AIBuildAI Kb service. For post-training it is organized in four sub-corpora: the **workflow** of one run (the ordered research actions and the judgment each settles), **methodology** cards (a method's paper and math, knobs and defaults per library, cost, and the training signals to watch), **dataset** cards (license, columns and splits, a pinned load line, screening, and which sets are evaluation benchmarks that must be held out), and **framework** cards (when to pick a library, how to start and watch a run, how to save a loadable result). Each skill records approaches that succeed and fail with their conditions and outcomes. An agent queries it with a description of the problem it faces and conditions its design on the result. The client connects to the public AIBuildAI Kb by default. That service is a fixed snapshot of the corpus and does not change between releases. To use another service that exposes the same tools, point `AIBUILDAI_KB_BASE_URL` at it. What your own runs teach you is kept on your machine by `memorize`, never in the knowledge base.

**Meta search (`search.kind: meta`).** A fixed tree of revisions cannot express a sweep before training, a tournament after it, or a fan-in that averages several models. Meta search makes the topology the output of an agent. A **meta agent** investigates the task (it may run pilots), reads the bundled design skill (a catalog of thirty-four workflow and search patterns, each with a loader-verified example package: sequential chain, router and specialists, map-reduce, parallel best-of-N, tournament, committee, orchestrator-workers, evaluator-optimizer, and others), queries the Kb, and writes a **search program**: a Python package composed from three primitives. An *Agent* is an LLM session with tools, policies, and a typed output; a *Program* is a deterministic computation in a managed worker with its own resource limits; a *Search* orchestrates Agents, Programs, and nested Searches through spawn, run, and wait, with typed inputs and outputs flowing between them. The package is checked by a verifier, optionally reviewed together with a LaTeX design report (`search.input.report`), then loaded and run as a child search in the same run and journal. The run's value is still the best score any node earned; the topology that produced it is task-specific.

**Grading, review, delivery.** Setup turns the task folder into a frozen README, a score program, and a baseline attempt, so every role and the final ranking use one definition of success. Optional review roles gate Designer, Coder, Reviser, Setup, Worker, Meta, and Writer submissions. The Finalizer turns the winner into the deliverable.

**Other fixed searches.** `tree` is the v2.5 code search with Judge and Selector, `linear` and `parallel` its forms, and `nb` is a single chain of Workers. Every piece of work is a work unit with typed input and output, a declared time budget, and journaled status; each runs under a bubblewrap sandbox with the task folder read-only and cgroup limits on memory and processes.

The full design is in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Project layout

```
cli.py, cli_impl.py    entry point: command dispatch, run and resume orchestration
config.py              the YAML schema (single source of defaults and docs); example.yaml is generated from it
bootstrap.py           composition root: opens the run, wires the engine and the workspace
aibuildai_version.py   version derivation and the console-script entry
engine/                searches (builtin/aibuildai, builtin/meta and the fixed kinds under builtin/), work units, events, MCP
infra/                 Claude backend, execution, sandbox, owned tools, PostgreSQL, Kb client
output/                the web workspace, transcripts, reports
memory/                user memory: reader, summarizer, curator
plugins/               built-in skills (meta-search-design, checkpoint selection, augmentation, ensembling, routing, paper writing)
startup/               config loading, model endpoint, resource materialization, run launch
examples/              ready-to-run configs
check.sh               the machine gate: pyright, ruff, semgrep, lock drift
```

There is no test suite on this line; `./check.sh` is the gate.

## Citation

The post-train paper is to be released. Until then, cite the AIBuildAI papers:

```bibtex
@article{zhang2026aibuildai,
    title={AIBuildAI: An AI Agent for Automatically Building AI Models},
    author={Ruiyi Zhang and Peijia Qin and Qi Cao and Li Zhang and Pengtao Xie},
    year={2026},
    journal={arXiv},
    url={https://arxiv.org/abs/2604.14455}
}

@article{zhang2026aibuildai2,
    title={AIBuildAI-2: A Knowledge-Enhanced Agent for Automatically Building AI Models},
    author={Ruiyi Zhang and Peijia Qin and Qi Cao and Li Zhang and Pengtao Xie},
    year={2026},
    journal={arXiv},
    url={https://arxiv.org/abs/2605.27873}
}
```

## License

[MIT](LICENSE)

## Community

Questions and discussion: join the [AIBuildAI Community Slack](https://join.slack.com/t/aibuildaicommunity/shared_invite/zt-4a40v9kus-bQr~NIAZSKJkTwYxj5Elww). Use the issue tracker for bugs and feature requests.
