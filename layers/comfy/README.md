# comfy

The mesh engine as one ComfyUI node, so a single graph runs prompt to GLB. The contract is [`CONTRACT.md`](CONTRACT.md); this is the working notes.

```bash
python3 src/client.py --image ../image2mesh/fixtures/bench-subject.png --out-dir ../../out
uvx pytest tests/ -q
```

`init` mounts this folder into the ComfyUI container at `custom_nodes/text_to_3d` and points it at the engine on the host. Load `workflows/text_to_3d.json` in ComfyUI and the graph is the whole pipeline: klein renders the reference image, node 14 reconstructs it, and the GLB lands in the directory the preview server is already watching.

Measured on the Strix Halo host, `bench-subject.png` at resolution 512 with `--target-faces 8000`, first call so the model load is inside the number: 305.7 s to a 7,842-triangle, 441,476-byte GLB that validated, loaded in the viewer, and measured a 35.2 degree median minimum angle with no degenerate faces. An LLM server was resident on the same iGPU throughout, so this is a contended figure, not a best case.

## What lives where

| | |
| --- | --- |
| `src/client.py` | the layer: validate, POST, parse the GLB, write it, emit |
| `src/node.py` | the adapter: an `IMAGE` tensor to PNG bytes, and back as a path |
| `__init__.py` | the two names ComfyUI reads off a custom node |
| `workflows/text_to_3d.json` | the graph, in API format |
| `docker/` | the one-container image: ComfyUI with the engine beside it |

## One container

Two containers is the default and what `init` starts. `docker/Dockerfile` builds the single-artifact version instead, stacking the engine's runtime files onto the ComfyUI image:

```bash
docker build -f layers/comfy/docker/Dockerfile -t text-to-3d/comfy:merged .
```

It buys one image, one port, one health check. It costs three things worth knowing before choosing it: the engine inherits ComfyUI's privileged container where on its own it has `/dev/dri` and nothing else, restarting ComfyUI restarts the engine and pays the TRELLIS model load again, and nothing gets faster, because the HTTP hop it removes was moving one PNG and one GLB either side of a multi-minute reconstruction.

`docker/supervise.sh` is the part that has to be right. Compose gives two restart policies and two health checks for free; inside one container that policy is a shell script, and the rule it encodes is that the container dies when either child does. A container answering on 8188 with a dead mesh engine looks healthy right up until a graph reaches the node.

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
