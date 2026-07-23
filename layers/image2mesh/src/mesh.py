#!/usr/bin/env python3
"""image2mesh blackbox: one image by reference in, one GLB by reference out.

Drives the Vulkan-only TRELLIS.2 engine. Three runners, same contract:

    docker   one-shot container, pays model load every call (default)
    server   POST to a resident t2m-server, model load already paid
    binary   a locally built t2m-cli, for development

    python3 mesh.py --image out/abc.png --out-dir out
    python3 mesh.py --request request.json

Prints a MeshResult envelope on stdout, or an error envelope on stderr with a
non-zero exit. See ../CONTRACT.md. Stdlib only.
"""

import argparse
import grp
import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from schema_check import SchemaError, load, validate, with_defaults  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
LAYER = os.path.dirname(HERE)
REQ_SCHEMA = os.path.join(LAYER, "schema", "mesh_request.json")
RES_SCHEMA = os.path.join(LAYER, "schema", "mesh_result.json")

CONTRACT_VERSION = "1.0"

GLB_MAGIC = 0x46546C67
CHUNK_JSON = 0x4E4F534A
CHUNK_BIN = 0x004E4942


class MeshError(Exception):
    def __init__(self, code, message, detail=""):
        super().__init__(message)
        self.code, self.message, self.detail = code, message, detail

    def envelope(self):
        env = {"contractVersion": CONTRACT_VERSION, "code": self.code, "message": self.message}
        if self.detail:
            env["detail"] = self.detail
        return env


# ---- GLB inspection ---------------------------------------------------------


def read_glb(path):
    """Parse a GLB far enough to prove a glTF loader can open it.

    Returns the parsed JSON chunk. Raises GLB_INVALID on anything a three.js
    GLTFLoader would choke on, so a broken file never leaves this layer wearing
    a success envelope.
    """
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError as exc:
        raise MeshError("GLB_INVALID", f"cannot read {path}", str(exc))

    if len(data) < 20:
        raise MeshError("GLB_INVALID", f"{path} is {len(data)} bytes, too short to be a GLB")
    magic, version, length = struct.unpack_from("<III", data, 0)
    if magic != GLB_MAGIC:
        raise MeshError("GLB_INVALID", "missing the glTF magic; this is not a GLB")
    if version != 2:
        raise MeshError("GLB_INVALID", f"glTF container version {version}, expected 2")
    if length != len(data):
        raise MeshError("GLB_INVALID",
                        f"header declares {length} bytes, file is {len(data)}")

    offset, gltf, saw_bin = 12, None, False
    while offset + 8 <= len(data):
        chunk_len, chunk_type = struct.unpack_from("<II", data, offset)
        body = data[offset + 8: offset + 8 + chunk_len]
        if len(body) != chunk_len:
            raise MeshError("GLB_INVALID", "a chunk runs past the end of the file")
        if chunk_type == CHUNK_JSON and gltf is None:
            try:
                gltf = json.loads(body.decode("utf-8"))
            except (ValueError, UnicodeDecodeError) as exc:
                raise MeshError("GLB_INVALID", "the JSON chunk does not parse", str(exc))
        elif chunk_type == CHUNK_BIN:
            saw_bin = True
        offset += 8 + chunk_len + (-chunk_len % 4)

    if gltf is None:
        raise MeshError("GLB_INVALID", "no JSON chunk")
    if not saw_bin:
        raise MeshError("GLB_INVALID", "no BIN chunk, so the accessors have no buffer")
    if not gltf.get("meshes"):
        raise MeshError("GLB_INVALID", "the glTF declares no meshes")
    return gltf


def geometry_of(gltf):
    """Triangle and attribute counts read out of the glTF, not guessed."""
    accessors = gltf.get("accessors", [])
    meshes = gltf.get("meshes", [])
    primitives = triangles = 0
    has_uv = has_normals = False

    for mesh in meshes:
        for prim in mesh.get("primitives", []):
            primitives += 1
            attrs = prim.get("attributes", {})
            has_uv = has_uv or "TEXCOORD_0" in attrs
            has_normals = has_normals or "NORMAL" in attrs
            mode = prim.get("mode", 4)          # 4 = TRIANGLES
            index = prim.get("indices")
            if index is None:
                index = attrs.get("POSITION")
                divisor = 3
            else:
                divisor = 3
            if index is not None and index < len(accessors) and mode == 4:
                triangles += accessors[index].get("count", 0) // divisor

    return {
        "meshes": len(meshes),
        "primitives": primitives,
        "triangles": triangles,
        "materials": len(gltf.get("materials", [])),
        "images": len(gltf.get("images", [])),
        "hasUv": has_uv,
        "hasNormals": has_normals,
        "extensionsUsed": gltf.get("extensionsUsed", []),
    }


# ---- request handling -------------------------------------------------------


def _check_image(ref):
    path = ref["uri"]
    if path.startswith("file://"):
        path = path[7:]
    if not os.path.isfile(path):
        raise MeshError("IMAGE_MISSING", f"no image at {path}")
    with open(path, "rb") as fh:
        digest = hashlib.sha256(fh.read()).hexdigest()
    if digest != ref["checksum"]["sha256"]:
        raise MeshError("CHECKSUM_MISMATCH",
                        "the image on disk does not match the checksum in the request",
                        f"want {ref['checksum']['sha256']}, got {digest}")
    return os.path.abspath(path)


def engine_flags(req):
    """MeshRequest -> t2m-cli flags. Order is stable so runs are comparable."""
    flags = ["--res", str(req["resolution"]),
             "--seed", str(req["seed"]),
             # A CPU fallback would take tens of minutes and silently break the
             # only promise this layer makes about how it runs.
             "--require-gpu"]
    if not req["texture"]:
        flags.append("--no-texture")
    if req["backgroundRemoval"] != "auto":
        flags += ["--bg-removal", req["backgroundRemoval"]]
    if req.get("textureResolution"):
        flags += ["--tex-res", str(req["textureResolution"])]
    if req.get("atlasPx"):
        flags += ["--atlas", str(req["atlasPx"])]
    if req.get("decimateFaces") is not None:
        flags += ["--decim", str(req["decimateFaces"])]
    if req.get("maxTokens"):
        flags += ["--max-tokens", str(req["maxTokens"])]
    if req.get("band"):
        flags += ["--band", str(req["band"])]
    if req["boxUv"]:
        flags.append("--box-uv")
    return flags


def _render_gids():
    gids = []
    for name in ("render", "video"):
        try:
            gids.append(str(grp.getgrnam(name).gr_gid))
        except KeyError:
            pass
    return gids


def run_docker(req, image_path, out_path, timeout):
    if not shutil.which("docker"):
        raise MeshError("ENGINE_FAILED", "docker is not on PATH")
    models = req["modelsDir"]
    if not os.path.isdir(models):
        raise MeshError("MODELS_MISSING", f"no model directory at {models}",
                        "fetch them with scripts/fetch-models.sh")

    in_dir, in_name = os.path.split(image_path)
    out_dir, out_name = os.path.split(out_path)
    cmd = ["docker", "run", "--rm", "--device", "/dev/dri"]
    for gid in _render_gids():
        cmd += ["--group-add", gid]
    cmd += [
        "-v", f"{models}:/models:ro",
        "-v", f"{in_dir}:/in:ro",
        "-v", f"{out_dir}:/out",
        "-u", f"{os.getuid()}:{os.getgid()}",
        req["dockerImage"], "cli",
        "--image", f"/in/{in_name}",
        "--output", f"/out/{out_name}",
        "--models", "/models",
    ] + engine_flags(req)

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise MeshError("TIMEOUT", f"the engine did not finish within {timeout}s")

    if proc.returncode == 78:
        code = "MODELS_MISSING" if "no weights" in proc.stderr else "NO_VULKAN_DEVICE"
        raise MeshError(code, proc.stderr.strip().splitlines()[0], proc.stderr[-800:])
    if proc.returncode != 0:
        raise MeshError("ENGINE_FAILED", f"t2m-cli exited {proc.returncode}",
                        (proc.stderr or proc.stdout)[-2000:])
    return proc.stderr


def run_binary(req, image_path, out_path, timeout):
    binary = req.get("binaryPath")
    if not binary or not os.path.isfile(binary):
        raise MeshError("INVALID_REQUEST", f"binaryPath does not point at a file: {binary}")
    cmd = [binary, "--image", image_path, "--output", out_path,
           "--models", req["modelsDir"]] + engine_flags(req)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise MeshError("TIMEOUT", f"the engine did not finish within {timeout}s")
    if proc.returncode != 0:
        raise MeshError("ENGINE_FAILED", f"t2m-cli exited {proc.returncode}",
                        (proc.stderr or proc.stdout)[-2000:])
    return proc.stderr


def run_server(req, image_path, out_path, timeout):
    endpoint = req["endpoint"].rstrip("/")
    boundary = "----t2m" + uuid.uuid4().hex
    with open(image_path, "rb") as fh:
        payload = fh.read()

    fields = {"seed": str(req["seed"]), "resolution": str(req["resolution"])}
    if req["backgroundRemoval"] != "auto":
        fields["bg_removal"] = req["backgroundRemoval"]

    body = b""
    for key, value in fields.items():
        body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{key}\"\r\n\r\n"
                 f"{value}\r\n").encode()
    body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"image\"; "
             f"filename=\"{os.path.basename(image_path)}\"\r\n"
             f"Content-Type: image/png\r\n\r\n").encode() + payload + b"\r\n"
    body += f"--{boundary}--\r\n".encode()

    request = urllib.request.Request(
        endpoint + "/generate", data=body, method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            glb = resp.read()
    except urllib.error.HTTPError as exc:
        raise MeshError("ENGINE_FAILED", f"t2m-server returned HTTP {exc.code}",
                        exc.read().decode("utf-8", "replace")[:800])
    except (urllib.error.URLError, OSError) as exc:
        raise MeshError("ENGINE_UNREACHABLE", f"no t2m-server at {endpoint}", str(exc))

    try:
        with open(out_path, "wb") as fh:
            fh.write(glb)
    except OSError as exc:
        raise MeshError("OUTPUT_WRITE_FAILED", f"cannot write {out_path}", str(exc))
    return ""


RUNNERS = {"docker": run_docker, "server": run_server, "binary": run_binary}


def generate(request):
    """Run one MeshRequest through the engine. Returns a validated MeshResult."""
    req_schema = load(REQ_SCHEMA)
    try:
        validate(request, req_schema)
    except SchemaError as exc:
        raise MeshError("INVALID_REQUEST", str(exc))
    req = with_defaults(request, req_schema)

    image_path = _check_image(req["image"])
    out_dir = req.get("outDir") or os.path.dirname(image_path)
    try:
        os.makedirs(out_dir, exist_ok=True)
    except OSError as exc:
        raise MeshError("OUTPUT_WRITE_FAILED", f"cannot create {out_dir}", str(exc))

    stem = os.path.splitext(os.path.basename(image_path))[0]
    out_path = os.path.abspath(os.path.join(out_dir, f"{stem}-r{req['resolution']}.glb"))

    started = time.monotonic()
    log = RUNNERS[req["runner"]](req, image_path, out_path, req["timeoutSeconds"])
    elapsed_ms = int((time.monotonic() - started) * 1000)

    if not os.path.isfile(out_path):
        raise MeshError("ENGINE_FAILED", "the engine exited cleanly but wrote no GLB",
                        (log or "")[-800:])

    gltf = read_glb(out_path)
    with open(out_path, "rb") as fh:
        blob = fh.read()

    engine = {"backend": "vulkan", "runner": req["runner"],
              "resolution": req["resolution"], "seed": req["seed"]}
    device = _device_from_log(log)
    if device:
        engine["device"] = device

    result = {
        "contractVersion": CONTRACT_VERSION,
        "glb": {
            "uri": out_path,
            "mediaType": "model/gltf-binary",
            "byteSize": len(blob),
            "checksum": {"sha256": hashlib.sha256(blob).hexdigest()},
        },
        "geometry": geometry_of(gltf),
        "engine": engine,
        "elapsedMs": elapsed_ms,
    }
    validate(result, load(RES_SCHEMA))
    return result


def _device_from_log(log):
    for line in (log or "").splitlines():
        if line.startswith("vulkan: "):
            return line.split("vulkan: ", 1)[1].strip()
    return None


def main(argv=None):
    parser = argparse.ArgumentParser(description="TRELLIS.2 image-to-GLB on Vulkan")
    parser.add_argument("--image", help="path to a PNG; its sha256 is computed here")
    parser.add_argument("--request", help="path to a MeshRequest JSON file, or - for stdin")
    parser.add_argument("--out-dir")
    parser.add_argument("--res", type=int, choices=[512, 1024, 1536], default=512)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--no-texture", action="store_true")
    parser.add_argument("--bg-removal", choices=["auto", "threshold", "birefnet"], default="auto")
    parser.add_argument("--runner", choices=["docker", "server", "binary"], default="docker")
    parser.add_argument("--docker-image", default="text-to-3d/engine:vulkan")
    parser.add_argument("--binary-path")
    parser.add_argument("--endpoint", default="http://127.0.0.1:8189")
    parser.add_argument("--models-dir",
                        default=os.environ.get("TRELLIS_MODELS", "/home/hec/models/gguf/trellis2"))
    parser.add_argument("--timeout", type=int, default=1800)
    args = parser.parse_args(argv)

    if args.request:
        raw = sys.stdin.read() if args.request == "-" else open(args.request, encoding="utf-8").read()
        request = json.loads(raw)
    elif args.image:
        path = os.path.abspath(args.image)
        try:
            with open(path, "rb") as fh:
                digest = hashlib.sha256(fh.read()).hexdigest()
        except OSError as exc:
            print(json.dumps(MeshError("IMAGE_MISSING", f"no image at {path}", str(exc)).envelope(),
                             indent=2), file=sys.stderr)
            return 1
        request = {
            "image": {"uri": path, "mediaType": "image/png",
                      "byteSize": os.path.getsize(path), "checksum": {"sha256": digest}},
            "resolution": args.res,
            "seed": args.seed,
            "texture": not args.no_texture,
            "backgroundRemoval": args.bg_removal,
            "runner": args.runner,
            "dockerImage": args.docker_image,
            "endpoint": args.endpoint,
            "modelsDir": args.models_dir,
            "timeoutSeconds": args.timeout,
        }
        if args.out_dir:
            request["outDir"] = args.out_dir
        if args.binary_path:
            request["binaryPath"] = args.binary_path
    else:
        parser.error("one of --image or --request is required")

    try:
        print(json.dumps(generate(request), indent=2))
    except MeshError as exc:
        print(json.dumps(exc.envelope(), indent=2), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
