<h1 align="center">AIBuildAI LLM-Post-Train Agent</h1>

<h3 align="center">🏆 #1 on <a href="https://github.com/aisa-group/PostTrainBench">PostTrainBench</a></h3>

---

<p align="center">
  <a href="docs/aibuildai-llm-post-train-agent.pdf"><img src="https://img.shields.io/badge/Tech%20Report-PDF-b31b1b?logo=adobeacrobatreader&logoColor=white" alt="Tech report"></a>
  <a href="https://join.slack.com/t/aibuildaicommunity/shared_invite/zt-4a40v9kus-bQr~NIAZSKJkTwYxj5Elww"><img src="https://img.shields.io/badge/Slack-AIBuildAI%20Community-4A154B?logo=slack" alt="Slack"></a>
  <a href="https://discord.gg/JWrbhmkV6k"><img src="https://img.shields.io/badge/Discord-AIBuildAI%20Community-5865F2?logo=discord&logoColor=white" alt="Discord"></a>
</p>

---

The **AIBuildAI LLM-Post-Train Agent** is a recursive self-improving (RSI) agent for automated, customized post-training of LLMs. It autonomously designs post-training algorithms tailored to each enterprise's proprietary data and use cases, collects and curates its own training data, writes code, runs experiments, and iteratively improves model performance. This repository is its source code, for running from source, reading, and modifying.

Two things make it more than a coding agent. A **knowledge system**: a retrieval service of curated skill documents on post-training methods, datasets and frameworks, which every agent in a run queries over MCP when it faces a design decision. And **meta search**: instead of executing one fixed strategy, a meta agent studies the task, consults the knowledge system, and writes the search program the run then executes, so the shape of the search fits the task rather than being fixed in advance. Every run also gets a live web workspace and a durable, resumable execution graph.

## Current Results

On [PostTrainBench](https://github.com/aisa-group/PostTrainBench), the benchmark of autonomous LLM post-training across model families and target evaluations, the AIBuildAI LLM-Post-Train Agent ranks #1 with an overall score of 46.6%, ahead of every frontier model and agent evaluated and second only to the human expert baseline (51.1%).

<p align="center">
  <img src="assets/ptb-results.png" width="80%" alt="PostTrainBench results">
</p>

## Requirements

- Linux x86_64 with a systemd user session, Python 3.11 to 3.13
- A C toolchain (`gcc`, `make`) with zlib and readline headers: the first run builds a private PostgreSQL from the official source tarball
- `bwrap` (package `bubblewrap`) with unprivileged user namespaces enabled, for the sandbox around agents and training subprocesses
- Network access to GitHub releases and ftp.postgresql.org: the run fetches pinned copies of the tools it owns (ruff, tectonic, micromamba, caddy, PostgreSQL) into `~/.cache/aibuildai` and never uses the host's copies
- Access to a model: for Claude, a Claude Code login or an Anthropic API key; for any other model, an Anthropic-compatible endpoint and its key (DeepSeek and OpenRouter endpoints are built in)
- A CUDA GPU is recommended; the agents detect and use available hardware

No conda is needed: Program environments are created with the owned micromamba. A run is bounded by one cgroup-v2 tree, so your systemd user manager must delegate the `memory` and `pids` controllers (stock Ubuntu does). Check with:

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

### Example: post-train a base model for grade-school math

The example post-trains `Qwen/Qwen3-1.7B-Base` into a model that solves grade-school math word problems ([GSM8K](https://huggingface.co/datasets/openai/gsm8k)), the shape of task this line is built for, at a size that produces a result in about two hours on one GPU. One script builds the whole task from public sources and writes the config:

```bash
bash examples/gsm8k-qwen3-1.7b-base/setup.sh ~/gsm8k-example   # about 4 GB, a couple of minutes
aibuildai run ~/gsm8k-example/task.yaml
```

The script downloads the base weights and both GSM8K splits from the Hugging Face Hub, writes the task statement and the frozen grader into `~/gsm8k-example/task/`, and warms the pip cache with the packages the run installs into its own Program environment. The task folder is what the run gets: the base weights it may tune, the 7473-problem train split, the grader, and the 1319-problem test split the grader reads.

The grader is the whole measurement, and the run's job is to train against it: it prompts the model with exactly `Question: {question}\nAnswer:`, decodes greedily, and reads the number after the last `####`. The base model scores 0.105 on the first 200 problems of that split, because a base model has no answer format at all. The config keeps the meta search described above, a 90-minute exploration wall clock, and per-agent budgets sized for it; raise them for a serious run.

The deliverable is a merged model directory. Grade it with the task's own grader, in the run's Program environment:

```bash
H=~/gsm8k-example/playground/gsm8k-qwen3-1.7b-base/<run-home>
$H/program-environment/env/bin/python ~/gsm8k-example/task/grade/evaluate.py \
    --model-path $H/deliverable/final_model --limit -1
```

### Your own task

Copy `examples/gsm8k-qwen3-1.7b-base.yaml` (or start from `aibuildai config`, which prints every field with its documentation) and point `run.data_root` at your task folder: everything the run gets, holding what the task asks for in any readable form (a README, a task statement, a paper) plus every data file. Setup reads the folder whole, freezes the statement as the run's README, and writes the run's score program from it.

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

**The task and the base search.** Post-training is expensive to evaluate, one score means training a model through one or more stages, so only tens of pipelines fit in a budget, and the decisions interact: the right algorithm depends on the data mix, the mix on the base model. The starting point is a whole-experiment tree search (`search.kind: nb_tree`): a Designer seeds the run with dissimilar starting plans, a Worker (the *experimenter*) conducts each experiment end to end in one session and returns proposals that become child experiments, and the task's own score program grades every finished node after its session has closed. Two components adapt it to post-training.

**Knowledge system.** The Kb is a remote retrieval service that serves a corpus of *skill documents* over MCP; this repository holds the client (`mcps: kb`, tools `mcp__kb__search_skills`, `load_skill`, `read_reference`); the service and its corpus are the AIBuildAI Kb service. For post-training it is organized in four sub-corpora: the **workflow** of one run (the ordered research actions and the judgment each settles), **methodology** cards (a method's paper and math, knobs and defaults per library, cost, and the training signals to watch), **dataset** cards (license, columns and splits, a pinned load line, screening, and which sets are evaluation benchmarks that must be held out), and **framework** cards (when to pick a library, how to start and watch a run, how to save a loadable result). Each skill records approaches that succeed and fail with their conditions and outcomes. An agent queries it with a description of the problem it faces and conditions its design on the result. The client connects to the public AIBuildAI Kb by default. That service is a fixed snapshot of the corpus and does not change between releases. To use another service that exposes the same tools, point `AIBUILDAI_KB_BASE_URL` at it. What your own runs teach you is kept on your machine by `memorize`, never in the knowledge base.

**Meta search (`search.kind: meta`).** A fixed tree of revisions cannot express a sweep before training, a tournament after it, or a fan-in that averages several models. Meta search makes the topology the output of an agent. A **meta agent** investigates the task (it may run pilots), reads the bundled design skill (a catalog of thirty-four workflow and search patterns, each with a loader-verified example package: sequential chain, router and specialists, map-reduce, parallel best-of-N, tournament, committee, orchestrator-workers, evaluator-optimizer, and others), queries the Kb, and writes a **search program**: a Python package composed from three primitives. An *Agent* is an LLM session with tools, policies, and a typed output; a *Program* is a deterministic computation in a managed worker with its own resource limits; a *Search* orchestrates Agents, Programs, and nested Searches through spawn, run, and wait, with typed inputs and outputs flowing between them. The package is checked by a verifier, optionally reviewed together with a LaTeX design report (`search.input.report`), then loaded and run as a child search in the same run and journal. The run's value is still the best score any node earned; the topology that produced it is task-specific.

**Grading, review, delivery.** Setup turns the task folder into a frozen README, a score program, and a baseline attempt, so every role and the final ranking use one definition of success. Optional review roles gate Designer, Coder, Reviser, Setup, Worker, Meta, and Writer submissions. The Finalizer turns the winner into the deliverable.

**Execution.** Every piece of work is a work unit with typed input and output, a declared time budget, and journaled status; each runs under a bubblewrap sandbox with the task folder read-only and cgroup limits on memory and processes.

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

The technical report for this line is [`docs/aibuildai-llm-post-train-agent.pdf`](docs/aibuildai-llm-post-train-agent.pdf); its paper is to be released. Until then, cite the AIBuildAI papers:

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

[Apache License 2.0](LICENSE)

## Community

Questions and discussion: join the [AIBuildAI Community Slack](https://join.slack.com/t/aibuildaicommunity/shared_invite/zt-4a40v9kus-bQr~NIAZSKJkTwYxj5Elww) or the [AIBuildAI Community Discord](https://discord.gg/JWrbhmkV6k). Use the issue tracker for bugs and feature requests.

## Contact us

For enterprises interested in adopting or deploying the AIBuildAI LLM-Post-Train Agent at scale, including technical consulting, custom post-training engagements, or partnership inquiries, contact us at pengtao.xie@aibuildai.io.

Long-term active contributors to this repository are eligible for coding agent sponsorship, such as Claude Code, Cursor, or OpenAI Codex. Email pengtao.xie@aibuildai.io with your most important commits or pull requests.
