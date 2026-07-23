# Engine changes on top of upstream

What was removed to get to a Vulkan-only build is in [`engine/PROVENANCE.md`](engine/PROVENANCE.md). This file tracks behaviour and performance changes, each with the measurement behind it. Method: [`bench/README.md`](bench/README.md).

## Results

Filled in as changes land. Same box, same input image, same seed, res 512.

| Tag | Wall | Engine | Peak GTT delta | Triangles | Change |
| --- | --- | --- | --- | --- | --- |
| _pending_ | | | | | |

## Changes

### 1. Map the weight files instead of staging them through the heap

`Model::load` read every tensor into a `std::vector<uint8_t>` and then copied that into the backend buffer, so each byte was handled twice. Models are loaded and freed stage by stage, which at res 512 means roughly 11 GB moved through that path per run.

The POSIX path now maps the file read-only and passes a pointer into the page cache straight to `ggml_backend_tensor_set`, leaving one copy: the one into the backend buffer. `madvise(MADV_SEQUENTIAL | MADV_WILLNEED)` tells the kernel the access pattern. The heap-staging loop is kept as the fallback for Windows and for a file that cannot be mapped, so nothing loses a platform.

Measured: see the table.
