# preview

The viewer for everything this repo produces. The contract is [`CONTRACT.md`](CONTRACT.md); this is the working notes.

```bash
python3 src/serve.py --dir ../../out --open
```

The model list is down the left and stays there. Only the middle pane changes. The page opens on **Gallery**, a contact sheet where every card is that GLB actually rendered, at 512px, by the same studio the turntable uses. **Single** swaps the sheet for the turntable, with the source image behind a second tab, because seeing both is the only way to tell a bad reconstruction from a bad prompt. Picking a model anywhere opens it on the turntable, and the wordmark in the top bar goes back to the sheet. Filter by name, or walk the list with the arrow keys.

Three scopes, three strips. The top bar is the page: which layout, and what you are filtering for. The sidebar is the models, and only the models. The viewer owns the asset: its name above the render, its controls below it.

That last strip is the rule worth keeping. The sidebar answers "which asset", so anything acting on the one already loaded goes in the viewer's footer, under the render it changes: auto rotate and its speed, wireframe, grid, quality, reset view, and the clips of an animated GLB beside them. The whole strip is gone when there is nothing to control, so no control is ever on screen greyed out.

Cards are titled by subject, not by file name: `red-sports-car-3f2a9c1b-r512.glb` reads as **Red Sports Car**. Assets generated before `text2image` put the subject in the name are all digest, and keep their file name rather than showing a blank card.

Under the name are flags, not facts. The line used to read `r1024 · rigged · 11.8k tris · 1.7 MB · 3 days ago`, which is everything that was easy to produce and answers nothing anybody asks while scanning a grid of characters. It now reads size, then what the thing is (**Model**, or **Humanoid** when the skeleton has a root and both legs under their Mixamo names), then what it can do: **Rigged** when it carries a skin, **Animated** when it carries clips. A prop stops after the first two, because "not rigged, not animated" about a treasure chest is two answers to questions nobody asked about a treasure chest.

No boxes. Four bordered pills under every name turn a calm list into a spreadsheet, and the border was never carrying meaning the colour was not. Colour is the signal, the word is the same signal for anyone who cannot use colour, and a hairline dot separates them.

Everything else, the file name, the id, the resolution tag, the triangle count, the timestamp, is behind **About this model** in the footer. It is real and occasionally needed; it was just never the question being asked while looking at the model.

The card art is the GLB, not the PNG it was reconstructed from. Using the source image was tempting and dishonest: it is what FLUX drew, and a grid of those flatters a reconstruction that may have thrown half of it away. The source image is the fallback, for a model whose render fails or a page with no WebGL.

A GLB with animation gets its clips in the viewer footer. The mesh pipeline produces static GLBs; the rig layer is what puts clips in one.

A rigged asset replaces the mesh it was rigged from in the list rather than sitting next to it. They are the same character, the rigged file is the same mesh with a skeleton added, and showing both is the same model twice. The one that is dropped is named in `supersedes` and is still served by name, so an existing link keeps working.

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
- The layout swap is CSS, off `#app[data-view]`. The viewer footer is hidden off `[data-mode="image"]` and `[data-loaded="false"]`, so the strip disappears rather than its controls going grey. jsdom loads no stylesheet, so a test cannot see any of that; assert the attribute, not the visibility.
- The sheet is emptied when it is not showing rather than hidden, so there is never a stale grid holding a second copy of every thumbnail image alive.
- The speed slider's `value` in `ui.js` is the opening rotation speed. `main.js` reads `ui.rotation` at startup and pushes it into the viewer, so that attribute is the only place it is set.
- Nothing is downloaded: system fonts, icons written as inline SVG, and the one checkbox tick is a `data:` URI. A CDN font or icon set would break the offline promise the rest of the layer keeps.
- The import map in `index.html` maps `three` and `three/addons/` at the exact npm layout. The addons import each other by relative path, so flattening `web/vendor/three/` breaks `GLTFLoader`.
- TRELLIS writes real metallic and roughness. Without an environment map, metal renders black. The environment here is a graded sky dome plus three emissive soft boxes, built in code, so the page still needs no downloaded asset.
- **Colours in `studio.js` are linear, not sRGB.** The composer renders into a linear target and `OutputPass` encodes at the very end, so a value that looks like a dark hex is not one: `0.075` lands near `0.30` once encoded. The backdrop was a grey fog bank until this was worked out.
- **`material.envMapIntensity` does not scale `scene.environment`.** It scales a material's own `envMap`. Measured on the floor, the scene environment was supplying 72% of its brightness and the property changed nothing; the floor is `MeshPhongMaterial` with `reflectivity: 0` for that reason, which is the one non-PBR material on the page and is deliberate.
- **Every wide, dark gradient on this page bands, and the fix is noise.** The backdrop dome crosses a handful of eight-bit steps over a full screen height, which shows up as four or five stacked bars; the floor's light pool does the same in rings. Both are dithered, the dome in its fragment shader and the floor with a generated grain texture, at roughly one step of amplitude. The grain doubles as surface detail: a floor of one flat colour under a smooth ramp has no high frequencies for the eye to catch and reads as a sheet of plastic.
- **Do not put the key light at the camera's azimuth.** It was at `(3, 6, 4)`, a few degrees off the opening camera and 50 degrees up, so every shadow fell straight behind its model and was hidden by it. 45 degrees off-axis and 38 degrees up is what makes the shadow visible, and side light is what gives a shape form.
- The gallery renders one thumbnail at a time through a single hidden WebGL context. Twenty live canvases would be twenty contexts, and browsers start dropping the oldest at sixteen. Thumbnails are keyed on path *and* mtime, so a regenerated asset redraws.
- Quality off drops the ambient occlusion and the bloom and draws straight to the canvas. The box that generates these assets is usually mid-inference on the same iGPU.
- Models arrive in wildly different scales. `frame()` normalises the longest axis to 1.4 units and sits the model on the floor, so do not add a fixed camera distance. The shadow camera is sized there too, or a tall model's shadow gets clipped.
- A skinned mesh keeps its bind-pose bounding box, so it culls out of view mid-clip unless `frustumCulled` is off. That one is invisible until a character walks and vanishes.
- Switching to the image tab hides the canvas, and a hidden element has no size. Coming back has to tell the renderer to resize, or the model arrives stretched.
- The clip list the server sends is what the file claims; the list the renderer parsed replaces it on load, because the renderer is the thing that will actually play them.
