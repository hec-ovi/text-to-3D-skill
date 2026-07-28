# CONTRACT - preview

`contractVersion: 1.1`

## Purpose

Show a generated GLB in a browser, on a turntable, so a human can decide whether it is any good.

## Inputs

| Param | Schema | Preconditions |
| --- | --- | --- |
| `--dir <path>` | no schema: a filesystem path, not a payload | The directory exists and holds `.glb` files. It is read only; this layer never writes into it. |
| `?id=<id>` | no schema: a query parameter | Optional, on the page and on `GET /api/models`. The `id` of a model in `--dir`; a file name is accepted too. On the page an unknown id falls back to the newest model and says so; on the API it is a 404. |
| `?model=<name>` | no schema: a query parameter | Optional, page only. The older spelling of `?id=`, still honoured so existing links keep working. |

Entry point: `python3 src/serve.py --dir ../../out`. Python 3.10+, standard library only. The page itself needs no build step: three.js is vendored under `web/vendor/`, so it runs with no network.

## Outputs

| Param | Schema | Postconditions |
| --- | --- | --- |
| `ModelList` from `GET /api/models` | [`schema/model_list.json`](schema/model_list.json) | One entry per `.glb` in the directory, sorted by `modifiedAt` newest first, except that a `<stem>-rigged.glb` replaces the `<stem>.glb` it was rigged from and names it in `supersedes`. Every entry has an `id`. `triangles` and `materials` are read out of each file, not guessed; a file that does not parse gets `readable: false` and is listed anyway rather than silently dropped. |
| `ModelList` from `GET /api/models?id=<id>` | [`schema/model_list.json`](schema/model_list.json) | The same envelope holding exactly the one matching entry, so a caller that resolved an id parses what it parses for a list. `404 NOT_FOUND` when nothing matches. |
| GLB bytes from `GET /models/<name>` | none: the file verbatim | Served as `model/gltf-binary`, with an `ETag` over mtime and size and `Cache-Control: no-cache`. A reload revalidates and gets a 304; a regenerated asset changes both mtime and size, so a stale file can never win. |
| Image bytes from `GET /images/<name>` | none: the file verbatim | Only `.png`, `.jpg`, `.jpeg` and `.webp` are served; anything else is a `NOT_FOUND`. Same `ETag` handling. |
| The viewer page from `GET /` | none: HTML, CSS and ES modules | Two layouts over one model list, opening on the gallery. The model list is in the sidebar and is present in both, and is the page's only listbox; only the main area swaps. Gallery: a contact sheet of tiles, each showing that GLB rendered in the browser. Single: the selected model on a turntable or its source image behind two tabs. GLBs imported from elsewhere may also expose their existing animation clips. |

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
- An `id` is stable for a given file name and unique within one listing: the file stem folded to `[A-Za-z0-9._-]`, disambiguated with a hash suffix when two names fold together. Two models can never share an id, because a shared id would serve the wrong file.
- Paths that escape `--dir` are refused, checked with `os.path.commonpath`, not by string prefix.
- A model that fails to parse is reported as unreadable and never handed to the loader.
- A rigged asset is the mesh it was rigged from with a skeleton added, so only one of the pair is listed and it is the rigged one. The other is still on disk and still served by name; the entry that replaced it says which file that was.
- The source image is paired by exact stem: `<stem>-r<res>.glb` comes from `<stem>.png`. The engine's own `<stem>-r<res>_base.png` texture atlas is an output, so it never matches and is never shown as the input.
- The triangle count in the footer is the one three.js counted after building the geometry, not the one the server predicted. When they disagree, the renderer wins, because that is what you are looking at. The same rule holds for the clip list: the server's is what the file claims, the renderer's is what can be played.
- The page works offline. Nothing is fetched from a CDN.

## How to modify this blackbox safely

1. `web/ui.js` is the DOM and the state; `web/scene.js` is the WebGL. Keep them apart. `ui.js` importing three.js would make it untestable in jsdom, which is why the tests can drive the whole interface without a GPU.
2. New viewer controls go in the viewer's footer, not the sidebar and not the top bar: the sidebar is the model list and nothing else, and the top bar is the page. Add the element in `ui.js` with a real `<label>` so it is reachable by role and name, add the callback, implement it in `scene.js`. Anything that changes the viewport's size, including switching back from the image tab, must call `onLayoutChange`, or the canvas keeps the aspect ratio it had while it was hidden.
3. New fields in the list: add to `schema/model_list.json` and fill them in `list_models()`. Additive only, minor bump. `animations` and `joints` are read from the GLB's JSON chunk, which costs nothing extra because the file is already open for the triangle count.
4. Upgrading three.js means replacing the files under `web/vendor/three/` and updating `VERSION`. Keep the npm directory layout, because the addons import each other by relative path and the import map depends on it.
5. Tests: `uvx pytest tests/ -q` for the server, `npm install && npm test` for the DOM. Both run without a GPU. `npm install` is only needed for the tests; the page itself never needs it.
