# webconfig-static — frontend assets served by the daemon (spec §2/§3).
#
# Vanilla JS, native ES modules, no build step, no node at build time. The
# frontend is a directory tree (core/, components/, pages/, styles/ + index.html
# + main.css) wired with real import/export and CSS @import — browsers resolve
# the module graph straight from static files, so the *whole tree* must be
# installed, not a handful of monolithic files. Total <500 KB (spec §2).
#
# Single source of truth: the frontend lives at the repo root in `frontend/`
# and is packaged inside the same `webconfigd-${PV}.tar.gz` source tarball the
# daemon recipe fetches. We unpack that tarball and install its `frontend/`
# subtree recursively into /usr/share/webconfig/static, which is the path the
# daemon serves (resolve_static_dir → /usr/share/webconfig/static, spec §2).
# This replaces an earlier recipe that shipped five stale monolithic files
# (app.js/pages.js/schema-form.js/styles.css/index.html) that had drifted from
# the real modular tree — a released image would have shown an outdated UI.

SUMMARY = "SoCcentric WebConfig frontend static assets"
DESCRIPTION = "Vanilla-JS, no-build frontend for the on-device WebConfig UI."
LICENSE = "CLOSED"

# Same source tarball as webconfigd.bb (the whole repo, incl. frontend/).
SRC_URI = "file://webconfigd-${PV}.tar.gz;unpack=1"

S = "${WORKDIR}/webconfigd-${PV}"

STATIC_DIR = "${D}/usr/share/webconfig/static"

do_install() {
    install -d ${STATIC_DIR}
    # Copy the frontend tree preserving subdirectory structure. `cp -a` keeps
    # the module/CSS import graph intact; --no-preserve=ownership avoids
    # leaking the build host's uid/gid into the image.
    cp -a --no-preserve=ownership ${S}/frontend/. ${STATIC_DIR}/

    # Fail loud if the tarball did not actually contain the frontend tree — a
    # silent skip would ship a daemon with no UI (spec §2).
    test -f ${STATIC_DIR}/index.html || bbfatal "webconfig-static: frontend/index.html missing from source tarball"
    test -f ${STATIC_DIR}/main.css   || bbfatal "webconfig-static: frontend/main.css missing from source tarball"
    test -f ${STATIC_DIR}/core/app.js || bbfatal "webconfig-static: frontend/core/app.js missing from source tarball"

    # spec §2: total payload <500 KB. Warn (not fail) so a slightly-over image
    # still builds but the size regression is visible in the build log.
    size=$(du -sk ${STATIC_DIR} | cut -f1)
    if [ "$size" -gt 512 ]; then
        bbnote "webconfig-static: frontend is ${size} KB (>500 KB spec §2 budget) — trim assets"
    fi
}

FILES:${PN} = "/usr/share/webconfig/static"