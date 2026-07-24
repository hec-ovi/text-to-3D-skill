#!/usr/bin/env bash
# Copy the root SKILL.md over the install copies. Run after editing it.
#
#   scripts/sync-skill.sh && scripts/check-skill.sh
set -euo pipefail
cd "$(dirname "$0")/.."
for target in skills/text-to-3d/SKILL.md plugins/text-to-3d/skills/text-to-3d/SKILL.md; do
  mkdir -p "$(dirname "$target")"
  cp SKILL.md "$target"
  echo "wrote $target"
done
