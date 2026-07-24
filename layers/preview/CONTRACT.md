# CONTRACT - preview

`contractVersion: 1.0`

## Purpose

Show a generated GLB in a browser, on a turntable, so a human can decide whether it is any good.

## Inputs

| Param | Schema | Preconditions |
| --- | --- | --- |
| `--dir <path>` | no schema: a filesystem path, not a payload | The directory exists and holds `.glb` files. It is read only; this layer never writes into it. |
| `?model=<name>` | no schema: a query parameter | Optional. Names a file in `--dir`. An unknown name falls back to the newest model and says so. |

Entry point: `python3 src/serve.py --dir ../../out`. Python 3.10+, standard library only. The page itself needs no build step: three.js is vendored under `web/vendor/`, so it runs with no network.

## Outputs

| Param | Schema | Postconditions |
| --- | --- | --- |
| `ModelList` from `GET /api/models` | [`schema/model_list.json`](schema/model_list.json) | One entry per `.glb` in the directory, sorted by `modifiedAt` newest first. `triangles` and `materials` are read out of each file, not guessed; a file that does not parse gets `readable: false` and is listed anyway rather than silently dropped. |
| GLB bytes from `GET /models/<name>` | none: the file verbatim | Served as `model/gltf-binary` with `Cache-Control: no-store`, so regenerating an asset and reloading shows the new mesh. |
| Image bytes from `GET /images/<name>` | none: the file verbatim | Only `.png`, `.jpg`, `.jpeg` and `.webp` are served; anything else is a `NOT_FOUND`. |
| The viewer page from `GET /` | none: HTML, CSS and ES modules | Loads the selected model, frames it, spins it. |

## Events

None. The page polls once at load and again on `visibilitychange`, plus whenever Refresh is pressed.

## Errors

Closed set, [`schema/error.json`](schema/error.json), returned as JSON with a matching HTTP status.

| Code | Status | Cause |
| --- | --- | --- |
| `DIR_MISSING` | 404 | `--dir` does not exist. |
| `NOT_FOUND` | 404 | No such model or asset. |
| `FORBIDDEN` | 403 | The requested path escapes the served directory. |
| `PORT_IN_USE` | exit 1 | The port could not be bound. Printed to stderr, not served. |

## Dependencies

None on any other layer. It reads a directory of GLBs; it neither knows nor cares that `image2mesh` produced them. That is the whole reason it can be pointed at any folder.

Vendored: three.js `0.185.1` under `web/vendor/three/` (MIT, version recorded in `web/vendor/three/VERSION`), specifically the core build plus `GLTFLoader`, `OrbitControls` and `RoomEnvironment`.

## Invariants

- The served directory is never written to.
- Paths that escape `--dir` are refused, checked with `os.path.commonpath`, not by string prefix.
- A model that fails to parse is reported as unreadable and never handed to the loader.
- The source image is paired by exact stem: `<stem>-r<res>.glb` comes from `<stem>.png`. The engine's own `<stem>-r<res>_base.png` texture atlas is an output, so it never matches and is never shown as the input.
- The triangle count in the footer is the one three.js counted after building the geometry, not the one the server predicted. When they disagree, the renderer wins, because that is what you are looking at.
- The page works offline. Nothing is fetched from a CDN.

## How to modify this blackbox safely

1. `web/ui.js` is the DOM and the state; `web/scene.js` is the WebGL. Keep them apart. `ui.js` importing three.js would make it untestable in jsdom, which is why the tests can drive the whole interface without a GPU.
2. New viewer controls: add the element in `ui.js` with a real `<label>` so it is reachable by role and name, add the callback, implement it in `scene.js`. Anything that changes the viewport's width must call `onLayoutChange`, or the canvas keeps the old aspect ratio.
3. New fields in the list: add to `schema/model_list.json` and fill them in `list_models()`. Additive only, minor bump.
4. Upgrading three.js means replacing the files under `web/vendor/three/` and updating `VERSION`. Keep the npm directory layout, because the addons import each other by relative path and the import map depends on it.
5. Tests: `uvx pytest tests/ -q` for the server, `npm install && npm test` for the DOM. Both run without a GPU. `npm install` is only needed for the tests; the page itself never needs it.
