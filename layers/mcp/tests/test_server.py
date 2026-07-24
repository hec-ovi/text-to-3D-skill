"""End-to-end tests for the MCP blackbox.

A real server process, driven over real stdio JSON-RPC. The generation tools are
pointed at stand-in layer CLIs so nothing needs a GPU; everything else, the
handshake, the tool table, the id resolution, the file copy, is the real thing.
"""

import hashlib
import json
import os
import struct
import subprocess
import sys
import threading

import pytest

LAYER = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVER = os.path.join(LAYER, "src", "server.py")
sys.path.insert(0, os.path.join(LAYER, "src"))

import server as server_module  # noqa: E402
from schema_check import load, validate  # noqa: E402

RESULT_SCHEMA = load(os.path.join(LAYER, "schema", "tool_result.json"))


# ---- fixtures ---------------------------------------------------------------


def make_glb(triangles=4, animations=(), joints=0):
    """Real GLB bytes: header, JSON chunk, BIN chunk."""
    index_count = triangles * 3
    indices = struct.pack(f"<{index_count}H", *([0] * index_count))
    blob = indices + b"\x00" * (-len(indices) % 4)
    gltf = {
        "asset": {"version": "2.0"},
        "meshes": [{"primitives": [{"attributes": {"POSITION": 1}, "indices": 0, "mode": 4}]}],
        "accessors": [
            {"bufferView": 0, "componentType": 5123, "count": index_count, "type": "SCALAR"},
            {"bufferView": 0, "componentType": 5126, "count": index_count, "type": "VEC3"},
        ],
        "bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": len(indices)}],
        "buffers": [{"byteLength": len(blob)}],
        "materials": [{"pbrMetallicRoughness": {}}],
    }
    if joints:
        gltf["skins"] = [{"joints": list(range(joints))}]
    if animations:
        gltf["animations"] = [{"name": n, "channels": [], "samplers": []} for n in animations]

    json_bytes = json.dumps(gltf).encode("utf-8")
    json_bytes += b" " * (-len(json_bytes) % 4)
    body = struct.pack("<II", len(json_bytes), 0x4E4F534A) + json_bytes
    body += struct.pack("<II", len(blob), 0x004E4942) + blob
    return struct.pack("<III", 0x46546C67, 2, 12 + len(body)) + body


class Client:
    """A minimal MCP client: writes a request, reads the matching response."""

    def __init__(self, out_dir, env=None):
        self.proc = subprocess.Popen(
            [sys.executable, SERVER, "--out-dir", str(out_dir),
             "--preview-url", "http://127.0.0.1:8190"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, bufsize=1, env={**os.environ, **(env or {})})
        self.next_id = 0
        self.notifications = []

    def request(self, method, params=None, timeout=30):
        self.next_id += 1
        message = {"jsonrpc": "2.0", "id": self.next_id, "method": method}
        if params is not None:
            message["params"] = params
        self.proc.stdin.write(json.dumps(message) + "\n")
        self.proc.stdin.flush()

        answer = {}
        done = threading.Event()

        def read():
            for line in self.proc.stdout:
                payload = json.loads(line)
                if payload.get("id") == message["id"]:
                    answer.update(payload)
                    done.set()
                    return
                self.notifications.append(payload)

        thread = threading.Thread(target=read, daemon=True)
        thread.start()
        assert done.wait(timeout), f"no response to {method} in {timeout}s"
        return answer

    def notify(self, method, params=None):
        message = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        self.proc.stdin.write(json.dumps(message) + "\n")
        self.proc.stdin.flush()

    def call(self, name, arguments=None, timeout=30):
        return self.request("tools/call",
                            {"name": name, "arguments": arguments or {}}, timeout=timeout)

    def close(self):
        try:
            self.proc.stdin.close()
            self.proc.wait(timeout=5)
        except Exception:
            self.proc.kill()


@pytest.fixture
def client(tmp_path):
    session = Client(tmp_path)
    try:
        yield session, tmp_path
    finally:
        session.close()


# ---- the handshake ----------------------------------------------------------


def test_initialize_answers_with_the_protocol_and_the_server_name(client):
    session, _ = client
    result = session.request("initialize", {
        "protocolVersion": "2025-11-25",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "1"},
    })["result"]

    assert result["protocolVersion"] == "2025-11-25"
    assert result["serverInfo"]["name"] == "text-to-3d"
    assert result["capabilities"]["tools"] == {"listChanged": False}
    assert "targetFaces" in result["instructions"]


def test_a_notification_gets_no_answer_and_the_server_stays_up(client):
    session, _ = client
    session.notify("notifications/initialized")
    assert session.request("ping")["result"] == {}


def test_an_unknown_method_is_a_protocol_error(client):
    session, _ = client
    answer = session.request("resources/list")
    assert answer["error"]["code"] == -32601


def test_every_tool_declares_a_schema_and_its_hints(client):
    session, _ = client
    tools = session.request("tools/list")["result"]["tools"]
    names = [t["name"] for t in tools]
    assert names == ["generate_model", "generate_image", "rig_model",
                     "list_models", "get_preview", "search_assets",
                     "fetch_asset", "download_glb"]
    for tool in tools:
        assert tool["inputSchema"]["type"] == "object"
        assert tool["description"]
        assert "annotations" in tool
    by_name = {t["name"]: t for t in tools}
    assert by_name["list_models"]["annotations"]["readOnlyHint"] is True
    assert by_name["generate_model"]["annotations"]["readOnlyHint"] is False
    # Only the two library tools reach off this machine.
    reaching = {t["name"] for t in tools if t["annotations"].get("openWorldHint")}
    assert reaching == {"search_assets", "fetch_asset"}


# ---- the tools --------------------------------------------------------------


def test_list_models_reads_the_directory(client):
    session, out = client
    (out / "one-r512.glb").write_bytes(make_glb(triangles=7))
    (out / "two-rigged.glb").write_bytes(make_glb(triangles=3, animations=["idle"], joints=19))

    result = session.call("list_models")["result"]
    validate(result, RESULT_SCHEMA)
    payload = result["structuredContent"]
    assert payload["count"] == 2
    rigged = next(m for m in payload["models"] if m["id"] == "two-rigged")
    assert rigged["animations"] == ["idle"]
    assert rigged["joints"] == 19
    assert rigged["previewUrl"] == "http://127.0.0.1:8190/?id=two-rigged"


def test_list_models_filters_by_name(client):
    session, out = client
    (out / "helmet-r512.glb").write_bytes(make_glb())
    (out / "barrel-r512.glb").write_bytes(make_glb())

    payload = session.call("list_models", {"filter": "helm"})["result"]["structuredContent"]
    assert [m["name"] for m in payload["models"]] == ["helmet-r512.glb"]


def test_get_preview_returns_a_url_and_the_source_image(client):
    session, out = client
    (out / "hero-r512.glb").write_bytes(make_glb(triangles=9))
    (out / "hero.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 32)

    result = session.call("get_preview", {"id": "hero-r512"})["result"]
    validate(result, RESULT_SCHEMA)
    payload = result["structuredContent"]
    assert payload["viewer"] == "http://127.0.0.1:8190/?id=hero-r512"
    assert payload["sourceImage"] == str(out / "hero.png")
    assert payload["triangles"] == 9


def test_a_result_carries_a_resource_link_to_the_file_not_its_bytes(client):
    session, out = client
    (out / "hero-r512.glb").write_bytes(make_glb(triangles=9))

    result = session.call("get_preview", {"id": "hero-r512"})["result"]
    kinds = [c["type"] for c in result["content"]]
    assert kinds == ["text", "resource_link"]
    link = result["content"][1]
    assert link["uri"] == f"file://{out / 'hero-r512.glb'}"
    assert link["mimeType"] == "model/gltf-binary"
    # The whole point: no base64 anywhere in the result.
    assert "blob" not in json.dumps(result)
    assert len(json.dumps(result)) < 4000


def test_an_unknown_id_is_a_tool_error_the_model_can_read(client):
    session, _ = client
    result = session.call("get_preview", {"id": "ghost"})["result"]
    assert result["isError"] is True
    assert "no model with id ghost" in result["content"][0]["text"]
    assert "list_models" in result["content"][0]["text"]


def test_an_unknown_tool_is_a_tool_error_not_a_crash(client):
    session, _ = client
    result = session.call("teleport")["result"]
    assert result["isError"] is True
    assert "no tool named teleport" in result["content"][0]["text"]


def test_download_copies_the_glb_and_reports_its_hash(client, tmp_path):
    session, out = client
    blob = make_glb(triangles=11)
    (out / "hero-r512.glb").write_bytes(blob)
    destination = tmp_path / "project" / "assets"
    destination.mkdir(parents=True)

    result = session.call("download_glb",
                          {"id": "hero-r512", "destination": str(destination)})["result"]
    validate(result, RESULT_SCHEMA)
    payload = result["structuredContent"]
    copied = destination / "hero-r512.glb"
    assert payload["path"] == str(copied)
    assert copied.read_bytes() == blob
    assert payload["sha256"] == hashlib.sha256(blob).hexdigest()


def test_download_to_a_missing_directory_is_a_tool_error(client, tmp_path):
    session, out = client
    (out / "hero-r512.glb").write_bytes(make_glb())
    result = session.call("download_glb", {"id": "hero-r512",
                                           "destination": str(tmp_path / "nope" / "x.glb")})["result"]
    assert result["isError"] is True


def test_generation_reports_progress_while_it_runs(client, tmp_path, monkeypatch):
    """A tool call that takes minutes must keep the client's idle timer alive."""
    toolkit = server_module.Toolkit(str(tmp_path), "http://127.0.0.1:8190")
    sent = []

    class Recorder(server_module.Server):
        def send(self, message):
            sent.append(message)

    def slow(args):
        import time as _time
        _time.sleep(6.5)
        return {"id": "x", "path": "", "byteSize": 0}

    toolkit.slow_tool = slow
    session = Recorder(toolkit, stdin=None, stdout=None)
    server_module.TOOLS.append({"name": "slow_tool", "title": "", "description": "",
                                "inputSchema": {"type": "object"}, "annotations": {}})
    try:
        session.call_tool({"name": "slow_tool", "arguments": {}}, {"progressToken": "p1"})
    finally:
        server_module.TOOLS.pop()

    progress = [m for m in sent if m.get("method") == "notifications/progress"]
    assert progress, "no progress notification was sent during a long call"
    assert progress[0]["params"]["progressToken"] == "p1"
    assert "elapsed" in progress[0]["params"]["message"]


def test_ids_match_the_preview_layers_ids(tmp_path):
    """One id addresses the asset in both layers, or the preview URL is a lie."""
    sys.path.insert(0, os.path.join(os.path.dirname(LAYER), "preview", "src"))
    import serve as preview

    for name in ("hero-r512.glb", "a brass helmet (final).glb", "two-rigged.glb"):
        assert server_module.asset_id(name) == preview.model_id(name)


# ---- generation, with the GPU layers stood in for ---------------------------


def stub_cli(tmp_path, name, envelope, exit_code=0, artifact=None):
    """A stand-in layer CLI: records its argv, prints an envelope, writes a file."""
    argv_path = tmp_path / f"{name}-argv.json"
    envelope_path = tmp_path / f"{name}-envelope.json"
    envelope_path.write_text(json.dumps(envelope))
    script = tmp_path / f"{name}.py"
    script.write_text(f"""
import json, sys, pathlib
json.dump(sys.argv[1:], open({str(argv_path)!r}, "w"))
artifact = {artifact!r}
if artifact:
    pathlib.Path(artifact).write_bytes({make_glb(triangles=42)!r})
body = open({str(envelope_path)!r}).read()
print(body, file=sys.stderr if {exit_code} else sys.stdout)
sys.exit({exit_code})
""")
    return str(script), argv_path


def test_generate_model_returns_a_handle_and_the_flags_reach_the_pipeline(tmp_path):
    glb = tmp_path / "out" / "asset-r512.glb"
    glb.parent.mkdir()
    pipeline, argv_path = stub_cli(tmp_path, "pipeline", {
        "contractVersion": "1.1",
        "glb": {"uri": str(glb), "mediaType": "model/gltf-binary", "byteSize": 1,
                "checksum": {"sha256": "0" * 64}},
        "triangles": 42,
        "timings": {"imageMs": 9100, "meshMs": 120000},
    }, artifact=str(glb))

    session = Client(glb.parent, env={"T2M_PIPELINE": pipeline})
    try:
        result = session.call("generate_model",
                              {"prompt": "a brass lantern", "targetFaces": 4000,
                               "seed": 7})["result"]
    finally:
        session.close()

    validate(result, RESULT_SCHEMA)
    payload = result["structuredContent"]
    assert payload["id"] == "asset-r512"
    assert payload["path"] == str(glb)
    assert payload["previewUrl"] == "http://127.0.0.1:8190/?id=asset-r512"
    assert payload["triangles"] == 42
    assert payload["prompt"] == "a brass lantern"

    argv = json.load(open(argv_path))
    assert "--target-faces" in argv and argv[argv.index("--target-faces") + 1] == "4000"
    assert "--seed" in argv and argv[argv.index("--seed") + 1] == "7"


def test_a_failing_stage_comes_back_as_its_own_code(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    pipeline, _ = stub_cli(tmp_path, "pipeline", {
        "contractVersion": "1.1", "code": "TEXT2IMAGE_FAILED", "message": "ComfyUI is down",
    }, exit_code=1)

    session = Client(out, env={"T2M_PIPELINE": pipeline})
    try:
        result = session.call("generate_model", {"prompt": "a mug"})["result"]
    finally:
        session.close()

    assert result["isError"] is True
    assert "TEXT2IMAGE_FAILED" in result["content"][0]["text"]
    assert "ComfyUI is down" in result["content"][0]["text"]


def test_generate_then_rig_chains_through_one_call(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    glb = out / "hero-r512.glb"
    rigged = out / "hero-r512-rigged.glb"
    pipeline, _ = stub_cli(tmp_path, "pipeline", {
        "contractVersion": "1.1",
        "glb": {"uri": str(glb), "mediaType": "model/gltf-binary", "byteSize": 1,
                "checksum": {"sha256": "0" * 64}},
        "triangles": 42, "timings": {},
    }, artifact=str(glb))
    rig, rig_argv = stub_cli(tmp_path, "rig", {
        "contractVersion": "1.0",
        "glb": {"uri": str(rigged), "mediaType": "model/gltf-binary", "byteSize": 1,
                "checksum": {"sha256": "0" * 64}},
        "subject": "humanoid",
        "skeleton": {"bones": ["mixamorig:Hips"], "joints": 19, "naming": "mixamo"},
        "animations": [{"name": "idle", "frames": 61, "durationSeconds": 2.5, "loop": True}],
        "engine": {"blender": "5.2.0 LTS", "binding": "bone-heat"}, "elapsedMs": 1147,
    }, artifact=str(rigged))

    session = Client(out, env={"T2M_PIPELINE": pipeline, "T2M_RIG": rig})
    try:
        result = session.call("generate_model",
                              {"prompt": "a viking", "rig": "humanoid"})["result"]
    finally:
        session.close()

    payload = result["structuredContent"]
    assert payload["id"] == "hero-r512-rigged"
    assert payload["source"] == "hero-r512"
    assert payload["clips"] == ["idle"]
    assert payload["bones"] == 1
    argv = json.load(open(rig_argv))
    assert argv[argv.index("--subject") + 1] == "humanoid"


def test_search_assets_passes_the_query_through_and_returns_ids(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    library, argv_path = stub_cli(tmp_path, "assets", {
        "contractVersion": "1.0", "source": "polyhaven", "query": "chair", "total": 2,
        "assets": [{"id": "painted_wooden_chair_01", "name": "Painted Wooden Chair 01",
                    "license": "CC0", "thumbnailUrl": "https://example/x.png"}],
        "elapsedMs": 120,
    })

    session = Client(out, env={"T2M_ASSETS": library})
    try:
        result = session.call("search_assets", {"query": "chair", "limit": 5})["result"]
    finally:
        session.close()

    payload = result["structuredContent"]
    assert payload["total"] == 2
    assert payload["assets"][0]["id"] == "painted_wooden_chair_01"
    argv = json.load(open(argv_path))
    assert argv[0] == "search"
    assert argv[argv.index("--query") + 1] == "chair"
    assert argv[argv.index("--limit") + 1] == "5"


def test_fetch_asset_lands_in_the_output_directory_with_a_preview_url(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    glb = out / "chair-polyhaven.glb"
    library, argv_path = stub_cli(tmp_path, "assets", {
        "contractVersion": "1.0", "source": "polyhaven", "id": "chair",
        "glb": {"uri": str(glb), "mediaType": "model/gltf-binary", "byteSize": 1,
                "checksum": {"sha256": "0" * 64}},
        "license": "CC0", "elapsedMs": 6090,
    }, artifact=str(glb))

    session = Client(out, env={"T2M_ASSETS": library})
    try:
        result = session.call("fetch_asset", {"id": "chair", "resolution": "2k"})["result"]
    finally:
        session.close()

    payload = result["structuredContent"]
    assert payload["id"] == "chair-polyhaven"
    assert payload["license"] == "CC0"
    assert payload["previewUrl"] == "http://127.0.0.1:8190/?id=chair-polyhaven"
    argv = json.load(open(argv_path))
    assert argv[argv.index("--resolution") + 1] == "2k"
