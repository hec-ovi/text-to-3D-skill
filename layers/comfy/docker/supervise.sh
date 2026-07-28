#!/bin/bash
# Two processes, one container, one fate.
#
# bash, not sh, for exactly one line: `wait -n`, which returns when the first
# child exits. dash has no equivalent, and every workaround for it is worse:
# polling with `kill -0` races the shell's own reaping, and wrapping each child
# in a subshell that drops a marker file breaks signal delivery to the real
# process. Both images this runs in are Ubuntu, so bash is already there.
#
# Compose gave this away for free: two services, two restart policies, two sets
# of logs. Inside one container it has to be written down, and the only honest
# policy is that the container dies when either child does. A container still
# answering on 8188 with a dead mesh engine looks healthy right up until a graph
# reaches the node, and then reports a connection refused that nobody watching
# the container can act on.
set -eu

MODELS="${T2M_MODELS:-/models}"
ENGINE_PORT="${T2M_ENGINE_PORT:-8189}"
COMFY_PORT="${T2M_COMFY_PORT:-8188}"
COMFY_ARGS="${T2M_COMFY_ARGS:---listen 0.0.0.0}"
# Overridable for the same reason init's docker command is: the policy this
# script encodes is worth a test, and a test cannot install ROCm.
ENGINE_BIN="${T2M_ENGINE_BIN:-/opt/t2m/t2m-server}"
COMFY_DIR="${T2M_COMFY_DIR:-/app/ComfyUI}"
COMFY_BIN="${T2M_COMFY_BIN:-python}"

# The two failures that otherwise surface minutes later as a wrong answer.
if [ "${T2M_SKIP_CHECKS:-0}" != "1" ]; then
  if ! vulkaninfo --summary >/dev/null 2>&1; then
    echo "FATAL: no Vulkan device inside the container." >&2
    echo "  run with: --device /dev/dri --group-add \$(getent group render | cut -d: -f3)" >&2
    exit 78
  fi
  echo "vulkan: $(vulkaninfo --summary 2>/dev/null | awk -F'= ' '/deviceName/ {print $2; exit}')" >&2
  if [ ! -f "${MODELS}/ss_flow.gguf" ]; then
    echo "FATAL: no TRELLIS weights at ${MODELS}." >&2
    echo "  mount them: -v /home/hec/models/gguf/trellis2:/models:ro" >&2
    exit 78
  fi
fi

mkdir -p "${T2M_OUT_DIR:-/app/ComfyUI/output/t2m}"

engine_pid=""
comfy_pid=""

# Ctrl-C and `docker stop` have to reach the children. Without this the shell
# takes the signal, exits, and the container is torn down with two processes
# mid-write.
stop() {
  trap - TERM INT
  [ -n "$engine_pid" ] && kill -TERM "$engine_pid" 2>/dev/null || true
  [ -n "$comfy_pid" ] && kill -TERM "$comfy_pid" 2>/dev/null || true
  wait
  exit 0
}
trap stop TERM INT

echo "[supervise] starting t2m-server on ${ENGINE_PORT}" >&2
"${ENGINE_BIN}" --host 127.0.0.1 --port "${ENGINE_PORT}" \
  --models "${MODELS}" --require-gpu &
engine_pid=$!

# ComfyUI's node list is read at startup, and the node's default endpoint is
# this engine, so the engine being up first is one less confusing first run.
tries=0
until curl -fsS "http://127.0.0.1:${ENGINE_PORT}/health" >/dev/null 2>&1; do
  tries=$((tries + 1))
  if [ "$tries" -gt 600 ]; then
    echo "FATAL: t2m-server did not answer /health within 600s" >&2
    kill -TERM "$engine_pid" 2>/dev/null || true
    exit 1
  fi
  if ! kill -0 "$engine_pid" 2>/dev/null; then
    echo "FATAL: t2m-server exited during startup" >&2
    exit 1
  fi
  sleep 1
done
echo "[supervise] engine ready" >&2

echo "[supervise] starting ComfyUI on ${COMFY_PORT}" >&2
cd "${COMFY_DIR}"
# shellcheck disable=SC2086
"${COMFY_BIN}" main.py --port "${COMFY_PORT}" ${COMFY_ARGS} &
comfy_pid=$!

# Returns on the first child to exit, whichever it is. Everything after this
# line is the container ending. `|| true` because the child's own non-zero exit
# comes back through here and `set -e` would take it as this script's failure.
wait -n "$engine_pid" "$comfy_pid" || true

if kill -0 "$engine_pid" 2>/dev/null; then
  echo "[supervise] ComfyUI exited; stopping the engine" >&2
else
  echo "[supervise] the mesh engine exited; stopping ComfyUI" >&2
fi
kill -TERM "$engine_pid" "$comfy_pid" 2>/dev/null || true
wait 2>/dev/null || true
exit 1
