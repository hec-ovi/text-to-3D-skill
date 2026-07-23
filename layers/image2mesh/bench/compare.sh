#!/usr/bin/env bash
# Run the same asset through both engines back to back and print the delta.
#
#   bench/compare.sh [--res 512]
#
# Stops ComfyUI first so the 15 GB it keeps resident is not competing for
# bandwidth, and starts it again afterwards if it was running.
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
RES=512
IMAGE="${HERE}/../fixtures/bench-subject.png"
COMFY_DIR="${COMFY_DIR:-/home/hec/workspace/comfyui-strix-docker}"

while [ $# -gt 0 ]; do
  case "$1" in
    --res) RES="$2"; shift 2 ;;
    --image) IMAGE="$2"; shift 2 ;;
    *) echo "unknown flag: $1" >&2; exit 2 ;;
  esac
done

COMFY_WAS_UP=0
if docker ps --format '{{.Names}}' | grep -q '^strix-beast$'; then
  COMFY_WAS_UP=1
  echo "stopping ComfyUI so it is not competing for bandwidth"
  (cd "$COMFY_DIR" && docker compose stop) >/dev/null 2>&1
fi

restore() {
  if [ "$COMFY_WAS_UP" = 1 ]; then
    echo "starting ComfyUI again"
    (cd "$COMFY_DIR" && docker compose start) >/dev/null 2>&1
  fi
}
trap restore EXIT

# Warm the page cache so neither tag pays cold disk.
echo "warming the page cache"
cat "${TRELLIS_MODELS:-/home/hec/models/gguf/trellis2}"/*.gguf > /dev/null 2>&1

"${HERE}/run-bench.sh" --tag baseline --image "$IMAGE" \
  --docker-image text-to-3d/engine:baseline --entry "" --res "$RES" >/dev/null
"${HERE}/run-bench.sh" --tag trimmed --image "$IMAGE" \
  --docker-image text-to-3d/engine:vulkan --res "$RES" >/dev/null

python3 - "$HERE/results" "$RES" <<'PY'
import json, os, sys
results_dir, res = sys.argv[1], sys.argv[2]

def load(tag):
    path = os.path.join(results_dir, f"{tag}-r{res}.json")
    return json.load(open(path)) if os.path.isfile(path) else None

before, after = load("baseline"), load("trimmed")
if not before or not after:
    sys.exit("missing a result file; run run-bench.sh for both tags first")

def row(label, a, b, unit="", better_lower=True):
    if a in (None, 0) or b is None:
        return f"| {label} | {a} | {b} | |"
    delta = (b - a) / a * 100
    arrow = "faster" if (delta < 0) == better_lower else "slower"
    if abs(delta) < 0.5:
        arrow = "same"
    return f"| {label} | {a}{unit} | {b}{unit} | {delta:+.1f}% {arrow} |"

print(f"\nres {res}, same image, same seed\n")
print("| | baseline | trimmed | delta |")
print("| --- | --- | --- | --- |")
print(row("wall seconds", before["wallSeconds"], after["wallSeconds"], "s"))
print(row("engine seconds", before["engineSeconds"], after["engineSeconds"], "s"))
print(row("peak GTT delta", before["gttDeltaMiB"], after["gttDeltaMiB"], " MiB"))
print(f"| triangles | {before['triangles']} | {after['triangles']} | "
      f"{'same mesh' if before['triangles'] == after['triangles'] else 'DIFFERENT'} |")
print(f"| GLB bytes | {before['glbBytes']} | {after['glbBytes']} | |")

for tag, record in (("baseline", before), ("trimmed", after)):
    print(f"\n{tag} phases:")
    for phase in record["phases"]:
        print(f"  {phase['seconds']:8.2f}s  {phase['phase']}")
PY
