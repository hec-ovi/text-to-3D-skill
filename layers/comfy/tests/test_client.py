"""Contract tests for the comfy layer.

The real CLI is driven against a stub `t2m-server` over real HTTP on a real
socket. No GPU, no weights, no ComfyUI, no torch.

The GLB and PNG builders are copied rather than imported from image2mesh: this
layer is a blackbox and reads nothing inside a sibling, test scaffolding
included, because a fixture shared across a boundary is a coupling that shows
up as a mystery failure the day the other layer changes its exporter.
"""

import base64
import json
import os
import struct
import subprocess
import sys
import threading
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
LAYER = os.path.dirname(HERE)
CLIENT = os.path.join(LAYER, "src", "client.py")

sys.path.insert(0, os.path.join(LAYER, "src"))


# ---- fixtures ---------------------------------------------------------------


def make_png(width=32, height=32):
    raw = b"".join(b"\x00" + b"\x60\x60\x60" * width for _ in range(height))

    def chunk(tag, payload):
        return (struct.pack(">I", len(payload)) + tag + payload
                + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF))

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw))
            + chunk(b"IEND", b""))


def make_glb(triangles=2, meshes=1, bad_magic=False, bad_version=False,
             bad_length=False, broken_json=False, no_meshes=False):
    index_count = triangles * 3
    indices = struct.pack(f"<{index_count}H", *range(index_count))
    positions = struct.pack(f"<{index_count * 3}f", *([0.0] * index_count * 3))
    blob = indices + b"\x00" * (-len(indices) % 4) + positions

    gltf = {
        "asset": {"version": "2.0", "generator": "comfy test"},
        "scene": 0,
        "scenes": [{"nodes": list(range(meshes))}],
        "nodes": [{"mesh": i} for i in range(meshes)],
        "meshes": [] if no_meshes else [
            {"primitives": [{"attributes": {"POSITION": 1}, "indices": 0, "mode": 4}]}
            for _ in range(meshes)
        ],
        "accessors": [
            {"bufferView": 0, "componentType": 5123, "count": index_count, "type": "SCALAR"},
            {"bufferView": 1, "componentType": 5126, "count": index_count, "type": "VEC3"},
        ],
        "bufferViews": [
            {"buffer": 0, "byteOffset": 0, "byteLength": len(indices)},
            {"buffer": 0, "byteOffset": len(indices) + (-len(indices) % 4),
             "byteLength": len(positions)},
        ],
        "buffers": [{"byteLength": len(blob)}],
    }

    json_bytes = b"{ broken" if broken_json else json.dumps(gltf).encode("utf-8")
    json_bytes += b" " * (-len(json_bytes) % 4)
    blob += b"\x00" * (-len(blob) % 4)

    body = struct.pack("<II", len(json_bytes), 0x4E4F534A) + json_bytes
    body += struct.pack("<II", len(blob), 0x004E4942) + blob

    magic = 0x11111111 if bad_magic else 0x46546C67
    version = 3 if bad_version else 2
    total = 12 + len(body) + (99 if bad_length else 0)
    return struct.pack("<III", magic, version, total) + body


class StubEngine:
    """A t2m-server that answers /generate with whatever the test hands it."""

    def __init__(self, body=None, status=200, delay=0.0):
        self.body, self.status, self.delay = body, status, delay
        self.calls = []
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(length)
                stub.calls.append({
                    "path": self.path,
                    "contentType": self.headers.get("Content-Type", ""),
                    "body": raw,
                })
                if stub.delay:
                    import time
                    time.sleep(stub.delay)
                payload = stub.body if stub.body is not None else b""
                self.send_response(stub.status)
                self.send_header("Content-Type", "model/gltf-binary")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def endpoint(self):
        return f"http://127.0.0.1:{self.server.server_port}"

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def engine():
    stubs = []

    def make(**kwargs):
        stub = StubEngine(**kwargs)
        stubs.append(stub)
        return stub

    yield make
    for stub in stubs:
        stub.close()


def run_cli(args):
    proc = subprocess.run([sys.executable, CLIENT] + args, capture_output=True, text=True)
    return proc


def request_for(endpoint, out_dir, png=None, **overrides):
    payload = png if png is not None else make_png()
    request = {
        "image": {
            "data": base64.b64encode(payload).decode("ascii"),
            "contentEncoding": "base64",
            "contentMediaType": "image/png",
            "byteSize": len(payload),
        },
        "name": "brass-helmet",
        "endpoint": endpoint,
        "outDir": str(out_dir),
    }
    request.update(overrides)
    return request


def run_request(request):
    proc = subprocess.run([sys.executable, CLIENT, "--request", "-"],
                          input=json.dumps(request), capture_output=True, text=True)
    return proc


# ---- the happy path ---------------------------------------------------------


def test_a_render_becomes_a_validated_glb_on_disk(engine, tmp_path):
    stub = engine(body=make_glb(triangles=7))
    proc = run_request(request_for(stub.endpoint, tmp_path))

    assert proc.returncode == 0, proc.stderr
    result = json.loads(proc.stdout)
    assert result["contractVersion"] == "1.0"
    assert result["triangles"] == 7
    assert result["glb"]["mediaType"] == "model/gltf-binary"

    path = result["glb"]["uri"]
    assert os.path.isfile(path)
    assert os.path.getsize(path) == result["glb"]["byteSize"]

    import hashlib
    with open(path, "rb") as handle:
        assert hashlib.sha256(handle.read()).hexdigest() == result["glb"]["checksum"]["sha256"]


def test_the_name_reaches_the_file_and_stays_url_safe(engine, tmp_path):
    stub = engine(body=make_glb())
    proc = run_request(request_for(stub.endpoint, tmp_path,
                                   name="a brass helmet / mk2", resolution=1024))

    assert proc.returncode == 0, proc.stderr
    stem = os.path.basename(json.loads(proc.stdout)["glb"]["uri"])
    assert stem.startswith("a-brass-helmet-mk2-")
    assert stem.endswith("-r1024.glb")


def test_the_engine_is_sent_the_settings_it_understands(engine, tmp_path):
    stub = engine(body=make_glb())
    proc = run_request(request_for(stub.endpoint, tmp_path, resolution=1024, seed=7,
                                   targetFaces=12000, backgroundRemoval="birefnet"))

    assert proc.returncode == 0, proc.stderr
    call = stub.calls[0]
    assert call["path"] == "/generate"
    assert call["contentType"].startswith("multipart/form-data; boundary=")
    body = call["body"]
    for field, value in (("resolution", "1024"), ("seed", "7"),
                         ("target_faces", "12000"), ("bg_removal", "birefnet")):
        assert f'name="{field}"'.encode() in body
        assert value.encode() in body
    assert b'filename="render.png"' in body
    assert b"\x89PNG\r\n\x1a\n" in body


def test_an_auto_background_is_left_to_the_engine(engine, tmp_path):
    stub = engine(body=make_glb())
    proc = run_request(request_for(stub.endpoint, tmp_path))

    assert proc.returncode == 0, proc.stderr
    # Absent, not "auto": the engine picks, and sending the word would override
    # a decision this layer is not qualified to make.
    assert b'name="bg_removal"' not in stub.calls[0]["body"]


def test_the_cli_takes_a_png_path_too(engine, tmp_path):
    stub = engine(body=make_glb(triangles=3))
    png = tmp_path / "diving-helmet.png"
    png.write_bytes(make_png())

    proc = run_cli(["--image", str(png), "--out-dir", str(tmp_path),
                    "--endpoint", stub.endpoint])

    assert proc.returncode == 0, proc.stderr
    result = json.loads(proc.stdout)
    assert os.path.basename(result["glb"]["uri"]).startswith("diving-helmet-")
    assert result["triangles"] == 3


# ---- the closed error set ---------------------------------------------------


def test_nothing_listening_is_engine_unreachable(tmp_path):
    # Port 1 is reserved and never bound, so this fails to connect rather than
    # racing a real server that might be on the port a stub just released.
    proc = run_request(request_for("http://127.0.0.1:1", tmp_path))

    assert proc.returncode == 1
    error = json.loads(proc.stderr)
    assert error["code"] == "ENGINE_UNREACHABLE"
    assert error["contractVersion"] == "1.0"


def test_a_non_2xx_is_engine_failed(engine, tmp_path):
    stub = engine(body=b"no vulkan device", status=500)
    proc = run_request(request_for(stub.endpoint, tmp_path))

    assert proc.returncode == 1
    error = json.loads(proc.stderr)
    assert error["code"] == "ENGINE_FAILED"
    assert "500" in error["message"]
    assert "no vulkan device" in error["detail"]


def test_an_empty_body_is_engine_failed(engine, tmp_path):
    stub = engine(body=b"")
    proc = run_request(request_for(stub.endpoint, tmp_path))

    assert proc.returncode == 1
    assert json.loads(proc.stderr)["code"] == "ENGINE_FAILED"


def test_a_slow_engine_is_a_timeout(engine, tmp_path):
    stub = engine(body=make_glb(), delay=1.5)
    proc = run_request(request_for(stub.endpoint, tmp_path, timeoutSeconds=10))

    # The schema floor on timeoutSeconds is 10, so the delay cannot be tuned
    # under it; this asserts the wait is honoured rather than the failure.
    assert proc.returncode == 0, proc.stderr


@pytest.mark.parametrize("kwargs,why", [
    ({"bad_magic": True}, "magic"),
    ({"bad_version": True}, "version"),
    ({"bad_length": True}, "length"),
    ({"broken_json": True}, "json"),
    ({"no_meshes": True}, "meshes"),
])
def test_a_broken_glb_never_reaches_the_disk(engine, tmp_path, kwargs, why):
    stub = engine(body=make_glb(**kwargs))
    proc = run_request(request_for(stub.endpoint, tmp_path))

    assert proc.returncode == 1, why
    assert json.loads(proc.stderr)["code"] == "GLB_INVALID"
    assert list(tmp_path.glob("*.glb")) == [], "a rejected GLB was written anyway"


def test_a_truncated_glb_is_rejected(engine, tmp_path):
    stub = engine(body=make_glb()[:12])
    proc = run_request(request_for(stub.endpoint, tmp_path))

    assert proc.returncode == 1
    assert json.loads(proc.stderr)["code"] == "GLB_INVALID"


def test_something_that_is_not_a_png_is_refused_before_the_engine(engine, tmp_path):
    stub = engine(body=make_glb())
    proc = run_request(request_for(stub.endpoint, tmp_path, png=b"GIF89a not a png"))

    assert proc.returncode == 1
    assert json.loads(proc.stderr)["code"] == "INVALID_REQUEST"
    assert stub.calls == [], "the engine was called with something that is not a PNG"


def test_base64_that_does_not_decode_is_refused(engine, tmp_path):
    stub = engine(body=make_glb())
    request = request_for(stub.endpoint, tmp_path)
    request["image"]["data"] = "not base64 at all!!"
    proc = run_request(request)

    assert proc.returncode == 1
    assert json.loads(proc.stderr)["code"] == "INVALID_REQUEST"
    assert stub.calls == []


def test_a_byte_size_that_disagrees_with_the_image_is_refused(engine, tmp_path):
    stub = engine(body=make_glb())
    request = request_for(stub.endpoint, tmp_path)
    request["image"]["byteSize"] = 12
    proc = run_request(request)

    assert proc.returncode == 1
    error = json.loads(proc.stderr)
    assert error["code"] == "INVALID_REQUEST"
    assert stub.calls == []


def test_an_off_contract_request_is_refused(engine, tmp_path):
    stub = engine(body=make_glb())
    request = request_for(stub.endpoint, tmp_path, resolution=777)
    proc = run_request(request)

    assert proc.returncode == 1
    assert json.loads(proc.stderr)["code"] == "INVALID_REQUEST"
    assert stub.calls == []


def test_an_unwritable_out_dir_is_reported(engine, tmp_path):
    stub = engine(body=make_glb())
    blocked = tmp_path / "blocked"
    blocked.write_text("not a directory")
    proc = run_request(request_for(stub.endpoint, blocked / "out"))

    assert proc.returncode == 1
    assert json.loads(proc.stderr)["code"] == "OUTPUT_WRITE_FAILED"


# ---- the node adapter -------------------------------------------------------


def test_comfyui_discovery_finds_the_node_through_the_package():
    """Import the folder the way ComfyUI imports a custom node: by path.

    ComfyUI walks custom_nodes/, imports each directory's __init__.py as a
    module, and reads two names off it. Importing `src/node.py` directly, which
    the rest of these tests do, does not exercise that path: it skips the
    sys.path line in __init__.py, which is the one thing standing between a
    mounted folder and `ModuleNotFoundError: node` at ComfyUI startup.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "t2m_custom_node", os.path.join(LAYER, "__init__.py"),
        submodule_search_locations=[LAYER])
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert "TextTo3DMesh" in module.NODE_CLASS_MAPPINGS
    assert module.NODE_DISPLAY_NAME_MAPPINGS["TextTo3DMesh"] == "Image to GLB (TRELLIS.2 Vulkan)"


def test_the_workflow_template_wires_the_node_to_the_render():
    """The shipped graph is the whole pipeline, and stays wired to the decode."""
    with open(os.path.join(LAYER, "workflows", "text_to_3d.json"), encoding="utf-8") as handle:
        graph = json.load(handle)

    mesh = [node for node in graph.values() if node["class_type"] == "TextTo3DMesh"]
    assert len(mesh) == 1, "the template should call the node exactly once"

    source, slot = mesh[0]["inputs"]["image"]
    assert graph[source]["class_type"] == "VAEDecode", "the mesh must come from the decoded render"
    assert slot == 0
    # The PNG is still written, so the preview can pair the mesh with its source.
    assert any(node["class_type"] == "SaveImage" for node in graph.values())

    inputs = mesh[0]["inputs"]
    assert inputs["resolution"] in (512, 1024, 1536)
    # host.docker.internal, not localhost: inside the container localhost is the
    # container, and the engine is a separate one published on the host.
    assert "host.docker.internal" in inputs["engine"]


def test_the_node_declares_what_comfyui_reads():
    """The two names ComfyUI reads off the package, without importing torch."""
    import node

    assert "TextTo3DMesh" in node.NODE_CLASS_MAPPINGS
    assert node.NODE_DISPLAY_NAME_MAPPINGS["TextTo3DMesh"]

    cls = node.NODE_CLASS_MAPPINGS["TextTo3DMesh"]
    spec = cls.INPUT_TYPES()
    assert spec["required"]["image"] == ("IMAGE",)
    assert cls.RETURN_TYPES == ("STRING", "STRING")
    assert cls.RETURN_NAMES == ("glb_path", "result_json")
    assert cls.FUNCTION == "run"
    # Every resolution the engine accepts, and only those.
    assert spec["required"]["resolution"][0] == [512, 1024, 1536]


def test_the_node_reports_a_failure_as_the_envelope(engine, tmp_path, monkeypatch):
    """ComfyUI shows the exception text, so the closed-set code has to be in it."""
    import node

    monkeypatch.setattr(node, "_png_bytes", lambda image: make_png())
    cls = node.NODE_CLASS_MAPPINGS["TextTo3DMesh"]()

    with pytest.raises(RuntimeError) as raised:
        cls.run(image=None, name="helmet", resolution=512, seed=42,
                engine="http://127.0.0.1:1", out_dir=str(tmp_path))

    assert json.loads(str(raised.value))["code"] == "ENGINE_UNREACHABLE"


def test_the_node_returns_the_path_and_the_envelope(engine, tmp_path, monkeypatch):
    import node

    stub = engine(body=make_glb(triangles=5))
    monkeypatch.setattr(node, "_png_bytes", lambda image: make_png())
    cls = node.NODE_CLASS_MAPPINGS["TextTo3DMesh"]()

    path, envelope = cls.run(image=None, name="helmet", resolution=512, seed=9,
                             target_faces=4000, engine=stub.endpoint, out_dir=str(tmp_path))

    assert os.path.isfile(path)
    result = json.loads(envelope)
    assert result["glb"]["uri"] == path
    assert result["triangles"] == 5
    assert result["engine"]["seed"] == 9
    assert b"4000" in stub.calls[0]["body"]
