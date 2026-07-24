# assets

Search a CC0 library and pull a model into the same folder everything else lands in. The contract is [`CONTRACT.md`](CONTRACT.md); this is the working notes.

```bash
python3 src/assets.py search --query "chair wood" --limit 5
python3 src/assets.py fetch --id painted_wooden_chair_01 --out-dir ../../out
```

Measured here: a search is one request and about 160 ms; a fetch of a 724-triangle chair at 1k textures is 493 KB and about 6 seconds, Blender conversion included. Generating the same chair takes two to four minutes, so search first when a stock prop would do.

## Why Poly Haven and not threejsassets.com

The site that prompted this feature has no API of any kind: no JSON feed, no documented download endpoint, downloads gated behind account-bound keys, and terms that explicitly forbid automated bulk access. There is no lawful way to wire it into a tool. The full survey, including what Sketchfab, Poly Pizza, ambientCG, Kenney, Quaternius and Icosa Gallery each offer, is in `.research/searchable-3d-asset-libraries/FINDINGS.md`.

What is left after the licence filter is small. Poly Haven has a few hundred models, a keyless REST API, CC0 on everything, and terms that permit commercial use outright. The big catalogues (Sketchfab's 700,000+, Poly Pizza's 7,000+) are OAuth-gated and dominated by CC-BY, whose attribution has to travel with the asset everywhere it is used. A tool that bakes a fetched model into a user's exported scene cannot honour that by construction, so CC0 is not a preference here, it is the constraint.

## Why it converts through Blender

Poly Haven ships `.gltf` plus a `.bin` plus a folder of textures. That is four to a dozen files with relative paths, and every consumer downstream (the preview server, the rig layer, a browser) wants one. Blender imports the set and exports a GLB with buffers and images embedded, which takes about two seconds and removes the whole category of "it loaded but has no textures".

## Things that will bite you

- The `User-Agent` is not decoration. Poly Haven's terms require a unique one naming the software and say unnamed traffic gets blocked eventually.
- Sidecar paths come from the manifest and are written verbatim. `../` in one would write outside the job directory, so the path is normalised and checked against the working directory before anything is opened.
- The API has no query parameter, so search fetches the catalogue and filters locally. That is fine at a few hundred entries and would not be at a hundred thousand.
- Asset ids are the library's, not ours, until the fetch: the file lands as `<id>-polyhaven.glb` and from then on it is addressed like anything else here.
