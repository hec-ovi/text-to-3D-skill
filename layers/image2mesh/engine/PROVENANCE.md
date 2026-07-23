# Provenance

This engine started as [pwilkin/trellis.cpp](https://github.com/pwilkin/trellis.cpp), a C++/GGML port of Microsoft's [TRELLIS.2](https://github.com/microsoft/TRELLIS.2) image-to-3D pipeline. Upstream builds for CUDA, HIP/ROCm, Vulkan and CPU. This copy builds for Vulkan and nothing else.

- Upstream commit: `101007fb8f4b5b032a2fb81d535e1c813438401e` (v0.5.3, 2026-07-22)
- ggml fork: [pwilkin/ggml](https://github.com/pwilkin/ggml) branch `trellis-patches`, commit `f33ab068ddc95aa5df86af65a9c29d396bad3bb5`, carried as a submodule under `thirdparty/ggml` (MIT, fork of ggml-org/ggml)
- Model weights: `microsoft/TRELLIS.2-4B` (MIT), converted to GGUF by [ilintar/trellis2-gguf](https://huggingface.co/ilintar/trellis2-gguf). Not redistributed here; `scripts/fetch-models.sh` pulls and checksums them.

Upstream carries no LICENSE file at the pinned commit.

## What was removed

| Removed | Why |
| --- | --- |
| `src/decimate_qem.cu`, `src/deform_conv.cu` | CUDA/HIP kernels. The Vulkan compute shaders (`decimate_qem.comp`, `deform_conv.comp`) cover the same four decimation rounds and the deformable conv. |
| CUDA and HIP branches of `CMakeLists.txt` | ~130 lines of `enable_language(CUDA)`, `enable_language(HIP)`, arch detection and ROCm path probing. A Vulkan-only build never reaches them. |
| 15 `src/test_*.cpp` binaries | Numeric validation against PyTorch reference tensors. Useful when porting the model, dead weight in a runtime image. |
| `src/smoke_test.cpp`, `src/decode_replay.cpp`, `src/post_replay.cpp` | Developer replay tools. |
| `app/` (1.5 MB) | Tauri desktop app. |
| `assets/` (115 MB) | Showcase renders and screenshots. |
| `tools/` (1.2 MB) | Python safetensors to GGUF conversion. The GGUFs are pre-converted; nothing at runtime needs Python. |
| `docs/` (728 KB) | Reverse-engineered architecture notes. Read them [upstream](https://github.com/pwilkin/trellis.cpp/tree/main/docs/spec) when changing model code. |
| `install/`, `.github/` | Windows installer scripts and CI. |

207 MB of checkout became 1.8 MB of source plus a 25 MB ggml submodule. The 44 files in `src/` became 22.

## What was kept and why

`decimate_qem.cpp` and `deform_conv_cpu.cpp` look like CPU code to remove, but both are the dispatchers: they hold the entry point the rest of the engine calls, pick the Vulkan implementation when the device initialises, and keep a CPU path for the case where it does not. Removing them removes the entry point.

`thirdparty/cpp-httplib` is kept for `t2m-server`. Model load dominates a single request, so a resident process is the difference between paying it once and paying it every time.

## Changes on top of upstream

Tracked in [`../CHANGES.md`](../CHANGES.md) with the measurement behind each one.
