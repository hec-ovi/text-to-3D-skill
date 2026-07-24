---
name: text-to-3d
description: Generate a 3D model from a text description and return a GLB file that drops straight into three.js. Runs fully local on an AMD Strix Halo iGPU: FLUX.2 klein makes the reference image through ComfyUI, then TRELLIS.2 reconstructs it into a textured mesh on a Vulkan-only engine, no ROCm and no cloud. Use when the user asks for a 3D asset, a model, a mesh, a GLB or a glTF from a description.
when_to_use: User wants a 3D asset generated from words ("make me a 3D barrel", "generate a low-poly sword for my three.js scene", "I need a GLB of a rusty lantern"). Skip for editing an existing mesh, for 2D images alone, or for scenes with more than one object.
user-invocable: true
argument-hint: "<what the object is>"
---

# text-to-3d

One object in words, one GLB out. Two stages, both local:

```
prompt -> FLUX.2 klein (ComfyUI, ROCm) -> PNG -> TRELLIS.2 (Vulkan container) -> GLB
```

## Before running

Both backends must be up. Check in this order and start what is missing.

```bash
curl -sf http://127.0.0.1:8188/system_stats >/dev/null && echo "comfy ok"
docker image inspect text-to-3d/engine:vulkan >/dev/null 2>&1 && echo "engine ok"
ls /home/hec/models/gguf/trellis2/*.gguf | wc -l    # want 10
```

- ComfyUI down: `cd ../comfyui-strix-docker && docker compose up -d`, then wait for `/system_stats` to answer.
- Engine image missing: `cd layers/image2mesh && docker build -f docker/Dockerfile -t text-to-3d/engine:vulkan .`
- Weights missing or short: `scripts/fetch-models.sh` (20 GB, checksummed, resumable).

## Run it

```bash
python3 layers/pipeline/src/pipeline.py --prompt "a brass diving helmet" --out-dir out
```

Prints a JSON envelope. `--glb-path-only` prints just the path, for piping.

Useful flags:

| Flag | Default | When to change it |
| --- | --- | --- |
| `--res 512\|1024\|1536` | 512 | 1024 for detail worth the extra minutes. 1536 needs headroom. |
| `--seed N` | from the prompt | Pin it to reproduce an asset exactly. |
| `--no-texture` | off | Geometry only, when the caller applies its own material. |
| `--bg-removal birefnet` | auto | The subject has specular highlights the threshold matte punches holes through. |
| `--steps N` | 4 | klein is a 4-step model; more steps rarely helps. |
| `--drop-image` | keeps it | You only want the mesh. |

## Reading the result

```json
{
  "glb": { "uri": "/abs/path/xxxx-r512.glb", "byteSize": 4210688,
           "checksum": { "sha256": "..." } },
  "triangles": 149982,
  "timings": { "imageMs": 9100, "meshMs": 210400 }
}
```

The GLB is validated before you see it: glTF magic, container version 2, a JSON chunk that parses, a BIN chunk, at least one mesh. `triangles` is counted from the file, not predicted.

## Showing the user the result

A path is not a preview. When the user will want to see the asset, start the turntable and give them the link:

```bash
python3 layers/preview/src/serve.py --dir out &
# then point them at http://127.0.0.1:8190/?model=<the file name>
```

It lists every GLB in the folder newest first, spins the selected one, and has wireframe and orbit controls. Add `--open` to open a browser directly.

## Using it in three.js

```js
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js'
new GLTFLoader().load('/models/asset.glb', (gltf) => scene.add(gltf.scene))
```

Textures ship as WebP inside the GLB via `EXT_texture_webp`. Current browsers read it; if you need a wider floor, rebuild the engine with `-DT2M_WEBP=OFF` for PNG textures and a larger file.

## When it fails

Every failure is a JSON envelope on stderr with a code from a closed set. Read `code` first, then `cause`.

| Code | What to do |
| --- | --- |
| `TEXT2IMAGE_FAILED` + `cause.code: BACKEND_UNREACHABLE` | ComfyUI is down. Start it. |
| `TEXT2IMAGE_FAILED` + `cause.code: MODEL_MISSING` | ComfyUI cannot see the klein weights under its models mount. |
| `IMAGE2MESH_FAILED` + `cause.code: NO_VULKAN_DEVICE` | The container got no `/dev/dri`, or the render group id is wrong. |
| `IMAGE2MESH_FAILED` + `cause.code: MODELS_MISSING` | Run `scripts/fetch-models.sh`. |
| `IMAGE2MESH_FAILED` + `cause.code: GLB_INVALID` | The engine wrote a file no loader would open. Keep it and file it, this is a bug. |

## Generating several assets

The default runner starts a container per call and pays model load every time. For a batch, start the resident server once and point the pipeline at it:

```bash
docker run -d --name t2m-server --device /dev/dri \
  --group-add "$(getent group render | cut -d: -f3)" \
  -v /home/hec/models/gguf/trellis2:/models:ro -v "$PWD/out:/work" \
  -p 8189:8189 text-to-3d/engine:vulkan server --host 0.0.0.0 --port 8189 --models /models

python3 layers/pipeline/src/pipeline.py --prompt "..." --runner server
```

## What this skill will not do

- Multi-object scenes. TRELLIS.2 reconstructs one subject; a prompt with two things gets you one confused thing.
- Rigging, animation, or edits to an existing mesh.
- Running without a GPU. `--require-gpu` is always passed to the engine, so there is no silent CPU path that takes half an hour.
