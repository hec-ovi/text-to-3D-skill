# CONTRACT - rig

`contractVersion: 1.0`

## Purpose

Turn one static humanoid GLB into a skinned one with a Mixamo-named skeleton and clips that play.

## Inputs

| Param | Schema | Preconditions |
| --- | --- | --- |
| `RigRequest` | [`schema/rig_request.json`](schema/rig_request.json) | `glb.uri` exists and parses as a GLB with at least one `POSITION` attribute. When `glb.checksum` is present the file must hash to it. A rig server answers `GET /health` at `endpoint`. `outDir` is writable. |

Entry point: `python3 src/rig.py --glb <file> --out-dir <dir>`, or `--request <file|->`. Python 3.10+, standard library only: no torch, no numpy, no Blender.

## Outputs

| Param | Schema | Postconditions |
| --- | --- | --- |
| `RigResult` | [`schema/rig_result.json`](schema/rig_result.json) | `glb.uri` exists and hashes to `glb.checksum.sha256`. The file was parsed back from its own bytes and carries a skin before this envelope was emitted. `skeleton.names` are Mixamo's, so a clip authored against that skeleton plays with no retargeting. `animations` lists the clips actually written. |

The GLB crosses **by reference** in both directions. The mesh itself crosses to the model **inline**, as base64 float32 arrays, because the model needs the vertex array in the GLB's own order and a path would let something else reorder it.

## Events

None. One call blocks for the length of one prediction, roughly thirty seconds on this hardware.

## Errors

Closed set, [`schema/error.json`](schema/error.json). Written to stderr as JSON, exit code 1.

| Code | Cause |
| --- | --- |
| `INVALID_REQUEST` | Request failed schema validation. |
| `GLB_MISSING` | Nothing at `glb.uri`. |
| `CHECKSUM_MISMATCH` | The file on disk is not the one the request describes. |
| `GLB_INVALID` | The input does not parse, or the rigged output failed to parse back. |
| `NOT_A_CHARACTER` | The predicted skeleton is not a humanoid, so it cannot be named or animated. |
| `MODEL_UNREACHABLE` | Nothing answering at `endpoint`. |
| `MODEL_FAILED` | The rig server errored, or returned weights for a different vertex count. |
| `TIMEOUT` | No rig within `timeoutSeconds`. |
| `OUTPUT_WRITE_FAILED` | `outDir` is not writable. |

## Dependencies

- A running `t2m-rig` server, reached over HTTP and treated as nothing but that URL. [`docker/`](docker/Dockerfile) builds it.
- SkinTokens checkpoints on disk, about 1.6 GiB, mounted read-only into that container.

No dependency on any other layer. `RigRequest.glb` is shape-compatible with `image2mesh`'s `MeshResult.glb` on purpose, but this layer never imports that one and does not care what produced the mesh.

## Invariants

- **The input mesh is never rebuilt.** Skinning appends buffer views, accessors and nodes; no existing accessor, material or texture is rewritten. What comes out is the same mesh, bound to bones.
- The model sees the GLB's own vertex array, in file order, so the weights it returns map one to one onto the primitive they came from. Nothing re-indexes or welds in between.
- Bone names are Mixamo's, derived from the shape of the predicted tree rather than from joint order, because order is only stable until the model predicts one fewer finger.
- Weights are cut to at most four influences per vertex and rescaled to sum to one. The model returns neither.
- A non-humanoid is refused rather than named. A chair with `Hips` is a rig a walk cycle would then act on.
- No Blender, and no flash-attn. The two places SkinTokens requires the latter are handled in the container by a shim and a one-argument patch, without editing their source.
- No envelope is emitted for a GLB that failed to parse back.

## How to modify this blackbox safely

1. Clips live in `src/clips.py` and are generated, not shipped: Mixamo's own clips cannot be redistributed, and a CC0 pack authored on another skeleton would need retargeting, which is the step that breaks walks. A clip must start and end on the same pose or it stutters once a loop; there is a test for that.
2. Never write a full revolution as quaternion keys. 360 degrees ends on the negated quaternion and glTF's LINEAR interpolation drives it through zero. That is why there is no turn clip.
3. Naming changes go in `src/skeleton.py` and must keep `fixtures/skeleton-viking.json` passing. That fixture is what the model really predicted, not a shape written to make a test pass.
4. `src/` stays stdlib. The model lives in the container; the moment the driver imports torch it stops running everywhere else in the toolkit.
5. Run `uvx pytest tests/ -q` from this folder. The tests drive the real CLI against a stub server; no GPU and no checkpoints.
