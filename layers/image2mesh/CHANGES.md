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
| baseline (pinned upstream, Vulkan) | 224.9 s | 224.3 s | 3612 MiB | 138524 | 5204732 B |
| after change 1 (mmap weights) | 222.9 s | 222.3 s | 3614 MiB | 138524 | 5204700 B |
| after change 3 (parallel edge build) | **192.2 s** | **191.7 s** | 3616 MiB | 138520 | 5182056 B |

**14.5% faster end to end.** All of it lands in one phase: the GLB write went from 61.4 s to 30.1 s. Every other phase is within noise of upstream, because nothing else was touched.

| Phase | baseline | now |
| --- | --- | --- |
| `[1/6]` preprocess | 17.8 s | 17.4 s |
| `[2/6]` DINOv3 | 0.4 s | 0.4 s |
| `[3/6]` sparse-structure flow | 45.0 s | 44.6 s |
| `[4/7]` shape SLAT flow | 44.5 s | 44.3 s |
| `[5/7]` shape decode | 15.3 s | 15.2 s |
| `[6/7]` texture SLAT flow | 40.0 s | 39.7 s |
| `[7/7]` write GLB | 61.4 s | **30.1 s** |

## What is still on the table

The three flow stages are 128 s and are the model doing its job; nothing here touches them. Inside the decimation the remaining cost is now the GPU dispatch and its transfers, 12.5 s, which re-uploads vertices, faces, adjacency, edges and boundary flags every round through a staging buffer into device-local memory. On a UMA part that staging copy is avoidable, but it means allocating the working buffers host-visible and reworking the barriers, which is a much larger change than this one.

## Changes

### 1. Map the weight files instead of staging them through the heap

`Model::load` read every tensor into a `std::vector<uint8_t>` and then copied that into the backend buffer, so each byte was handled twice. Models are loaded and freed stage by stage, which at res 512 means roughly 11 GB moved through that path per run.

The POSIX path now maps the file read-only and passes a pointer into the page cache straight to `ggml_backend_tensor_set`, leaving one copy: the one into the backend buffer. `madvise(MADV_SEQUENTIAL | MADV_WILLNEED)` states the access pattern. The heap-staging loop stays as the fallback for Windows and for a file that cannot be mapped.

**Measured: no wall-clock change.** 222.3 s against 222.8 s is inside the noise, and the mesh is byte-identical in triangle count. With a warm page cache the second copy was never the bottleneck, so removing it buys nothing on the clock. What it does buy is one less full-size heap allocation per stage (the staging vector grew to the largest tensor in each file) and a cheaper cold-cache path, since the kernel now reads ahead instead of the process blocking on `fread` per tensor.

Kept because it is strictly less work for the same output, but it is not a speedup and is not claimed as one.

### 3. Build the decimation edge list per vertex, in parallel, instead of through one hash map

With change 2 in place the decimation splits like this, first round of a 6.46 M face mesh, 66 rounds in total:

| Step | Round 1 | All 66 rounds | Share |
| --- | --- | --- | --- |
| CSR vertex to face adjacency | 21 ms | 0.3 s | 1% |
| unique edge list + boundary flags | 1647 ms | **22.5 s** | **62%** |
| four GPU kernels, upload and download | 848 ms | 12.5 s | 35% |
| host stream compaction | 59 ms | 0.8 s | 2% |

The edge list was built by counting every directed edge into an `unordered_map<uint64_t,int>`, which is 3F hash updates per round, 19.4 M of them on round one, then walking the map in bucket order. On a 32-thread part it ran on one thread and cost twice what the GPU did.

It does not need a map. The CSR built immediately above already gives every vertex its incident faces, so each vertex can emit its own edges with no shared state: gather the neighbours it sees across those faces, and a neighbour `u > v` that appears exactly once is an edge carried by a single face, which is precisely the boundary test. That is parallel over vertices, one thread per 64k of them. The only cross-thread write is the boundary flag on the higher endpoint, which is a relaxed atomic store of a constant 1.

**Measured: 1647 ms to 24 ms on round one, 22.5 s to 1.8 s across the run.**

Two consequences worth stating. Because each thread owns a contiguous vertex range, the edge list comes out ordered by `(v, u)` instead of by hash bucket, which is deterministic across builds and libstdc++ versions where the old order was not. And because the GPU breaks cost ties on edge index, a different order picks marginally different collapses: the mesh is 138520 triangles against 138524, bounding box identical to five decimals, and the GLB still validates clean against the Khronos glTF-Validator. Same mesh, not the same bytes.

### 2. Per-round attribution for the decimation loop

`T2M_DECIMATE_TIMING=1` makes `decimate_qem_vk` print, per round, the face count, the edge count, and the split between building CSR adjacency, building the unique edge list, the GPU dispatch plus its transfers, and the host-side stream compaction. The Vulkan driver only runs four compute kernels per round; adjacency, edge building and compaction are all host work, and that split is invisible from outside the binary.

Diagnostics only, off by default, no effect on output.
