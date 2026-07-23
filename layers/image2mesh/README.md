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
| `bench/` | how every performance number here was produced |
| `CHANGES.md` | engine changes on top of upstream, each with its measurement |

## Things that will bite you

- `decimate_qem.cpp` and `deform_conv_cpu.cpp` look like CPU code that a Vulkan-only build should not need. They are the dispatchers. Delete them and the Vulkan implementations lose their entry point.
- The container needs `/dev/dri` **and** the host's render group id. Without the group it starts, finds no device, and exits 78 rather than falling back to the CPU.
- Weights are mounted read-only at `/models`. The entrypoint checks for `ss_flow.gguf` and exits 78 if it is missing, because the engine's own failure for missing weights is much further downstream and much less clear.
- Peak memory is the reason resolution matters: 512 runs one pass, 1024 and 1536 add a cascade.
- The GLB is parsed before any result is emitted. If you change the exporter, run the tests: five malformed shapes are covered, and a real GLB is checked when `T2M_RUN_GPU=1`.
