import contextlib
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

LAYER = Path(__file__).resolve().parents[1]
ROOT = LAYER.parents[1]
CLI = LAYER / "src" / "init.py"
LAUNCHER = ROOT / "scripts" / "init.py"
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


class ReadyHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"{}"
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format, *_args):
        pass


@contextlib.contextmanager
def ready_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), ReadyHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def fake_docker(tmp_path):
    log = tmp_path / "docker.log"
    executable = tmp_path / "docker"
    # The compose overlay is passed through the environment, so the stand-in
    # records what it was handed as well as what it was called with.
    executable.write_text(
        "#!/bin/sh\n"
        "printf '%s\\n' \"$*\" >> \"$T2M_DOCKER_LOG\"\n"
        "printf 'env T2M_TOOLKIT=%s T2M_OUT_DIR=%s\\n' \"$T2M_TOOLKIT\" \"$T2M_OUT_DIR\" "
        ">> \"$T2M_DOCKER_LOG\"\n"
        "printf 'docker progress: %s\\n' \"$*\"\n"
        "exit 0\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    return executable, log


def base_args(tmp_path, comfy_endpoint, engine_endpoint):
    comfy = tmp_path / "comfy"
    comfy.mkdir()
    (comfy / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    models = tmp_path / "models"
    models.mkdir()
    for name in MODEL_FILES:
        (models / name).write_bytes(b"model")
    return [
        "--toolkit-dir",
        str(ROOT),
        "--comfy-dir",
        str(comfy),
        "--models-dir",
        str(models),
        "--out-dir",
        str(tmp_path / "out"),
        "--runtime-dir",
        str(tmp_path / "runtime"),
        "--comfy-endpoint",
        comfy_endpoint,
        "--engine-endpoint",
        engine_endpoint,
        "--preview-port",
        str(free_port()),
        "--timeout",
        "10",
        "--no-fetch",
        "--no-build",
    ]


def test_cli_starts_compose_and_preview_end_to_end(tmp_path):
    docker, docker_log = fake_docker(tmp_path)
    env = dict(os.environ, T2M_DOCKER=str(docker), T2M_DOCKER_LOG=str(docker_log))
    with ready_server() as comfy, ready_server() as engine:
        completed = subprocess.run(
            [sys.executable, str(CLI), *base_args(tmp_path, comfy, engine)],
            text=True,
            capture_output=True,
            env=env,
            timeout=20,
        )

    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result["services"]["comfyui"]["state"] == "ready"
    assert result["services"]["engine"]["state"] == "ready"
    assert result["services"]["preview"]["state"] == "ready"
    assert Path(result["paths"]["outDir"]).is_dir()
    assert Path(result["paths"]["runtimeDir"], "preview.pid").is_file()
    with urllib.request.urlopen(
        result["services"]["preview"]["endpoint"] + "/api/models", timeout=2
    ) as response:
        assert response.status == 200
    commands = docker_log.read_text(encoding="utf-8")
    assert "compose version" in commands
    assert "up -d --no-build" in commands
    assert "docker progress: compose version" in completed.stderr
    os.kill(result["services"]["preview"]["pid"], signal.SIGTERM)


def test_comfyui_starts_with_the_graph_node_overlaid(tmp_path):
    """The node is mounted by starting the sibling stack with the overlay on top."""
    docker, docker_log = fake_docker(tmp_path)
    env = dict(os.environ, T2M_DOCKER=str(docker), T2M_DOCKER_LOG=str(docker_log))
    with ready_server() as comfy, ready_server() as engine:
        args = base_args(tmp_path, comfy, engine)
        completed = subprocess.run(
            [sys.executable, str(CLI), *args, "--no-preview"],
            text=True,
            capture_output=True,
            env=env,
            timeout=20,
        )

    assert completed.returncode == 0, completed.stderr
    commands = docker_log.read_text(encoding="utf-8")
    overlay = str(ROOT / "docker-compose.comfy.yml")
    assert overlay in commands, "ComfyUI was started without the node overlay"
    # Relative host paths in a compose file resolve against the project
    # directory, which here is the other repository, so both are absolute.
    out_dir = str(Path(args[args.index("--out-dir") + 1]))
    assert f"env T2M_TOOLKIT={ROOT} T2M_OUT_DIR={out_dir}" in commands
    # The engine stack is this repo's own file and takes no overlay.
    engine_up = [line for line in commands.splitlines() if "engine" in line]
    assert engine_up and overlay not in engine_up[0]


def test_the_overlay_can_be_declined(tmp_path):
    docker, docker_log = fake_docker(tmp_path)
    env = dict(os.environ, T2M_DOCKER=str(docker), T2M_DOCKER_LOG=str(docker_log))
    with ready_server() as comfy, ready_server() as engine:
        completed = subprocess.run(
            [sys.executable, str(CLI), *base_args(tmp_path, comfy, engine),
             "--no-preview", "--no-comfy-node"],
            text=True,
            capture_output=True,
            env=env,
            timeout=20,
        )

    assert completed.returncode == 0, completed.stderr
    commands = docker_log.read_text(encoding="utf-8")
    assert "docker-compose.comfy.yml" not in commands
    assert "env T2M_TOOLKIT= T2M_OUT_DIR=" in commands


def test_missing_models_fail_before_services_start(tmp_path):
    docker, docker_log = fake_docker(tmp_path)
    env = dict(os.environ, T2M_DOCKER=str(docker), T2M_DOCKER_LOG=str(docker_log))
    with ready_server() as comfy, ready_server() as engine:
        args = base_args(tmp_path, comfy, engine)
        models = Path(args[args.index("--models-dir") + 1])
        (models / MODEL_FILES[-1]).unlink()
        completed = subprocess.run(
            [sys.executable, str(CLI), *args, "--no-preview"],
            text=True,
            capture_output=True,
            env=env,
            timeout=10,
        )

    assert completed.returncode == 1
    error = json.loads(completed.stderr[completed.stderr.index("{") :])
    assert error["code"] == "MODELS_MISSING"
    assert MODEL_FILES[-1] in error["detail"]
    assert not docker_log.exists()


def test_launcher_reaches_the_toolkit_entry_point():
    completed = subprocess.run(
        [sys.executable, str(LAUNCHER), "--toolkit-dir", str(ROOT), "--help"],
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert completed.returncode == 0
    assert "start ComfyUI" in completed.stdout
