# CONTRACT - comfy

`contractVersion: 1.0`

## Purpose

Expose the resident Vulkan mesh engine to a ComfyUI graph as one node, so a single graph runs prompt to GLB.

## Inputs

| Param | Schema | Preconditions |
| --- | --- | --- |
| `MeshNodeRequest` | [`schema/mesh_node_request.json`](schema/mesh_node_request.json) | `image` is a PNG carried inline as base64, small enough to be worth inlining (a render, not a texture atlas). A `t2m-server` answers `GET /health` at `endpoint`. `outDir` is writable by this process and readable by whoever opens the GLB. |

Entry points:

- `python3 src/client.py --image <png> --out-dir <dir>`, or `--request <file|->`, for a shell caller and for the contract tests.
- `NODE_CLASS_MAPPINGS["TextTo3DMesh"]`, discovered by ComfyUI when this folder is mounted at `custom_nodes/text_to_3d`.

The node takes ComfyUI's `IMAGE` tensor and returns the GLB path as a `STRING`. Converting the tensor to PNG bytes is the only part that touches torch or numpy, and it happens in `src/node.py`; `src/client.py` is stdlib and never imports either.

## Outputs

| Param | Schema | Postconditions |
| --- | --- | --- |
| `MeshNodeResult` | [`schema/mesh_node_result.json`](schema/mesh_node_result.json) | `glb.uri` exists and hashes to `glb.checksum.sha256`. The file was parsed before this envelope was emitted: glTF magic, container version 2, a header length matching the file, a JSON chunk that parses, and at least one mesh. `triangles` is read out of that parse. |

The GLB crosses the boundary **by reference**: path, media type, byte size, sha256. The PNG crosses **inline** as base64, because a ComfyUI node is handed pixels in memory and has nowhere on the shared filesystem to put them that the graph agreed on.

## Events

None. The call blocks for the length of one reconstruction, minutes rather than seconds.

## Errors

Closed set, [`schema/error.json`](schema/error.json). Written to stderr as JSON with exit code 1 from the CLI, and raised as `RuntimeError` with the envelope as its message from the node, because that is what ComfyUI puts in front of a user.

| Code | Cause |
| --- | --- |
| `INVALID_REQUEST` | Request failed schema validation, or the inline image is not a PNG. |
| `ENGINE_UNREACHABLE` | Nothing answering at `endpoint`. |
| `ENGINE_FAILED` | The engine returned a non-2xx status, or returned no bytes. |
| `TIMEOUT` | No result within `timeoutSeconds`. |
| `OUTPUT_WRITE_FAILED` | `outDir` is not writable. |
| `GLB_INVALID` | Bytes came back that a glTF loader would reject. |

## Dependencies

- A running `t2m-server`, reached over HTTP at `endpoint` and treated as nothing but that URL.

No dependency on any other layer in this repo. This layer never imports `image2mesh`, and deliberately carries its own copy of the multipart POST and the GLB reader: those are the two things that would otherwise couple a ComfyUI process to a driver it has no reason to load.

## Invariants

- The engine is called over HTTP and never as a subprocess. Model load is the fixed cost a resident server exists to pay once, and a per-node `t2m-cli` would pay it for every graph run.
- The layer writes only inside `outDir`.
- No envelope is emitted for a GLB that failed structural validation.
- The node's own imports of torch and numpy are lazy, so the CLI and the tests run on a machine with neither.

## How to modify this blackbox safely

1. Adding a knob: add it to `schema/mesh_node_request.json` with a `default`, map it into the multipart fields, add the widget in `src/node.py`, bump `contractVersion` minor.
2. Keep `src/client.py` stdlib. The moment it imports torch, the contract tests need a GPU image to run.
3. The workflow template in `workflows/` is a fixture, not an interface. A graph saved from a newer ComfyUI is fine to replace it with, as long as the node ids the README quotes are updated with it.
4. Run `uvx pytest tests/ -q` from this folder. The tests drive the real CLI against a stub engine over real HTTP; no GPU and no weights.
