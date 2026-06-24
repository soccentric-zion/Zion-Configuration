#!/usr/bin/env bash
# ----------------------------------------------------------------------------
# Populate static/vendor/ with all frontend dependencies for AIR-GAPPED use.
#
# The dashboard makes ZERO external requests at runtime: index.html loads
# everything from /static/vendor/. Run this script on a machine WITH internet,
# then copy the whole project (including static/vendor/) to the device.
#
# Pin versions here so air-gapped builds are reproducible.
# ----------------------------------------------------------------------------
set -euo pipefail

VUE=3.4.38
ROUTER=4.4.5
PV=3.53.1
PI=7.0.0
PF=3.3.1
CHART=4.4.1

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VDIR="$ROOT/static/vendor"
mkdir -p "$VDIR/themes" "$VDIR/primeicons/fonts"

dl() { echo "  $2"; curl -fsSL "$1" -o "$VDIR/$2"; }

echo "Fetching core libraries..."
dl "https://unpkg.com/vue@$VUE/dist/vue.global.prod.js" "vue.global.prod.js"
dl "https://unpkg.com/vue-router@$ROUTER/dist/vue-router.global.prod.js" "vue-router.global.prod.js"
dl "https://unpkg.com/chart.js@$CHART/dist/chart.umd.js" "chart.umd.js"

echo "Fetching CSS..."
dl "https://unpkg.com/primevue@$PV/resources/primevue.min.css" "primevue.min.css"
dl "https://unpkg.com/primevue@$PV/resources/themes/lara-light-blue/theme.css" "themes/lara-light-blue.css"
dl "https://unpkg.com/primevue@$PV/resources/themes/lara-dark-blue/theme.css" "themes/lara-dark-blue.css"
dl "https://unpkg.com/primeflex@$PF/primeflex.min.css" "primeflex.min.css"
dl "https://unpkg.com/primeicons@$PI/primeicons.css" "primeicons/primeicons.css"
for ext in woff2 woff ttf eot svg; do
  curl -fsSL "https://unpkg.com/primeicons@$PI/fonts/primeicons.$ext" \
    -o "$VDIR/primeicons/fonts/primeicons.$ext" || true
done

echo "Assembling offline PrimeVue bundle (core foundation + component closure)..."
# core/core.min.js provides the foundation (base, styles, icons, composables,
# directives) but only embeds a few components; build_vendor_bundle.py appends
# the remaining component UMD files in dependency order plus the Toast/Confirm
# service plugins, producing a single static/vendor/primevue-all.min.js.
python3 "$ROOT/deploy/build_vendor_bundle.py"

echo "Done. static/vendor/ populated for offline use."
