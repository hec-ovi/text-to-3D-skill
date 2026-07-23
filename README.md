<h1 align="center">text-to-3D-skill</h1>

<p align="center">
  <strong>Text in, GLB out, fully local on an AMD Strix Halo APU. FLUX.2 klein draws the reference image through ComfyUI, then TRELLIS.2 reconstructs it into a textured mesh on a Vulkan-only engine. No ROCm on the 3D half, no cloud on either.</strong>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/GPU-Vulkan_(RADV)-A41E22" alt="Vulkan" />
  <img src="https://img.shields.io/badge/AMD-Strix_Halo_gfx1151-ED1C24?logo=amd&logoColor=white" alt="Strix Halo" />
  <img src="https://img.shields.io/badge/Base-Ubuntu_26.04-E95420?logo=ubuntu&logoColor=white" alt="Ubuntu 26.04" />
  <img src="https://img.shields.io/badge/Output-glTF_2.0_binary-87B441" alt="GLB" />
  <img src="https://img.shields.io/badge/License-MIT-blue" alt="MIT" />
</p>

---

## What this is

A skill that turns a sentence into a `.glb` you can hand straight to three.js.

```
"a brass antique diving helmet"
   -> FLUX.2 klein 4B, ComfyUI, ROCm      -> 1024x1024 PNG
   -> TRELLIS.2 4B, our Vulkan container  -> textured GLB
```

Two stages because TRELLIS.2 is an image-to-3D model, not a text-to-3D one. The image stage runs on the ComfyUI stack that already exists on this box; the 3D stage runs in a container this repo builds.

## Why Vulkan for the 3D half

Strix Halo has no officially smooth ROCm story for every workload, and the 3D engine does not need one. `trellis.cpp` already had a Vulkan backend, so the mesh side runs on Mesa RADV against `/dev/dri` with no `/dev/kfd`, no ROCm libraries, and no `--privileged`. The container is 804 MB and starts in under a second.

The engine here is `trellis.cpp` trimmed to that one path. 207 MB of upstream checkout became 1.8 MB of source and 22 files in `src/`: the CUDA and HIP kernels, 15 PyTorch-comparison test binaries, the Tauri desktop app, the safetensors converter and 115 MB of showcase renders are gone. The build fails loudly if `ggml-vulkan` is missing rather than quietly producing a CPU binary. What was removed and why: [`layers/image2mesh/engine/PROVENANCE.md`](layers/image2mesh/engine/PROVENANCE.md). What was changed on top, with measurements: [`layers/image2mesh/CHANGES.md`](layers/image2mesh/CHANGES.md).

## Prerequisites

- AMD Strix Halo (Ryzen AI Max+, gfx1151) on a recent amdgpu kernel. Other Vulkan GPUs should work; only this one is tested.
- Docker and Compose.
- [comfyui-strix-docker](https://github.com/hec-ovi/comfyui-strix-docker) running, with the FLUX.2 klein weights under its models mount: `flux-2-klein-4b.safetensors`, `qwen_3_4b.safetensors`, `flux2-vae.safetensors`.
- 20 GB of disk for the TRELLIS.2 GGUFs, plus Python 3.10+ for the drivers (standard library only, no pip install).

## Setup

```bash
# 1. weights (~16 GiB on disk, every file checksummed against the HF API)
./scripts/fetch-models.sh

# 2. the Vulkan engine image
cd layers/image2mesh && docker build -f docker/Dockerfile -t text-to-3d/engine:vulkan . && cd ../..

# 3. the image backend
cd ../comfyui-strix-docker && docker compose up -d && cd -
```

`fetch-models.sh` is resumable and re-runnable: complete files are skipped, partial ones resume, anything that fails its sha256 is refetched. `--verify-only` checks what is on disk without downloading.

## Use it

```bash
python3 layers/pipeline/src/pipeline.py --prompt "a brass antique diving helmet" --out-dir out
```

Prints a JSON envelope with the GLB path, its sha256, the triangle count and per-stage timings. `--glb-path-only` prints just the path.

```js
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js'
new GLTFLoader().load('/models/asset.glb', (gltf) => scene.add(gltf.scene))
```

Textures are WebP inside the GLB via `EXT_texture_webp`. Rebuild with `-DT2M_WEBP=OFF` if you need PNG instead.

Generating more than one asset in a session? Start the resident server once and pass `--runner server`, so model load is paid at startup instead of per call. The command is in [`SKILL.md`](SKILL.md).

## Layout

Three blackboxes. Each owns a folder, declares a contract, and is changed without reading any other one's source. [`docs/INDEX.md`](docs/INDEX.md) maps "the thing you want to change" to the one folder to open.

| Layer | Owns | Contract |
| --- | --- | --- |
| [`layers/text2image`](layers/text2image) | prompt framing, the ComfyUI graph, the klein weights | [CONTRACT.md](layers/text2image/CONTRACT.md) |
| [`layers/image2mesh`](layers/image2mesh) | the Vulkan engine, the container, GLB validation | [CONTRACT.md](layers/image2mesh/CONTRACT.md) |
| [`layers/pipeline`](layers/pipeline) | stage order, error wrapping | [CONTRACT.md](layers/pipeline/CONTRACT.md) |

Everything crossing a boundary is a schema-validated JSON envelope, and binary payloads cross by reference: path, media type, byte size, sha256. The mesh layer re-hashes the PNG it is handed, so a mismatch fails the run instead of silently reconstructing the wrong picture.

## Tests

```bash
./scripts/test.sh          # all three layers
```

No GPU and no weights needed: the tests stand in only for ComfyUI and the engine binary, and drive the real CLIs for everything else, including five malformed-GLB shapes that must never leave the mesh layer wearing a success envelope. The one test that does need the GPU is skipped unless `T2M_RUN_GPU=1`.

## Limits

- One object per prompt. TRELLIS.2 reconstructs a single subject; ask for two things and you get one confused thing.
- No rigging, no animation, no editing an existing mesh.
- No CPU path. `--require-gpu` is always passed, so a missing Vulkan device is an error rather than a twenty-minute fallback.
- Tested on one machine, the gfx1151 box described in [`layers/image2mesh/bench/README.md`](layers/image2mesh/bench/README.md).
