# preview

The viewer for everything this repo produces. The contract is [`CONTRACT.md`](CONTRACT.md); this is the working notes.

```bash
python3 src/serve.py --dir ../../out --open
```

The left column lists every GLB in the folder, newest first, each card carrying the image the mesh was reconstructed from, its triangle count, its size and its age. Filter by name, or walk the list with the arrow keys. The main area has two tabs: the turntable, and the source image at full size, because seeing both is the only way to tell a bad reconstruction from a bad prompt.

A rigged asset gets a Motion panel under the list with one entry per clip in the file, and a pause button. Picking a clip cross-fades to it over a quarter of a second.

Every model has a stable id, so `?id=hero-r512` deep links one, and `GET /api/models?id=hero-r512` resolves the same thing from a script.

## What lives where

| | |
| --- | --- |
| `src/serve.py` | stdlib HTTP: the page, `/api/models`, the GLB and image bytes |
| `web/ui.js` | the list, the tabs, the motion panel, the URL sync. No three.js import |
| `web/scene.js` | the WebGL turntable: lighting, shadow, framing, loading, the mixer |
| `web/vendor/three/` | three.js 0.185.1, vendored so the page works offline |
| `tests/test_serve.py` | the server over real HTTP |
| `tests/ui.test.js` | the interface in jsdom, driven by user-event, HTTP faked by MSW |

## Things that will bite you

- **Keep `ui.js` free of three.js.** The split is what lets the interface be tested without a GPU. Importing `three` there would drag WebGL into jsdom and the suite would die.
- The import map in `index.html` maps `three` and `three/addons/` at the exact npm layout. The addons import each other by relative path, so flattening `web/vendor/three/` breaks `GLTFLoader`.
- TRELLIS writes real metallic and roughness. Without an environment map, metal renders black; `RoomEnvironment` is generated in code, which is why the page still needs no downloaded asset.
- Models arrive in wildly different scales. `frame()` normalises the longest axis to 1.4 units and sits the model on the floor, so do not add a fixed camera distance. The shadow camera is sized there too, or a tall model's shadow gets clipped.
- A skinned mesh keeps its bind-pose bounding box, so it culls out of view mid-clip unless `frustumCulled` is off. That one is invisible until a character walks and vanishes.
- Switching to the image tab hides the canvas, and a hidden element has no size. Coming back has to tell the renderer to resize, or the model arrives stretched.
- The clip list the server sends is what the file claims; the list the renderer parsed replaces it on load, because the renderer is the thing that will actually play them.
