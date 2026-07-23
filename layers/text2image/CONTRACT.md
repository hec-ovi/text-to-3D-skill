# CONTRACT - text2image

`contractVersion: 1.0`

## Purpose

Turn one subject description into a single PNG of that subject, framed so the image-to-mesh stage can reconstruct it.

## Inputs

| Param | Schema | Preconditions |
| --- | --- | --- |
| `ImageRequest` | [`schema/image_request.json`](schema/image_request.json) | `prompt` is non-empty. `endpoint` points at a reachable ComfyUI with FLUX.2 klein weights present under its `models/` mount. `outDir` is writable by this process, and readable by whoever consumes the result. `width`/`height` are multiples of 16. |

Entry point: `python3 src/klein.py --prompt "<subject>" --out-dir <dir>`, or `--request <file|->` to pass the envelope directly. Python 3.10+, standard library only.

## Outputs

| Param | Schema | Postconditions |
| --- | --- | --- |
| `ImageResult` | [`schema/image_result.json`](schema/image_result.json) | `image.uri` exists on disk and its bytes hash to `image.checksum.sha256`. `image.byteSize` matches the file size. `image.width`/`height` are read from the PNG IHDR, not echoed from the request. Printed to stdout as JSON, exit code 0. |

The PNG crosses the boundary **by reference**: path, media type, byte size, sha256. Bytes are never passed inline or streamed.

## Events

None. Rendering is synchronous; progress is not reported.

## Errors

Closed set, [`schema/error.json`](schema/error.json). Written to stderr as JSON, exit code 1.

| Code | Cause |
| --- | --- |
| `INVALID_REQUEST` | Request failed schema validation, or the workflow template lost a node the layer injects into. |
| `BACKEND_UNREACHABLE` | No ComfyUI answering at `endpoint`. |
| `GRAPH_REJECTED` | ComfyUI returned non-200 for `/prompt`, or accepted it without a `prompt_id`. |
| `MODEL_MISSING` | Rejection names a weight file ComfyUI cannot see. |
| `RENDER_FAILED` | The run errored, finished with no image, or returned bytes that are not a PNG. |
| `TIMEOUT` | No image within `timeoutSeconds`. |
| `OUTPUT_WRITE_FAILED` | `outDir` is not writable. |

## Dependencies

- A running ComfyUI that serves `POST /prompt`, `GET /history/{id}`, `GET /view`. On this machine that is `comfyui-strix-docker` (ComfyUI + ROCm 7.13, gfx1151) on `http://127.0.0.1:8188`.
- Weights visible to *that* container, not to this layer: `flux-2-klein-4b.safetensors`, `qwen_3_4b.safetensors`, `flux2-vae.safetensors`.

No dependency on any other layer in this repo.

## Invariants

- Same `prompt` and no explicit `seed` produces the same seed, hence the same image, for a fixed model set. The seed is `sha256(framed prompt)`, top 63 bits.
- The layer never mutates `templates/flux2_klein_t2i.json`; the graph is deep-copied per call.
- `promptSent` in the result is exactly what reached the sampler, framing included.
- Output filenames are content-addressed (`<sha256[:16]>.png`), so two identical renders collapse onto one file.

## How to modify this blackbox safely

1. Changing the sampler, scheduler, step count or model files is internal. Edit `templates/flux2_klein_t2i.json` and, if node ids move, the `N_*` constants at the top of `src/klein.py`. No contract change.
2. Changing the framing text is internal too, but it changes every derived seed. Say so in the commit.
3. Adding an optional input: add it to `schema/image_request.json` with a `default`, bump `contractVersion` minor. Existing callers keep working.
4. Renaming or removing an output field is breaking: add the new shape alongside, migrate `pipeline`, then delete the old one.
5. Run `python3 -m pytest tests/ -q` from this folder. The tests never call a real ComfyUI; they drive the HTTP layer through a stub server.
