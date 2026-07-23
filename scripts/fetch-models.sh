#!/usr/bin/env bash
# Fetch the TRELLIS.2 GGUF weights and verify every byte.
#
# Sizes and sha256 digests come from the Hugging Face API at run time (the LFS
# oid is the file's sha256), so a truncated or corrupted download is caught here
# instead of surfacing as a garbage mesh an hour later.
#
#   scripts/fetch-models.sh [dest-dir]        default: $TRELLIS_MODELS or /home/hec/models/gguf/trellis2
#   scripts/fetch-models.sh --verify-only     check what is on disk, download nothing
#
# Roughly 20 GB over 10 files. Safe to re-run: complete files are skipped,
# partial ones resume, corrupt ones are re-fetched.
set -uo pipefail

REPO="ilintar/trellis2-gguf"
API="https://huggingface.co/api/models/${REPO}/tree/main"
BASE="https://huggingface.co/${REPO}/resolve/main"
MAX_ATTEMPTS=4

VERIFY_ONLY=0
DEST="${TRELLIS_MODELS:-/home/hec/models/gguf/trellis2}"
for arg in "$@"; do
  case "$arg" in
    --verify-only) VERIFY_ONLY=1 ;;
    -*) echo "unknown flag: $arg" >&2; exit 2 ;;
    *) DEST="$arg" ;;
  esac
done

command -v curl >/dev/null || { echo "curl is required" >&2; exit 1; }
command -v python3 >/dev/null || { echo "python3 is required" >&2; exit 1; }
command -v sha256sum >/dev/null || { echo "sha256sum is required" >&2; exit 1; }

echo "manifest: ${API}"
MANIFEST="$(curl -fsSL "$API" | python3 -c '
import json, sys
try:
    tree = json.load(sys.stdin)
except ValueError:
    sys.exit("could not parse the Hugging Face file listing")
rows = 0
for entry in tree:
    if not entry["path"].endswith(".gguf"):
        continue
    lfs = entry.get("lfs") or {}
    size, oid = lfs.get("size", entry.get("size")), lfs.get("oid")
    if not oid:
        sys.exit(entry["path"] + ": no sha256 in the listing")
    print(entry["path"], size, oid)
    rows += 1
if not rows:
    sys.exit("the listing contains no .gguf files")
')" || { echo "FATAL: could not read the model manifest" >&2; exit 1; }

mkdir -p "$DEST" || exit 1
echo "dest:     ${DEST}"
echo

ok=0; failed=0
while read -r name size sha; do
  path="${DEST}/${name}"

  verify() {
    [ -f "$path" ] || return 1
    local have
    have=$(stat -c %s "$path")
    [ "$have" = "$size" ] || return 1
    [ "$(sha256sum "$path" | cut -d' ' -f1)" = "$sha" ]
  }

  if verify; then
    printf 'ok       %-24s %s\n' "$name" "$(numfmt --to=iec "$size")"
    ok=$((ok + 1))
    continue
  fi

  if [ "$VERIFY_ONLY" = 1 ]; then
    if [ -f "$path" ]; then
      printf 'BAD      %-24s %s on disk, want %s\n' "$name" \
        "$(numfmt --to=iec "$(stat -c %s "$path")")" "$(numfmt --to=iec "$size")"
    else
      printf 'MISSING  %-24s %s\n' "$name" "$(numfmt --to=iec "$size")"
    fi
    failed=$((failed + 1))
    continue
  fi

  got=0
  for attempt in $(seq 1 $MAX_ATTEMPTS); do
    # Size mismatch means the resume point is not trustworthy: start clean.
    if [ -f "$path" ] && [ "$(stat -c %s "$path")" -gt "$size" ]; then
      rm -f "$path"
    fi
    printf 'get      %-24s %s (attempt %d/%d)\n' "$name" "$(numfmt --to=iec "$size")" \
      "$attempt" "$MAX_ATTEMPTS"
    curl -fL --retry 5 --retry-delay 5 --no-progress-meter -C - -o "$path" "${BASE}/${name}"
    if verify; then
      got=1
      break
    fi
    echo "         checksum/size mismatch, refetching from scratch"
    rm -f "$path"
  done

  if [ "$got" = 1 ]; then
    printf 'ok       %-24s verified\n' "$name"
    ok=$((ok + 1))
  else
    printf 'FAILED   %-24s gave up after %d attempts\n' "$name" "$MAX_ATTEMPTS"
    failed=$((failed + 1))
  fi
done <<< "$MANIFEST"

echo
echo "verified ${ok}, failed ${failed}, total $(du -sh "$DEST" | cut -f1) in ${DEST}"
[ "$failed" = 0 ] || exit 1
