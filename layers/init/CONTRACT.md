# CONTRACT - init

`contractVersion: 1.0`

## Purpose

Start and verify every service required to turn text into a static GLB and preview it.

## Inputs

| Param | Schema | Preconditions |
| --- | --- | --- |
| `InitRequest` | [`schema/init_request.json`](schema/init_request.json) | `toolkitDir` is this repository, `comfyDir` contains the ComfyUI Compose stack, Docker can reach the GPU devices, and the configured paths are writable. Missing TRELLIS weights may be fetched when `fetchModels` is true. |

Entry point: `python3 src/init.py`, CLI flags, or `--request <file|->`. Python 3.10+, standard library only.

## Outputs

| Param | Schema | Postconditions |
| --- | --- | --- |
| `InitResult` | [`schema/init_result.json`](schema/init_result.json) | ComfyUI and the resident Vulkan engine answered their health endpoints. The preview service also answered when requested. `paths` contains absolute directories. |

## Events

None. Progress is written to stderr so stdout remains one JSON result.

## Errors

Closed set, [`schema/error.json`](schema/error.json). Written to stderr as JSON, exit code 1.

| Code | Cause |
| --- | --- |
| `INVALID_REQUEST` | The request failed schema validation. |
| `TOOLKIT_MISSING` | The toolkit path lacks its Compose file or service entry points. |
| `COMFYUI_MISSING` | The ComfyUI path lacks its Compose file. |
| `DEPENDENCY_MISSING` | Docker, Python, or a required launcher is unavailable. |
| `MODELS_MISSING` | One or more TRELLIS GGUF files remain absent after the optional fetch. |
| `DOWNLOAD_FAILED` | The model fetcher exited unsuccessfully. |
| `START_FAILED` | Docker Compose could not start ComfyUI or the engine. |
| `SERVICE_TIMEOUT` | A required health endpoint did not answer before the deadline. |
| `OUTPUT_WRITE_FAILED` | The output or runtime directory could not be created. |
| `PREVIEW_FAILED` | The preview process did not start or become reachable. |

## Dependencies

- The sibling ComfyUI Compose project, treated only as a path and a health endpoint.
- The root [`docker-compose.yml`](../../docker-compose.yml), which starts the resident image2mesh engine.
- The [`preview`](../preview/CONTRACT.md) entry point, started as a subprocess.
- [`scripts/fetch-models.sh`](../../scripts/fetch-models.sh), used only when weights are missing.

The layer imports no sibling source.

## Invariants

- A success result means every reported service answered over HTTP during this run.
- Re-running init is safe. Compose converges existing services, complete model files are not downloaded again, and a reachable preview server is reused.
- No Blender image, binary, layer, or process is inspected or started.
- stdout contains only the validated result envelope.

## How to modify this blackbox safely

1. Add any new service to both result schemas and the end-to-end CLI test.
2. Keep service processes behind health checks. A successful start command is not proof that a service is ready.
3. Keep external commands overridable through the existing `T2M_*` environment variables so contract tests can exercise the real entry point without a GPU.
4. Run `uvx pytest tests/ -q` from this folder.
