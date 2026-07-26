#!/usr/bin/env bash
# Consistency checks for the skill files and the plugin manifests.
#
#   scripts/check-skill.sh
#
# The skill exists in three places (root, skills/, plugins/) because a plugin
# install carries only its own copy. They drift the moment one is edited alone,
# and a drifted skill is worse than no skill: the agent reads whichever copy the
# install route handed it.
set -u
cd "$(dirname "$0")/.."

fail=0
err() { echo "FAIL: $1"; fail=1; }
ok() { echo "ok: $1"; }

ROOT=SKILL.md
COPIES=(skills/text-to-3d/SKILL.md plugins/text-to-3d/skills/text-to-3d/SKILL.md)

for f in "${COPIES[@]}"; do
  if cmp -s "$ROOT" "$f"; then ok "$f identical to $ROOT"; else err "$f differs from $ROOT (run scripts/sync-skill.sh)"; fi
done

# The resolver table is the entry point: every id in it must have its section.
for id in init generate lowpoly preview batch; do
  grep -qF "<a id=\"$id\"></a>" "$ROOT" || err "$ROOT has no section anchored at $id"
done
grep -q '^| `generate` |' "$ROOT" || err "$ROOT has no capability table"
ok "capability ids resolve to sections"

# Frontmatter the loader needs.
for key in name description; do
  grep -q "^${key}:" "$ROOT" || err "$ROOT frontmatter is missing $key"
done
ok "frontmatter keys present"

for rejected in '<a id="mcp"' '<a id="rig"' 'layers/mcp' 'layers/rig'; do
  if grep -qiF "$rejected" "$ROOT"; then
    err "$ROOT still exposes removed surface: $rejected"
  fi
done
ok "removed surfaces are absent"

# The manifests have to agree on the version, or the marketplace installs one
# version and reports another.
market=$(python3 -c "import json;print(json.load(open('.claude-plugin/marketplace.json'))['plugins'][0]['version'])")
plugin=$(python3 -c "import json;print(json.load(open('plugins/text-to-3d/.claude-plugin/plugin.json'))['version'])")
meta=$(python3 -c "import json;print(json.load(open('.claude-plugin/marketplace.json'))['metadata']['version'])")
if [ "$market" = "$plugin" ] && [ "$market" = "$meta" ]; then
  ok "plugin version $market agrees across both manifests"
else
  err "version drift: marketplace $market, metadata $meta, plugin $plugin"
fi

for f in "${COPIES[@]}"; do
  script="$(dirname "$f")/scripts/init.py"
  metadata="$(dirname "$f")/agents/openai.yaml"
  [ -f "$script" ] && ok "$script exists" || err "$script is missing"
  [ -f "$metadata" ] && ok "$metadata exists" || err "$metadata is missing"
done

exit $fail
