#!/usr/bin/env bash
# Zion-Configuration — per-repo validation gate.  See .claude/lib/validate-lib.sh.
#
#   quick   seconds. Manifest/schema parse + clippy & rustfmt.
#   full    minutes. `make check` = build && test && lint.
#
# WebConfig: the on-device config daemon + UI. Ships into Zion-OS as the
# meta-webconfig Yocto layer, so a broken board manifest breaks the image build
# in a repo that has no idea why.
set -uo pipefail
# shellcheck source=lib/validate-lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/validate-lib.sh"

TIER="$(tier_or_die "${1:-quick}")"

# --- quick -----------------------------------------------------------------

check "board manifests are valid JSON" "python3" \
  'for f in board-manifests/*.json; do
     python3 -c "import json,sys; json.load(open(sys.argv[1]))" "$f" \
       || { echo "invalid JSON: $f"; exit 1; }
   done'

check "zion board manifest declares its hardware blocks" "python3" \
  'python3 - <<PY
import json, sys
m = json.load(open("board-manifests/zion.json"))
blob = json.dumps(m).lower()
missing = [k for k in ("stm32", "can") if k not in blob]
if missing:
    print("zion.json no longer mentions:", ", ".join(missing))
    sys.exit(1)
PY'

check "JSON schemas parse" "python3" \
  'for f in schemas/*.json; do
     python3 -c "import json,sys; json.load(open(sys.argv[1]))" "$f" \
       || { echo "invalid JSON: $f"; exit 1; }
   done'

assert "meta-webconfig layer.conf present for the OS build" \
  'test -f meta-webconfig/conf/layer.conf'

# Directory on disk is meta-webconfig; soccentric-webconfig is the Yocto
# *collection* name inside layer.conf. Distinct concepts, previously confused.
assert "layer.conf declares its collection" \
  'grep -qE "BBFILE_COLLECTIONS|LAYERDEPENDS|BBPATH" meta-webconfig/conf/layer.conf'

check "clippy -D warnings + rustfmt --check" "cargo" 'make lint'

[ "$TIER" = "quick" ] && { report "$TIER"; exit $?; }

# --- full ------------------------------------------------------------------

# `make check` is the repo's own documented full gate: build && test && lint.
check "full gate (build && test && lint)" "cargo" 'make check'

report "$TIER"
