# preview

The viewer for everything this repo produces. The contract is [`CONTRACT.md`](CONTRACT.md); this is the working notes.

```bash
python3 src/serve.py --dir ../../out --open
```

The model list is down the left and stays there. Only the middle pane changes. The page opens on **Gallery**, a contact sheet where every card is that GLB actually rendered, at 512px, by the same studio the turntable uses. **Single** swaps the sheet for the turntable, with the source image behind a second tab, because seeing both is the only way to tell a bad reconstruction from a bad prompt. Picking a model anywhere opens it on the turntable, and the wordmark in the top bar goes back to the sheet. Cards carry the triangle count, the size and the age either way; filter by name, or walk the list with the arrow keys.

The top bar names the file on the turntable, the bottom bar carries its numbers and whatever the loader last said. Render controls (auto rotate and its speed, wireframe, grid, quality, reset view) sit in the viewer's own bar rather than floating over the render, and they step out of the way on the image tab where they mean nothing.

Cards are titled by subject, not by file name: `red-sports-car-3f2a9c1b-r512.glb` reads as **Red Sports Car**, with resolution and other legacy suffixes kept as tags. The full name is on hover and spelled out in the top bar. Assets generated before `text2image` put the subject in the name are all digest, and keep their file name rather than showing a blank card.

The card art is the GLB, not the PNG it was reconstructed from. Using the source image was tempting and dishonest: it is what FLUX drew, and a grid of those flatters a reconstruction that may have thrown half of it away. The source image is the fallback, for a model whose render fails or a page with no WebGL.

A GLB imported from elsewhere gets a Motion panel when it already contains animation clips. The text-to-3D pipeline itself produces static GLBs.

The sidebar list is the page's one `listbox`, and its rows are the only `option`s. Sheet tiles are plain buttons carrying `aria-current`: two listboxes over the same models would be two selections for a screen reader to reconcile. Tiles look identical at rest, including the one that is loaded, and light up with an accent bar under the pointer; the sidebar row is where you read which model the turntable has.

Every model has a stable id, so `?id=hero-r512` deep links one, and `GET /api/models?id=hero-r512` resolves the same thing from a script.

## What lives where

| | |
| --- | --- |
| `src/serve.py` | stdlib HTTP: the page, `/api/models`, the GLB and image bytes |
| `web/ui.js` | the list, the layouts, the tabs, the motion panel, the URL sync. No three.js import |
| `web/studio.js` | the look: environment, three-point rig, floor, framing. Shared by the turntable and the cards |
| `web/scene.js` | the WebGL turntable: the render graph, loading, the mixer |
| `web/thumbs.js` | one hidden renderer that draws each GLB once into a data URL for its card |
| `web/vendor/three/` | three.js 0.185.1, vendored so the page works offline |
| `tests/test_serve.py` | the server over real HTTP |
| `tests/ui.test.js` | the interface in jsdom, driven by user-event, HTTP faked by MSW |

## Things that will bite you

- **Keep `ui.js` free of three.js.** The split is what lets the interface be tested without a GPU. Importing `three` there would drag WebGL into jsdom and the suite would die.
- The layout swap is CSS, off `#app[data-view]`, and the image tab hides the render controls off `#app[data-mode]`. jsdom loads no stylesheet, so a test cannot see either of those; assert the attribute, not the visibility.
- The sheet is emptied when it is not showing rather than hidden, so there is never a stale grid holding a second copy of every thumbnail image alive.
- The speed slider's `value` in `ui.js` is the opening rotation speed. `main.js` reads `ui.rotation` at startup and pushes it into the viewer, so that attribute is the only place it is set.
- Nothing is downloaded: system fonts, icons written as inline SVG, and the one checkbox tick is a `data:` URI. A CDN font or icon set would break the offline promise the rest of the layer keeps.
- The import map in `index.html` maps `three` and `three/addons/` at the exact npm layout. The addons import each other by relative path, so flattening `web/vendor/three/` breaks `GLTFLoader`.
- TRELLIS writes real metallic and roughness. Without an environment map, metal renders black. The environment here is a graded sky dome plus three emissive soft boxes, built in code, so the page still needs no downloaded asset.
- **Colours in `studio.js` are linear, not sRGB.** The composer renders into a linear target and `OutputPass` encodes at the very end, so a value that looks like a dark hex is not one: `0.075` lands near `0.30` once encoded. The backdrop was a grey fog bank until this was worked out.
- **`material.envMapIntensity` does not scale `scene.environment`.** It scales a material's own `envMap`. Measured on the floor, the scene environment was supplying 72% of its brightness and the property changed nothing; the floor is `MeshPhongMaterial` with `reflectivity: 0` for that reason, which is the one non-PBR material on the page and is deliberate.
- **Do not put the key light at the camera's azimuth.** It was at `(3, 6, 4)`, a few degrees off the opening camera and 50 degrees up, so every shadow fell straight behind its model and was hidden by it. 45 degrees off-axis and 38 degrees up is what makes the shadow visible, and side light is what gives a shape form.
- The gallery renders one thumbnail at a time through a single hidden WebGL context. Twenty live canvases would be twenty contexts, and browsers start dropping the oldest at sixteen. Thumbnails are keyed on path *and* mtime, so a regenerated asset redraws.
- Quality off drops the ambient occlusion and the bloom and draws straight to the canvas. The box that generates these assets is usually mid-inference on the same iGPU.
- Models arrive in wildly different scales. `frame()` normalises the longest axis to 1.4 units and sits the model on the floor, so do not add a fixed camera distance. The shadow camera is sized there too, or a tall model's shadow gets clipped.
- A skinned mesh keeps its bind-pose bounding box, so it culls out of view mid-clip unless `frustumCulled` is off. That one is invisible until a character walks and vanishes.
- Switching to the image tab hides the canvas, and a hidden element has no size. Coming back has to tell the renderer to resize, or the model arrives stretched.
- The clip list the server sends is what the file claims; the list the renderer parsed replaces it on load, because the renderer is the thing that will actually play them.
