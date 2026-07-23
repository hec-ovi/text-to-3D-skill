# image2mesh runtime: the trimmed Vulkan-only engine.
#
#   docker build -f docker/Dockerfile -t text-to-3d/engine:vulkan ..
#
# Run needs the render node and the host's render/video groups. No /dev/kfd, no
# ROCm, no --privileged.

# ---------- build ----------------------------------------------------------
FROM ubuntu:26.04 AS build

ENV DEBIAN_FRONTEND=noninteractive
# glslc from 26.04 compiles the integer-dot shader variants older bases skip.
RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential cmake ninja-build git ca-certificates pkg-config \
      libvulkan-dev glslc glslang-tools spirv-tools spirv-headers \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src
COPY engine/ /src/

# GGML_NATIVE is on by default and this image is built on the box it runs on.
RUN cmake -B build -G Ninja -DCMAKE_BUILD_TYPE=Release \
 && cmake --build build -j"$(nproc)" --target t2m-cli t2m-server

# ---------- runtime --------------------------------------------------------
FROM ubuntu:26.04

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
      libvulkan1 mesa-vulkan-drivers vulkan-tools libgomp1 curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Strix Halo's dedicated VRAM is a small BIOS carve-out; the 128 GB of unified
# RAM is reachable as GTT. ggml's Vulkan allocator reads this as a presence
# check (any value enables it) and asks for host-visible memory first.
ENV GGML_VK_PREFER_HOST_MEMORY=1
ENV T2M_MODELS=/models

COPY --from=build /src/build/t2m-cli /src/build/t2m-server /opt/t2m/
COPY --from=build /src/build/*.so* /opt/t2m/
COPY docker/entrypoint.sh /opt/t2m/entrypoint.sh
RUN chmod +x /opt/t2m/entrypoint.sh

ENV LD_LIBRARY_PATH=/opt/t2m
ENV PATH=/opt/t2m:$PATH
WORKDIR /work
ENTRYPOINT ["/opt/t2m/entrypoint.sh"]
CMD ["--help"]
