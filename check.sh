#!/usr/bin/env bash
# The machine gate. Run it before you push.
#
# These six invocations are NOT tests, and this project has no tests (see
# README.md "Checking your work"). They are the compiler: each settles a question that is mechanical
# and enumerable -- is this type sound, is this syntax legal, does this source
# match a pattern we have decided must never appear -- so a machine decides it,
# never forgets it, and is never lenient. That is the whole class of judgment a
# machine may own here. Everything a machine cannot settle is written down as a
# rule for the person or agent doing the work to read.
set -euo pipefail
cd "$(dirname "$0")"

# The three binaries are dev extras of this package, so they sit beside the python
# that has it installed. Derive their location from that python instead of hoping
# each tool is on PATH: a hook, a build script or a cron shell has no environment
# activated, and this gate calling `pyright` bare died with a bare exit 127 there.
#
# This still trusts PATH for `python` itself -- it moves the trust from three names
# to one. That is safe because the failure is LOUD: pick the wrong python and the
# loop below refuses to run and names the tool it could not find. A gate that
# silently cannot find its own checker is worse than no gate; a gate that stops and
# says so is merely inconvenient. A caller that must not depend on the ambient
# python (the checklist stamper) prepends its own interpreter's bin dir to PATH.
PYBIN="$(dirname "$(command -v python3 || command -v python)")"
export PATH="${PYBIN}:${PATH}"
for tool in pyright ruff semgrep; do
    command -v "${tool}" >/dev/null || {
        echo "${tool} not found on PATH or in ${PYBIN}." >&2
        echo "Install the dev extras: pip install -e '.[dev]' (semgrep is provisioned separately" >&2
        echo "-- it hard-pins mcp==1.23.3, which conflicts with the product's mcp)." >&2
        exit 1
    }
done

# The production import surface. ruff's default gate scans exactly these.
PROD=(output engine infra)

# Everything that ships or is imported by something that ships. Wider than PROD:
# the parameter-annotation gate covers it, because an unannotated parameter is a
# type hole anywhere, not only in the three core packages.
SHIPPED=("${PROD[@]}" memory plugins startup
         cli.py cli_impl.py config.py bootstrap.py aibuildai_version.py scripts)

PATTERNS=("${PROD[@]}" startup
          cli.py cli_impl.py config.py bootstrap.py)

echo "pyright"
# Eight checker threads: the whole-repository pass is 3x faster than one
# thread on this class of host and stays polite on a shared 64-core box.
pyright --pythonpath "$(command -v python)" --threads 8

echo "ruff"
ruff check --no-cache --quiet "${PROD[@]}"

echo "ruff (parameter annotations)"
# node_modules is excluded because an installed frontend under output/web
# ships third-party Python helpers that are not ours to annotate.
ruff check --no-cache --quiet \
    --select ANN001,ANN002,ANN003 \
    --exclude 'plugins/**/scripts' \
    --exclude '**/node_modules' \
    "${SHIPPED[@]}"

echo "semgrep"
# --metrics=off: the default phones home before scanning, which cost 10 s of
# every gate run on this network and reports nothing this gate needs.
semgrep --config .semgrep/rules.yaml --error --quiet -j 8 --metrics=off --no-git-ignore "${PATTERNS[@]}"

echo "agent public members"
PYTHONPATH="${PWD}" python scripts/check_agent_public_members.py

echo "lock files"
python scripts/check_lock_drift.py

echo "skill example packages"
# Every complete example under the meta-search-design skill is activated by
# the same loader and contract checks a generated Meta package faces, from
# its own input_payload.json. An example that stops loading fails here
# instead of teaching the MetaAgent a package shape the product rejects.
PYTHONPATH="${PWD}" python scripts/check_skill_examples.py

echo "generated api reference"
# The Skill API pages are generated from the live authoring facades and their
# docstrings. A facade or docstring change without regeneration would teach
# the MetaAgent a stale surface; this re-renders into a temp dir and diffs.
PYTHONPATH="${PWD}" python scripts/gen_reference.py --check

echo "web api schema stamp"
# The generated frontend client (schema.d.ts + dist/) is committed; this arm
# fails when the Pydantic response models moved but `npm run build` (which
# regenerates types + stamp first) was not rerun. Pure Python, no Node.js.
PYTHONPATH="${PWD}" python -m output.web.app --check

echo "frontend palette"
# The Web display may use only the project CSS variables served by
# output/web/theme.py. A raw colour literal in the hand-written frontend
# source is a second palette copy that drifts; generated output
# (src/api/schema.d.ts, dist/) and the package manager tree are exempt.
color_hits=$(grep -rnE '#[0-9a-fA-F]{3,8}\b|\brgba?\(|\bhsla?\(' \
    output/web/frontend/src output/web/frontend/index.html \
    --include='*.ts' --include='*.tsx' --include='*.css' --include='*.html' \
    | grep -v 'src/api/schema.d.ts' || true)
if [ -n "$color_hits" ]; then
    echo "raw colour literal in the Web frontend (use --aibuildai-* variables):" >&2
    echo "$color_hits" >&2
    exit 1
fi

echo "frontend bans"
# The Web page's standing security and design bans, each mechanical:
# no direct HTML injection (dangerouslySetInnerHTML), embedded Markdown HTML
# only through rehype-raw paired with rehype-sanitize in the same file, and
# the execution navigator never uses filesystem icons (the containment
# tree is an execution trace, not folders and files; src/workspace/ is the
# real filesystem tree and the one place those icons belong).
for f in $(grep -rlE 'rehype-raw' output/web/frontend/src --include='*.ts' --include='*.tsx'); do
    grep -q 'rehype-sanitize' "$f" || { echo "rehype-raw without rehype-sanitize: $f" >&2; exit 1; }
done
ban_hits=$(grep -rnE 'dangerouslySetInnerHTML|\bFolder[A-Za-z]*\b|\bFileText\b|\bFile\b' \
    output/web/frontend/src \
    --include='*.ts' --include='*.tsx' \
    | grep -v 'src/api/schema.d.ts' \
    | grep -vE 'src/workspace/.*\b(Folder[A-Za-z]*|FileText|File)\b' || true)
if [ -n "$ban_hits" ]; then
    echo "banned frontend construct (raw HTML path or filesystem icon):" >&2
    echo "$ban_hits" >&2
    exit 1
fi

echo "frontend concrete backend names"
# Handwritten browser source may name only the public family words. A
# PascalCase name with a prefix before a family suffix is a concrete backend
# class, even when it appears only in a comment or string.
concrete_backend_hits=$(grep -rnE '\b[A-Z][A-Za-z0-9]*(Search|Composite|Agent|Program)\b' \
    output/web/frontend/src \
    --include='*.ts' --include='*.tsx' --include='*.css' \
    | grep -v 'src/api/schema.d.ts' || true)
if [ -n "$concrete_backend_hits" ]; then
    echo "concrete backend Execution class in handwritten frontend source:" >&2
    echo "$concrete_backend_hits" >&2
    exit 1
fi

echo "retired identifiers"
retired_ok=true
for symbol in CandidateSearch best_model_pth_path WinnerRef select_winner explore_middle SearchState search_state step_dir_for steps_dir_for trial_dir_for scratch_dir_for CLONE_FIRST optional_knobs pinned_knobs shape_knobs run_config_path RUN_STEPS RUN_TRIALS RUN_ARTIFACTS model_designs _search_capability _search_cgroup _validate_k8s_run_totals _validate_host_capacity owning_search_input caller_directory unitFamily AGENT_KIND TRAINING_KIND EVALUATOR_KIND SCORING_KIND KIND_LABELS TrainingInspector ScoringInspector evaluator_type evaluator_enabled evaluator_required OutputVerifier VerifyContext verifier_capability reviews_producer_artifacts declares_work_unit MetaSearchOutput ScoreProgramOutput ScoringResult work_unit_capability keep_producer producer_handle result_upstream producer_path producer_failure select_result graded_results failure_explanation writeup_warning progress_lines concurrency_ceiling admission_stop_reason exhaustion_stop_reason load_builtin_work_unit_types run_candidate_writer forum_dir_for forum_paper_link FORUM_DIRNAME RUN_FORUM ForumConfig; do
    hits=$(grep -rFl "$symbol" \
        README.md \
        engine/ \
        output/web/frontend/src/ \
        "plugins/aibuildai-builtin-marketplace/aibuildai-builtin/skills/meta-search-design/SKILL.md" \
        "plugins/aibuildai-builtin-marketplace/aibuildai-builtin/skills/meta-search-design/references/examples/" \
        --include='*.py' --include='*.j2' --include='*.md' --include='*.ts' --include='*.tsx' --include='*.css' \
        2>/dev/null || true)
    if [ -n "$hits" ]; then
        echo "retired identifier '${symbol}' found in:" >&2
        echo "$hits" >&2
        retired_ok=false
    fi
done
if [ "$retired_ok" = false ]; then
    exit 1
fi

echo "clean"
