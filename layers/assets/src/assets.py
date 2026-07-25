#!/usr/bin/env python3
"""assets blackbox: search a CC0 library, fetch a model, hand back a GLB.

    python3 assets.py search --query chair
    python3 assets.py fetch --id ArmChair_01 --out-dir out

Poly Haven is the source. It is the only library in this space with a keyless
REST API, CC0 on everything, and explicit permission to use it commercially.
The catalogue is small, a few hundred models, which is the trade: the big
catalogues (Sketchfab, Poly Pizza) are OAuth-gated and dominated by CC-BY,
whose attribution has to travel with the asset everywhere it is used, which a
tool that bakes assets into someone else's export cannot honour. See
`.research/searchable-3d-asset-libraries/FINDINGS.md`.

Poly Haven ships .gltf plus sidecar files, not GLB, so a fetch converts through
Blender into one self-contained GLB that lands in the same directory, with the
same id rules, as anything this repo generates.

Prints a JSON envelope on stdout, or an error envelope on stderr with a
non-zero exit. See ../CONTRACT.md. Stdlib only.
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from schema_check import SchemaError, load, validate, with_defaults  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
LAYER = os.path.dirname(HERE)
SEARCH_REQ = os.path.join(LAYER, "schema", "search_request.json")
SEARCH_RES = os.path.join(LAYER, "schema", "search_result.json")
FETCH_REQ = os.path.join(LAYER, "schema", "fetch_request.json")
FETCH_RES = os.path.join(LAYER, "schema", "fetch_result.json")
CONVERT = os.path.join(HERE, "blender_convert.py")

CONTRACT_VERSION = "1.0"

# Poly Haven's terms require a unique User-Agent naming the software, and say
# requests without one will eventually be blocked. This is that name.
USER_AGENT = "text-to-3d-skill/0.2 (+https://github.com/hec-ovi/text-to-3D-skill)"
THUMBNAIL = "https://cdn.polyhaven.com/asset_img/thumbs/{id}.png?width=256"
LICENSE = "CC0"

BLENDER_CANDIDATES = (
    os.environ.get("BLENDER", ""),
    "/home/hec/opt/blender-5.2.0-linux-x64/blender",
    "blender",
)


class AssetError(Exception):
    def __init__(self, code, message, detail=""):
        super().__init__(message)
        self.code, self.message, self.detail = code, message, detail

    def envelope(self):
        env = {"contractVersion": CONTRACT_VERSION, "code": self.code, "message": self.message}
        if self.detail:
            env["detail"] = self.detail
        return env


def find_blender(explicit=None):
    import shutil
    candidates = [explicit] if explicit else list(BLENDER_CANDIDATES)
    for candidate in candidates:
        if not candidate:
            continue
        found = candidate if os.path.isfile(candidate) else shutil.which(candidate)
        if found and os.access(found, os.X_OK):
            return found
    raise AssetError("BLENDER_MISSING", "no Blender executable found",
                     "a fetch converts .gltf plus sidecars into one GLB")


def _get(url, timeout, binary=False):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
    except urllib.error.HTTPError as exc:
        raise AssetError("LIBRARY_ERROR", f"the library returned HTTP {exc.code} for {url}")
    except (urllib.error.URLError, OSError) as exc:
        raise AssetError("LIBRARY_UNREACHABLE", f"cannot reach {url}", str(exc))
    if binary:
        return body
    try:
        return json.loads(body.decode("utf-8"))
    except ValueError as exc:
        raise AssetError("LIBRARY_ERROR", f"{url} did not return JSON", str(exc))


def _matches(entry, name, needle):
    if not needle:
        return True
    haystack = " ".join([name, entry.get("name", "")]
                        + entry.get("tags", []) + entry.get("categories", [])).lower()
    return all(word in haystack for word in needle.lower().split())


def search(request):
    """Query the catalogue. Returns a validated SearchResult."""
    schema = load(SEARCH_REQ)
    try:
        validate(request, schema)
    except SchemaError as exc:
        raise AssetError("INVALID_REQUEST", str(exc))
    req = with_defaults(request, schema)

    started = time.monotonic()
    catalogue = _get(f"{req['endpoint'].rstrip('/')}/assets?t=models", req["timeoutSeconds"])
    if not isinstance(catalogue, dict):
        raise AssetError("LIBRARY_ERROR", "the catalogue is not an object of assets")

    hits = []
    for ident, entry in catalogue.items():
        if not _matches(entry, ident, req.get("query")):
            continue
        hits.append({
            "id": ident,
            "name": entry.get("name", ident),
            "license": LICENSE,
            "authors": sorted(entry.get("authors", {})),
            "categories": entry.get("categories", []),
            "tags": entry.get("tags", [])[:8],
            "triangles": entry.get("polycount"),
            "thumbnailUrl": THUMBNAIL.format(id=urllib.parse.quote(ident)),
        })
    hits.sort(key=lambda h: (h["triangles"] or 10 ** 9))
    limited = hits[: req["limit"]]

    result = {
        "contractVersion": CONTRACT_VERSION,
        "source": "polyhaven",
        "query": req.get("query", ""),
        "total": len(hits),
        "assets": limited,
        "elapsedMs": int((time.monotonic() - started) * 1000),
    }
    validate(result, load(SEARCH_RES))
    return result


def _pick_files(files, resolution):
    gltf = files.get("gltf") or {}
    if not gltf:
        raise AssetError("NO_GLTF", "this asset has no glTF variant")
    if resolution not in gltf:
        resolution = sorted(gltf)[0]
    entry = gltf[resolution].get("gltf")
    if not entry or "url" not in entry:
        raise AssetError("NO_GLTF", f"no glTF file at resolution {resolution}")
    return resolution, entry


def fetch(request):
    """Download one asset and convert it to a GLB. Returns a validated FetchResult."""
    schema = load(FETCH_REQ)
    try:
        validate(request, schema)
    except SchemaError as exc:
        raise AssetError("INVALID_REQUEST", str(exc))
    req = with_defaults(request, schema)

    out_dir = os.path.abspath(req["outDir"])
    try:
        os.makedirs(out_dir, exist_ok=True)
    except OSError as exc:
        raise AssetError("OUTPUT_WRITE_FAILED", f"cannot create {out_dir}", str(exc))

    endpoint = req["endpoint"].rstrip("/")
    started = time.monotonic()
    files = _get(f"{endpoint}/files/{urllib.parse.quote(req['id'])}", req["timeoutSeconds"])
    if not isinstance(files, dict) or not files:
        raise AssetError("ASSET_MISSING", f"no asset named {req['id']}")
    resolution, entry = _pick_files(files, req["resolution"])

    # Blender is looked for here, after the asset has been resolved, and not
    # before. Asking first meant a machine with no Blender answered every
    # question with BLENDER_MISSING: a typo in an id, an asset with no glTF
    # variant, all of it masked by an environment problem the caller had not
    # got to yet. What is wrong with the request comes first.
    blender = find_blender(req.get("blenderPath"))

    downloaded = 0
    with tempfile.TemporaryDirectory(prefix="t2m-assets-") as work:
        main = os.path.join(work, f"{req['id']}.gltf")
        with open(main, "wb") as fh:
            body = _get(entry["url"], req["timeoutSeconds"], binary=True)
            fh.write(body)
            downloaded += len(body)

        # The sidecars carry their own relative paths, and the .gltf references
        # them by exactly those paths. Writing them anywhere else produces a
        # file that opens and renders untextured.
        for relative, meta in (entry.get("include") or {}).items():
            safe = os.path.normpath(relative)
            target = os.path.abspath(os.path.join(work, safe))
            if os.path.isabs(safe) or safe.startswith("..") \
                    or os.path.commonpath([target, work]) != work:
                raise AssetError("LIBRARY_ERROR",
                                 f"the manifest escapes its directory: {relative}")
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "wb") as fh:
                body = _get(meta["url"], req["timeoutSeconds"], binary=True)
                fh.write(body)
                downloaded += len(body)

        out_path = os.path.join(out_dir, f"{req['id']}-polyhaven.glb")
        cmd = [blender, "--background", "--factory-startup", "--python-exit-code", "1",
               "--python", CONVERT, "--", main, out_path]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True,
                                  timeout=req["timeoutSeconds"])
        except subprocess.TimeoutExpired:
            raise AssetError("TIMEOUT", f"the conversion did not finish within "
                                        f"{req['timeoutSeconds']}s")
        if proc.returncode != 0 or not os.path.isfile(out_path):
            raise AssetError("CONVERT_FAILED", "Blender could not convert the glTF",
                             (proc.stderr or proc.stdout)[-1200:])

    with open(out_path, "rb") as fh:
        blob = fh.read()

    result = {
        "contractVersion": CONTRACT_VERSION,
        "source": "polyhaven",
        "id": req["id"],
        "glb": {
            "uri": out_path,
            "mediaType": "model/gltf-binary",
            "byteSize": len(blob),
            "checksum": {"sha256": hashlib.sha256(blob).hexdigest()},
        },
        "license": LICENSE,
        "resolution": resolution,
        "downloadedBytes": downloaded,
        "elapsedMs": int((time.monotonic() - started) * 1000),
    }
    validate(result, load(FETCH_RES))
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description="search and fetch CC0 3D assets")
    sub = parser.add_subparsers(dest="command", required=True)

    finder = sub.add_parser("search", help="query the catalogue")
    finder.add_argument("--query", default="")
    finder.add_argument("--limit", type=int, default=20)
    finder.add_argument("--endpoint", default="https://api.polyhaven.com")
    finder.add_argument("--timeout", type=int, default=60)

    getter = sub.add_parser("fetch", help="download one asset as a GLB")
    getter.add_argument("--id", required=True)
    getter.add_argument("--out-dir", default=os.path.join(os.getcwd(), "out"))
    getter.add_argument("--resolution", default="1k", choices=["1k", "2k", "4k"])
    getter.add_argument("--endpoint", default="https://api.polyhaven.com")
    getter.add_argument("--blender-path")
    getter.add_argument("--timeout", type=int, default=300)
    getter.add_argument("--glb-path-only", action="store_true")

    args = parser.parse_args(argv)
    try:
        if args.command == "search":
            result = search({"query": args.query, "limit": args.limit,
                             "endpoint": args.endpoint, "timeoutSeconds": args.timeout})
            print(json.dumps(result, indent=2))
        else:
            request = {"id": args.id, "outDir": args.out_dir, "resolution": args.resolution,
                       "endpoint": args.endpoint, "timeoutSeconds": args.timeout}
            if args.blender_path:
                request["blenderPath"] = args.blender_path
            result = fetch(request)
            print(result["glb"]["uri"] if args.glb_path_only else json.dumps(result, indent=2))
    except AssetError as exc:
        print(json.dumps(exc.envelope(), indent=2), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
