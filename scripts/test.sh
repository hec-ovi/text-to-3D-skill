#!/usr/bin/env bash
# Run every layer's contract tests. No GPU, no weights, no network.
#
#   scripts/test.sh              all layers
#   scripts/test.sh image2mesh   one layer
#
# Set T2M_RUN_GPU=1 to also run the tests that need the iGPU and the weights.
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LAYERS=("text2image" "image2mesh" "pipeline")
[ $# -gt 0 ] && LAYERS=("$@")

if command -v uvx >/dev/null 2>&1; then
  RUN=(uvx pytest)
elif python3 -c "import pytest" 2>/dev/null; then
  RUN=(python3 -m pytest)
else
  echo "need pytest: install uv (https://docs.astral.sh/uv/) or pip install pytest" >&2
  exit 1
fi

failed=0
for layer in "${LAYERS[@]}"; do
  dir="${ROOT}/layers/${layer}"
  [ -d "${dir}/tests" ] || { echo "no such layer: ${layer}" >&2; failed=1; continue; }
  echo "== ${layer} =="
  ( cd "$dir" && "${RUN[@]}" tests/ -q ) || failed=1
  echo
done

exit $failed
