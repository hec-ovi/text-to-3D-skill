# CONTRACT - assets

`contractVersion: 1.0`

## Purpose

Find an existing CC0 model instead of generating one, and hand it back as a GLB that behaves like anything this repo made.

## Inputs

| Param | Schema | Preconditions |
| --- | --- | --- |
| `AssetSearchRequest` | [`schema/search_request.json`](schema/search_request.json) | The library at `endpoint` is reachable. No key, no account. |
| `AssetFetchRequest` | [`schema/fetch_request.json`](schema/fetch_request.json) | `id` came from a search. Blender 5.x is on the machine: the library ships glTF plus sidecars and a fetch converts them into one GLB. `outDir` is writable. |

Entry points: `python3 src/assets.py search --query "<words>"` and `python3 src/assets.py fetch --id <id> --out-dir <dir>`. Python 3.10+, standard library only.

## Outputs

| Param | Schema | Postconditions |
| --- | --- | --- |
| `AssetSearchResult` | [`schema/search_result.json`](schema/search_result.json) | Matches sorted by triangle count, cheapest first, capped at `limit`; `total` is the count before the cap. Every entry carries `license: "CC0"`, because this layer surfaces nothing else. |
| `AssetFetchResult` | [`schema/fetch_result.json`](schema/fetch_result.json) | `glb.uri` exists, hashes to `glb.checksum.sha256`, and is one self-contained file: buffers and images embedded, no sidecars left in `outDir`. Shape-compatible with `image2mesh`'s `MeshResult.glb`. |

## Events

None. Both operations are synchronous.

## Errors

Closed set, [`schema/error.json`](schema/error.json). Written to stderr as JSON, exit code 1.

| Code | Cause |
| --- | --- |
| `INVALID_REQUEST` | Request failed schema validation. |
| `LIBRARY_UNREACHABLE` | The endpoint did not answer. |
| `LIBRARY_ERROR` | It answered with an HTTP error, with something that is not JSON, or with a file manifest whose paths escape their directory. |
| `ASSET_MISSING` | No asset with that id. |
| `NO_GLTF` | The asset exists but has no glTF variant to convert. |
| `BLENDER_MISSING` | No Blender to convert with. |
| `CONVERT_FAILED` | Blender could not open the glTF or wrote nothing. |
| `TIMEOUT` | The fetch did not finish in `timeoutSeconds`. |
| `OUTPUT_WRITE_FAILED` | `outDir` is not writable. |

## Dependencies

Poly Haven's public API (`https://api.polyhaven.com`), and Blender 5.x as a subprocess for the conversion. No other layer.

## Invariants

- **CC0 only.** Poly Haven is the source precisely because everything on it is CC0: no attribution obligation, redistribution allowed, commercial use explicitly permitted by its terms. A tool that bakes fetched assets into someone else's export cannot honour a CC-BY attribution chain, which is what rules out the larger catalogues. The `license` field is a constant in the schema, not a passthrough, so a future source that is not CC0 fails validation instead of leaking terms nobody read.
- Every request carries a `User-Agent` naming this software, which Poly Haven's terms require and warn they will block traffic without.
- A fetched GLB lands in `outDir` under `<id>-polyhaven.glb`, so its id follows the same rule as everything else and the preview layer lists it without knowing where it came from.
- Sidecar paths come from the library and are written verbatim relative to a temporary directory. A path that escapes that directory is refused, checked with `os.path.commonpath`.
- Nothing but the GLB survives a fetch. The `.gltf`, the `.bin` and the textures live in a temporary directory that goes away.

## How to modify this blackbox safely

1. Adding a source means a new adapter behind the same two envelopes, and a `source` enum bump in both result schemas. Check the licence first: if it is not CC0, the `license` constant has to become a real field and every consumer has to start carrying attribution.
2. The search filter is a plain substring match over name, tags and categories, done here rather than server-side because the API has no query parameter. It fetches the whole catalogue, which is a few hundred entries.
3. Run `uvx pytest tests/ -q` from this folder. A stub HTTP server stands in for the library, serving a real `.gltf` and `.bin` from `fixtures/`, so the download, the conversion and the envelopes are all exercised offline. The conversion tests skip themselves when Blender is missing.
