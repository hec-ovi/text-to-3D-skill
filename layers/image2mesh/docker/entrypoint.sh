#!/bin/sh
# Fail loudly on the two things that silently produce a useless container:
# no Vulkan device, and no weights.
set -e

if [ "${T2M_SKIP_CHECKS:-0}" != "1" ]; then
  if ! vulkaninfo --summary >/dev/null 2>&1; then
    echo "FATAL: no Vulkan device inside the container." >&2
    echo "  run with: --device /dev/dri --group-add \$(getent group render | cut -d: -f3)" >&2
    exit 78
  fi
  device=$(vulkaninfo --summary 2>/dev/null | awk -F'= ' '/deviceName/ {print $2; exit}')
  echo "vulkan: ${device:-unknown}" >&2

  if [ ! -f "${T2M_MODELS:-/models}/ss_flow.gguf" ]; then
    echo "FATAL: no weights at ${T2M_MODELS:-/models}." >&2
    echo "  mount them: -v /home/hec/models/gguf/trellis2:/models:ro" >&2
    echo "  fetch them: scripts/fetch-models.sh" >&2
    exit 78
  fi
fi

case "$1" in
  server) shift; exec /opt/t2m/t2m-server "$@" ;;
  cli)    shift; exec /opt/t2m/t2m-cli "$@" ;;
  *)      exec /opt/t2m/t2m-cli "$@" ;;
esac
