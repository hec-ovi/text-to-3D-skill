<h1 align="center">text-to-3D-skill</h1>

<p align="center">
  <strong>Type a sentence, get a game-ready GLB, rigged and animated if you want one, generated entirely on your own AMD APU. No cloud, and no ROCm on the 3D half.</strong>
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

You are blocking out a level and you need a barrel, a crate and a market stall now. Instead of trawling asset stores or modelling them yourself, you run three prompts, get three textured GLBs a few minutes later, and drop them into three.js or Godot at whatever polycount your budget allows.

```
"a brass antique diving helmet"
   -> FLUX.2 klein 4B, ComfyUI, ROCm      -> 1024x1024 PNG
   -> TRELLIS.2 4B, our Vulkan container  -> textured GLB
```

Two stages because TRELLIS.2 is an image-to-3D model, not a text-to-3D one. The image stage runs on the ComfyUI stack that already exists on this box; the 3D stage runs in a container this repo builds.

A third, optional stage rigs the result: a measured skeleton, bone-heat skinning and a walk cycle, so a generated character arrives able to move.

## Why Vulkan for the 3D half

Strix Halo has no officially smooth ROCm story for every workload, and the 3D engine does not need one. `trellis.cpp` already had a Vulkan backend, so the mesh side runs on Mesa RADV against `/dev/dri` with no `/dev/kfd`, no ROCm libraries, and no `--privileged`. The container is 804 MB and starts in under a second.

The engine here is `trellis.cpp` trimmed to that one path. 207 MB of upstream checkout became 1.8 MB of source and 22 files in `src/`: the CUDA and HIP kernels, 15 PyTorch-comparison test binaries, the Tauri desktop app, the safetensors converter and 115 MB of showcase renders are gone. The build fails loudly if `ggml-vulkan` is missing rather than quietly producing a CPU binary. What was removed and why: [`layers/image2mesh/engine/PROVENANCE.md`](layers/image2mesh/engine/PROVENANCE.md). What was changed on top, with measurements: [`layers/image2mesh/CHANGES.md`](layers/image2mesh/CHANGES.md).

## Prerequisites

- AMD Strix Halo (Ryzen AI Max+, gfx1151) on a recent amdgpu kernel. Other Vulkan GPUs should work; only this one is tested.
- Docker and Compose.
- [comfyui-strix-docker](https://github.com/hec-ovi/comfyui-strix-docker) running, with the FLUX.2 klein weights under its models mount: `flux-2-klein-4b.safetensors`, `qwen_3_4b.safetensors`, `flux2-vae.safetensors`.
- 20 GB of disk for the TRELLIS.2 GGUFs, plus Python 3.10+ for the drivers (standard library only, no pip install).
- Blender 5.x if you want rigging. Anywhere on PATH, or pointed at by `$BLENDER`. Nothing else in the repo needs it.

## Install it as a skill

```
/plugin marketplace add hec-ovi/text-to-3D-skill
/plugin install text-to-3d@text-to-3d-skill
/reload-plugins
```

Or clone it, which is what you want if you are going to run the pipeline rather than only read the skill:

```bash
git clone https://github.com/hec-ovi/text-to-3D-skill ~/.claude/skills/text-to-3d
```

The plugin route installs [`SKILL.md`](SKILL.md) alone: a capability table with ids (`generate`, `lowpoly`, `rig`, `preview`, `mcp`, `batch`) that an agent reads to decide what to run. The code, the weights and the container come from the clone.

To drive it from another MCP client, [`.mcp.json`](.mcp.json) registers the stdio server this repo ships:

```json
{ "mcpServers": { "text-to-3d": { "command": "python3",
  "args": ["layers/mcp/src/server.py", "--out-dir", "out"], "timeout": 900000 } } }
```

Six tools, every one returning an id, a path and a preview URL rather than bytes: `generate_model`, `generate_image`, `rig_model`, `list_models`, `get_preview`, `download_glb`. Why never bytes, and why there is no SDK: [`layers/mcp/README.md`](layers/mcp/README.md).

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

## Search before you generate

Generating takes minutes; fetching an existing model takes seconds. `layers/assets` searches Poly Haven, which is a few hundred models, all CC0, no key and no account, and converts what you pick into a single GLB in the same folder:

```bash
python3 layers/assets/src/assets.py search --query "chair wood" --limit 5
python3 layers/assets/src/assets.py fetch --id painted_wooden_chair_01 --out-dir out
```

The site that prompted this, threejsassets.com, has no API at all and forbids automated access in its terms, so it cannot be wired in. Why Poly Haven and what the alternatives cost in licence terms: [`layers/assets/README.md`](layers/assets/README.md).

## Low poly, and making it move

`--target-faces N` sets the quadric simplify target. At res 512 the default is 150K faces; asking for 4000 gives a 3810-triangle, 304 KB GLB in the same run time, textured, because the collapse happens before the UV unwrap and the texture is baked onto the simplified mesh.

```bash
python3 layers/pipeline/src/pipeline.py --prompt "a viking warrior" --target-faces 6000 --out-dir out
python3 layers/rig/src/rig.py --glb out/<asset>.glb --subject humanoid --out-dir out
```

That second command gives a humanoid 19 Mixamo-named bones fitted to the mesh by measuring it, bone-heat skinning weights, and `idle`, `walk`, `run` and `jump` clips in the GLB. It took 1.1 s on the 5957-face warrior above. A prop instead gets no armature at all: `--subject prop` puts TRS clips on the node and `--socket <name>` adds a named attachment empty, which is what a game engine actually wants from a barrel.

Rig after decimation, never before: simplifying a skinned mesh throws the weights away. Why the skeleton is measured rather than predicted by a neural rigger, and why the clips are authored rather than downloaded, is in [`layers/rig/README.md`](layers/rig/README.md).

The result carries `poseWarnings`, because the pose the mesh was reconstructed in is permanent: it becomes the rest pose and every clip plays on top of it, so a figure caught mid-stride limps in all four. Run over the characters already in `out/`, the check found one with its feet 36% of its height apart front to back, two with an arm that never separates from the torso, and bent limbs on most of them. It is a warning rather than an error, because the rig cannot fix a pose and a file plus a note beats a refusal. What the image stage asks for to avoid them is in [`layers/text2image/src/klein.py`](layers/text2image/src/klein.py); a prompt framed as a character also gets four times the atlas texels, since a head is an eighth of a figure and the atlas hands them out by surface area.

## Look at it

```bash
python3 layers/preview/src/serve.py --dir out --open
```

`http://127.0.0.1:8190` has two layouts over one list. **Gallery** is a grid, every GLB in the folder rendered in the browser at 512px. **Single** puts the list down the left and the selected model on a turntable, with the picture it was reconstructed from behind a second tab. Filter by name, walk the list with the arrow keys, drag to orbit, scroll to zoom, flip to wireframe.

Both layouts render through the same studio: a sky dome and three soft boxes baked to an environment map, a three-point rig with a key 45 degrees off the camera so shadows fall where they can be seen, screen-space ambient occlusion, a narrow bloom and Khronos PBR Neutral tone mapping. The occlusion is the part that matters for these assets: TRELLIS bakes none into the atlas, so without it every crevice, eye socket and panel gap is lit exactly as brightly as the surface beside it and the form goes flat. Quality off drops the occlusion and the bloom, for when the same iGPU is busy generating.

Card art is the GLB, not the PNG it came from. Using the source image would have been easy and dishonest: it is what FLUX drew, and a grid of those flatters a reconstruction that may have lost half of it.

three.js is vendored, so the page works with no network and no build step. Every model gets a stable id, so `?id=<id>` deep links one asset and `GET /api/models?id=<id>` resolves it from a script. `?model=<file name>` still works.

## Performance

One 1024x1024 image to a textured GLB at res 512, on the gfx1151 box, same input and seed for both:

| | pinned upstream (Vulkan) | this repo |
| --- | --- | --- |
| engine time | 224.3 s | **191.7 s** |
| triangles | 138524 | 138520 |
| peak GTT | 3612 MiB | 3616 MiB |

All 32.6 s of the difference comes from one place. Profiling the run showed the GLB write phase was 27% of it, more than any single flow stage, and that 62% of the decimation inside it was a single-threaded `unordered_map` building the edge list, costing twice what the GPU kernels did. The CSR adjacency built one line earlier already has the information, so each vertex now emits its own edges in parallel: 1647 ms to 24 ms on the first round. Full method, numbers and the changes that did **not** help are in [`layers/image2mesh/CHANGES.md`](layers/image2mesh/CHANGES.md).

The output GLB passes the Khronos glTF-Validator with 0 errors and 0 warnings (`scripts/validate-glb.mjs`).

## Layout

Seven blackboxes. Each owns a folder, declares a contract, and is changed without reading any other one's source. [`docs/INDEX.md`](docs/INDEX.md) maps "the thing you want to change" to the one folder to open.

| Layer | Owns | Contract |
| --- | --- | --- |
| [`layers/text2image`](layers/text2image) | prompt framing, the ComfyUI graph, the klein weights | [CONTRACT.md](layers/text2image/CONTRACT.md) |
| [`layers/image2mesh`](layers/image2mesh) | the Vulkan engine, the container, GLB validation | [CONTRACT.md](layers/image2mesh/CONTRACT.md) |
| [`layers/pipeline`](layers/pipeline) | stage order, error wrapping | [CONTRACT.md](layers/pipeline/CONTRACT.md) |
| [`layers/rig`](layers/rig) | skeletons, skinning, the clip set, prop sockets | [CONTRACT.md](layers/rig/CONTRACT.md) |
| [`layers/preview`](layers/preview) | the three.js turntable and the server behind it | [CONTRACT.md](layers/preview/CONTRACT.md) |
| [`layers/assets`](layers/assets) | searching and fetching stock CC0 models | [CONTRACT.md](layers/assets/CONTRACT.md) |
| [`layers/mcp`](layers/mcp) | the MCP tool surface over stdio | [CONTRACT.md](layers/mcp/CONTRACT.md) |

Everything crossing a boundary is a schema-validated JSON envelope, and binary payloads cross by reference: path, media type, byte size, sha256. The mesh layer re-hashes the PNG it is handed, so a mismatch fails the run instead of silently reconstructing the wrong picture.

## Tests

```bash
./scripts/test.sh          # all seven layers, 226 tests, plus the skill checks
```

No GPU and no weights needed: the tests stand in only for ComfyUI, the engine binary and Blender, and drive the real CLIs for everything else, including five malformed-GLB shapes that must never leave the mesh layer wearing a success envelope. The one test that needs the GPU is skipped unless `T2M_RUN_GPU=1`, and the rig layer's five real-Blender tests skip themselves with a note when Blender is not installed.

The preview layer's share is 33 HTTP tests against a real server, plus 39 DOM tests (vitest, jsdom, Testing Library, MSW) that drive the interface with real clicks and keystrokes. The DOM half needs `npm install` in `layers/preview` once; without it it is skipped with a note rather than failing.

## Limits

- One object per prompt. TRELLIS.2 reconstructs a single subject; ask for two things and you get one confused thing.
- Faces do not survive close inspection. The same character at 296590 triangles and at 8000 has the same melted face, so this is the reconstruction's ceiling rather than the decimation's: the head is an eighth of the figure and gets the texels its surface area earns. A character now gets four times the atlas by default, which buys back some of it; helmets, hoods and stylised characters are still the way around the rest.
- Glossy subjects come back matte, and it is the texture rather than the renderer. TRELLIS bakes a metallicRoughness map that measures, on the red sports car above, a mean roughness of 0.72 and metalness of 0.33 over a 2048 atlas. Car paint is roughness 0.15 to 0.3 with a clearcoat over it, so the file itself says matte and the viewer is showing you the file. Anything that reads as polished in the reference image will not read as polished in the GLB.
- Nothing checks that the mesh matches the prompt. The pose is checked, the file is validated, the triangles are measured. Whether it is the thing you asked for is still your eyes on the gallery.
- Rigging covers one upright humanoid or one prop. No quadrupeds, no vehicles, no faces or fingers, and no editing an existing mesh.
- No CPU path. `--require-gpu` is always passed, so a missing Vulkan device is an error rather than a twenty-minute fallback.
- Tested on one machine, the gfx1151 box described in [`layers/image2mesh/bench/README.md`](layers/image2mesh/bench/README.md).
