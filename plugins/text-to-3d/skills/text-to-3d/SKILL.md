---
name: text-to-3d
description: Generate a 3D asset from a text description and return a GLB that drops straight into three.js, optionally low-poly and rigged with idle, walk, run and jump. Runs fully local on an AMD Strix Halo iGPU: FLUX.2 klein makes the reference image through ComfyUI, TRELLIS.2 reconstructs it on a Vulkan-only engine, Blender fits the skeleton. No ROCm on the 3D half, no cloud on any of it. Use when the user asks for a 3D asset, a model, a mesh, a GLB, a glTF, a game-ready character, or a rig for one.
when_to_use: User wants a 3D asset generated from words ("make me a 3D barrel", "generate a low-poly sword for my three.js scene", "I need a rigged character that walks"). Also for rigging or previewing a GLB that already exists. Skip for editing mesh geometry, for 2D images alone, or for scenes with more than one object.
user-invocable: true
argument-hint: "<what the object is>"
---

# text-to-3d

One object in words, one GLB out, on this machine:

```
prompt -> FLUX.2 klein (ComfyUI) -> PNG -> TRELLIS.2 (Vulkan) -> GLB -> Blender -> rigged GLB
```

## Pick the capability, then read only that section

| id | Do this when | Section | Returns |
| --- | --- | --- | --- |
| `generate` | The user wants an asset from a description | [Generate](#generate) | an asset id, a path, a preview URL |
| `lowpoly` | It is going into a game or a web scene | [Low poly](#lowpoly) | the same, at a triangle budget you set |
| `rig` | It is a character, or a prop that needs an attach point | [Rig](#rig) | an asset id whose GLB carries a skeleton and clips |
| `assets` | A stock model would do, and minutes matter | [Assets](#assets) | an asset id, fetched in seconds |
| `preview` | The user should see it | [Preview](#preview) | a URL for one asset |
| `mcp` | Another agent or client should drive this | [MCP](#mcp) | the same ids, over a protocol |
| `batch` | Several assets in one session | [Batch](#batch) | ordering that avoids a 500 s stall |

**Everything is addressed by id.** An asset's id is its file name without `.glb`, so `out/abc123-r512.glb` is `abc123-r512`. That one string is the handle everywhere: `?id=abc123-r512` in the viewer, `--id` in the MCP tools, `GET /api/models?id=abc123-r512` from a script. Hand the user a URL, never a path they cannot open.

## Before running

```bash
curl -sf http://127.0.0.1:8188/system_stats >/dev/null && echo "comfy ok"
docker image inspect text-to-3d/engine:vulkan >/dev/null 2>&1 && echo "engine ok"
ls /home/hec/models/gguf/trellis2/*.gguf | wc -l    # want 10
command -v blender || ls /home/hec/opt/blender-*/blender    # only for rigging
```

- ComfyUI down: `cd ../comfyui-strix-docker && docker compose up -d`, then wait for `/system_stats` to answer. The first render after a start pays a full weight load: measured at 333 s against 10 to 14 s warm. That is not a hang.
- A mesh run since the last image render evicts klein's weights, and the next image pays that load again: measured at 516 s. See [Batch](#batch); it applies to two assets, not just to ten.
- Engine image missing: `cd layers/image2mesh && docker build -f docker/Dockerfile -t text-to-3d/engine:vulkan .`
- Weights missing or short: `scripts/fetch-models.sh` (20 GB, checksummed, resumable).

<a id="generate"></a>
## Generate

### Write the prompt first

The prompt is already wrapped in framing that asks for one complete, centred, evenly lit object, so do not repeat that. What you supply is the **subject**, and it decides what you get:

- **Name the whole object and its parts.** "a sword" produced a bare blade with no hilt, no crossguard and no grip, measured, and the reconstruction faithfully rebuilt that blade. "a medieval longsword with a leather-wrapped grip, a steel crossguard and a round pommel" is the same effort and a whole sword.
- **Say the material and the style.** "brass", "weathered oak", "matte plastic", "low-poly stylised". klein renders what it is told.
- **For a character, describe the stance, never the action.** Ask for "standing straight, arms slightly away from the body, facing forward". A prompt like "a warrior walking" bakes a stride into the mesh, and the rig binds in whatever pose it is handed: the walk clip is then applied on top of a walking pose and the motion doubles. The movement comes from [Rig](#rig), not from the picture.
- **One subject.** Two things in a prompt gets you one confused thing.

```bash
python3 layers/pipeline/src/pipeline.py --prompt "a brass diving helmet" --out-dir out
```

Prints a JSON envelope; `--glb-path-only` prints just the path, for piping. Two to four minutes.

| Flag | Default | When to change it |
| --- | --- | --- |
| `--res 512\|1024\|1536` | 512 | **Use 1024 for characters and figures.** 512 spreads its detail budget over the whole body and the texture comes out washed out; the same image at 1024 keeps leather brown, fur reading as fur and steel separate. Compact props are fine at 512. 1536 needs headroom. |
| `--target-faces N` | 150K at res 512, 300K at 1024 | See [Low poly](#lowpoly). |
| `--seed N` | from the prompt | Pin it to reproduce an asset exactly. |
| `--no-texture` | off | Geometry only, when the caller applies its own material. |
| `--bg-removal birefnet` | auto | The subject has specular highlights the threshold matte punches holes through. |
| `--steps N` | 4 | klein is a 4-step model; more steps rarely helps. |
| `--drop-image` | keeps it | You only want the mesh. |

```json
{
  "glb": { "uri": "/abs/path/xxxx-r512.glb", "byteSize": 4210688,
           "checksum": { "sha256": "..." } },
  "triangles": 149982,
  "timings": { "imageMs": 9100, "meshMs": 210400 }
}
```

The GLB is validated before you see it: glTF magic, container version 2, a JSON chunk that parses, a BIN chunk, at least one mesh. `triangles` is counted from the file, not predicted.

**Then look at it.** Validation proves a loader can open the file, not that the object is right; a blade with no hilt validates perfectly. Open the intermediate PNG next to the GLB, or the [Preview](#preview) page, and check the subject is complete before you tell the user it is done. If a part is missing, that is a prompt problem: name the missing part and generate again.

<a id="lowpoly"></a>
## Low poly

`--target-faces N` sets the quadric simplify target. Measured on this box at res 512, same image and seed: 4000 gives **3810 triangles in a 304 KB GLB**, against 147330 triangles and 4.8 MB by default, in the same run time. The collapse happens before the UV unwrap, so the texture is baked onto the simplified mesh rather than reprojected onto it.

```bash
python3 layers/pipeline/src/pipeline.py --prompt "a viking warrior" --target-faces 6000 --out-dir out
```

**Pass it whenever the asset is for a game, an engine or a web scene**, which is most of the time. Without it a prop arrives at 143600 triangles and 4.2 MB, measured on a sword, which is renderer-grade and nobody's game budget. Rough budgets: a few thousand faces for a prop, 5K to 10K for a stylised character, 20K to 50K for a hero asset, the default only when the mesh is going into an offline renderer. Decimate **before** rigging, always: simplifying a skinned mesh throws its weights away.

<a id="rig"></a>
## Rig

A generated mesh is a statue. This turns one into something that walks.

```bash
python3 layers/rig/src/rig.py --glb out/hero-r512.glb --subject humanoid --out-dir out
python3 layers/rig/src/rig.py --glb out/barrel-r512.glb --subject prop --socket socket_top
```

- **humanoid**: 19 Mixamo-named bones fitted by measuring the mesh, following each limb band by band wherever the pose put it, then bone-heat skinning and `idle`, `walk`, `run`, `jump` in the file. A second or two on a decimated mesh. A mesh that arrived in a pose is neutralised first, so a walk cycle starts from standing rather than stacking on top of a stride.
- **prop**: no armature at all. `spin` and `bob` on the node itself, plus a named empty as an attachment point, which is what an engine wants from a barrel.

Needs Blender 5.x (`$BLENDER`, `/home/hec/opt/blender-5.2.0-linux-x64/blender`, or on PATH). It will not rig a quadruped, a vehicle, or a figure whose arms are fused to its body: bone heat spreads weight across the fused bridge and the arm drags the torso. Regenerate instead.

Output is `<stem>-rigged.glb`, so the id gains `-rigged`. In three.js the clips play through `AnimationMixer`:

```js
const mixer = new THREE.AnimationMixer(gltf.scene)
mixer.clipAction(gltf.animations.find((c) => c.name === 'walk')).play()
```

<a id="assets"></a>
## Assets

Generating takes minutes. Fetching an existing CC0 model takes seconds, and lands in the same folder with the same id rules:

```bash
python3 layers/assets/src/assets.py search --query "chair wood" --limit 5
python3 layers/assets/src/assets.py fetch --id painted_wooden_chair_01 --out-dir out
```

Poly Haven only: a few hundred models, every one CC0, so nothing you hand the user carries an attribution obligation. Search first for generic props (a chair, a barrel, a lantern), generate for anything specific to the user's idea. threejsassets.com has no API and forbids automated access, so it cannot be used from here.

<a id="preview"></a>
## Preview

A path is not a preview. Start the viewer once and hand the user a link:

```bash
python3 layers/preview/src/serve.py --dir out &
curl -s "http://127.0.0.1:8190/api/models?id=abc123-r512"    # resolve one asset
# then: http://127.0.0.1:8190/?id=abc123-r512
```

The page lists every GLB in the folder newest first with its source image, triangle count and age; the selected one spins on a turntable; the Image tab shows the picture it was reconstructed from at full size; a rigged asset gets a Motion panel that plays its clips. `--open` opens a browser directly.

<a id="mcp"></a>
## MCP

The same capabilities over stdio, for a client that is not this shell:

```bash
python3 layers/mcp/src/server.py --out-dir out
```

Tools: `generate_model`, `generate_image`, `rig_model`, `list_models`, `get_preview`, `search_assets`, `fetch_asset`, `download_glb`. Every one returns an id, a path and a preview URL; none of them return bytes, because a 20 MB GLB is three hundred times a client's result budget. `generate_model` takes `rig: "humanoid"` to chain both stages in one call, and emits progress notifications while it runs so the client's idle timer stays alive.

Register it in `.mcp.json` (already in this repo):

```json
{ "mcpServers": { "text-to-3d": { "command": "python3",
  "args": ["layers/mcp/src/server.py", "--out-dir", "out"], "timeout": 900000 } } }
```

<a id="batch"></a>
## Batch

Do **all the images first, then all the meshes**, rather than alternating. The mesh stage reads about 11 GB of weights and holds 3.6 GB of GTT, which is enough to push ComfyUI's weights out of memory on a busy box; the next image render then pays a full reload. Measured here: a warm klein render is 10 to 14 seconds, the same render after an eviction was 522 seconds. If the image stage suddenly takes minutes, that is a reload, not klein being slow. `docker logs strix-beast | grep 'Requested to load'` confirms it.

The default runner starts a container per call and pays model load every time. For a batch, start the resident server once and point the pipeline at it:

```bash
docker run -d --name t2m-server --device /dev/dri \
  --group-add "$(getent group render | cut -d: -f3)" \
  -v /home/hec/models/gguf/trellis2:/models:ro -v "$PWD/out:/work" \
  -p 8189:8189 text-to-3d/engine:vulkan server --host 0.0.0.0 --port 8189 --models /models

python3 layers/pipeline/src/pipeline.py --prompt "..." --runner server
```

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
| `BLENDER_MISSING` | Rigging only. Install Blender 5.x or set `$BLENDER`. |
| `RIG_FAILED` + "bone heat" | The mesh is too dirty or its limbs are fused. Decimate harder, or regenerate. |

## Faces, and what the ceiling actually is

A generated character's face will not survive close inspection, and no knob here fixes that. Measured on this box: the same subject at 296590 triangles and at 8000 triangles has the same melted face, eyes smeared into dark patches and the mouth a red smudge. The detail is lost in the reconstruction, not in the decimation, so raising `--target-faces` buys nothing for the face.

The cause is scale. TRELLIS sees one view; the head is roughly an eighth of the figure's height, so at res 1024 the whole head falls in a hundred-odd voxels and the atlas gives the face the share of its texels that its surface area earns, which is a few percent. `--tex-res 1024 --atlas 4096` is the best configuration available and it improves the skin, not the features.

What to do instead, in order of how well it works:

- **Frame the character so the face is not the point.** A closed helmet, a hood, a mask, a visor. Ask for it in the prompt; it reads as a design choice rather than a defect.
- **Ask for a stylised or low-poly character** rather than a photoreal one. A flat-shaded face has no features to get wrong, which is why the reference look works at 5000 triangles.
- **Keep the camera off the face.** These assets hold up at gameplay distance and fall apart in a portrait.

## What this skill will not do

- Multi-object scenes. TRELLIS.2 reconstructs one subject; a prompt with two things gets you one confused thing.
- A photoreal face. See above: the reconstruction cannot resolve one at body scale, at any triangle count.
- Edit an existing mesh, or rig anything that is not one upright figure or one prop.
- Run without a GPU. `--require-gpu` is always passed to the engine, so there is no silent CPU path that takes half an hour.
