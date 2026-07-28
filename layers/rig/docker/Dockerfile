# rig runtime: SkinTokens on ROCm, with no Blender and no flash-attn.
#
#   docker build -f layers/rig/docker/Dockerfile -t text-to-3d/rig:rocm .
#
# Built on the ComfyUI image because that is where a working gfx1151 PyTorch
# already lives: torch 2.9.1+rocm7.13 from TheRock, pinned to one coherent
# build. Installing a second ROCm torch beside it would be three gigabytes to
# arrive at the same place, and a second chance to get the pin wrong.
ARG COMFY_IMAGE=comfyui-strix-halo:latest
FROM ${COMFY_IMAGE}

ARG SKINTOKENS_REF=main

RUN apt-get update && apt-get install -y --no-install-recommends \
      libgl1 libglib2.0-0 libgomp1 git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# torch, torchvision and torchaudio come from the base image and must not be
# resolved again: pip would happily pull the CUDA build over the ROCm one and
# the failure appears much later, as a device that is suddenly not there.
RUN uv pip install --no-deps --python /app/.venv/bin/python \
      python-box addict fast-simplification \
 && uv pip install --python /app/.venv/bin/python \
      "transformers>=4.57.0" "diffusers>=0.35.0" einops omegaconf lightning \
      trimesh open3d huggingface_hub "numpy>=1.26.0" \
      --no-build-isolation

# bpy is in their requirements and is not installed here. It is a mesh reader
# behind an abstract parser, and this server feeds the model through the `npz`
# loader they already support, so Blender never enters the picture.
RUN git clone --depth 1 --branch ${SKINTOKENS_REF} \
      https://github.com/VAST-AI-Research/SkinTokens.git /opt/skintokens

COPY layers/rig/docker/shim/ /opt/shim/
COPY layers/rig/docker/server.py /opt/rig-server.py

ENV PYTHONPATH=/opt/shim
ENV T2M_SKINTOKENS=/opt/skintokens
ENV T2M_RIG_CKPT=/models/articulation_xl_quantization_256_token_4/grpo_1400.ckpt
ENV T2M_RIG_PORT=8191
ENV HSA_OVERRIDE_GFX_VERSION=11.5.1

EXPOSE 8191
HEALTHCHECK --interval=30s --timeout=5s --retries=40 --start-period=300s \
  CMD curl -fsS http://localhost:8191/health || exit 1

ENTRYPOINT ["python", "/opt/rig-server.py"]
