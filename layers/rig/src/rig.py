#!/usr/bin/env python3
"""rig blackbox: a GLB by reference in, a rigged and animated GLB by reference out.

Drives Blender in background mode. Two subjects, two different answers:

    humanoid   a measured skeleton, bone-heat skinning, locomotion clips
    prop       no armature; TRS clips on the node plus an optional socket

    python3 rig.py --glb out/hero-r512.glb --subject humanoid --out-dir out
    python3 rig.py --request request.json

Prints a RigResult envelope on stdout, or an error envelope on stderr with a
non-zero exit. See ../CONTRACT.md. Stdlib only.
"""

import argparse
import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from schema_check import SchemaError, load, validate, with_defaults  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
LAYER = os.path.dirname(HERE)
REQ_SCHEMA = os.path.join(LAYER, "schema", "rig_request.json")
RES_SCHEMA = os.path.join(LAYER, "schema", "rig_result.json")
BLENDER_SCRIPT = os.path.join(HERE, "blender_rig.py")

CONTRACT_VERSION = "1.2"

GLB_MAGIC = 0x46546C67
CHUNK_JSON = 0x4E4F534A
CHUNK_BIN = 0x004E4942

# Where Blender lives when it was not installed through the package manager.
BLENDER_CANDIDATES = (
    os.environ.get("BLENDER", ""),
    "/home/hec/opt/blender-5.2.0-linux-x64/blender",
    "blender",
)

# Blender in a container, so the host needs Docker and nothing else. Built by
# docker/Dockerfile, and tagged by Blender's own version rather than by this
# layer because layers/assets runs its glTF conversion in the same image.
DOCKER_IMAGE = "text-to-3d/blender:5.2"


class RigError(Exception):
    def __init__(self, code, message, detail=""):
        super().__init__(message)
        self.code, self.message, self.detail = code, message, detail

    def envelope(self):
        env = {"contractVersion": CONTRACT_VERSION, "code": self.code, "message": self.message}
        if self.detail:
            env["detail"] = self.detail
        return env


def _executable(candidate):
    found = candidate if os.path.isfile(candidate) else shutil.which(candidate)
    return found if found and os.access(found, os.X_OK) else None


def find_blender(explicit=None):
    # An explicit path that is wrong is a mistake worth reporting, not something
    # to paper over by silently running a different Blender.
    if explicit:
        found = _executable(explicit)
        if not found:
            raise RigError("BLENDER_MISSING", f"no Blender executable at {explicit}")
        return found
    for candidate in BLENDER_CANDIDATES:
        found = _executable(candidate) if candidate else None
        if found:
            return found
    raise RigError("BLENDER_MISSING", "no Blender executable found",
                   "run with --runner docker, or install Blender 5.x, "
                   "or pass blenderPath, or set BLENDER")


def _docker_available(image):
    """True when Docker answers and the Blender image is already built."""
    if not _executable("docker"):
        return False
    probe = subprocess.run(["docker", "image", "inspect", image],
                           capture_output=True, text=True)
    return probe.returncode == 0


def resolve_runner(requested, explicit_path, image):
    """How Blender will be reached: ("binary", path) or ("docker", image).

    `auto` prefers a local binary, because a subprocess starts faster than a
    container and needs no image built, then falls back to the container rather
    than failing. That fallback is the case that matters: a fresh clone already
    has Docker for the mesh engine and no Blender at all.
    """
    if requested == "binary" or explicit_path:
        return "binary", find_blender(explicit_path)
    if requested == "docker":
        if not _executable("docker"):
            raise RigError("BLENDER_MISSING", "runner is docker but there is no docker on PATH")
        if not _docker_available(image):
            raise RigError("BLENDER_MISSING", f"the image {image} is not built",
                           f"cd layers/rig && docker build -f docker/Dockerfile -t {image} .")
        return "docker", image
    for candidate in BLENDER_CANDIDATES:
        found = _executable(candidate) if candidate else None
        if found:
            return "binary", found
    if _docker_available(image):
        return "docker", image
    raise RigError("BLENDER_MISSING",
                   "no Blender on this machine and no Blender image built",
                   f"cd layers/rig && docker build -f docker/Dockerfile -t {image} .")


# ---- GLB inspection ---------------------------------------------------------


def read_glb(path):
    """Parse a GLB far enough to prove a glTF loader can open it."""
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError as exc:
        raise RigError("GLB_INVALID", f"cannot read {path}", str(exc))

    if len(data) < 20:
        raise RigError("GLB_INVALID", f"{path} is {len(data)} bytes, too short to be a GLB")
    magic, version, length = struct.unpack_from("<III", data, 0)
    if magic != GLB_MAGIC:
        raise RigError("GLB_INVALID", "missing the glTF magic; this is not a GLB")
    if version != 2:
        raise RigError("GLB_INVALID", f"glTF container version {version}, expected 2")
    if length != len(data):
        raise RigError("GLB_INVALID", f"header declares {length} bytes, file is {len(data)}")

    offset, gltf, saw_bin = 12, None, False
    while offset + 8 <= len(data):
        chunk_len, chunk_type = struct.unpack_from("<II", data, offset)
        body = data[offset + 8: offset + 8 + chunk_len]
        if len(body) != chunk_len:
            raise RigError("GLB_INVALID", "a chunk runs past the end of the file")
        if chunk_type == CHUNK_JSON and gltf is None:
            try:
                gltf = json.loads(body.decode("utf-8"))
            except (ValueError, UnicodeDecodeError) as exc:
                raise RigError("GLB_INVALID", "the JSON chunk does not parse", str(exc))
        elif chunk_type == CHUNK_BIN:
            saw_bin = True
        offset += 8 + chunk_len + (-chunk_len % 4)

    if gltf is None:
        raise RigError("GLB_INVALID", "no JSON chunk")
    if not saw_bin:
        raise RigError("GLB_INVALID", "no BIN chunk, so the accessors have no buffer")
    if not gltf.get("meshes"):
        raise RigError("GLB_INVALID", "the glTF declares no meshes")
    return gltf


def rig_summary(gltf, subject):
    """What the written file actually contains, read back out of it.

    Every number here comes from the export, never from what the job was asked
    to do, so a clip that failed to author cannot be reported as present.
    """
    skins = gltf.get("skins", [])
    animations = gltf.get("animations", [])
    joints = len(skins[0].get("joints", [])) if skins else 0
    channels = sum(len(a.get("channels", [])) for a in animations)

    if subject == "humanoid":
        if not skins:
            raise RigError("RIG_FAILED", "the exported GLB has no skin, so nothing is rigged")
        if not any("JOINTS_0" in p.get("attributes", {})
                   for m in gltf.get("meshes", []) for p in m.get("primitives", [])):
            raise RigError("RIG_FAILED", "no mesh primitive carries JOINTS_0 skinning data")
    return {
        "skins": len(skins),
        "joints": joints,
        "animations": [a.get("name", "") for a in animations],
        "animationChannels": channels,
        "nodes": len(gltf.get("nodes", [])),
    }


# ---- the job ----------------------------------------------------------------


def _check_glb(ref):
    path = ref["uri"]
    if path.startswith("file://"):
        path = path[7:]
    if not os.path.isfile(path):
        raise RigError("GLB_MISSING", f"no GLB at {path}")
    with open(path, "rb") as fh:
        digest = hashlib.sha256(fh.read()).hexdigest()
    if "checksum" in ref and digest != ref["checksum"]["sha256"]:
        raise RigError("CHECKSUM_MISMATCH",
                       "the GLB on disk does not match the checksum in the request",
                       f"want {ref['checksum']['sha256']}, got {digest}")
    return os.path.abspath(path)


DEFAULT_CLIPS = {"humanoid": ["idle", "walk", "run", "jump"], "prop": ["spin"]}
VALID_CLIPS = {"humanoid": {"idle", "walk", "run", "jump"}, "prop": {"spin", "bob"}}


def rig(request):
    """Run one RigRequest through Blender. Returns a validated RigResult."""
    req_schema = load(REQ_SCHEMA)
    try:
        validate(request, req_schema)
    except SchemaError as exc:
        raise RigError("INVALID_REQUEST", str(exc))
    req = with_defaults(request, req_schema)

    subject = req["subject"]
    clips = req.get("animations") or DEFAULT_CLIPS[subject]
    unknown = [c for c in clips if c not in VALID_CLIPS[subject]]
    if unknown:
        raise RigError("INVALID_REQUEST",
                       f"{subject} has no clip named {unknown[0]}",
                       f"available: {sorted(VALID_CLIPS[subject])}")

    glb_path = _check_glb(req["glb"])
    runner, blender = resolve_runner(req.get("runner", "auto"),
                                     req.get("blenderPath"),
                                     req.get("dockerImage") or DOCKER_IMAGE)

    out_dir = req.get("outDir") or os.path.dirname(glb_path)
    try:
        os.makedirs(out_dir, exist_ok=True)
    except OSError as exc:
        raise RigError("OUTPUT_WRITE_FAILED", f"cannot create {out_dir}", str(exc))

    stem = os.path.splitext(os.path.basename(glb_path))[0]
    out_path = os.path.abspath(os.path.join(out_dir, f"{stem}-rigged.glb"))

    with tempfile.TemporaryDirectory(prefix="t2m-rig-") as work:
        job_path = os.path.join(work, "job.json")
        report_path = os.path.join(work, "report.json")

        if runner == "docker":
            # Everything the run touches is staged into one directory mounted at
            # /work, so the job file can name container paths and nothing has to
            # translate a host path inside the container. The input is copied
            # rather than bind-mounted because it can live anywhere on the host,
            # and mounting arbitrary parent directories into a container to
            # reach one file is a much larger hole than a copy.
            staged_glb = os.path.join(work, "input.glb")
            shutil.copyfile(glb_path, staged_glb)
            job = {
                "glb": "/work/input.glb",
                "out": "/work/output.glb",
                "outJson": "/work/report.json",
                "subject": subject,
                "animations": clips,
                "socket": req.get("socket"),
                "nodeName": stem,
            }
        else:
            job = {
                "glb": glb_path,
                "out": out_path,
                "outJson": report_path,
                "subject": subject,
                "animations": clips,
                "socket": req.get("socket"),
                "nodeName": stem,
            }
        with open(job_path, "w", encoding="utf-8") as fh:
            json.dump(job, fh)

        if runner == "docker":
            cmd = [
                "docker", "run", "--rm",
                # As the caller, so the GLB that comes back is theirs and not
                # root's. Blender then needs a writable HOME, which the image
                # already points at /tmp.
                "--user", f"{os.getuid()}:{os.getgid()}",
                "--network", "none",
                "-v", f"{work}:/work",
                "-v", f"{HERE}:/scripts:ro",
                blender,
                "--background", "--factory-startup", "--python-exit-code", "1",
                "--python", "/scripts/blender_rig.py", "--", "/work/job.json",
            ]
        else:
            cmd = [blender, "--background", "--factory-startup",
                   "--python-exit-code", "1", "--python", BLENDER_SCRIPT, "--", job_path]

        started = time.monotonic()
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True,
                                  timeout=req["timeoutSeconds"])
        except subprocess.TimeoutExpired:
            raise RigError("TIMEOUT", f"Blender did not finish within {req['timeoutSeconds']}s")
        elapsed_ms = int((time.monotonic() - started) * 1000)

        # The container wrote into the staging directory; lift the result out to
        # where the caller asked for it.
        if runner == "docker":
            produced = os.path.join(work, "output.glb")
            if os.path.isfile(produced):
                shutil.copyfile(produced, out_path)

        report = None
        if os.path.isfile(report_path):
            try:
                with open(report_path, encoding="utf-8") as fh:
                    report = json.load(fh)
            except ValueError:
                report = None

        if report is None:
            raise RigError("BLENDER_FAILED",
                           f"Blender exited {proc.returncode} without writing a report",
                           (proc.stderr or proc.stdout)[-2000:])
        if not report.get("ok"):
            message = report.get("error", "the rig script failed")
            code = "RIG_FAILED" if "bone heat" in message or "weight" in message \
                else "BLENDER_FAILED"
            raise RigError(code, message, (proc.stderr or "")[-1200:])

    if not os.path.isfile(out_path):
        raise RigError("BLENDER_FAILED", "the rig script reported success but wrote no GLB")

    gltf = read_glb(out_path)
    inner = report["result"]
    summary = rig_summary(gltf, subject)

    with open(out_path, "rb") as fh:
        blob = fh.read()

    result = {
        "contractVersion": CONTRACT_VERSION,
        "glb": {
            "uri": out_path,
            "mediaType": "model/gltf-binary",
            "byteSize": len(blob),
            "checksum": {"sha256": hashlib.sha256(blob).hexdigest()},
        },
        "subject": subject,
        "skeleton": {
            "bones": inner.get("bones", []),
            "joints": summary["joints"],
            "naming": "mixamo" if subject == "humanoid" else "none",
        },
        "animations": inner.get("animations", []),
        "geometry": {"vertices": inner.get("vertices", 0), "faces": inner.get("faces", 0)},
        "engine": {"blender": inner.get("blender", ""),
                   "runner": runner,
                   "binding": "bone-heat" if subject == "humanoid" else "none"},
        "elapsedMs": elapsed_ms,
    }
    if "weightedVertices" in inner:
        result["skeleton"]["weightedVertices"] = inner["weightedVertices"]
    if "socket" in inner:
        result["socket"] = inner["socket"]
    # A humanoid always reports on the pose it was handed, even when there is
    # nothing wrong with it: an empty list is the useful answer to "did anyone
    # look?", and its absence would be indistinguishable from an old result.
    if subject == "humanoid":
        result["poseWarnings"] = inner.get("pose", [])

    # The clips the file has, not the clips that were asked for.
    exported = set(summary["animations"])
    result["animations"] = [a for a in result["animations"] if a["name"] in exported]
    if len(result["animations"]) != len(clips):
        missing = sorted(set(clips) - exported)
        raise RigError("RIG_FAILED", f"the export is missing the clips {missing}",
                       f"exported: {sorted(exported)}")

    validate(result, load(RES_SCHEMA))
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description="skeleton, skinning and clips for a GLB")
    parser.add_argument("--glb", help="path to a GLB; its sha256 is computed here")
    parser.add_argument("--request", help="path to a RigRequest JSON file, or - for stdin")
    parser.add_argument("--subject", choices=["humanoid", "prop"], default="humanoid")
    parser.add_argument("--animations", help="comma separated clip names")
    parser.add_argument("--socket", help="prop only: name an attachment empty")
    parser.add_argument("--out-dir")
    parser.add_argument("--blender-path")
    parser.add_argument("--runner", choices=["auto", "binary", "docker"], default="auto",
                        help="how to reach Blender. auto prefers a host binary and falls "
                             "back to the container")
    parser.add_argument("--docker-image", help=f"override the Blender image (default {DOCKER_IMAGE})")
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--glb-path-only", action="store_true")
    args = parser.parse_args(argv)

    if args.request:
        raw = sys.stdin.read() if args.request == "-" else open(args.request, encoding="utf-8").read()
        request = json.loads(raw)
    elif args.glb:
        path = os.path.abspath(args.glb)
        try:
            with open(path, "rb") as fh:
                digest = hashlib.sha256(fh.read()).hexdigest()
        except OSError as exc:
            print(json.dumps(RigError("GLB_MISSING", f"no GLB at {path}", str(exc)).envelope(),
                             indent=2), file=sys.stderr)
            return 1
        request = {
            "glb": {"uri": path, "mediaType": "model/gltf-binary",
                    "byteSize": os.path.getsize(path), "checksum": {"sha256": digest}},
            "subject": args.subject,
            "timeoutSeconds": args.timeout,
        }
        if args.animations:
            request["animations"] = [c.strip() for c in args.animations.split(",") if c.strip()]
        if args.out_dir:
            request["outDir"] = args.out_dir
        if args.blender_path:
            request["blenderPath"] = args.blender_path
        if args.runner != "auto":
            request["runner"] = args.runner
        if args.docker_image:
            request["dockerImage"] = args.docker_image
        if args.socket:
            request["socket"] = args.socket
    else:
        parser.error("one of --glb or --request is required")

    try:
        result = rig(request)
    except RigError as exc:
        print(json.dumps(exc.envelope(), indent=2), file=sys.stderr)
        return 1

    print(result["glb"]["uri"] if args.glb_path_only else json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
