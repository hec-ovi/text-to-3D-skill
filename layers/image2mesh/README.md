# image2mesh

Image in, GLB out, on a Vulkan-only TRELLIS.2 engine. The contract is [`CONTRACT.md`](CONTRACT.md); this is the working notes.

```bash
docker build -f docker/Dockerfile -t text-to-3d/engine:vulkan .
python3 src/mesh.py --image fixtures/bench-subject.png --out-dir out
uvx pytest tests/ -q
```

## What lives where

| | |
| --- | --- |
| `engine/` | the C++ engine, Vulkan only. [`PROVENANCE.md`](engine/PROVENANCE.md) says what upstream code was dropped |
| `docker/` | the runtime image and its entrypoint checks |
| `src/mesh.py` | the driver: validate, run, parse the GLB, emit |
| `src/quality.py` | a diagnostic, off to the side: triangle quality for any GLB |
| `bench/` | how every performance number here was produced |
| `CHANGES.md` | engine changes on top of upstream, each with its measurement |

## Is the mesh any good?

`src/quality.py` answers it with numbers. A wireframe cannot: every triangle is
drawn, front and back, so a clean mesh two surfaces deep looks like a heap of
slivers, and an afternoon went into chasing a decimation bug that was not there.

```bash
python3 src/quality.py ../../out/*.glb
```

Measured over what this repo has produced, plus a hand-authored CC0 asset for
scale:

| | tris | median min angle | under 10 deg | radius ratio | degenerate |
| --- | --- | --- | --- | --- | --- |
| helmet, res 512 | 138520 | 37.5 deg | 1.2% | 1.42 | 0 |
| warrior, decimated to 6k | 5970 | 33.7 deg | 0.9% | 1.54 | 0 |
| knight, decimated to 4k | 3810 | 34.8 deg | 2.7% | 1.48 | 0 |
| Poly Haven chair (authored) | 724 | 14.1 deg | 32.0% | 2.71 | 0 |

An equilateral triangle is 60 degrees at a radius ratio of 1.0. So the
quadric simplifier holds its shape quality down to 4k faces, and the
hand-authored asset is four times worse by every measure, which is normal for
triangulated quads and fine for rendering. Whatever is soft about these assets
is the reconstruction's frequency limit and the texel budget, not the
triangulation.

## Things that will bite you

- `decimate_qem.cpp` and `deform_conv_cpu.cpp` look like CPU code that a Vulkan-only build should not need. They are the dispatchers. Delete them and the Vulkan implementations lose their entry point.
- The container needs `/dev/dri` **and** the host's render group id. Without the group it starts, finds no device, and exits 78 rather than falling back to the CPU.
- Weights are mounted read-only at `/models`. The entrypoint checks for `ss_flow.gguf` and exits 78 if it is missing, because the engine's own failure for missing weights is much further downstream and much less clear.
- Peak memory is the reason resolution matters: 512 runs one pass, 1024 and 1536 add a cascade.
- The GLB is parsed before any result is emitted. If you change the exporter, run the tests: five malformed shapes are covered, and a real GLB is checked when `T2M_RUN_GPU=1`.
