# Zion-Configuration — Docker build/run helpers.
#
# Builds the production webconfigd container image. Native builds use the legacy
# builder; aarch64/armv7 builds are TRUE cross-compiles (no QEMU emulation):
# the builder stage stays on the host architecture and compiles the Rust
# daemon against the matching cross GNU toolchain, then assembles an image for
# the target architecture. Both Raspberry Pi 5 and Zynq MPSoC are aarch64, so
# the arm64 image (`build-arm64`) is the one to deploy to them.

IMAGE_NAME ?= zion-webconfigd
TAG        ?= latest
IMAGE      := $(IMAGE_NAME):$(TAG)

# Rust target triples for the architectures we ship to.
X86_64_TARGET := x86_64-unknown-linux-gnu
AARCH64_TARGET := aarch64-unknown-linux-gnu
ARMV7_TARGET  := armv7-unknown-linux-gnueabihf

.PHONY: build build-arm64 build-armv7 run stop logs clean shell push

build:
	@echo "Building native $(X86_64_TARGET) image $(IMAGE) ..."
	docker build --build-arg RUST_TARGET=$(X86_64_TARGET) -f Dockerfile -t $(IMAGE) ..

# aarch64 — Raspberry Pi 5 / Zynq MPSoC. True cross-compile on the host, then a
# single-platform arm64 image. `--load` requires a single platform, which this is.
build-arm64:
	@echo "Cross-compiling $(AARCH64_TARGET) image $(IMAGE)-arm64 ..."
	docker buildx build --platform linux/arm64 \
		--build-arg RUST_TARGET=$(AARCH64_TARGET) \
		--load -f Dockerfile -t $(IMAGE)-arm64 ..

# armv7 — older 32-bit Pi 2/3-class devices.
build-armv7:
	@echo "Cross-compiling $(ARMV7_TARGET) image $(IMAGE)-armv7 ..."
	docker buildx build --platform linux/arm/v7 \
		--build-arg RUST_TARGET=$(ARMV7_TARGET) \
		--load -f Dockerfile -t $(IMAGE)-armv7 ..

run:
	@echo "Running $(IMAGE) (port 8443) ..."
	docker run --rm -d --name webconfigd \
		-p 8443:8443 \
		-v webconfig-data:/data \
		$(IMAGE)

stop:
	docker stop webconfigd 2>/dev/null || true

logs:
	docker logs -f webconfigd

clean:
	docker rmi $(IMAGE) 2>/dev/null || true

shell:
	docker run --rm -it --entrypoint /bin/sh $(IMAGE)

# Push to a registry (set REGISTRY to push elsewhere).
push:
	docker tag $(IMAGE) $(REGISTRY)/$(IMAGE)
	docker push $(REGISTRY)/$(IMAGE)
