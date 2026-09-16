#!/usr/bin/env bash
# Build the GSM8K x Qwen3-1.7B-Base example from scratch.
#
#     bash examples/gsm8k-qwen3-1.7b-base/setup.sh <workdir>
#
# leaves a folder the run can be started from directly:
#
#     <workdir>/task/        the task folder (base weights, splits, grader)
#     <workdir>/playground/  where the run writes
#     <workdir>/task.yaml    the run config, its paths already filled in
#
# It downloads about 4 GB from the Hugging Face Hub and, unless --no-prefetch is
# given, warms the pip cache with the packages the run will install into its own
# Program environment, so that install is fast rather than a cold download.
set -euo pipefail

EXAMPLE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PREFETCH=1
WORKDIR=""

while [ $# -gt 0 ]; do
    case "$1" in
        --no-prefetch) PREFETCH=0 ;;
        -h|--help) sed -n '2,12p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
        -*) echo "unknown option: $1" >&2; exit 2 ;;
        *) [ -z "$WORKDIR" ] || { echo "one workdir, got a second: $1" >&2; exit 2; }
           WORKDIR="$1" ;;
    esac
    shift
done
[ -n "$WORKDIR" ] || { echo "usage: bash setup.sh <workdir> [--no-prefetch]" >&2; exit 2; }

mkdir -p "$WORKDIR"
WORKDIR="$(cd "$WORKDIR" && pwd)"
TASK_DIR="$WORKDIR/task"
SETUP_VENV="$WORKDIR/.setup-venv"

# The downloads need two packages that the example itself does not: keep them in
# a throwaway environment rather than in whatever python the user is standing in.
echo "=== preparing the download environment"
python3 -m venv "$SETUP_VENV"
"$SETUP_VENV/bin/pip" install --quiet --upgrade pip
"$SETUP_VENV/bin/pip" install --quiet "huggingface_hub>=0.25" "pyarrow>=14"

echo "=== building the task folder"
"$SETUP_VENV/bin/python" "$EXAMPLE_DIR/fetch_task.py" --task-dir "$TASK_DIR"

if [ "$PREFETCH" = "1" ]; then
    echo "=== warming the pip cache for the run's Program environment"
    # pip's HTTP cache is shared across environments, so downloading here is what
    # makes the environment the run builds for itself arrive in minutes.
    PREFETCH_DIR="$(mktemp -d)"
    trap 'rm -rf "$PREFETCH_DIR"' EXIT
    "$SETUP_VENV/bin/pip" download --quiet --dest "$PREFETCH_DIR" \
        torch transformers trl peft accelerate datasets || \
        echo "    prefetch failed, continuing: the run will download these itself"
    rm -rf "$PREFETCH_DIR"
    trap - EXIT
fi

echo "=== writing the run config"
mkdir -p "$WORKDIR/playground"
sed -e "s#/path/to/gsm8k/task#$TASK_DIR#" \
    -e "s#/path/to/gsm8k/playground#$WORKDIR/playground#" \
    "$EXAMPLE_DIR/../gsm8k-qwen3-1.7b-base.yaml" > "$WORKDIR/task.yaml"

rm -rf "$SETUP_VENV"

cat <<EOF

The example is ready. Start the run with:

    aibuildai run $WORKDIR/task.yaml

EOF
