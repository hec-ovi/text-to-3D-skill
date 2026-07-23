# Engine changes on top of upstream

What was removed to get to a Vulkan-only build is in [`engine/PROVENANCE.md`](engine/PROVENANCE.md). This file tracks behaviour and performance changes, each with the measurement behind it. Method: [`bench/README.md`](bench/README.md).

## Where the time actually goes

Pinned upstream, Vulkan backend, res 512, one 1024x1024 input, Strix Halo iGPU. This is the shape of the problem, and it is not what the phase names suggest:

| Phase | Seconds | Share |
| --- | --- | --- |
| `[1/6]` preprocess (BiRefNet background removal) | 18.0 | 8% |
| `[2/6]` DINOv3 conditioning | 0.3 | 0% |
| `[3/6]` sparse-structure flow + decode | 44.4 | 20% |
| `[4/7]` shape SLAT flow | 43.9 | 20% |
| `[5/7]` FlexiDualGrid shape decode | 15.3 | 7% |
| `[6/7]` texture SLAT flow + PBR decode | 39.9 | 18% |
| `[7/7]` write GLB | 60.9 | **27%** |
| total | 222.8 | |

The three flow stages are 128 s of GPU work and are the model doing its job. The write phase is the surprise: 60.9 s, more than any single flow, and 44.7 s of it is one step, the QEM decimation that takes the 6.46 M face dual-grid mesh down to 138 K. Upstream's own README puts that step at about 5 s on ROCm, so the Vulkan path is roughly nine times slower for the same work.

## Results

Same box, same input image, same seed, res 512. Single runs; treat anything under 5% as noise.

| Tag | Wall | Engine | Peak GTT delta | Triangles | GLB |
| --- | --- | --- | --- | --- | --- |
| baseline (pinned upstream, Vulkan) | 223.4 s | 222.8 s | 3613 MiB | 138524 | 5204732 B |
| after change 1 | 222.9 s | 222.3 s | 3614 MiB | 138524 | 5204700 B |

## Changes

### 1. Map the weight files instead of staging them through the heap

`Model::load` read every tensor into a `std::vector<uint8_t>` and then copied that into the backend buffer, so each byte was handled twice. Models are loaded and freed stage by stage, which at res 512 means roughly 11 GB moved through that path per run.

The POSIX path now maps the file read-only and passes a pointer into the page cache straight to `ggml_backend_tensor_set`, leaving one copy: the one into the backend buffer. `madvise(MADV_SEQUENTIAL | MADV_WILLNEED)` states the access pattern. The heap-staging loop stays as the fallback for Windows and for a file that cannot be mapped.

**Measured: no wall-clock change.** 222.3 s against 222.8 s is inside the noise, and the mesh is byte-identical in triangle count. With a warm page cache the second copy was never the bottleneck, so removing it buys nothing on the clock. What it does buy is one less full-size heap allocation per stage (the staging vector grew to the largest tensor in each file) and a cheaper cold-cache path, since the kernel now reads ahead instead of the process blocking on `fread` per tensor.

Kept because it is strictly less work for the same output, but it is not a speedup and is not claimed as one.

### 2. Per-round attribution for the decimation loop

`T2M_DECIMATE_TIMING=1` makes `decimate_qem_vk` print, per round, the face count, the edge count, and the split between building CSR adjacency, building the unique edge list, the GPU dispatch plus its transfers, and the host-side stream compaction. The Vulkan driver only runs four compute kernels per round; adjacency, edge building and compaction are all host work, and that split is invisible from outside the binary.

Diagnostics only, off by default, no effect on output.
