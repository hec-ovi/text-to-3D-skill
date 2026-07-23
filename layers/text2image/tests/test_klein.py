"""End-to-end tests for the text2image blackbox.

Drives the real CLI entry point against a stub ComfyUI (stdlib http.server) and
checks the side effects: the PNG on disk and the envelope on stdout. No mocking
of internal functions.
"""

import io
import json
import os
import struct
import subprocess
import sys
import threading
import zlib
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

LAYER = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLI = os.path.join(LAYER, "src", "klein.py")
sys.path.insert(0, os.path.join(LAYER, "src"))

import klein  # noqa: E402
from schema_check import SchemaError, load, validate  # noqa: E402


def make_png(width=64, height=48):
    """A real, decodable PNG of a solid colour."""
    raw = b"".join(b"\x00" + b"\x7f\x40\x20" * width for _ in range(height))

    def chunk(tag, payload):
        return (struct.pack(">I", len(payload)) + tag + payload
                + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF))

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw))
            + chunk(b"IEND", b""))


class StubComfy:
    """Minimal ComfyUI: /prompt, /history/{id}, /view. Records what it was sent."""

    def __init__(self, png=None, reject=None, error_run=False, empty_outputs=False):
        self.png = png if png is not None else make_png()
        self.reject = reject          # (status, body) to answer /prompt with
        self.error_run = error_run
        self.empty_outputs = empty_outputs
        self.graphs = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def _send(self, status, body, ctype="application/json"):
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                payload = json.loads(self.rfile.read(length))
                outer.graphs.append(payload["prompt"])
                if outer.reject:
                    status, body = outer.reject
                    return self._send(status, body.encode())
                self._send(200, json.dumps({"prompt_id": "pid-1"}).encode())

            def do_GET(self):
                if self.path.startswith("/history/"):
                    if outer.error_run:
                        entry = {"status": {"status_str": "error", "messages": ["boom"]}}
                    elif outer.empty_outputs:
                        entry = {"status": {"status_str": "success", "completed": True},
                                 "outputs": {}}
                    else:
                        entry = {"status": {"status_str": "success", "completed": True},
                                 "outputs": {"13": {"images": [
                                     {"filename": "t2m_00001_.png", "subfolder": "",
                                      "type": "output"}]}}}
                    return self._send(200, json.dumps({"pid-1": entry}).encode())
                if self.path.startswith("/view"):
                    return self._send(200, outer.png, "image/png")
                self._send(404, b"{}")

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.server.shutdown()
        self.server.server_close()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.server.server_port}"


def run_cli(*args):
    proc = subprocess.run([sys.executable, CLI, *args], capture_output=True, text=True)
    return proc


# ---- happy path -------------------------------------------------------------


def test_cli_writes_png_and_prints_valid_envelope(tmp_path):
    with StubComfy() as comfy:
        proc = run_cli("--prompt", "a brass diving helmet",
                       "--out-dir", str(tmp_path), "--endpoint", comfy.url)

    assert proc.returncode == 0, proc.stderr
    result = json.loads(proc.stdout)
    validate(result, load(os.path.join(LAYER, "schema", "image_result.json")))

    path = result["image"]["uri"]
    assert os.path.isfile(path)
    written = open(path, "rb").read()
    assert result["image"]["byteSize"] == len(written)
    import hashlib
    assert result["image"]["checksum"]["sha256"] == hashlib.sha256(written).hexdigest()
    assert (result["image"]["width"], result["image"]["height"]) == (64, 48)


def test_prompt_is_framed_for_single_object_reconstruction(tmp_path):
    with StubComfy() as comfy:
        proc = run_cli("--prompt", "a brass diving helmet",
                       "--out-dir", str(tmp_path), "--endpoint", comfy.url)
        graph = comfy.graphs[0]

    sent = graph["4"]["inputs"]["text"]
    assert "a brass diving helmet" in sent
    assert "plain light grey background" in sent
    assert json.loads(proc.stdout)["promptSent"] == sent


def test_raw_prompt_bypasses_framing(tmp_path):
    with StubComfy() as comfy:
        run_cli("--prompt", "exactly this", "--raw-prompt",
                "--out-dir", str(tmp_path), "--endpoint", comfy.url)
        assert comfy.graphs[0]["4"]["inputs"]["text"] == "exactly this"


def test_same_prompt_yields_same_seed(tmp_path):
    with StubComfy() as comfy:
        first = json.loads(run_cli("--prompt", "a rusty kettle", "--out-dir", str(tmp_path),
                                   "--endpoint", comfy.url).stdout)
        second = json.loads(run_cli("--prompt", "a rusty kettle", "--out-dir", str(tmp_path),
                                    "--endpoint", comfy.url).stdout)
    assert first["seed"] == second["seed"]
    assert first["image"]["uri"] == second["image"]["uri"]  # content-addressed


def test_explicit_seed_and_dims_reach_the_graph(tmp_path):
    with StubComfy() as comfy:
        run_cli("--prompt", "a lantern", "--seed", "1234", "--width", "768",
                "--height", "512", "--steps", "6",
                "--out-dir", str(tmp_path), "--endpoint", comfy.url)
        graph = comfy.graphs[0]

    assert graph["9"]["inputs"]["noise_seed"] == 1234
    assert graph["6"]["inputs"]["width"] == 768
    assert graph["7"]["inputs"]["height"] == 512
    assert graph["7"]["inputs"]["steps"] == 6


def test_template_on_disk_is_not_mutated(tmp_path):
    template_path = os.path.join(LAYER, "templates", "flux2_klein_t2i.json")
    before = open(template_path, encoding="utf-8").read()
    with StubComfy() as comfy:
        run_cli("--prompt", "a chair", "--out-dir", str(tmp_path), "--endpoint", comfy.url)
    assert open(template_path, encoding="utf-8").read() == before


def test_request_envelope_via_stdin(tmp_path):
    with StubComfy() as comfy:
        request = {"prompt": "a stone idol", "outDir": str(tmp_path), "endpoint": comfy.url}
        proc = subprocess.run([sys.executable, CLI, "--request", "-"],
                              input=json.dumps(request), capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert os.path.isfile(json.loads(proc.stdout)["image"]["uri"])


# ---- failure paths ----------------------------------------------------------


def error_envelope(proc):
    validate(json.loads(proc.stderr), load(os.path.join(LAYER, "schema", "error.json")))
    return json.loads(proc.stderr)


def test_backend_unreachable(tmp_path):
    proc = run_cli("--prompt", "x", "--out-dir", str(tmp_path),
                   "--endpoint", "http://127.0.0.1:1")
    assert proc.returncode == 1
    assert error_envelope(proc)["code"] == "BACKEND_UNREACHABLE"


def test_graph_rejected(tmp_path):
    with StubComfy(reject=(400, '{"error": "bad node"}')) as comfy:
        proc = run_cli("--prompt", "x", "--out-dir", str(tmp_path), "--endpoint", comfy.url)
    assert proc.returncode == 1
    assert error_envelope(proc)["code"] == "GRAPH_REJECTED"


def test_missing_model_is_reported_as_model_missing(tmp_path):
    body = '{"error": {"message": "value not in list: unet_name"}}'
    with StubComfy(reject=(400, body)) as comfy:
        proc = run_cli("--prompt", "x", "--out-dir", str(tmp_path), "--endpoint", comfy.url)
    assert error_envelope(proc)["code"] == "MODEL_MISSING"


def test_execution_error_is_render_failed(tmp_path):
    with StubComfy(error_run=True) as comfy:
        proc = run_cli("--prompt", "x", "--out-dir", str(tmp_path), "--endpoint", comfy.url)
    assert error_envelope(proc)["code"] == "RENDER_FAILED"


def test_run_without_images_is_render_failed(tmp_path):
    with StubComfy(empty_outputs=True) as comfy:
        proc = run_cli("--prompt", "x", "--out-dir", str(tmp_path), "--endpoint", comfy.url)
    assert error_envelope(proc)["code"] == "RENDER_FAILED"


def test_non_png_bytes_rejected(tmp_path):
    with StubComfy(png=b"definitely not a png") as comfy:
        proc = run_cli("--prompt", "x", "--out-dir", str(tmp_path), "--endpoint", comfy.url)
    assert error_envelope(proc)["code"] == "RENDER_FAILED"


def test_timeout(tmp_path):
    with StubComfy(empty_outputs=False) as comfy:
        request = {"prompt": "x", "outDir": str(tmp_path), "endpoint": comfy.url,
                   "timeoutSeconds": 10}
        # /history answers with an entry that never carries outputs
        comfy.empty_outputs = True
        with pytest.raises(klein.RenderError) as exc:
            klein.render({**request, "timeoutSeconds": 10})
        assert exc.value.code in {"RENDER_FAILED", "TIMEOUT"}


def test_unwritable_out_dir(tmp_path):
    blocked = tmp_path / "ro"
    blocked.mkdir()
    os.chmod(blocked, 0o500)
    try:
        with StubComfy() as comfy:
            proc = run_cli("--prompt", "x", "--out-dir", str(blocked / "sub"),
                           "--endpoint", comfy.url)
        assert error_envelope(proc)["code"] == "OUTPUT_WRITE_FAILED"
    finally:
        os.chmod(blocked, 0o700)


# ---- schema boundary --------------------------------------------------------


def test_empty_prompt_is_invalid_request():
    with pytest.raises(klein.RenderError) as exc:
        klein.render({"prompt": ""})
    assert exc.value.code == "INVALID_REQUEST"


def test_unknown_field_is_rejected():
    with pytest.raises(klein.RenderError) as exc:
        klein.render({"prompt": "x", "sampler": "dpmpp"})
    assert exc.value.code == "INVALID_REQUEST"


def test_odd_dimensions_are_rejected():
    with pytest.raises(klein.RenderError) as exc:
        klein.render({"prompt": "x", "width": 1000, "height": 1001})
    assert exc.value.code == "INVALID_REQUEST"


def test_result_schema_rejects_a_short_checksum():
    schema = load(os.path.join(LAYER, "schema", "image_result.json"))
    bad = {
        "contractVersion": "1.0",
        "image": {"uri": "/tmp/a.png", "mediaType": "image/png", "byteSize": 10,
                  "checksum": {"sha256": "abc"}, "width": 1, "height": 1},
        "seed": 1, "model": {"unet": "u", "clip": "c", "vae": "v"}, "elapsedMs": 0,
    }
    with pytest.raises(SchemaError):
        validate(bad, schema)
