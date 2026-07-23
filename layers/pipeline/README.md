# pipeline

The orchestrator: text2image, then image2mesh. The contract is [`CONTRACT.md`](CONTRACT.md); this is the working notes.

```bash
python3 src/pipeline.py --prompt "a brass antique diving helmet" --out-dir out
uvx pytest tests/ -q
```

## Things that will bite you

- This layer calls the other two as **subprocesses**, never as imports. That is the point: either can be rewritten in another language without touching this file.
- The image reference is passed through field for field. Do not "helpfully" re-hash it here; the mesh layer re-hashes, and a disagreement between the two is a real bug worth failing on.
- A stage's error is wrapped, not rethrown. `code` says which stage, `cause` carries that layer's own closed-set code. Callers should read both.
- The tests import the sibling layers' test helpers (the stub ComfyUI and the stand-in engine) but never their source. That is the one place a cross-layer import is allowed, and it is test scaffolding, not runtime code.
