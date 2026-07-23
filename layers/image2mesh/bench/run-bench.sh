#!/usr/bin/env bash
# Time one image -> GLB run and record what it cost.
#
#   bench/run-bench.sh --tag baseline --image path.png --docker-image text-to-3d/engine:baseline
#   bench/run-bench.sh --tag trimmed  --image path.png --docker-image text-to-3d/engine:vulkan
#
# Writes bench/results/<tag>-r<res>.json: wall time, per-phase seconds parsed
# out of the engine log, peak GTT and VRAM sampled from the kernel's amdgpu
# counters, and the resulting triangle count. Same input image and seed across
# tags, or the numbers mean nothing.
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
TAG=""
IMAGE=""
DOCKER_IMAGE="text-to-3d/engine:vulkan"
MODELS="${TRELLIS_MODELS:-/home/hec/models/gguf/trellis2}"
RES=512
SEED=42
ENTRY="cli"

while [ $# -gt 0 ]; do
  case "$1" in
    --tag) TAG="$2"; shift 2 ;;
    --image) IMAGE="$2"; shift 2 ;;
    --docker-image) DOCKER_IMAGE="$2"; shift 2 ;;
    --models) MODELS="$2"; shift 2 ;;
    --res) RES="$2"; shift 2 ;;
    --seed) SEED="$2"; shift 2 ;;
    --entry) ENTRY="$2"; shift 2 ;;
    *) echo "unknown flag: $1" >&2; exit 2 ;;
  esac
done

[ -n "$TAG" ] || { echo "--tag is required" >&2; exit 2; }
[ -f "$IMAGE" ] || { echo "--image must point at a file" >&2; exit 2; }
[ -d "$MODELS" ] || { echo "no models at $MODELS" >&2; exit 2; }

OUT="${HERE}/results"
WORK="${HERE}/work"
mkdir -p "$OUT" "$WORK"
LOG="${WORK}/${TAG}-r${RES}.log"
GLB="${WORK}/${TAG}-r${RES}.glb"
rm -f "$GLB"

gtt_used() { cat /sys/class/drm/card*/device/mem_info_gtt_used 2>/dev/null | head -1; }
vram_used() { cat /sys/class/drm/card*/device/mem_info_vram_used 2>/dev/null | head -1; }

# Sample the amdgpu counters while the run is in flight. These are system-wide,
# so anything else resident on the GPU (ComfyUI, a browser) sits in the absolute
# number: the delta over the pre-run reading is the figure to compare.
BASE_GTT=$(gtt_used)
BASE_VRAM=$(vram_used)
PEAK_FILE="${WORK}/${TAG}-peak"
: > "$PEAK_FILE"
( while :; do echo "$(gtt_used) $(vram_used)" >> "$PEAK_FILE"; sleep 2; done ) &
SAMPLER=$!
trap 'kill $SAMPLER 2>/dev/null' EXIT

RENDER_GID="$(getent group render | cut -d: -f3)"
VIDEO_GID="$(getent group video | cut -d: -f3)"

echo "bench ${TAG}: ${DOCKER_IMAGE}, res ${RES}, seed ${SEED}"
START=$(date +%s.%N)
# The engine keeps stdout unbuffered and prints a [n/m] banner per stage, so
# stamping each line gives per-phase seconds without touching the C++.
docker run --rm --device /dev/dri \
  --group-add "${RENDER_GID}" --group-add "${VIDEO_GID}" \
  -u "$(id -u):$(id -g)" \
  -v "${MODELS}:/models:ro" \
  -v "$(cd "$(dirname "$IMAGE")" && pwd):/in:ro" \
  -v "${WORK}:/out" \
  "${DOCKER_IMAGE}" ${ENTRY:+$ENTRY} \
  --image "/in/$(basename "$IMAGE")" \
  --output "/out/$(basename "$GLB")" \
  --models /models --res "$RES" --seed "$SEED" --require-gpu \
  2>&1 | python3 -u -c '
import sys, time
start = time.monotonic()
for line in sys.stdin:
    sys.stdout.write(f"{time.monotonic() - start:8.2f} {line}")
' > "$LOG"
STATUS=${PIPESTATUS[0]}
END=$(date +%s.%N)
kill $SAMPLER 2>/dev/null

WALL=$(python3 -c "print(round(${END} - ${START}, 2))")

python3 - "$TAG" "$RES" "$SEED" "$DOCKER_IMAGE" "$WALL" "$STATUS" "$LOG" "$GLB" \
         "$PEAK_FILE" "$OUT" "$BASE_GTT" "$BASE_VRAM" <<'PY'
import json, os, re, struct, sys

tag, res, seed, image, wall, status, log_path, glb, peak_path, out_dir, base_gtt, base_vram = sys.argv[1:]
log = open(log_path, encoding="utf-8", errors="replace").read()

# The engine prints an unbuffered [n/m] banner per stage and one total at the
# end; the bench stamps every line, so the gap between two banners is that
# stage's cost. Time before the first banner is process start plus the first
# model load.
stamped = re.findall(r"^\s*([\d.]+) \[(\d)/(\d)\] (.+)$", log, re.M)
end = re.search(r"^\s*([\d.]+) done in ([\d.]+)s", log, re.M)

marks = [(float(t), f"[{a}/{b}] {c.strip()}") for t, a, b, c in stamped]
if end:
    marks.append((float(end.group(1)), "done"))

banners = []
if marks:
    banners.append({"phase": "startup + first model load",
                    "startSeconds": 0.0, "seconds": round(marks[0][0], 2)})
for i in range(len(marks) - 1):
    start_s, label = marks[i]
    banners.append({"phase": label, "startSeconds": round(start_s, 2),
                    "seconds": round(marks[i + 1][0] - start_s, 2)})

total = re.search(r"done in ([\d.]+)s", log)

peak_gtt = peak_vram = 0
for line in open(peak_path, encoding="utf-8", errors="replace"):
    parts = line.split()
    if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
        peak_gtt = max(peak_gtt, int(parts[0]))
        peak_vram = max(peak_vram, int(parts[1]))

triangles = 0
if os.path.isfile(glb):
    data = open(glb, "rb").read()
    if len(data) > 20 and struct.unpack_from("<I", data, 0)[0] == 0x46546C67:
        offset = 12
        while offset + 8 <= len(data):
            clen, ctype = struct.unpack_from("<II", data, offset)
            if ctype == 0x4E4F534A:
                try:
                    gltf = json.loads(data[offset + 8: offset + 8 + clen].decode("utf-8"))
                    acc = gltf.get("accessors", [])
                    for mesh in gltf.get("meshes", []):
                        for prim in mesh.get("primitives", []):
                            idx = prim.get("indices")
                            if idx is not None and idx < len(acc):
                                triangles += acc[idx].get("count", 0) // 3
                except ValueError:
                    pass
                break
            offset += 8 + clen + (-clen % 4)

record = {
    "tag": tag,
    "dockerImage": image,
    "resolution": int(res),
    "seed": int(seed),
    "exitCode": int(status),
    "wallSeconds": float(wall),
    "engineSeconds": float(total.group(1)) if total else None,
    "phases": banners,
    "peakGttMiB": round(peak_gtt / 1048576),
    "peakVramMiB": round(peak_vram / 1048576),
    "gttDeltaMiB": round((peak_gtt - int(base_gtt or 0)) / 1048576),
    "vramDeltaMiB": round((peak_vram - int(base_vram or 0)) / 1048576),
    "glbBytes": os.path.getsize(glb) if os.path.isfile(glb) else 0,
    "triangles": triangles,
}
path = os.path.join(out_dir, f"{tag}-r{res}.json")
with open(path, "w", encoding="utf-8") as fh:
    json.dump(record, fh, indent=2)
    fh.write("\n")

print(json.dumps(record, indent=2))
print(f"\nwrote {path}", file=sys.stderr)
PY

exit $STATUS
