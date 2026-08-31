# syntax=docker/dockerfile:1
# ──────────────────────────────────────────────────────────────────────────────
# SoCcentric WebConfig (webconfigd) — production container image.
#
# Builds the on-device configuration daemon and packages it together with the
# vanilla-JS frontend, JSON schemas and board manifest so a containerized / dev
# deployment behaves like the Yocto image (`/usr/share/webconfig/static`,
# `/usr/share/webconfig/schemas`, `<share>/board-manifest.json`).
#
# ARCHITECTURES
#   The builder stage is pinned to the *build* platform (`--platform=$BUILDPLATFORM`)
#   and the runtime stage to the *target* platform (`--platform=$TARGETPLATFORM`).
#   Cross-compiling for aarch64 (Raspberry Pi 5, Zynq MPSoC) from an x86 host is
#   therefore a REAL cross-compile: the Rust compilation runs natively on the
#   host via the aarch64 GNU toolchain — no QEMU emulation anywhere.
#
#   `RUST_TARGET` selects the Rust target triple and the matching cross toolchain.
#   It defaults to the host (x86_64) but is normally driven by docker/Makefile.
#
# Build with:
#   # native host arch
#   docker build -f docker/Dockerfile -t zion-webconfigd .
#
#   # aarch64 (Raspberry Pi 5 / Zynq MPSoC) — requires buildx
#   docker buildx build --platform linux/arm64 \
#       --build-arg RUST_TARGET=aarch64-unknown-linux-gnu \
#       -t zion-webconfigd-arm64 .
#
#   # armv7 (older Raspberry Pi 2/3-class) — requires buildx
#   docker buildx build --platform linux/arm/v7 \
#       --build-arg RUST_TARGET=armv7-unknown-linux-gnueabihf \
#       -t zion-webconfigd-armv7 .
#
#   docker run --rm -p 8443:8443 -v webconfig-data:/data zion-webconfigd
#
# Uses the exact pinned Rust toolchain (rust-toolchain.toml) for
# reproducibility. The daemon has NO OpenSSL/DB system deps (rustls + bundled
# sqlite) so the runtime image stays minimal.
# ──────────────────────────────────────────────────────────────────────────────

# Predefined buildx args with sane defaults so legacy `docker build` (which does
# not auto-populate them) still works for the native host case.
ARG BUILDPLATFORM=linux/amd64
ARG TARGETPLATFORM=linux/amd64
# Rust target triple. Host default unless overridden by docker/Makefile.
ARG RUST_TARGET=x86_64-unknown-linux-gnu

# ──────────────────────────────────────────────────────────────────────────────
# Stage 1 — builder
#   Compile webconfigd with the pinned toolchain, natively on the build
#   platform, targeting $RUST_TARGET.
# ──────────────────────────────────────────────────────────────────────────────
FROM --platform=$BUILDPLATFORM rust:1.97.1-slim AS builder

ARG RUST_TARGET

# apt build helpers. Only the matching cross GNU toolchain is installed when the
# target differs from the host; the native build needs just pkg-config/gcc.
# protobuf-compiler (protoc) is required by hq-agent's build script
# (tonic-build/prost-build compiles proto/zionhq/agent.proto at build time).
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      pkg-config \
      ca-certificates \
      gcc \
      protobuf-compiler \
 && case "$RUST_TARGET" in \
      aarch64-unknown-linux-gnu) \
        apt-get install -y --no-install-recommends gcc-aarch64-linux-gnu libc6-dev-arm64-cross ;; \
      armv7-unknown-linux-gnueabihf) \
        apt-get install -y --no-install-recommends gcc-arm-linux-gnueabihf libc6-dev-armhf-cross ;; \
      x86_64-unknown-linux-gnu) : ;; \
      *) echo "Unsupported RUST_TARGET: $RUST_TARGET" >&2; exit 1 ;; \
    esac \
 && rm -rf /var/lib/apt/lists/*

# The cross C compiler for the bundled sqlite3 (rusqlite `bundled` feature
# compiles the C source via the `cc` crate, which keys off CC_<target>).
ENV CC_aarch64_unknown_linux_gnu=aarch64-linux-gnu-gcc \
    CC_armv7_unknown_linux_gnueabihf=arm-linux-gnueabihf-gcc

WORKDIR /build

# The cross linker is declared in .cargo/config.toml, so carry it into the build.
# (Explicit /build/ destination so the copy is independent of the base image's
# WORKDIR, and so cargo discovers /build/.cargo/config.toml first.)
COPY .cargo /build/.cargo/

# .cargo/config.toml replaces crates.io with a vendored sources directory
# ([source.vendored-sources] directory = "vendor"). The vendored crates must be
# present in the image or every dependency resolution dies with
# "failed to read root of directory source: /vendor" (cargo exit 101).
COPY vendor /build/vendor/

# Copy manifest + lock + toolchain first to leverage the layer cache.
COPY Cargo.toml Cargo.lock ./
COPY rust-toolchain.toml ./
COPY crates ./crates

# board-manifests + schemas are embedded at compile time via include_str!(), so
# they must be present before cargo build runs.
COPY board-manifests ./board-manifests
COPY schemas ./schemas

# Add the cross target.  This is a no-op for the native host target and would
# otherwise force an unnecessary (and flaky) rustup channel sync against
# static.rust-lang.org, so skip the network call entirely when RUST_TARGET
# matches the toolchain's host triple.
RUN if [ "$RUST_TARGET" = "$(rustc --print host-tuple)" ]; then \
        echo "RUST_TARGET=$RUST_TARGET is the native host target; no rustup target add needed"; \
    else \
        rustup target add "$RUST_TARGET"; \
    fi

# Compile (deps cached) and copy the binary out of the cache mount to /out.
RUN --mount=type=cache,target=/usr/local/cargo/registry \
    --mount=type=cache,target=/build/target \
    cargo build --release -p webconfigd --target "$RUST_TARGET" \
 && mkdir -p /out \
 && cp "target/$RUST_TARGET/release/webconfigd" /out/webconfigd

# Copy the runtime assets the image serves.
COPY frontend ./frontend

# ──────────────────────────────────────────────────────────────────────────────
# Stage 2 — runtime
#   Minimal image (target arch) containing the daemon, static UI, schemas and
#   manifest.
# ──────────────────────────────────────────────────────────────────────────────
FROM debian:13-slim AS runtime

# webconfigd is a static-ish binary (rustls, bundled sqlite); only libc + ca
# certs are needed at runtime.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ca-certificates \
      tzdata \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --system --no-create-home --uid 1001 webconfig

# The canonical install layout the daemon resolves by default:
#   /usr/share/webconfig/static   → WEBCONFIG_STATIC_DIR default
#   /usr/share/webconfig/schemas  → schema drop-in dir
#   /usr/share/webconfig/board-manifest.json → BSP-installed canonical manifest
COPY --from=builder /out/webconfigd /usr/bin/webconfigd
COPY --from=builder /build/frontend       /usr/share/webconfig/static
COPY --from=builder /build/schemas        /usr/share/webconfig/schemas
COPY --from=builder /build/board-manifests/zion.json /usr/share/webconfig/board-manifest.json

# Mutable state lives on the /data partition (mirrors the device layout).
RUN mkdir -p /data/webconfig \
 && chown -R webconfig:webconfig /data

USER webconfig
WORKDIR /data

ENV WEBCONFIG_PORT=8443 \
    WEBCONFIG_PLATFORM=zion \
    WEBCONFIG_STATIC_DIR=/usr/share/webconfig/static \
    WEBCONFIG_DATA_DIR=/data/webconfig \
    RUST_LOG=info

EXPOSE 8443

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD wget -q --no-check-certificate -O /dev/null https://127.0.0.1:8443/ || exit 1

ENTRYPOINT ["/usr/bin/webconfigd"]
