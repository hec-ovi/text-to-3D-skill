#!/usr/bin/env bash
# Copy the root SKILL.md over the install copies. Run after editing it.
#
#   scripts/sync-skill.sh && scripts/check-skill.sh
set -euo pipefail
cd "$(dirname "$0")/.."
for target in skills/text-to-3d/SKILL.md plugins/text-to-3d/skills/text-to-3d/SKILL.md; do
  dir="$(dirname "$target")"
  mkdir -p "$dir/scripts"
  cp SKILL.md "$target"
  cp scripts/init.py "$dir/scripts/init.py"
  chmod +x "$dir/scripts/init.py"
  echo "wrote $target"
done
