# Where to change what

Each layer is a blackbox. Open one folder, read its `CONTRACT.md` and `schema/`, change its `src/`, run its tests. Nothing outside that folder needs to change, and nothing outside it may read its `src/`.

| You want to change | Open | Do not open |
| --- | --- | --- |
| The image prompt framing, sampler, steps, klein weights, the ComfyUI graph | [`layers/text2image/`](../layers/text2image/CONTRACT.md) | anything else |
| Mesh resolution, UV/atlas/decimation knobs, GLB validation, how the engine is invoked | [`layers/image2mesh/`](../layers/image2mesh/CONTRACT.md) | `engine/` unless the change is in C++ |
| The TRELLIS.2 engine itself: kernels, Vulkan compute, model loading, mesh export | [`layers/image2mesh/engine/`](../layers/image2mesh/engine/PROVENANCE.md) | the Python driver above it |
| The container: base image, Vulkan drivers, device access, GTT behaviour | [`layers/image2mesh/docker/`](../layers/image2mesh/docker/Dockerfile) | |
| Stage order, what the CLI accepts, how failures are wrapped | [`layers/pipeline/`](../layers/pipeline/CONTRACT.md) | either stage's internals |
| What an agent is told about this skill | [`SKILL.md`](../SKILL.md) | |
| Fetching or verifying weights | [`scripts/fetch-models.sh`](../scripts/fetch-models.sh) | |
| Performance claims and how they were measured | [`layers/image2mesh/bench/`](../layers/image2mesh/bench/README.md) | |

## Data across the boundaries

Every value that crosses a layer boundary is a schema-validated JSON envelope. Binary payloads cross **by reference**: a path, a media type, a byte size and a sha256. No layer hands another one raw bytes.

```
TextToMeshRequest -> [pipeline]
                       -> ImageRequest -> [text2image] -> ImageResult { image: {uri, sha256, ...} }
                       -> MeshRequest  -> [image2mesh] -> MeshResult  { glb:   {uri, sha256, ...} }
                     -> TextToMeshResult
```

`ImageResult.image` is shape-compatible with `MeshRequest.image` on purpose, and the pipeline passes it through untouched. `image2mesh` re-hashes the file it is given, so if the two ever disagree the run fails loudly instead of reconstructing the wrong picture.

## Layer boundaries in one line each

- **text2image** owns everything about turning words into a picture, and knows nothing about meshes.
- **image2mesh** owns everything about turning a picture into a GLB, and does not care where the picture came from.
- **pipeline** owns the order and the error wrapping, and imports from neither.
