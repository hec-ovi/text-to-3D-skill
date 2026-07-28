#!/usr/bin/env bash
# Mirror the canonical skill onto every install surface. Run after editing it.
#
#   scripts/sync-skill.sh && scripts/check-skill.sh
#
# Canonical: SKILL.md, scripts/init.py and agents/openai.yaml at the repo root.
#
# The copies exist because a marketplace entry whose `source` is ./plugins/<name>
# gets that directory copied into the plugin cache and nothing else. A skill that
# lives only at the repo root installs as a plugin with no skill in it: the
# install reports success, and the agent never sees the file.
set -euo pipefail
cd "$(dirname "$0")/.."

for target in \
  skills/text-to-3d \
  plugins/text-to-3d/skills/text-to-3d \
  plugins/text-to-3d-codex/skills/text-to-3d
do
  mkdir -p "$target/scripts" "$target/agents"
  cp SKILL.md "$target/SKILL.md"
  cp scripts/init.py "$target/scripts/init.py"
  cp agents/openai.yaml "$target/agents/openai.yaml"
  chmod +x "$target/scripts/init.py"
  echo "synced -> $target"
done
