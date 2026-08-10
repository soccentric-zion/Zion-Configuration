#!/usr/bin/env bash
# Produce the release artifacts meta-webconfig/recipes-core/webconfig/webconfigd.bb
# and webconfig-static.bb fetch (Checkpoint 3.2): a source tarball
# (webconfigd-<version>.tar.gz, the whole tracked tree via `git archive` — no
# untracked/build cruft) and a vendored-crate tarball (vendor.tar.gz, for the
# offline/reproducible build story in spec §2), plus their real SHA-256
# checksums ready to paste into SRC_URI[sha256sum].
#
# This is intentionally minimal: it does not publish anywhere. A real release
# pipeline would upload dist/*.tar.gz to wherever SRC_URI's mirror lives and
# use these same checksums.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

version="$(grep -m1 '^version' Cargo.toml | sed -E 's/.*"([^"]*)".*/\1/')"
if [ -z "$version" ]; then
    echo "error: could not read version from Cargo.toml" >&2
    exit 1
fi

dist_dir="$repo_root/dist"
mkdir -p "$dist_dir"

src_tarball="$dist_dir/webconfigd-${version}.tar.gz"
vendor_tarball="$dist_dir/vendor.tar.gz"

echo "==> packaging source tarball (git-tracked files only): $src_tarball"
git archive --format=tar.gz --prefix="webconfigd-${version}/" -o "$src_tarball" HEAD

if [ ! -d "$repo_root/vendor" ]; then
    echo "error: vendor/ directory not found — run 'make vendor' first" >&2
    exit 1
fi

echo "==> packaging vendored crates: $vendor_tarball"
tar -czf "$vendor_tarball" -C "$repo_root" vendor

sha256() {
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$1" | cut -d' ' -f1
    else
        shasum -a 256 "$1" | cut -d' ' -f1
    fi
}

src_sha256="$(sha256 "$src_tarball")"
vendor_sha256="$(sha256 "$vendor_tarball")"

cat > "$dist_dir/CHECKSUMS.sha256" <<EOF
${src_sha256}  webconfigd-${version}.tar.gz
${vendor_sha256}  vendor.tar.gz
EOF

echo
echo "==> real checksums (also written to $dist_dir/CHECKSUMS.sha256):"
cat "$dist_dir/CHECKSUMS.sha256"
echo
echo "==> paste into meta-webconfig/recipes-core/webconfig/webconfigd.bb:"
echo "SRC_URI[sha256sum] = \"${src_sha256}\""
echo "# vendor.tar.gz checksum (verify at fetch time or wire into a second"
echo "# SRC_URI[vendor.sha256sum] entry if the recipe names it explicitly):"
echo "${vendor_sha256}"
