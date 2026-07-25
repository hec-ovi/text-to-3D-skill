# rig

> **Experimental.** Everything here runs and the export is a valid rigged glTF, but a generated character does not animate cleanly yet. The two reasons are baked lighting, which cannot follow a moving limb, and clothed figures whose arms never separate from the torso in cross-section so their bones come from the template. Both are upstream of Blender. See "The pose is the whole game" below and the notes in the root README.

A skeleton, skinning weights and standard clips for a generated GLB. The contract is [`CONTRACT.md`](CONTRACT.md); this is the working notes.

```bash
python3 src/rig.py --glb ../../out/hero-r512.glb --subject humanoid --out-dir ../../out
python3 src/rig.py --glb ../../out/barrel-r512.glb --subject prop --socket socket_top
```

A humanoid comes back with 19 Mixamo-named bones, every vertex weighted, and `idle`, `walk`, `run` and `jump` in the file. A prop comes back with no armature at all: TRS clips on its own node, and a named empty where you want to attach something.

Measured on this box: the low-poly warrior (5957 faces) took **1.1 s** end to end, Blender included.

## Why not a neural auto-rigger

Because none of them run here. UniRig, SkinTokens, Puppeteer, MagicArticulate, Anymate and RigAnything are all CUDA-first: spconv, flash-attn, torch-scatter, no ONNX or Vulkan path between them. This box is a Strix Halo iGPU whose 3D half deliberately runs on Vulkan with no ROCm. Blender's bone heat is CPU geometry work, deterministic, needs no model download, and finishes in under a second on a decimated mesh.

The trade is joint placement. A neural rigger infers where a shoulder is; this layer measures where the mesh is widest and calls that the shoulder line. That is why the mesh is sliced instead of matched against a fixed template: the crotch is the lowest band that fuses from two islands into one, the feet are the two islands nearest the floor, and the shoulders are the widest band in the upper half. A stocky dwarf and a tall elf come out of the same code with different bones.

## Why the clips are authored, not downloaded

Mixamo has no API, its auth has been broken since June 2025, and its licence forbids redistributing clips as standalone files. The CC0 libraries (Quaternius, Kenney) are authored for their own skeletons, and retargeting only looks right when the rest pose matches. Since this layer builds the skeleton, writing sine curves onto bones it owns is both shorter and more reliable than importing and remapping someone else's.

The phase relationship worth knowing: a negative X rotation swings a limb forward on this skeleton, so an arm opposing its own leg takes the same phase with the opposite sign. A half-cycle offset looks right on paper and cancels against the leg's negative amplitude, which marches the character with its arm and leg swinging together. That bug was found by printing the world position of the hand and foot bones across four frames, which is also the fastest way to check any future clip.

## What lives where

| | |
| --- | --- |
| `src/rig.py` | the driver: schema, checksum, Blender subprocess, GLB read-back |
| `src/blender_rig.py` | runs inside Blender: measure, fit, bind, author clips, export |
| `fixtures/humanoid.glb` | a blocky figure, six boxes, for the tests |
| `fixtures/humanoid-rigged-idle.glb` | what a rigged file looks like, for the stand-in Blender |
| `tests/test_rig.py` | the CLI against a stand-in, plus the real Blender when it is installed |

## Blender without installing Blender

```bash
docker build -f docker/Dockerfile -t text-to-3d/blender:5.2 .
python3 src/rig.py --glb ../../out/hero-r1024.glb --subject humanoid --runner docker
```

Blender was the last thing in this repo that had to be on the host by hand: 400 MB of binary that cannot be a Python import and will not fit in a standard library. It is an image now, the same as the mesh engine, and `--runner auto` (the default) uses a host binary if there is one and the container if there is not. Measured on a generated character: 29.4 s end to end through the container, 19 joints, four clips.

No GPU in that image and no `/dev/dri`. Bone heat is a CPU solver and so is the export, which is the whole reason this one can be a plain Ubuntu base with Blender untarred into it.

## The pose is the whole game

Whatever pose the mesh arrives in becomes the rest pose. Bone heat binds it, every clip plays on top of it, and there is no undoing it downstream: a figure reconstructed mid-stride walks with a limp in all four clips. That is why `text2image` spends a paragraph asking for an A-pose, and why `pose_report` in `src/fit.py` checks whether it got one.

Run over the characters this repo had already generated, it found: one figure with its feet 36% of its height apart front to back, two with an arm that never separates from the torso in cross-section, one with no measurable shoulder line under its hair, and bent limbs on most of them. Every clause added to the character framing answers one of those. The findings ride out on `RigResult.poseWarnings` rather than failing the run, because a rig on a bad pose is still a rig and the caller is the one who can regenerate.

## Things that will bite you

- **Bone heat fails loudly on a dirty mesh.** "Bone Heat Weighting: failed to find solution for one or more bones" means duplicate vertices, degenerate faces or an island no bone can see. The cleanup pass before binding (merge by distance, delete loose, dissolve degenerate, consistent normals) is not optional on a marching-cubes mesh.
- **Fused limbs are unfixable here.** If the generation welded an arm to the torso, heat diffuses across the bridge and raising the arm drags the body. Regenerate; do not rig it.
- **Do not print JSON on stdout from the Blender side.** Blender writes its own banner and add-on chatter there. The report goes to a file the driver named.
- Blender is Z-up and glTF is Y-up. The measuring code converts to Y-up, the bone builder converts back, and both conversions are one line each. Getting one of them wrong lays the skeleton on its side.
- Decimate before rigging, not after. Simplifying a skinned mesh drops weights, and bone heat on a 150k-triangle mesh is slower for no benefit.
