#!/usr/bin/env python3
"""Start and verify the static text-to-3D toolkit."""

import argparse
import collections
import json
import os
import shlex
import subprocess
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from schema_check import SchemaError, load, validate, with_defaults  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
LAYER = os.path.dirname(HERE)
ROOT = os.path.abspath(os.path.join(LAYER, "..", ".."))
REQUEST_SCHEMA = os.path.join(LAYER, "schema", "init_request.json")
RESULT_SCHEMA = os.path.join(LAYER, "schema", "init_result.json")
ERROR_SCHEMA = os.path.join(LAYER, "schema", "error.json")
CONTRACT_VERSION = "1.0"

MODEL_FILES = (
    "birefnet.gguf",
    "dinov3.gguf",
    "shape_dec.gguf",
    "shape_flow_1024.gguf",
    "shape_flow_512.gguf",
    "ss_dec.gguf",
    "ss_flow.gguf",
    "tex_dec.gguf",
    "tex_flow_1024.gguf",
    "tex_flow_512.gguf",
)


class InitError(Exception):
    def __init__(self, code, message, detail=""):
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail


def _absolute(path):
    return os.path.abspath(os.path.expanduser(path))


def _defaults(request):
    toolkit = _absolute(request.get("toolkitDir") or ROOT)
    result = with_defaults(request, load(REQUEST_SCHEMA))
    result.setdefault("toolkitDir", toolkit)
    result.setdefault("comfyDir", os.path.join(os.path.dirname(toolkit), "comfyui-strix-docker"))
    result.setdefault(
        "modelsDir",
        os.environ.get("TRELLIS_MODELS", "/home/hec/models/gguf/trellis2"),
    )
    result.setdefault("outDir", os.path.join(toolkit, "out"))
    result.setdefault("runtimeDir", os.path.join(toolkit, ".runtime"))
    for key in ("toolkitDir", "comfyDir", "modelsDir", "outDir", "runtimeDir"):
        result[key] = _absolute(result[key])
    return result


def _command(env_name, fallback):
    value = os.environ.get(env_name)
    return shlex.split(value) if value else list(fallback)


def _run(command, cwd, code, message):
    print("$ " + shlex.join(command), file=sys.stderr, flush=True)
    try:
        process = subprocess.Popen(
            command,
            cwd=cwd,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=1,
        )
    except FileNotFoundError as exc:
        raise InitError("DEPENDENCY_MISSING", f"command not found: {command[0]}", str(exc))
    tail = collections.deque(maxlen=80)
    for line in process.stdout:
        print(line, end="", file=sys.stderr, flush=True)
        tail.append(line)
    return_code = process.wait()
    output_tail = "".join(tail)
    if return_code:
        raise InitError(code, message, output_tail[-8000:])
    return output_tail


def _require_files(request):
    toolkit = request["toolkitDir"]
    required = (
        os.path.join(toolkit, "docker-compose.yml"),
        os.path.join(toolkit, "layers", "preview", "src", "serve.py"),
        os.path.join(toolkit, "scripts", "fetch-models.sh"),
    )
    missing = [path for path in required if not os.path.isfile(path)]
    if missing:
        raise InitError("TOOLKIT_MISSING", "toolkit files are missing", "\n".join(missing))
    comfy_compose = os.path.join(request["comfyDir"], "docker-compose.yml")
    if not os.path.isfile(comfy_compose):
        raise InitError("COMFYUI_MISSING", "ComfyUI Compose file is missing", comfy_compose)


def _make_dirs(request):
    for key in ("outDir", "runtimeDir"):
        try:
            os.makedirs(request[key], exist_ok=True)
        except OSError as exc:
            raise InitError(
                "OUTPUT_WRITE_FAILED",
                f"cannot create {request[key]}",
                str(exc),
            )


def _missing_models(models_dir):
    return [
        name
        for name in MODEL_FILES
        if not os.path.isfile(os.path.join(models_dir, name))
        or os.path.getsize(os.path.join(models_dir, name)) == 0
    ]


def _ensure_models(request):
    missing = _missing_models(request["modelsDir"])
    if not missing:
        print("models: 10 ready", file=sys.stderr, flush=True)
        return
    if request["fetchModels"]:
        fetcher = _command(
            "T2M_FETCH_MODELS",
            [os.path.join(request["toolkitDir"], "scripts", "fetch-models.sh")],
        )
        _run(
            fetcher + [request["modelsDir"]],
            request["toolkitDir"],
            "DOWNLOAD_FAILED",
            "TRELLIS model download failed",
        )
        missing = _missing_models(request["modelsDir"])
    if missing:
        raise InitError(
            "MODELS_MISSING",
            "TRELLIS model files are missing",
            ", ".join(missing),
        )


def _start_compose(request):
    docker = _command("T2M_DOCKER", ["docker"])
    _run(
        docker + ["compose", "version"],
        request["toolkitDir"],
        "DEPENDENCY_MISSING",
        "Docker Compose is unavailable",
    )
    build_flag = "--build" if request["buildImages"] else "--no-build"
    _run(
        docker
        + [
            "compose",
            "--project-directory",
            request["comfyDir"],
            "-f",
            os.path.join(request["comfyDir"], "docker-compose.yml"),
            "up",
            "-d",
            build_flag,
        ],
        request["comfyDir"],
        "START_FAILED",
        "ComfyUI did not start",
    )
    _run(
        docker
        + [
            "compose",
            "--project-directory",
            request["toolkitDir"],
            "-f",
            os.path.join(request["toolkitDir"], "docker-compose.yml"),
            "up",
            "-d",
            build_flag,
            "engine",
        ],
        request["toolkitDir"],
        "START_FAILED",
        "the resident Vulkan engine did not start",
    )


def _healthy(url, timeout=2):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return 200 <= response.status < 300
    except (OSError, urllib.error.URLError, urllib.error.HTTPError):
        return False


def _wait(url, name, deadline, error_code="SERVICE_TIMEOUT"):
    print(f"{name}: waiting for {url}", file=sys.stderr, flush=True)
    while time.monotonic() < deadline:
        if _healthy(url):
            print(f"{name}: ready at {url}", file=sys.stderr, flush=True)
            return
        time.sleep(1)
    raise InitError(error_code, f"{name} did not become ready", url)


def _start_preview(request, deadline):
    base = f"http://{request['previewHost']}:{request['previewPort']}"
    health = base + "/api/models"
    if _healthy(health):
        print(f"preview: reusing {base}", file=sys.stderr, flush=True)
        return {"state": "ready", "endpoint": base}

    preview = _command(
        "T2M_PREVIEW",
        [
            sys.executable,
            os.path.join(request["toolkitDir"], "layers", "preview", "src", "serve.py"),
        ],
    )
    command = preview + [
        "--dir",
        request["outDir"],
        "--host",
        request["previewHost"],
        "--port",
        str(request["previewPort"]),
    ]
    log_path = os.path.join(request["runtimeDir"], "preview.log")
    try:
        log = open(log_path, "ab")
        process = subprocess.Popen(
            command,
            cwd=request["toolkitDir"],
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        log.close()
        with open(
            os.path.join(request["runtimeDir"], "preview.pid"),
            "w",
            encoding="utf-8",
        ) as handle:
            handle.write(str(process.pid) + "\n")
    except (OSError, FileNotFoundError) as exc:
        raise InitError("PREVIEW_FAILED", "preview process could not start", str(exc))

    _wait(health, "preview", deadline, "PREVIEW_FAILED")
    if process.poll() is not None:
        detail = ""
        try:
            with open(log_path, "r", encoding="utf-8") as handle:
                detail = handle.read()[-2000:]
        except OSError:
            pass
        raise InitError("PREVIEW_FAILED", "preview process exited", detail)
    return {"state": "ready", "endpoint": base, "pid": process.pid}


def init_toolkit(raw_request):
    request = _defaults(raw_request)
    try:
        validate(request, load(REQUEST_SCHEMA))
    except SchemaError as exc:
        raise InitError("INVALID_REQUEST", str(exc))

    started = time.monotonic()
    _require_files(request)
    _make_dirs(request)
    _ensure_models(request)
    _start_compose(request)

    deadline = time.monotonic() + request["timeoutSeconds"]
    comfy_health = request["comfyEndpoint"].rstrip("/") + "/system_stats"
    engine_health = request["engineEndpoint"].rstrip("/") + "/health"
    _wait(comfy_health, "ComfyUI", deadline)
    _wait(engine_health, "engine", deadline)

    services = {
        "comfyui": {"state": "ready", "endpoint": request["comfyEndpoint"]},
        "engine": {"state": "ready", "endpoint": request["engineEndpoint"]},
    }
    if request["startPreview"]:
        services["preview"] = _start_preview(request, deadline)

    result = {
        "contractVersion": CONTRACT_VERSION,
        "services": services,
        "paths": {
            key: request[key]
            for key in ("toolkitDir", "comfyDir", "modelsDir", "outDir", "runtimeDir")
        },
        "elapsedMs": int((time.monotonic() - started) * 1000),
    }
    validate(result, load(RESULT_SCHEMA))
    return result


def _read_request(path):
    if path == "-":
        return json.load(sys.stdin)
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def _parser():
    parser = argparse.ArgumentParser(
        description="start ComfyUI, the resident Vulkan engine, and the GLB preview"
    )
    parser.add_argument("--request", help="InitRequest JSON file, or - for stdin")
    parser.add_argument("--toolkit-dir")
    parser.add_argument("--comfy-dir")
    parser.add_argument("--models-dir")
    parser.add_argument("--out-dir")
    parser.add_argument("--runtime-dir")
    parser.add_argument("--comfy-endpoint")
    parser.add_argument("--engine-endpoint")
    parser.add_argument("--preview-host")
    parser.add_argument("--preview-port", type=int)
    parser.add_argument("--timeout", type=int)
    parser.add_argument("--no-fetch", action="store_true")
    parser.add_argument("--no-build", action="store_true")
    parser.add_argument("--no-preview", action="store_true")
    return parser


def _request_from_args(args):
    if args.request:
        return _read_request(args.request)
    request = {}
    mapping = {
        "toolkit_dir": "toolkitDir",
        "comfy_dir": "comfyDir",
        "models_dir": "modelsDir",
        "out_dir": "outDir",
        "runtime_dir": "runtimeDir",
        "comfy_endpoint": "comfyEndpoint",
        "engine_endpoint": "engineEndpoint",
        "preview_host": "previewHost",
        "preview_port": "previewPort",
        "timeout": "timeoutSeconds",
    }
    for source, target in mapping.items():
        value = getattr(args, source)
        if value is not None:
            request[target] = value
    if args.no_fetch:
        request["fetchModels"] = False
    if args.no_build:
        request["buildImages"] = False
    if args.no_preview:
        request["startPreview"] = False
    return request


def main(argv=None):
    try:
        result = init_toolkit(_request_from_args(_parser().parse_args(argv)))
        print(json.dumps(result, indent=2))
    except (InitError, SchemaError, json.JSONDecodeError, OSError) as exc:
        if isinstance(exc, InitError):
            error = {
                "contractVersion": CONTRACT_VERSION,
                "code": exc.code,
                "message": exc.message,
            }
            if exc.detail:
                error["detail"] = exc.detail
        else:
            error = {
                "contractVersion": CONTRACT_VERSION,
                "code": "INVALID_REQUEST",
                "message": str(exc),
            }
        validate(error, load(ERROR_SCHEMA))
        print(json.dumps(error, indent=2), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
