# webconfig-manifest — the Zion board manifest (spec §3 platform layer).
#
# Installs the canonical JSON board manifest at
# /usr/share/webconfig/board-manifest.json (Checkpoint 0.6: JSON is the one
# canonical on-disk format/path — matches docs/board-manifest.md and the
# daemon's loader order in crates/webconfigd/src/bootstrap.rs). This repo is
# Zion-only, so there is exactly one manifest file in the common case.
#
# Single source of truth (same pattern as webconfig-static.bb): board-manifests/
# lives at the repo root and is packaged inside the same source tarball the
# daemon recipe fetches — never copied/duplicated into this layer's files/,
# so there is no risk of a stale duplicate drifting from the data the engine
# and its tests actually load.

SUMMARY = "SoCcentric WebConfig — Zion board manifest"
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/MIT;md5=0835ade698e0bcf8506ecda2f7b4f302"

# Same source tarball as webconfigd.bb / webconfig-static.bb (the whole repo,
# including board-manifests/).
SRC_URI = "file://webconfigd-${PV}.tar.gz;unpack=1"

S = "${WORKDIR}/webconfigd-${PV}"

# The real manifest file (Checkpoint 0.5: this used to default to
# "generic.yaml", the wrong platform's data in the wrong format, silently —
# every image built from this layer got the generic fallback). Override
# per-MACHINE only if a future board revision needs a distinct manifest file
# (see webconfig-manifest.bbappend for the real Zynq UltraScale+ MACHINE binding).
WEBCONFIG_BOARD_FILE ?= "zion.json"

do_install() {
    install -d ${D}${datadir}/webconfig
    install -m 0644 ${S}/board-manifests/${WEBCONFIG_BOARD_FILE} \
        ${D}${datadir}/webconfig/board-manifest.json

    # Fail loud (not a silent substitution) if the resolved manifest file is
    # missing from the source tarball (Checkpoint 0.5).
    test -f ${D}${datadir}/webconfig/board-manifest.json || \
        bbfatal "webconfig-manifest: board-manifests/${WEBCONFIG_BOARD_FILE} missing from source tarball"
}

FILES:${PN} = "${datadir}/webconfig/board-manifest.json"
