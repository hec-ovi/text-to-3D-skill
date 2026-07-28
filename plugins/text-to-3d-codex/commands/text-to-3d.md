---
description: Generate a static textured GLB from one subject description, locally
argument-hint: "<subject description>"
---

Activate the `text-to-3d` skill and generate one model.

1. Run `python3 scripts/init.py` first in a session and wait for every service to report `ready`.
2. Generate with `python3 layers/pipeline/src/pipeline.py --prompt "<subject>" --out-dir out --runner server`.
3. Pass `--target-faces N` when the asset is for a game, an engine, or a web scene.
4. Report the GLB path, its triangle count, and the preview link `http://127.0.0.1:8190/?id=<asset-id>`.

User argument: $ARGUMENTS
