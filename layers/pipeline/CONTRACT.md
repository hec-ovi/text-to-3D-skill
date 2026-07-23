# CONTRACT - pipeline

`contractVersion: 1.0`

## Purpose

Turn a sentence into a GLB by running text2image then image2mesh.

## Inputs

| Param | Schema | Preconditions |
| --- | --- | --- |
| `TextToMeshRequest` | [`schema/text_to_mesh_request.json`](schema/text_to_mesh_request.json) | `prompt` is non-empty. Both downstream layers' preconditions hold: a reachable ComfyUI at `comfyEndpoint`, and for `runner: docker` a built engine image with `/dev/dri` available. `enginePath` is set when `runner` is `binary`. |

Entry point: `python3 src/pipeline.py --prompt "<subject>" --out-dir <dir>`, or `--request <file|->`. `--glb-path-only` prints the path alone, for shell callers.

## Outputs

| Param | Schema | Postconditions |
| --- | --- | --- |
| `TextToMeshResult` | [`schema/text_to_mesh_result.json`](schema/text_to_mesh_result.json) | `glb` is the `image2mesh` reference verbatim, so the file exists and has already passed that layer's structural validation. `stages` carries both stage envelopes unmodified, so a caller can see the intermediate image, the seed, and the per-stage timings without re-running anything. |

## Events

None.

## Errors

Closed set, [`schema/error.json`](schema/error.json). Written to stderr as JSON, exit code 1.

| Code | Cause |
| --- | --- |
| `INVALID_REQUEST` | Request failed schema validation. |
| `TEXT2IMAGE_FAILED` | Stage one exited non-zero. Its own error envelope is attached as `cause`. |
| `IMAGE2MESH_FAILED` | Stage two exited non-zero. Its own error envelope is attached as `cause`. |
| `CONTRACT_VIOLATION` | A stage exited 0 but returned something that is not its declared envelope. |
| `TIMEOUT` | A stage did not finish within `timeoutSeconds`. |

A stage's error is wrapped, never rethrown raw: the code says which stage, `cause` carries that layer's own closed-set code.

## Dependencies

- [`text2image`](../text2image/CONTRACT.md), invoked as a subprocess.
- [`image2mesh`](../image2mesh/CONTRACT.md), invoked as a subprocess.

Both are called through their CLIs with an envelope on stdin. This layer imports nothing from either, and reads no file inside either one's `src/`.

## Invariants

- The image reference handed to stage two is the one stage one emitted, field for field. Nothing is re-hashed or re-pathed in between, so drift surfaces as `CHECKSUM_MISMATCH` inside `image2mesh` instead of being smoothed over here.
- Same prompt, same seed, same settings produces the same GLB.
- The mesh seed is derived from the image seed, so an explicit `seed` fixes both stages.
- On success both the PNG and the GLB exist, unless `keepImage` is false, which deletes the PNG after the GLB is written.

## How to modify this blackbox safely

1. New knobs are threaded, not invented: add the field here, map it into the stage request, and let the downstream schema validate it. If a downstream layer does not accept it, that is a downstream contract change first.
2. Never import from a sibling layer. If you need something from one, it belongs in that layer's output envelope.
3. Adding a third stage means a new layer folder with its own contract, plus a new `stages` entry (additive, minor bump).
4. Run `uvx pytest tests/ -q` from this folder. The tests run the real CLIs of all three layers; only ComfyUI and the GPU engine are stood in for.
