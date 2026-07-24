"""End-to-end tests for the preview server.

A real ThreadingHTTPServer on a real port, driven over real HTTP. Nothing
internal is mocked; the fixtures are actual GLB bytes on disk.
"""

import json
import os
import struct
import sys
import threading
import urllib.error
import urllib.request

import pytest

LAYER = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(LAYER, "src"))

import serve as serve_module  # noqa: E402
from schema_check import load, validate  # noqa: E402
from http.server import ThreadingHTTPServer  # noqa: E402

MODEL_LIST_SCHEMA = load(os.path.join(LAYER, "schema", "model_list.json"))
ERROR_SCHEMA = load(os.path.join(LAYER, "schema", "error.json"))


def make_glb(triangles=4, materials=1, meshes=1):
    """Real GLB bytes: header, JSON chunk, BIN chunk."""
    index_count = triangles * 3
    indices = struct.pack(f"<{index_count}H", *([0] * index_count))
    blob = indices + b"\x00" * (-len(indices) % 4)

    primitive = {"attributes": {"POSITION": 1}, "indices": 0, "mode": 4}
    gltf = {
        "asset": {"version": "2.0", "generator": "preview test"},
        "meshes": [{"primitives": [primitive]} for _ in range(meshes)],
        "accessors": [
            {"bufferView": 0, "componentType": 5123, "count": index_count, "type": "SCALAR"},
            {"bufferView": 0, "componentType": 5126, "count": index_count, "type": "VEC3"},
        ],
        "bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": len(indices)}],
        "buffers": [{"byteLength": len(blob)}],
    }
    if materials:
        gltf["materials"] = [{"pbrMetallicRoughness": {}} for _ in range(materials)]

    json_bytes = json.dumps(gltf).encode("utf-8")
    json_bytes += b" " * (-len(json_bytes) % 4)
    body = struct.pack("<II", len(json_bytes), 0x4E4F534A) + json_bytes
    body += struct.pack("<II", len(blob), 0x004E4942) + blob
    return struct.pack("<III", 0x46546C67, 2, 12 + len(body)) + body


@pytest.fixture
def server(tmp_path):
    """A running preview server over tmp_path. Yields (base_url, dir)."""
    os.environ["T2M_PREVIEW_QUIET"] = "1"
    handler = serve_module.make_handler(str(tmp_path))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_port}", tmp_path
    finally:
        httpd.shutdown()
        httpd.server_close()


def get(url):
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            return response.status, response.read(), dict(response.headers)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), dict(exc.headers)


# ---- the model list ---------------------------------------------------------


def test_lists_glbs_against_the_schema(server):
    base, directory = server
    (directory / "one.glb").write_bytes(make_glb(triangles=7))

    status, body, headers = get(base + "/api/models")
    assert status == 200
    assert headers["Content-Type"].startswith("application/json")

    payload = json.loads(body)
    validate(payload, MODEL_LIST_SCHEMA)
    assert payload["dir"] == str(directory)
    assert len(payload["models"]) == 1
    entry = payload["models"][0]
    assert entry["name"] == "one.glb"
    assert entry["uri"] == "/models/one.glb"
    assert entry["triangles"] == 7
    assert entry["materials"] == 1
    assert entry["readable"] is True


def test_newest_first(server):
    base, directory = server
    (directory / "older.glb").write_bytes(make_glb())
    (directory / "newer.glb").write_bytes(make_glb())
    os.utime(directory / "older.glb", (1_700_000_000, 1_700_000_000))
    os.utime(directory / "newer.glb", (1_800_000_000, 1_800_000_000))

    payload = json.loads(get(base + "/api/models")[1])
    assert [m["name"] for m in payload["models"]] == ["newer.glb", "older.glb"]


def test_counts_every_mesh(server):
    base, directory = server
    (directory / "many.glb").write_bytes(make_glb(triangles=5, meshes=3))
    payload = json.loads(get(base + "/api/models")[1])
    assert payload["models"][0]["triangles"] == 15


def test_unreadable_file_is_flagged_not_hidden(server):
    base, directory = server
    (directory / "broken.glb").write_bytes(b"not a glb at all, really")

    payload = json.loads(get(base + "/api/models")[1])
    validate(payload, MODEL_LIST_SCHEMA)
    entry = payload["models"][0]
    assert entry["readable"] is False
    assert "triangles" not in entry


def test_non_glb_files_are_ignored(server):
    base, directory = server
    (directory / "keep.glb").write_bytes(make_glb())
    (directory / "photo.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (directory / "notes.txt").write_text("hello")

    payload = json.loads(get(base + "/api/models")[1])
    assert [m["name"] for m in payload["models"]] == ["keep.glb"]


def test_empty_directory_is_a_valid_empty_list(server):
    base, _ = server
    payload = json.loads(get(base + "/api/models")[1])
    validate(payload, MODEL_LIST_SCHEMA)
    assert payload["models"] == []


def test_missing_directory_returns_an_error_envelope(tmp_path):
    os.environ["T2M_PREVIEW_QUIET"] = "1"
    handler = serve_module.make_handler(str(tmp_path / "does-not-exist"))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        status, body, _ = get(f"http://127.0.0.1:{httpd.server_port}/api/models")
    finally:
        httpd.shutdown()
        httpd.server_close()

    assert status == 404
    payload = json.loads(body)
    validate(payload, ERROR_SCHEMA)
    assert payload["code"] == "DIR_MISSING"


# ---- serving the bytes ------------------------------------------------------


def test_glb_is_served_with_the_gltf_media_type(server):
    base, directory = server
    blob = make_glb(triangles=9)
    (directory / "asset.glb").write_bytes(blob)

    status, body, headers = get(base + "/models/asset.glb")
    assert status == 200
    assert headers["Content-Type"] == "model/gltf-binary"
    assert body == blob


def test_a_regenerated_asset_is_never_served_from_cache(server):
    base, directory = server
    (directory / "asset.glb").write_bytes(make_glb())
    _status, _body, headers = get(base + "/models/asset.glb")
    assert headers["Cache-Control"] == "no-store"


def test_missing_model_is_a_not_found_envelope(server):
    base, _ = server
    status, body, _ = get(base + "/models/nope.glb")
    assert status == 404
    payload = json.loads(body)
    validate(payload, ERROR_SCHEMA)
    assert payload["code"] == "NOT_FOUND"


def test_path_traversal_is_refused(server):
    base, directory = server
    (directory.parent / "secret.txt").write_text("do not serve me")

    status, body, _ = get(base + "/models/..%2Fsecret.txt")
    assert status in (403, 404)
    payload = json.loads(body)
    validate(payload, ERROR_SCHEMA)
    assert payload["code"] in {"FORBIDDEN", "NOT_FOUND"}
    assert b"do not serve me" not in body


def test_absolute_traversal_is_refused(server):
    base, _ = server
    status, body, _ = get(base + "/models/%2Fetc%2Fpasswd")
    assert status in (403, 404)
    assert b"root:" not in body


# ---- the page ---------------------------------------------------------------


def test_root_serves_the_viewer_page(server):
    base, _ = server
    status, body, headers = get(base + "/")
    assert status == 200
    assert headers["Content-Type"].startswith("text/html")
    text = body.decode("utf-8")
    assert '<div id="app">' in text
    assert 'src="./main.js"' in text
    assert '"three": "./vendor/three/build/three.module.min.js"' in text


def test_modules_are_served_as_javascript(server):
    """A module served as text/plain is refused by every browser."""
    base, _ = server
    for path in ("/main.js", "/ui.js", "/scene.js",
                 "/vendor/three/build/three.module.min.js",
                 "/vendor/three/examples/jsm/loaders/GLTFLoader.js"):
        status, _body, headers = get(base + path)
        assert status == 200, path
        assert headers["Content-Type"].startswith("text/javascript"), path


def test_stylesheet_is_served(server):
    base, _ = server
    status, _body, headers = get(base + "/style.css")
    assert status == 200
    assert headers["Content-Type"].startswith("text/css")


def test_three_is_vendored_so_the_page_needs_no_network(server):
    """The import map points at these two files; without them the page is blank."""
    base, _ = server
    for path in ("/vendor/three/build/three.module.min.js",
                 "/vendor/three/build/three.core.min.js"):
        status, body, _ = get(base + path)
        assert status == 200
        assert len(body) > 100_000, path


def test_unknown_asset_is_a_not_found_envelope(server):
    base, _ = server
    status, body, _ = get(base + "/nope.js")
    assert status == 404
    validate(json.loads(body), ERROR_SCHEMA)


# ---- the source image -------------------------------------------------------


def png_bytes(width=64, height=48):
    import zlib
    raw = b"".join(b"\x00" + b"\x40\x50\x60" * width for _ in range(height))

    def chunk(tag, payload):
        return (struct.pack(">I", len(payload)) + tag + payload
                + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF))

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw))
            + chunk(b"IEND", b""))


def test_the_image_a_mesh_came_from_is_paired_by_name(server):
    base, directory = server
    (directory / "subject.png").write_bytes(png_bytes(1024, 1024))
    (directory / "subject-r512.glb").write_bytes(make_glb())

    payload = json.loads(get(base + "/api/models")[1])
    validate(payload, MODEL_LIST_SCHEMA)
    source = payload["models"][0]["source"]
    assert source["name"] == "subject.png"
    assert source["uri"] == "/images/subject.png"
    assert source["mediaType"] == "image/png"
    assert (source["width"], source["height"]) == (1024, 1024)


def test_pairing_works_at_every_resolution(server):
    base, directory = server
    (directory / "thing.png").write_bytes(png_bytes())
    (directory / "thing-r1536.glb").write_bytes(make_glb())

    payload = json.loads(get(base + "/api/models")[1])
    assert payload["models"][0]["source"]["name"] == "thing.png"


def test_a_mesh_with_no_image_beside_it_has_no_source(server):
    base, directory = server
    (directory / "orphan-r512.glb").write_bytes(make_glb())

    payload = json.loads(get(base + "/api/models")[1])
    validate(payload, MODEL_LIST_SCHEMA)
    assert "source" not in payload["models"][0]


def test_the_baked_texture_atlas_is_not_mistaken_for_the_source(server):
    """The engine writes <stem>-r<res>_base.png; that is an output, not the input."""
    base, directory = server
    (directory / "subject-r512.glb").write_bytes(make_glb())
    (directory / "subject-r512_base.png").write_bytes(png_bytes())

    payload = json.loads(get(base + "/api/models")[1])
    assert "source" not in payload["models"][0]


def test_source_image_bytes_are_served(server):
    base, directory = server
    blob = png_bytes(32, 32)
    (directory / "subject.png").write_bytes(blob)
    (directory / "subject-r512.glb").write_bytes(make_glb())

    status, body, headers = get(base + "/images/subject.png")
    assert status == 200
    assert headers["Content-Type"] == "image/png"
    assert body == blob


def test_the_images_route_refuses_non_images(server):
    base, directory = server
    (directory / "secret.txt").write_text("nope")
    status, body, _ = get(base + "/images/secret.txt")
    assert status == 404
    validate(json.loads(body), ERROR_SCHEMA)
    assert b"nope" not in body


def test_the_images_route_refuses_traversal(server):
    base, directory = server
    (directory.parent / "outside.png").write_bytes(png_bytes())
    status, _body, _ = get(base + "/images/..%2Foutside.png")
    assert status in (403, 404)
