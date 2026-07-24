# preview

A turntable for the GLBs the pipeline produces. The contract is [`CONTRACT.md`](CONTRACT.md); this is the working notes.

```bash
python3 src/serve.py --dir ../../out --open
```

Then pick a model from the dropdown. Drag to orbit, scroll to zoom, and the auto rotate checkbox with its speed slider drive the spin. Wireframe shows the mesh, Reset view puts the camera back where the framing put it.

The panel on the right shows the image the mesh was reconstructed from, so you can tell a bad reconstruction from a bad prompt without leaving the page. It is paired by name (`subject.png` to `subject-r512.glb`), which is exactly how `image2mesh` names its output. Untick Source image to give the viewport the full width.

## What lives where

| | |
| --- | --- |
| `src/serve.py` | stdlib HTTP: the page, `/api/models`, and the GLB bytes |
| `web/ui.js` | the controls, the model list, the URL sync. No three.js import |
| `web/scene.js` | the WebGL turntable: lighting, framing, loading, rotation |
| `web/vendor/three/` | three.js 0.185.1, vendored so the page works offline |
| `tests/test_serve.py` | the server over real HTTP |
| `tests/ui.test.js` | the controls in jsdom, driven by user-event, HTTP faked by MSW |

## Things that will bite you

- **Keep `ui.js` free of three.js.** The split is what lets the interface be tested without a GPU. Importing `three` there would drag WebGL into jsdom and the suite would die.
- The import map in `index.html` maps `three` and `three/addons/` at the exact npm layout. The addons import each other by relative path, so flattening `web/vendor/three/` breaks `GLTFLoader`.
- TRELLIS writes real metallic and roughness. Without an environment map, metal renders black; `RoomEnvironment` is generated in code, which is why the page still needs no downloaded asset.
- Models arrive in wildly different scales. `frame()` normalises the longest axis to 1.4 units and sits the model on the grid, so do not add a fixed camera distance.
- Hiding the source panel changes the viewport width, so the toggle has to tell the renderer to resize. Forget that and the canvas keeps a stale aspect ratio and the model looks stretched.
- `Cache-Control: no-store` is deliberate. Regenerating an asset and hitting reload should show the new mesh, and a cached 200 makes the engine look broken.
- A WebGL canvas cannot be read back after the frame is presented unless `preserveDrawingBuffer` is on, which costs every frame. `viewer.getState()` reports camera angle and flags instead; `window.__t2m` exposes it for the console.
