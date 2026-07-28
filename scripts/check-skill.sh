#!/usr/bin/env bash
# Consistency checks for the skill files and the plugin manifests.
#
#   scripts/check-skill.sh
#
# The skill exists on four surfaces (root, skills/, and one copy inside each
# plugin) because a marketplace entry whose `source` is ./plugins/<name> gets
# that directory copied into the plugin cache and nothing else. A skill left
# only at the repo root installs as a plugin with no skill in it, which fails
# silently: the install reports success and the agent never sees the file. Two
# other repos here hit exactly that and had to be repackaged by hand. They also
# drift the moment one copy is edited alone, and a drifted skill is worse than a
# missing one, because the agent reads whichever copy the install route handed
# it. Both failures are cheap to check and expensive to notice, so this runs
# with the tests rather than before a release.
set -u
cd "$(dirname "$0")/.."

fail=0
err() { echo "FAIL: $1"; fail=1; }
ok() { echo "ok: $1"; }

ROOT=SKILL.md
COPIES=(
  skills/text-to-3d/SKILL.md
  plugins/text-to-3d/skills/text-to-3d/SKILL.md
  plugins/text-to-3d-codex/skills/text-to-3d/SKILL.md
)

for f in "${COPIES[@]}"; do
  if cmp -s "$ROOT" "$f"; then ok "$f identical to $ROOT"; else err "$f differs from $ROOT (run scripts/sync-skill.sh)"; fi
done

# The resolver table is the entry point: every id in it must have its section.
for id in init generate budget preview batch; do
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

# Every install route needs its own manifest, and each one has to point at a
# directory that really holds the skill. A `source` that names a directory
# without a skills/ tree in it is the silent-install failure above.
market_source=$(python3 -c "import json;print(json.load(open('.claude-plugin/marketplace.json'))['plugins'][0]['source'])")
agent_source=$(python3 -c "import json;print(json.load(open('.agents/plugins/marketplace.json'))['plugins'][0]['source']['path'])")
for source in "$market_source" "$agent_source"; do
  dir="${source#./}"
  if [ -f "$dir/skills/text-to-3d/SKILL.md" ]; then
    ok "$source ships the skill it advertises"
  else
    err "$source has no skills/text-to-3d/SKILL.md, so installing it yields a plugin with no skill"
  fi
done

# The manifests have to agree on the version, or the marketplace installs one
# version and reports another.
market=$(python3 -c "import json;print(json.load(open('.claude-plugin/marketplace.json'))['plugins'][0]['version'])")
plugin=$(python3 -c "import json;print(json.load(open('plugins/text-to-3d/.claude-plugin/plugin.json'))['version'])")
codex=$(python3 -c "import json;print(json.load(open('plugins/text-to-3d-codex/.codex-plugin/plugin.json'))['version'])")
meta=$(python3 -c "import json;print(json.load(open('.claude-plugin/marketplace.json'))['metadata']['version'])")
if [ "$market" = "$plugin" ] && [ "$market" = "$meta" ] && [ "$market" = "$codex" ]; then
  ok "plugin version $market agrees across all three manifests"
else
  err "version drift: marketplace $market, metadata $meta, plugin $plugin, codex $codex"
fi

for f in "${COPIES[@]}"; do
  script="$(dirname "$f")/scripts/init.py"
  metadata="$(dirname "$f")/agents/openai.yaml"
  [ -f "$script" ] && ok "$script exists" || err "$script is missing"
  [ -f "$metadata" ] && ok "$metadata exists" || err "$metadata is missing"
done

# The canonical files the sync copies from.
for f in "$ROOT" scripts/init.py agents/openai.yaml; do
  [ -f "$f" ] && ok "canonical $f present" || err "canonical $f is missing"
done

exit $fail
