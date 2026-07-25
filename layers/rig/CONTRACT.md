# CONTRACT - rig

`contractVersion: 1.1`

## Purpose

Give a generated GLB a skeleton, skinning weights and standard clips, so it arrives in a game or a three.js scene able to move.

## Inputs

| Param | Schema | Preconditions |
| --- | --- | --- |
| `RigRequest` | [`schema/rig_request.json`](schema/rig_request.json) | `glb.uri` exists and, when `checksum` is present, its bytes hash to it. Blender 5.x is on the machine: `blenderPath`, else `$BLENDER`, else `/home/hec/opt/blender-5.2.0-linux-x64/blender`, else `blender` on PATH. `outDir` is writable. For `subject: humanoid` the mesh is one figure standing roughly upright, arms out or down; a curled-up or lying pose measures wrong. |

Entry point: `python3 src/rig.py --glb <file> --subject humanoid --out-dir <dir>`, or `--request <file|->`. Python 3.10+, standard library only. Blender is a subprocess, never an import.

`subject` picks the whole strategy:

- **humanoid**: the mesh is measured, a 19-bone skeleton is fitted to those measurements, bone heat binds the mesh to it, and `idle`, `walk`, `run` and `jump` are authored on the bones.
- **prop**: no armature at all. The node itself gets `spin` or `bob` TRS clips, and `socket` adds a named empty for attachment. A skeletal mesh costs per-frame CPU in every engine, and a barrel does not need one.

## Outputs

| Param | Schema | Postconditions |
| --- | --- | --- |
| `RigResult` | [`schema/rig_result.json`](schema/rig_result.json) | `glb.uri` exists and hashes to `glb.checksum.sha256`. It has been parsed before this envelope was emitted: glTF magic, container version 2, a JSON chunk, a BIN chunk, at least one mesh, and for a humanoid a skin plus `JOINTS_0` on a primitive. `skeleton.joints` and the `animations` list are read back out of the written file, so a clip that failed to author is an error rather than a claim. |

`poseWarnings` is present on every humanoid result and absent for a prop. It reports what is wrong with the pose the *source* mesh was reconstructed in, worst first, each finding carrying a `code` from a closed set, the `measured` number behind it, and a `detail` saying what it does to the rig. An empty list means nothing was found, which is a different answer from the field being missing.

These are observations, never failures. The pose is baked in: the rig binds it as the rest pose and every clip plays on top of it, so a figure caught mid-stride limps forever and no amount of animation gets it back to standing. The layer cannot fix that, and refusing to rig would leave the caller with nothing, so it rigs and says so. The caller is the one who can go back to `text2image` and regenerate.

The GLB crosses the boundary **by reference**: path, media type, byte size, sha256. `RigResult.glb` is shape-compatible with `image2mesh`'s `MeshResult.glb`, so anything that consumes one consumes the other.

## Events

None. Rigging is synchronous. Blender's own output goes to the subprocess pipes and is only surfaced inside an error's `detail`.

## Errors

Closed set, [`schema/error.json`](schema/error.json). Written to stderr as JSON, exit code 1.

| Code | Cause |
| --- | --- |
| `INVALID_REQUEST` | Request failed schema validation, or a clip was asked for that this subject does not have. |
| `GLB_MISSING` | Nothing at `glb.uri`. |
| `CHECKSUM_MISMATCH` | The file on disk is not the file the request describes. |
| `BLENDER_MISSING` | No Blender at `blenderPath`, or none found anywhere. |
| `BLENDER_FAILED` | Blender exited without writing a report, or reported a failure that is not a binding failure, or reported success and wrote no GLB. |
| `RIG_FAILED` | Bone heat could not solve, too few vertices took a weight, the export carries no skin, or a requested clip is not in the file. |
| `TIMEOUT` | Blender did not finish within `timeoutSeconds`. |
| `OUTPUT_WRITE_FAILED` | `outDir` is not writable. |
| `GLB_INVALID` | Blender wrote a file no glTF loader would open. |

## Dependencies

Blender 5.x, called as `blender --background --python src/blender_rig.py`. Nothing else, and no other layer. The input is a GLB; this layer neither knows nor cares that `image2mesh` produced it.

## Invariants

- The skeleton is **measured, not templated**. The mesh is sliced along its up axis and each band is split into connected islands in the horizontal plane, so a limb is followed from band to band wherever the pose put it: a leg swung forward in depth, an arm hanging down at 45 degrees. The crotch is the highest band still holding exactly two islands, the shoulder line is the highest band holding three. A limb that cannot be measured, an arm pressed flat against the body, falls back to the templated placement for that limb alone rather than reverting the whole rig.
- **Every bone's roll is aligned to world forward.** Roll decides what a rotation about a bone's local X does; a leg fitted pointing down and outwards otherwise gets a tilted local frame, and a walk cycle authored as an X swing throws that leg sideways instead of forward. One sagittal plane for the whole skeleton, whatever pose it was measured from.
- **The bind pose is neutralised.** Clips are absolute local rotations applied on top of whatever pose the mesh arrived in, so a mesh captured mid-stride would play its walk on top of a stride. Each bone carries a correction that takes its fitted rest orientation to the canonical one, pre-multiplied into every key, so an identity key is a neutral stance and a walk starts from standing. The correction is clamped per bone (55 degrees on a leg, 70 on an arm): straightening further than that means the fit was wrong, and it would tear the mesh rather than pose it.
- Bones use **Mixamo naming** (`mixamorig:Hips`, `mixamorig:LeftArm`, ...). That is what the free CC0 clip libraries and every retargeting tool already speak, so a caller can drop other clips onto this skeleton with a name map rather than a rewrite.
- Clips are **authored here, not imported**. Every free humanoid clip library is either licence-encumbered for redistribution or authored for a different rest pose, and a retarget needs the same rest pose to look right. Curves are written directly onto bones this layer created.
- One joint set per vertex. three.js reads `JOINTS_0`/`WEIGHTS_0` and drops any further sets, so nothing here emits a second one.
- Every number in the result is read back out of the exported file. The Blender side's own report supplies only what the file cannot say (bone order, the clip table, the vertex count after cleanup).
- A prop never gets an armature, and its `skeleton.naming` is `none`.
- The input GLB is never modified. Output is `<input-stem>-rigged.glb` in `outDir`.

## How to modify this blackbox safely

1. `src/rig.py` is the driver and owns the contract; `src/blender_rig.py` runs inside Blender and owns the geometry. They talk through a job JSON and a report JSON, never through stdout, because Blender prints its own banner there.
2. Adding a clip: add it to `CLIPS` and `clip_poses` in `blender_rig.py`, to `VALID_CLIPS` in `rig.py`, and to the `animations` enum in `schema/rig_request.json`. Bump `contractVersion` minor.
2b. Adding a pose finding: add the check to `pose_report` in `src/fit.py`, the code to the enum in `schema/rig_result.json`, and a case to `tests/test_fit.py`. Nothing in Blender needs to change; `fit.py` has no bpy import and the findings ride out on the report Blender already writes.
3. Changing the skeleton is breaking for anyone retargeting onto it. Add bones at the end, keep the Mixamo names, and do not renumber.
4. The mesh cleanup before binding (merge by distance, drop loose, dissolve degenerate, consistent normals) is what makes bone heat solve at all on a marching-cubes mesh. Removing a step brings back "failed to find solution for one or more bones".
5. Run `uvx pytest tests/ -q` from this folder. The tests that need Blender skip themselves with a note when it is not installed; the rest use a stand-in.

## What this layer does not do

- Faces, fingers, toes, twist bones or IK. Nineteen bones is a locomotion skeleton.
- Fix a mesh whose limbs are fused to its body. Bone heat will spread weight across the bridge and the arm will drag the torso with it. That is a generation problem; regenerate rather than rig it.
- Rig anything that is not one connected upright figure or one prop. Quadrupeds, vehicles and multi-part assemblies measure wrong.
