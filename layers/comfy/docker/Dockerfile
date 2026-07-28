# One container: ComfyUI, and the Vulkan mesh engine beside it.
#
#   docker build -f layers/comfy/docker/Dockerfile -t text-to-3d/comfy:merged .
#
# Built from the two images that already work rather than from source. The
# engine stage is this repo's own runtime image, and everything below the COPY
# is the ComfyUI stack the sibling project builds and pins.
#
# What this buys: one image, one port, one health check, one graph.
#
# What it costs, stated plainly because it is not free:
#
#   - The engine inherits this container's privileges. On its own it runs with
#     /dev/dri and nothing else: no /dev/kfd, no ROCm, not privileged. In here
#     it gets all of that, because ComfyUI needs it.
#   - Restarting ComfyUI restarts the engine, which drops the resident TRELLIS
#     weights and pays the model load again on the next asset.
#   - Nothing gets faster. The HTTP hop it removes was moving one PNG and one
#     GLB either side of a multi-minute reconstruction.
#
# The two-container layout in docker-compose.yml remains supported and is what
# `init` starts by default. This is for when one artifact is the requirement.

ARG ENGINE_IMAGE=text-to-3d/engine:vulkan
ARG COMFY_IMAGE=comfyui-strix-halo:latest

FROM ${ENGINE_IMAGE} AS engine

FROM ${COMFY_IMAGE}

# ComfyUI's image carries ROCm, which is a compute stack and not a Vulkan
# driver. The engine is Vulkan-only and will not start without RADV.
RUN apt-get update && apt-get install -y --no-install-recommends \
      libvulkan1 mesa-vulkan-drivers vulkan-tools libgomp1 curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=engine /opt/t2m/ /opt/t2m/

# The node, baked in rather than bind-mounted: a single-artifact image that
# still needs a host directory mounted to work is not a single artifact.
COPY layers/comfy/ /app/ComfyUI/custom_nodes/text_to_3d/
COPY layers/comfy/docker/supervise.sh /opt/t2m/supervise.sh
RUN chmod +x /opt/t2m/supervise.sh \
 && rm -rf /app/ComfyUI/custom_nodes/text_to_3d/tests \
           /app/ComfyUI/custom_nodes/text_to_3d/docker

# Weights belong in GTT, the 128 GB unified pool, not the small BIOS VRAM
# carve-out. ggml's Vulkan allocator reads this as a presence check.
ENV GGML_VK_PREFER_HOST_MEMORY=1
ENV T2M_MODELS=/models
# Both processes are in this container now, so the node talks to loopback
# rather than out through host-gateway.
ENV T2M_ENGINE=http://127.0.0.1:8189
ENV T2M_OUT_DIR=/app/ComfyUI/output/t2m
ENV LD_LIBRARY_PATH=/opt/t2m:$LD_LIBRARY_PATH

EXPOSE 8188

# Both, or neither. A container answering on 8188 with a dead mesh engine is
# worse than one that failed to start, because the graph looks fine until the
# node is reached and then reports a connection refused nobody can act on.
HEALTHCHECK --interval=30s --timeout=5s --retries=40 --start-period=600s \
  CMD curl -fsS http://localhost:8189/health && curl -fsS http://localhost:8188/system_stats || exit 1

ENTRYPOINT ["/opt/t2m/supervise.sh"]
