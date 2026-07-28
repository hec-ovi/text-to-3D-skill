#!/usr/bin/env python3
"""comfy blackbox: one PNG inline in, one GLB by reference out.

Talks to the resident Vulkan `t2m-server` over HTTP and nothing else. Stdlib
only, and deliberately free of torch and numpy so the contract tests run on a
machine with neither.

    python3 client.py --image render.png --out-dir out
    python3 client.py --request request.json

Prints a MeshNodeResult on stdout, or an error envelope on stderr with a
non-zero exit. See ../CONTRACT.md.

This layer carries its own multipart POST and its own GLB reader rather than
importing image2mesh. That duplication is the price of the boundary: a ComfyUI
worker process has no reason to load a Docker driver, and image2mesh has no
reason to know a graph exists.
"""

import argparse
import base64
import binascii
import hashlib
import json
import os
import re
import struct
import sys
import time
import urllib.error
import urllib.request
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from schema_check import SchemaError, load, validate, with_defaults  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
LAYER = os.path.dirname(HERE)
REQ_SCHEMA = os.path.join(LAYER, "schema", "mesh_node_request.json")
RES_SCHEMA = os.path.join(LAYER, "schema", "mesh_node_result.json")

CONTRACT_VERSION = "1.0"

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
GLB_MAGIC = 0x46546C67
CHUNK_JSON = 0x4E4F534A

# The name reaches a file system and a URL query, so it is folded to the same
# alphabet the preview layer accepts as an id.
UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


class NodeError(Exception):
    def __init__(self, code, message, detail=""):
        super().__init__(message)
        self.code, self.message, self.detail = code, message, detail

    def envelope(self):
        env = {"contractVersion": CONTRACT_VERSION, "code": self.code, "message": self.message}
        if self.detail:
            env["detail"] = self.detail
        return env


def read_glb(data):
    """Parse a GLB far enough to prove a loader can open it. Returns its JSON chunk.

    A second, smaller reader than the one in image2mesh, on purpose: this layer
    does not import that one, and only needs enough to refuse a broken file and
    count its triangles.
    """
    if len(data) < 20:
        raise NodeError("GLB_INVALID", f"the engine returned {len(data)} bytes, too short to be a GLB")
    magic, version, length = struct.unpack_from("<III", data, 0)
    if magic != GLB_MAGIC:
        raise NodeError("GLB_INVALID", "missing the glTF magic; this is not a GLB")
    if version != 2:
        raise NodeError("GLB_INVALID", f"glTF container version {version}, expected 2")
    if length != len(data):
        raise NodeError("GLB_INVALID", f"header declares {length} bytes, body is {len(data)}")

    offset, gltf = 12, None
    while offset + 8 <= len(data):
        chunk_len, chunk_type = struct.unpack_from("<II", data, offset)
        body = data[offset + 8: offset + 8 + chunk_len]
        if len(body) != chunk_len:
            raise NodeError("GLB_INVALID", "a chunk runs past the end of the file")
        if chunk_type == CHUNK_JSON and gltf is None:
            try:
                gltf = json.loads(body.decode("utf-8"))
            except (ValueError, UnicodeDecodeError) as exc:
                raise NodeError("GLB_INVALID", "the JSON chunk does not parse", str(exc))
        offset += 8 + chunk_len + (-chunk_len % 4)

    if gltf is None:
        raise NodeError("GLB_INVALID", "no JSON chunk")
    if not gltf.get("meshes"):
        raise NodeError("GLB_INVALID", "the glTF declares no meshes")
    return gltf


def triangles_of(gltf):
    accessors = gltf.get("accessors", [])
    total = 0
    for mesh in gltf.get("meshes", []):
        for prim in mesh.get("primitives", []):
            if prim.get("mode", 4) != 4:          # 4 = TRIANGLES
                continue
            index = prim.get("indices", prim.get("attributes", {}).get("POSITION"))
            if index is not None and index < len(accessors):
                total += accessors[index].get("count", 0) // 3
    return total


def _decode_image(ref):
    try:
        data = base64.b64decode(ref["data"], validate=True)
    except (binascii.Error, ValueError) as exc:
        raise NodeError("INVALID_REQUEST", "the inline image is not valid base64", str(exc))
    if not data.startswith(PNG_MAGIC):
        raise NodeError("INVALID_REQUEST", "the inline image is not a PNG")
    if ref.get("byteSize") and ref["byteSize"] != len(data):
        raise NodeError("INVALID_REQUEST",
                        "byteSize does not match the decoded image",
                        f"declared {ref['byteSize']}, decoded {len(data)}")
    return data


def _multipart(fields, filename, payload):
    """A multipart/form-data body. Returns (content_type, body)."""
    boundary = "----t2m" + uuid.uuid4().hex
    body = b""
    for key, value in fields.items():
        body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{key}\"\r\n\r\n"
                 f"{value}\r\n").encode()
    body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"image\"; "
             f"filename=\"{filename}\"\r\nContent-Type: image/png\r\n\r\n").encode()
    body += payload + b"\r\n"
    body += f"--{boundary}--\r\n".encode()
    return f"multipart/form-data; boundary={boundary}", body


def generate(request):
    """Run one MeshNodeRequest through the resident engine. Returns a MeshNodeResult."""
    req_schema = load(REQ_SCHEMA)
    try:
        validate(request, req_schema)
    except SchemaError as exc:
        raise NodeError("INVALID_REQUEST", str(exc))
    req = with_defaults(request, req_schema)

    png = _decode_image(req["image"])
    out_dir = os.path.abspath(req.get("outDir") or os.getcwd())
    try:
        os.makedirs(out_dir, exist_ok=True)
    except OSError as exc:
        raise NodeError("OUTPUT_WRITE_FAILED", f"cannot create {out_dir}", str(exc))

    fields = {"seed": str(req["seed"]), "resolution": str(req["resolution"])}
    if req["backgroundRemoval"] != "auto":
        fields["bg_removal"] = req["backgroundRemoval"]
    if req.get("targetFaces"):
        fields["target_faces"] = str(req["targetFaces"])

    endpoint = req["endpoint"].rstrip("/")
    content_type, body = _multipart(fields, "render.png", png)
    http = urllib.request.Request(endpoint + "/generate", data=body, method="POST",
                                  headers={"Content-Type": content_type})

    started = time.monotonic()
    try:
        with urllib.request.urlopen(http, timeout=req["timeoutSeconds"]) as response:
            glb = response.read()
    except urllib.error.HTTPError as exc:
        raise NodeError("ENGINE_FAILED", f"t2m-server returned HTTP {exc.code}",
                        exc.read().decode("utf-8", "replace")[:800])
    except TimeoutError:
        raise NodeError("TIMEOUT", f"no GLB within {req['timeoutSeconds']}s")
    except (urllib.error.URLError, OSError) as exc:
        # A socket timeout arrives wrapped in URLError, so the distinction
        # between "too slow" and "not there" has to be made on the reason.
        if isinstance(getattr(exc, "reason", None), TimeoutError):
            raise NodeError("TIMEOUT", f"no GLB within {req['timeoutSeconds']}s")
        raise NodeError("ENGINE_UNREACHABLE", f"no t2m-server at {endpoint}", str(exc))
    elapsed_ms = int((time.monotonic() - started) * 1000)

    if not glb:
        raise NodeError("ENGINE_FAILED", "the engine answered with no bytes")

    # Parsed before it is written, so a broken file never reaches the gallery
    # wearing a success envelope.
    gltf = read_glb(glb)

    digest = hashlib.sha256(glb).hexdigest()
    stem = UNSAFE.sub("-", req["name"]).strip("-.") or "comfy"
    path = os.path.join(out_dir, f"{stem}-{digest[:8]}-r{req['resolution']}.glb")
    try:
        with open(path, "wb") as handle:
            handle.write(glb)
    except OSError as exc:
        raise NodeError("OUTPUT_WRITE_FAILED", f"cannot write {path}", str(exc))

    result = {
        "contractVersion": CONTRACT_VERSION,
        "glb": {
            "uri": os.path.abspath(path),
            "mediaType": "model/gltf-binary",
            "byteSize": len(glb),
            "checksum": {"sha256": digest},
        },
        "triangles": triangles_of(gltf),
        "engine": {"endpoint": endpoint, "resolution": req["resolution"], "seed": req["seed"]},
        "elapsedMs": elapsed_ms,
    }
    validate(result, load(RES_SCHEMA))
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description="resident TRELLIS.2 engine, called over HTTP")
    parser.add_argument("--image", help="path to a PNG; it is inlined into the request")
    parser.add_argument("--request", help="path to a MeshNodeRequest JSON file, or - for stdin")
    parser.add_argument("--out-dir", default=os.getcwd())
    parser.add_argument("--name")
    parser.add_argument("--res", type=int, choices=[512, 1024, 1536], default=512)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--target-faces", type=int)
    parser.add_argument("--bg-removal", choices=["auto", "threshold", "birefnet"], default="auto")
    parser.add_argument("--endpoint", default=os.environ.get("T2M_ENGINE", "http://127.0.0.1:8189"))
    parser.add_argument("--timeout", type=int, default=1800)
    args = parser.parse_args(argv)

    if args.request:
        raw = sys.stdin.read() if args.request == "-" else open(args.request, encoding="utf-8").read()
        request = json.loads(raw)
    elif args.image:
        try:
            with open(args.image, "rb") as handle:
                payload = handle.read()
        except OSError as exc:
            print(json.dumps(NodeError("INVALID_REQUEST", f"cannot read {args.image}",
                                       str(exc)).envelope(), indent=2), file=sys.stderr)
            return 1
        request = {
            "image": {
                "data": base64.b64encode(payload).decode("ascii"),
                "contentEncoding": "base64",
                "contentMediaType": "image/png",
                "byteSize": len(payload),
            },
            "name": args.name or os.path.splitext(os.path.basename(args.image))[0],
            "outDir": args.out_dir,
            "resolution": args.res,
            "seed": args.seed,
            "backgroundRemoval": args.bg_removal,
            "endpoint": args.endpoint,
            "timeoutSeconds": args.timeout,
        }
        if args.target_faces:
            request["targetFaces"] = args.target_faces
    else:
        parser.error("one of --image or --request is required")

    try:
        print(json.dumps(generate(request), indent=2))
    except NodeError as exc:
        print(json.dumps(exc.envelope(), indent=2), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
