#!/usr/bin/env python3
"""pipeline blackbox: a sentence in, a GLB out.

Runs text2image then image2mesh. Each stage is invoked as its own process and
talks back in its own envelope; this layer reads those envelopes and never
imports anything from either one.

    python3 pipeline.py --prompt "a brass diving helmet" --out-dir out
    python3 pipeline.py --request request.json

Prints a TextToMeshResult on stdout, or an error envelope on stderr with a
non-zero exit. See ../CONTRACT.md. Stdlib only.
"""

import argparse
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from schema_check import SchemaError, load, validate, with_defaults  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
LAYER = os.path.dirname(HERE)
LAYERS = os.path.dirname(LAYER)
REQ_SCHEMA = os.path.join(LAYER, "schema", "text_to_mesh_request.json")
RES_SCHEMA = os.path.join(LAYER, "schema", "text_to_mesh_result.json")

TEXT2IMAGE = os.path.join(LAYERS, "text2image", "src", "klein.py")
IMAGE2MESH = os.path.join(LAYERS, "image2mesh", "src", "mesh.py")

CONTRACT_VERSION = "1.0"


class PipelineError(Exception):
    def __init__(self, code, message, stage=None, cause=None, detail=""):
        super().__init__(message)
        self.code, self.message, self.stage, self.cause, self.detail = (
            code, message, stage, cause, detail)

    def envelope(self):
        env = {"contractVersion": CONTRACT_VERSION, "code": self.code, "message": self.message}
        if self.stage:
            env["stage"] = self.stage
        if self.cause is not None:
            env["cause"] = self.cause
        if self.detail:
            env["detail"] = self.detail
        return env


def run_stage(script, request, stage, timeout, failure_code):
    """Invoke a layer CLI with an envelope on stdin and read its envelope back."""
    try:
        proc = subprocess.run(
            [sys.executable, script, "--request", "-"],
            input=json.dumps(request), capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise PipelineError("TIMEOUT", f"{stage} did not finish within {timeout}s", stage=stage)

    if proc.returncode != 0:
        cause = None
        if proc.stderr.strip():
            try:
                cause = json.loads(proc.stderr)
            except ValueError:
                cause = None
        raise PipelineError(failure_code, f"{stage} failed", stage=stage, cause=cause,
                            detail="" if cause else (proc.stderr or proc.stdout)[-1500:])

    try:
        return json.loads(proc.stdout)
    except ValueError:
        raise PipelineError("CONTRACT_VIOLATION", f"{stage} exited 0 without a JSON envelope",
                            stage=stage, detail=proc.stdout[-800:])


def generate(request):
    """Run one TextToMeshRequest end to end. Returns a validated TextToMeshResult."""
    req_schema = load(REQ_SCHEMA)
    try:
        validate(request, req_schema)
    except SchemaError as exc:
        raise PipelineError("INVALID_REQUEST", str(exc))
    req = with_defaults(request, req_schema)
    out_dir = os.path.abspath(req.get("outDir") or os.getcwd())

    started = time.monotonic()

    image_request = {
        "prompt": req["prompt"],
        "width": req["imageSize"],
        "height": req["imageSize"],
        "steps": req["steps"],
        "endpoint": req["comfyEndpoint"],
        "outDir": out_dir,
    }
    if "seed" in req:
        image_request["seed"] = req["seed"]

    image_result = run_stage(TEXT2IMAGE, image_request, "text2image",
                             req["timeoutSeconds"], "TEXT2IMAGE_FAILED")
    if "image" not in image_result:
        raise PipelineError("CONTRACT_VIOLATION", "text2image returned no image reference",
                            stage="text2image")

    # The seam: an ImageResult.image is shape-compatible with a MeshRequest.image.
    # Nothing is recomputed here, so a checksum drift between the stages surfaces
    # as CHECKSUM_MISMATCH inside image2mesh rather than being papered over.
    mesh_request = {
        "image": {k: v for k, v in image_result["image"].items()
                  if k in ("uri", "mediaType", "byteSize", "checksum")},
        "resolution": req["resolution"],
        "texture": req["texture"],
        "backgroundRemoval": req["backgroundRemoval"],
        "seed": image_result.get("seed", 42) % 2147483647,
        "runner": req["runner"],
        "endpoint": req["engineEndpoint"],
        "modelsDir": req["modelsDir"],
        "outDir": out_dir,
        "timeoutSeconds": req["timeoutSeconds"],
    }
    if req.get("enginePath"):
        mesh_request["binaryPath"] = req["enginePath"]

    mesh_result = run_stage(IMAGE2MESH, mesh_request, "image2mesh",
                            req["timeoutSeconds"] + 60, "IMAGE2MESH_FAILED")
    if "glb" not in mesh_result:
        raise PipelineError("CONTRACT_VIOLATION", "image2mesh returned no glb reference",
                            stage="image2mesh")

    if not req["keepImage"]:
        try:
            os.remove(image_result["image"]["uri"])
        except OSError:
            pass

    result = {
        "contractVersion": CONTRACT_VERSION,
        "prompt": req["prompt"],
        "glb": mesh_result["glb"],
        "triangles": mesh_result.get("geometry", {}).get("triangles", 0),
        "stages": {"text2image": image_result, "image2mesh": mesh_result},
        "timings": {
            "imageMs": image_result.get("elapsedMs", 0),
            "meshMs": mesh_result.get("elapsedMs", 0),
        },
        "elapsedMs": int((time.monotonic() - started) * 1000),
    }
    validate(result, load(RES_SCHEMA))
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description="text to GLB: FLUX.2 klein then TRELLIS.2")
    parser.add_argument("--prompt")
    parser.add_argument("--request", help="path to a TextToMeshRequest JSON file, or - for stdin")
    parser.add_argument("--out-dir", default=os.getcwd())
    parser.add_argument("--seed", type=int)
    parser.add_argument("--res", type=int, choices=[512, 1024, 1536], default=512)
    parser.add_argument("--steps", type=int, default=4)
    parser.add_argument("--image-size", type=int, default=1024)
    parser.add_argument("--no-texture", action="store_true")
    parser.add_argument("--bg-removal", choices=["auto", "threshold", "birefnet"], default="auto")
    parser.add_argument("--drop-image", action="store_true",
                        help="delete the intermediate PNG once the GLB is written")
    parser.add_argument("--comfy", default=os.environ.get("COMFY_URL", "http://127.0.0.1:8188"))
    parser.add_argument("--runner", choices=["docker", "server", "binary"], default="docker")
    parser.add_argument("--engine-endpoint", default="http://127.0.0.1:8189")
    parser.add_argument("--engine-path", help="local t2m-cli, for --runner binary")
    parser.add_argument("--models-dir",
                        default=os.environ.get("TRELLIS_MODELS", "/home/hec/models/gguf/trellis2"))
    parser.add_argument("--timeout", type=int, default=2400)
    parser.add_argument("--glb-path-only", action="store_true",
                        help="print just the GLB path instead of the envelope")
    args = parser.parse_args(argv)

    if args.request:
        raw = sys.stdin.read() if args.request == "-" else open(args.request, encoding="utf-8").read()
        request = json.loads(raw)
    elif args.prompt:
        request = {
            "prompt": args.prompt,
            "outDir": args.out_dir,
            "resolution": args.res,
            "steps": args.steps,
            "imageSize": args.image_size,
            "texture": not args.no_texture,
            "backgroundRemoval": args.bg_removal,
            "keepImage": not args.drop_image,
            "comfyEndpoint": args.comfy,
            "runner": args.runner,
            "engineEndpoint": args.engine_endpoint,
            "modelsDir": args.models_dir,
            "timeoutSeconds": args.timeout,
        }
        if args.seed is not None:
            request["seed"] = args.seed
        if args.engine_path:
            request["enginePath"] = args.engine_path
    else:
        parser.error("one of --prompt or --request is required")

    try:
        result = generate(request)
    except PipelineError as exc:
        print(json.dumps(exc.envelope(), indent=2), file=sys.stderr)
        return 1

    print(result["glb"]["uri"] if args.glb_path_only else json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
