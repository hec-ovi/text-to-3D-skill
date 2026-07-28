#!/usr/bin/env python3
"""rig blackbox: a static GLB by reference in, a skinned and animated one out.

The model runs in its own container and is reached over HTTP; this driver owns
everything else, and is stdlib only so it runs anywhere the rest of the toolkit
does. No Blender, no torch, no numpy.

    python3 rig.py --glb out/warrior-r1024.glb --out-dir out
    python3 rig.py --request request.json

Prints a RigResult on stdout, or an error envelope on stderr with a non-zero
exit. See ../CONTRACT.md.
"""

import argparse
import base64
import hashlib
import json
import os
import struct
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import clips as clip_builder            # noqa: E402
import skeleton as sk                   # noqa: E402
import skin as skinner                  # noqa: E402
from gltf import Glb, GltfError         # noqa: E402
from schema_check import SchemaError, load, validate, with_defaults  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
LAYER = os.path.dirname(HERE)
REQ_SCHEMA = os.path.join(LAYER, "schema", "rig_request.json")
RES_SCHEMA = os.path.join(LAYER, "schema", "rig_result.json")

CONTRACT_VERSION = "1.0"


class RigError(Exception):
    def __init__(self, code, message, detail=""):
        super().__init__(message)
        self.code, self.message, self.detail = code, message, detail

    def envelope(self):
        env = {"contractVersion": CONTRACT_VERSION, "code": self.code, "message": self.message}
        if self.detail:
            env["detail"] = self.detail
        return env


def _b64_floats(values, per):
    flat = [c for v in values for c in (v if per > 1 else (v,))]
    return base64.b64encode(struct.pack(f"<{len(flat)}f", *flat)).decode("ascii")


def _b64_ints(values, per):
    flat = [c for v in values for c in (v if per > 1 else (v,))]
    return base64.b64encode(struct.pack(f"<{len(flat)}i", *flat)).decode("ascii")


def _floats_from_b64(text, per):
    raw = base64.b64decode(text)
    count = len(raw) // 4
    flat = struct.unpack(f"<{count}f", raw)
    return [flat[i:i + per] for i in range(0, count, per)] if per > 1 else list(flat)


def call_model(endpoint, points, faces, timeout):
    """Ask the rig server for a skeleton and per-vertex weights."""
    payload = json.dumps({
        "vertices": _b64_floats(points, 3),
        "faces": _b64_ints(faces, 3),
        "vertexCount": len(points),
        "faceCount": len(faces),
    }).encode("utf-8")
    request = urllib.request.Request(endpoint.rstrip("/") + "/rig", data=payload,
                                     method="POST",
                                     headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
    except urllib.error.HTTPError as exc:
        raise RigError("MODEL_FAILED", f"the rig server returned HTTP {exc.code}",
                       exc.read().decode("utf-8", "replace")[:800])
    except TimeoutError:
        raise RigError("TIMEOUT", f"no rig within {timeout}s")
    except (urllib.error.URLError, OSError) as exc:
        if isinstance(getattr(exc, "reason", None), TimeoutError):
            raise RigError("TIMEOUT", f"no rig within {timeout}s")
        raise RigError("MODEL_UNREACHABLE", f"no rig server at {endpoint}", str(exc))

    try:
        result = json.loads(body)
    except ValueError:
        raise RigError("MODEL_FAILED", "the rig server did not answer with JSON",
                       body[:400].decode("utf-8", "replace"))

    for key in ("parents", "positions", "skin"):
        if key not in result:
            raise RigError("MODEL_FAILED", f"the rig server left out {key}")

    parents = list(result["parents"])
    positions = [tuple(p) for p in result["positions"]]
    joint_count = len(parents)
    if joint_count == 0:
        raise RigError("NOT_A_CHARACTER", "the model predicted no joints")
    rows = _floats_from_b64(result["skin"], joint_count)
    if len(rows) != len(points):
        raise RigError("MODEL_FAILED",
                       f"got weights for {len(rows)} vertices, the mesh has {len(points)}")
    return positions, parents, rows


def rig(request):
    """Run one RigRequest. Returns a validated RigResult."""
    schema = load(REQ_SCHEMA)
    try:
        validate(request, schema)
    except SchemaError as exc:
        raise RigError("INVALID_REQUEST", str(exc))
    req = with_defaults(request, schema)

    path = req["glb"]["uri"]
    if path.startswith("file://"):
        path = path[7:]
    if not os.path.isfile(path):
        raise RigError("GLB_MISSING", f"no GLB at {path}")
    with open(path, "rb") as handle:
        data = handle.read()
    digest = hashlib.sha256(data).hexdigest()
    if req["glb"].get("checksum", {}).get("sha256") not in (None, digest):
        raise RigError("CHECKSUM_MISMATCH",
                       "the GLB on disk is not the file the request describes")

    try:
        glb = Glb.parse(data)
        points, spans = glb.positions()
        faces = glb.triangles()
    except GltfError as exc:
        raise RigError("GLB_INVALID", str(exc))

    started = time.monotonic()
    positions, parents, rows = call_model(req["endpoint"], points, faces, req["timeoutSeconds"])
    model_ms = int((time.monotonic() - started) * 1000)

    try:
        names = sk.name_joints(positions, parents)
    except sk.SkeletonError as exc:
        # A chair has no hips. Saying so is the honest outcome; naming it Hips
        # would produce a rig that a walk cycle would then act on.
        raise RigError("NOT_A_CHARACTER", "the predicted skeleton is not a humanoid", str(exc))

    joints, weights = sk.prune_and_normalize(rows, limit=req["maxInfluences"])
    skinner.attach(glb, positions, parents, names, joints, weights, spans)

    built = clip_builder.build(names, positions) if req["animate"] else []
    if built:
        skinner.add_animations(glb, built, first_joint=len(glb.gltf["nodes"]) - len(names))

    out_dir = os.path.abspath(req.get("outDir") or os.path.dirname(path))
    try:
        os.makedirs(out_dir, exist_ok=True)
    except OSError as exc:
        raise RigError("OUTPUT_WRITE_FAILED", f"cannot create {out_dir}", str(exc))

    stem = os.path.splitext(os.path.basename(path))[0]
    out_path = os.path.join(out_dir, f"{stem}-rigged.glb")
    blob = glb.to_bytes()

    # Parsed again from its own bytes before the envelope is emitted: the whole
    # point of appending rather than rebuilding is that the file stays valid,
    # and that is worth proving rather than assuming.
    try:
        check = Glb.parse(blob)
    except GltfError as exc:
        raise RigError("GLB_INVALID", f"the rigged file does not parse back: {exc}")
    if not check.gltf.get("skins"):
        raise RigError("GLB_INVALID", "the rigged file carries no skin")

    try:
        with open(out_path, "wb") as handle:
            handle.write(blob)
    except OSError as exc:
        raise RigError("OUTPUT_WRITE_FAILED", f"cannot write {out_path}", str(exc))

    result = {
        "contractVersion": CONTRACT_VERSION,
        "glb": {
            "uri": os.path.abspath(out_path),
            "mediaType": "model/gltf-binary",
            "byteSize": len(blob),
            "checksum": {"sha256": hashlib.sha256(blob).hexdigest()},
        },
        "skeleton": {
            "joints": len(names),
            "root": names[next(i for i, p in enumerate(parents) if p < 0)],
            "convention": "mixamo",
            "names": names,
        },
        "animations": [clip["name"] for clip in built],
        "maxInfluences": req["maxInfluences"],
        "timings": {"modelMs": model_ms},
        "elapsedMs": int((time.monotonic() - started) * 1000),
    }
    validate(result, load(RES_SCHEMA))
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description="skin a static GLB and give it clips")
    parser.add_argument("--glb", help="path to a static GLB")
    parser.add_argument("--request", help="path to a RigRequest JSON file, or - for stdin")
    parser.add_argument("--out-dir")
    parser.add_argument("--endpoint", default=os.environ.get("T2M_RIG", "http://127.0.0.1:8191"))
    parser.add_argument("--no-animate", action="store_true",
                        help="skin the mesh but add no clips")
    parser.add_argument("--max-influences", type=int, choices=[1, 2, 3, 4], default=4)
    parser.add_argument("--timeout", type=int, default=1800)
    args = parser.parse_args(argv)

    if args.request:
        raw = sys.stdin.read() if args.request == "-" else open(args.request, encoding="utf-8").read()
        request = json.loads(raw)
    elif args.glb:
        path = os.path.abspath(args.glb)
        try:
            with open(path, "rb") as handle:
                digest = hashlib.sha256(handle.read()).hexdigest()
        except OSError as exc:
            print(json.dumps(RigError("GLB_MISSING", f"no GLB at {path}", str(exc)).envelope(),
                             indent=2), file=sys.stderr)
            return 1
        request = {
            "glb": {"uri": path, "mediaType": "model/gltf-binary",
                    "checksum": {"sha256": digest}},
            "endpoint": args.endpoint,
            "animate": not args.no_animate,
            "maxInfluences": args.max_influences,
            "timeoutSeconds": args.timeout,
        }
        if args.out_dir:
            request["outDir"] = args.out_dir
    else:
        parser.error("one of --glb or --request is required")

    try:
        print(json.dumps(rig(request), indent=2))
    except RigError as exc:
        print(json.dumps(exc.envelope(), indent=2), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
