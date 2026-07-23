# text2image

Prompt in, PNG out, via FLUX.2 klein on a running ComfyUI. The contract is [`CONTRACT.md`](CONTRACT.md); this is the working notes.

```bash
python3 src/klein.py --prompt "a brass antique diving helmet" --out-dir out
uvx pytest tests/ -q
```

## What lives where

| | |
| --- | --- |
| `src/klein.py` | the whole layer: build the graph, submit, poll, fetch, hash, emit |
| `src/schema_check.py` | a ~90 line JSON Schema subset, local to this layer on purpose |
| `templates/flux2_klein_t2i.json` | the ComfyUI graph in API format |
| `schema/` | the boundary: request, result, errors |

## Things that will bite you

- The graph is in **API format**, not the UI's save format. Export it from ComfyUI with "Save (API)" or it will not submit.
- Node ids are load-bearing. `src/klein.py` injects into nodes `4`, `6`, `7` and `9` by id; renumbering the template silently breaks the injection, which is why a missing node raises `INVALID_REQUEST` instead of rendering something with an empty prompt.
- klein is a 4-step model at cfg 1.0. Raising `--steps` mostly buys time, not quality.
- Changing `FRAMING` changes every derived seed, because the seed is the hash of the framed prompt, not the raw one.
- ComfyUI keeps weights resident after the first render. A cold first call runs several minutes; later ones are seconds. That is the backend's behaviour, not this layer's.
