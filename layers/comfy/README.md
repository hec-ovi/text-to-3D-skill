# comfy

The mesh engine as one ComfyUI node, so a single graph runs prompt to GLB. The contract is [`CONTRACT.md`](CONTRACT.md); this is the working notes.

```bash
python3 src/client.py --image ../image2mesh/fixtures/bench-subject.png --out-dir ../../out
uvx pytest tests/ -q
```

`init` mounts this folder into the ComfyUI container at `custom_nodes/text_to_3d` and points it at the engine on the host. Load `workflows/text_to_3d.json` in ComfyUI and the graph is the whole pipeline: klein renders the reference image, node 14 reconstructs it, and the GLB lands in the directory the preview server is already watching.

## What lives where

| | |
| --- | --- |
| `src/client.py` | the layer: validate, POST, parse the GLB, write it, emit |
| `src/node.py` | the adapter: an `IMAGE` tensor to PNG bytes, and back as a path |
| `__init__.py` | the two names ComfyUI reads off a custom node |
| `workflows/text_to_3d.json` | the graph, in API format |

## Why the node calls a server instead of doing the work

TRELLIS.2 in ComfyUI means one of two things. The native nodes are the obvious
one and they are not an option here: every maintained wrapper builds FlexGEMM,
CuMesh, nvdiffrast and flash-attn, and Microsoft's own HIP path targets gfx942,
not gfx1151. The measured alternative is the Vulkan engine this repo already
ships, which produced a 512 GLB in 154 seconds on the Radeon 8060S.

So the node is an HTTP client. It gets the graph without touching the
reconstruction, which is the part that took a Vulkan port to make work at all.

## Things that will bite you

- **Do not spawn `t2m-cli` per graph run.** Model load is the fixed cost the resident server exists to pay once. A subprocess per node execution puts it back on every asset.
- The container reaches the engine through `host.docker.internal`, which Linux does not provide by default. The overlay adds `host-gateway` for it; without that the node reports `ENGINE_UNREACHABLE` and the endpoint looks fine in the widget.
- `src/client.py` must stay stdlib. It is what the tests drive, and the moment it imports torch the suite needs a GPU image to run.
- torch, numpy and PIL are imported inside the call, not at module scope. ComfyUI imports every custom node at startup and one that raises on import takes the whole node list down with it.
- A batch of four images is four reconstructions, not one. The node takes the first frame, because the alternative is silently reconstructing a collage.
- `target_faces` is `0` for "let the engine decide". A ComfyUI widget cannot be left empty the way a JSON field can be omitted, and `0` is not a face count anyone would ask for.
- The GLB reader here is a second, smaller copy of the one in `image2mesh`. That is the boundary working as intended: a ComfyUI worker has no reason to import a Docker driver.
