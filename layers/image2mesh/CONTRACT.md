# CONTRACT - image2mesh

`contractVersion: 1.0`

## Purpose

Reconstruct one image into a textured GLB that a glTF loader can open.

## Inputs

| Param | Schema | Preconditions |
| --- | --- | --- |
| `MeshRequest` | [`schema/mesh_request.json`](schema/mesh_request.json) | `image.uri` exists and its bytes hash to `image.checksum.sha256`; the layer re-hashes and refuses a mismatch. `modelsDir` holds the ten TRELLIS.2 GGUFs. For `runner: docker`, the image named by `dockerImage` is built and `/dev/dri` is present. `outDir` is writable. |

Entry point: `python3 src/mesh.py --image <png> --out-dir <dir>`, or `--request <file|->`. Python 3.10+, standard library only.

## Outputs

| Param | Schema | Postconditions |
| --- | --- | --- |
| `MeshResult` | [`schema/mesh_result.json`](schema/mesh_result.json) | `glb.uri` exists and hashes to `glb.checksum.sha256`. The file has been parsed before this envelope was emitted: glTF magic, container version 2, a header length matching the file, a JSON chunk that parses, a BIN chunk, at least one mesh. `geometry` is read out of that parse, never predicted from the request. `engine.backend` is always `vulkan`. |

The GLB crosses the boundary **by reference**: path, media type, byte size, sha256.

## Events

None. Generation is synchronous and there is no progress channel; the engine's own log goes to stderr.

## Errors

Closed set, [`schema/error.json`](schema/error.json). Written to stderr as JSON, exit code 1.

| Code | Cause |
| --- | --- |
| `INVALID_REQUEST` | Request failed schema validation, or `binaryPath` does not point at a file. |
| `IMAGE_MISSING` | Nothing at `image.uri`. |
| `CHECKSUM_MISMATCH` | The file at `image.uri` is not the file the request describes. |
| `MODELS_MISSING` | No `modelsDir`, or the container found no weights at its mount. |
| `NO_VULKAN_DEVICE` | The container started without a usable Vulkan device. |
| `ENGINE_FAILED` | The engine exited non-zero, or exited clean and wrote no GLB. |
| `ENGINE_UNREACHABLE` | `runner: server` and nothing is listening at `endpoint`. |
| `TIMEOUT` | No result within `timeoutSeconds`. |
| `OUTPUT_WRITE_FAILED` | `outDir` is not writable. |
| `GLB_INVALID` | A file was produced that a glTF loader would reject. |

## Dependencies

- Docker with `/dev/dri` passed through, or a locally built `t2m-cli`, or a running `t2m-server`.
- TRELLIS.2 GGUF weights on disk (`scripts/fetch-models.sh`).

No dependency on any other layer. `MeshRequest.image` is shape-compatible with `text2image`'s `ImageResult.image` by design, but this layer never imports that one and does not care where the PNG came from.

## Invariants

- Vulkan or nothing. `--require-gpu` is always passed, so the engine refuses to fall back to CPU; there is no configuration of this layer that quietly runs on the processor for twenty minutes.
- Same image bytes, same seed, same resolution produces the same GLB.
- Output path is derived from the input: `<image-stem>-r<resolution>.glb`.
- No envelope is emitted for a GLB that failed structural validation.
- The layer never writes inside `modelsDir`, which is mounted read-only into the container.

## How to modify this blackbox safely

1. Engine source lives in `engine/`. It is Vulkan-only on purpose: see [`engine/PROVENANCE.md`](engine/PROVENANCE.md) for what was removed and why, and [`CHANGES.md`](CHANGES.md) for what was changed on top, with the measurement behind each change.
2. Rebuild after touching `engine/`: `docker build -f docker/Dockerfile -t text-to-3d/engine:vulkan .`
3. Adding a knob: add it to `schema/mesh_request.json` with a `default`, map it in `engine_flags()`, bump `contractVersion` minor.
4. Changing what `geometry` reports is breaking. Add fields alongside; do not repurpose existing ones.
5. Run `uvx pytest tests/ -q` from this folder. Tests that need a GPU and 20 GB of weights are marked `slow` and skipped unless `T2M_RUN_GPU=1`.
