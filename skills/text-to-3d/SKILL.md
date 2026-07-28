---
name: text-to-3d
description: Initialize and operate a fully local text-to-3D toolkit that turns one subject description into a textured GLB, at full density or to a triangle budget, using FLUX.2 klein through ComfyUI and TRELLIS.2 on Vulkan. Use for starting the local generation harness, creating static 3D models, meshes, GLBs or glTF assets from words, preparing game or three.js assets, batching several models, or opening the local preview gallery.
---

# text-to-3d

Turn one described subject into one static GLB:

```text
prompt -> FLUX.2 klein (ComfyUI) -> PNG -> TRELLIS.2 (Vulkan) -> GLB
```

## Choose a capability

| id | Use it for | Section |
| --- | --- | --- |
| `init` | Start and verify the local harness | [Init](#init) |
| `generate` | Generate one static GLB | [Generate](#generate) |
| `budget` | Generate to a triangle budget | [Triangle budget](#budget) |
| `preview` | Inspect generated models | [Preview](#preview) |
| `batch` | Generate several models efficiently | [Batch](#batch) |

Do not use Blender or attempt rigging, skeletons, humanoid movement, or animation. A character request produces a static character model.

<a id="init"></a>
## Init

Run init before the first generation in a session:

```bash
python3 scripts/init.py
```

The bundled launcher finds the toolkit from `--toolkit-dir`, `$TEXT_TO_3D_TOOLKIT`, or the current checkout. When it is installed as a standalone skill and no checkout exists, it clones the toolkit into `~/.local/share/text-to-3d-toolkit`.

Init is idempotent. It:

1. Verifies the ten TRELLIS.2 GGUFs and fetches missing files.
2. Starts the sibling `comfyui-strix-docker` Compose stack.
3. Builds and starts the resident Vulkan mesh engine.
4. Starts the local preview server.
5. Waits for all health checks and prints one JSON result.

The first run may build both images, download about 20 GB of weights, and load the models. Do not report success until the JSON result says every service is `ready`.

Useful overrides:

```bash
python3 scripts/init.py --toolkit-dir /path/to/text-to-3D-skill
python3 scripts/init.py --comfy-dir /path/to/comfyui-strix-docker
python3 scripts/init.py --no-fetch --no-build
```

<a id="generate"></a>
## Generate

Name one complete subject, its important parts, material, and style. The image prompt already supplies centering, a plain background, and even lighting.

```bash
python3 layers/pipeline/src/pipeline.py \
  --prompt "a brass diving helmet with round glass ports and copper fittings" \
  --out-dir out \
  --runner server
```

Use 1024 for full-body figures and 512 for compact props. Describe characters standing still, facing forward, with limbs visible. Do not ask for an action.

| Flag | Default | Change it when |
| --- | --- | --- |
| `--res 512\|1024\|1536` | 512 | A full figure needs more texture detail. |
| `--target-faces N` | 150K at 512 | The asset is for a game, web page, or real-time scene. |
| `--seed N` | Derived from prompt | The run must reproduce a prior asset. |
| `--no-texture` | Off | The caller will supply materials. |
| `--bg-removal birefnet` | Auto | Reflective highlights punch holes in the default matte. |
| `--drop-image` | Off | The intermediate PNG is not needed. |

The result is a schema-validated JSON envelope. The GLB path, checksum, byte size, triangle count, and stage timings come from the written file.

Inspect both the intermediate image and the GLB before reporting completion. A structurally valid model can still omit a requested part.

<a id="budget"></a>
## Triangle budget

A budget is a target, not a quality setting. The reconstruction runs at full
detail either way and the simplifier collapses the result to the count asked
for, with the texture baked afterwards onto the mesh that survives; measured at
4000 faces the shape quality is within a degree of the 138K version. So a
budget costs file size and nothing else, and asking for one never means asking
for a cruder model.

Set one for a game, engine, or web scene:

```bash
python3 layers/pipeline/src/pipeline.py \
  --prompt "a stylised red sports car" \
  --target-faces 12000 \
  --out-dir out \
  --runner server
```

Starting points:

- Small prop: 2K to 6K faces.
- Stylised full-body figure: 5K to 10K.
- Vehicle or hero asset: 20K to 50K.

Decimation runs before UV unwrap, so the texture is baked onto the simplified mesh.

<a id="preview"></a>
## Preview

Init starts the viewer at `http://127.0.0.1:8190/`. An asset id is its GLB file stem.

```text
http://127.0.0.1:8190/?id=<asset-id>
```

Resolve one id before handing over its link:

```bash
curl -fsS "http://127.0.0.1:8190/api/models?id=<asset-id>"
```

<a id="batch"></a>
## Batch

Generate every image first, then every mesh. Alternating stages can evict the image weights and make the next image reload take several minutes.

```bash
python3 layers/text2image/src/klein.py --prompt "..." --out-dir out
python3 layers/image2mesh/src/mesh.py --image out/first.png --out-dir out --runner server
```

The resident engine started by init avoids paying the TRELLIS model-load cost for every mesh.

## Failures

Read the outer `code`, then `cause.code` when present.

| Code | Action |
| --- | --- |
| `MODELS_MISSING` | Re-run init without `--no-fetch`. |
| `SERVICE_TIMEOUT` | Inspect the named endpoint and its Compose logs. |
| `TEXT2IMAGE_FAILED` plus `BACKEND_UNREACHABLE` | Re-run init and inspect the ComfyUI service. |
| `TEXT2IMAGE_FAILED` plus `MODEL_MISSING` | Check the ComfyUI models mount. |
| `IMAGE2MESH_FAILED` plus `NO_VULKAN_DEVICE` | Check `/dev/dri` and the render group id. |
| `IMAGE2MESH_FAILED` plus `GLB_INVALID` | Keep the output and report the engine bug. |

## Limits

- One subject, not a multi-object scene.
- Static meshes only. No animation, skeleton, rig, or Blender path.
- Faces hold up at gameplay distance, not as portrait assets.
- Vulkan GPU required. The engine refuses silent CPU fallback.
