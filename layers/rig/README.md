# rig

Static GLB in, skinned and animated GLB out. The contract is [`CONTRACT.md`](CONTRACT.md); this is the working notes.

```bash
docker compose up -d rig
python3 src/rig.py --glb ../../out/warrior-r1024.glb --out-dir ../../out
uvx pytest tests/ -q
```

## No Blender, and what that cost

The previous attempt at this was Blender headless: a Rigify metarig, bone-heat weights, and CC0 clips retargeted onto them. It produced characters that walked backwards and limbs that collapsed, and the reason is retargeting. Mapping bone to bone between two skeletons with different rest poses and different axis conventions is where the sign errors live, and a sign error in a hip rotation is a walk cycle in reverse.

This path removes the step rather than fixing it. SkinTokens predicts the skeleton, `src/skeleton.py` gives those joints Mixamo's names by reading the shape of the tree, and the clips are solved against that skeleton directly. There is no mapping, so there is nothing to map wrong.

Blender is gone from the runtime entirely. SkinTokens lists `bpy` in its requirements and uses it as a mesh reader behind an abstract parser; the container feeds the model through the `npz` loader it already supports, filled from the GLB itself.

## Running on AMD

SkinTokens says NVIDIA, CUDA 12.1+, flash-attn, 14 GB. The code says otherwise: no `.cu` files, no compiled extension, no spconv, and nothing CUDA-only in `requirements.txt`. Two things actually block it, and `docker/` handles both without editing their source:

| Blocker | Fix |
| --- | --- |
| Four modules import `flash_attn_interface` with no SDPA branch | `docker/shim/` supplies that module, backed by torch SDPA. Their own `attention_processor.py` already carries the same implementation as a fallback. |
| `tokenrig.py` hardcodes `attn_implementation="flash_attention_2"` | `docker/server.py` rewrites that one argument to `"sdpa"` before the model is built. |

Neither changes what is computed. Measured on the Radeon 8060S: the service loads in 15.1 s, then rigs an 11,168-vertex character into 34 joints in 13.0 s. An earlier run measured 31.9 s while a language model was resident on the same iGPU.

## What lives where

| | |
| --- | --- |
| `src/rig.py` | the driver: read the GLB, ask the model, name, skin, animate, write |
| `src/skeleton.py` | naming a predicted tree into Mixamo bones, and the weight budget |
| `src/skin.py` | the glTF mutation: nodes, skin, inverse binds, JOINTS/WEIGHTS |
| `src/clips.py` | generated idle and walk |
| `src/gltf.py` | enough glTF to append to a GLB without rebuilding it |
| `docker/` | the ROCm image, the shim, and the resident server |
| `fixtures/skeleton-viking.json` | what the model actually predicted for a generated viking |

## Things that will bite you

- **The model must see the GLB's own vertex array.** Load the mesh with anything that welds or reorders vertices and the weights come back describing a different mesh, which looks like a rig that is subtly, unfixably wrong rather than an error.
- glTF's first `JOINTS_0`/`WEIGHTS_0` pair holds four influences and most engines read only that set. The model returns up to nine per vertex and rows that sum to about 0.95, so both the cut and the rescale are required. Skip the rescale and the mesh shrinks toward the origin as soon as anything moves.
- A skinned mesh's node transform is ignored by the spec: vertices are taken to be in skin space. A leftover transform on that node makes the character jump the moment it is bound, so the skinned node is lifted to the scene root and stripped.
- **Never author a full revolution as quaternion keys.** 360 degrees ends on the negated quaternion, which is the same orientation with every component flipped, and LINEAR interpolation blends the components straight through zero. There was a turn clip; this is why there is not.
- A clip that does not start and end on the same pose stutters once per loop. There is a test.
- The skeleton is only named when the tree reads as a humanoid. A chair gets `NOT_A_CHARACTER`, because naming it `Hips` produces a rig that a walk cycle will then act on.
- This container needs `/dev/kfd` and privileged, because it is ROCm. The mesh engine deliberately has neither. They are separate services for that reason among others.
