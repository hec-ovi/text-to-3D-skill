"""End-to-end tests for the assets blackbox.

A real HTTP server stands in for Poly Haven, serving a catalogue, a file
manifest and a real .gltf with its .bin sidecar. Everything else is the real
thing: the search, the download, the Blender conversion, the envelopes. Nothing
here touches the network.
"""

import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

LAYER = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLI = os.path.join(LAYER, "src", "assets.py")
FIXTURES = os.path.join(LAYER, "fixtures")
sys.path.insert(0, os.path.join(LAYER, "src"))

import assets as assets_module  # noqa: E402
from schema_check import load, validate  # noqa: E402

SEARCH_SCHEMA = load(os.path.join(LAYER, "schema", "search_result.json"))
FETCH_SCHEMA = load(os.path.join(LAYER, "schema", "fetch_result.json"))
ERROR_SCHEMA = load(os.path.join(LAYER, "schema", "error.json"))

BLENDER = assets_module.find_blender.__wrapped__ if False else None
try:
    BLENDER = assets_module.find_blender()
except assets_module.AssetError:
    BLENDER = None
needs_blender = pytest.mark.skipif(not BLENDER, reason="no Blender on this machine")


CATALOGUE = {
    "painted_wooden_chair_01": {
        "name": "Painted Wooden Chair 01",
        "categories": ["furniture", "seating"],
        "tags": ["chair", "wood", "painted"],
        "authors": {"Kuutti Siitonen": "All"},
        "polycount": 724,
    },
    "brass_lantern": {
        "name": "Brass Lantern",
        "categories": ["decor"],
        "tags": ["lantern", "brass", "metal"],
        "authors": {"Someone Else": "All"},
        "polycount": 12000,
    },
    "no_gltf_asset": {
        "name": "Blend Only",
        "categories": ["decor"],
        "tags": ["blend"],
        "authors": {"Nobody": "All"},
        "polycount": 10,
    },
}


class StubLibrary:
    """Poly Haven's three endpoints, over real HTTP, with real file bytes."""

    def __init__(self):
        self.agents = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _json(self, payload, status=200):
                body = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _file(self, name):
                with open(os.path.join(FIXTURES, name), "rb") as fh:
                    body = fh.read()
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                outer.agents.append(self.headers.get("User-Agent", ""))
                base = f"http://127.0.0.1:{outer.port}"
                if self.path.startswith("/assets"):
                    return self._json(CATALOGUE)
                if self.path == "/files/painted_wooden_chair_01":
                    return self._json({"gltf": {"1k": {"gltf": {
                        "url": f"{base}/dl/cube.gltf",
                        "include": {"cube.bin": {"url": f"{base}/dl/cube.bin", "size": 840}},
                    }}}})
                if self.path == "/files/traversal":
                    return self._json({"gltf": {"1k": {"gltf": {
                        "url": f"{base}/dl/cube.gltf",
                        "include": {"../escaped.bin": {"url": f"{base}/dl/cube.bin"}},
                    }}}})
                if self.path == "/files/no_gltf_asset":
                    return self._json({"blend": {"1k": {}}})
                if self.path == "/files/ghost":
                    return self._json({})
                if self.path.startswith("/dl/"):
                    return self._file(os.path.basename(self.path))
                self._json({"error": "not found"}, status=404)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_port
        self.url = f"http://127.0.0.1:{self.port}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def library():
    stub = StubLibrary()
    try:
        yield stub
    finally:
        stub.close()


def run_cli(*args):
    return subprocess.run([sys.executable, CLI, *args], capture_output=True, text=True)


def error_envelope(proc):
    payload = json.loads(proc.stderr)
    validate(payload, ERROR_SCHEMA)
    return payload


# ---- search -----------------------------------------------------------------


def test_search_matches_name_tags_and_categories(library):
    proc = run_cli("search", "--query", "chair wood", "--endpoint", library.url)
    assert proc.returncode == 0, proc.stderr
    result = json.loads(proc.stdout)
    validate(result, SEARCH_SCHEMA)
    assert [a["id"] for a in result["assets"]] == ["painted_wooden_chair_01"]
    assert result["assets"][0]["license"] == "CC0"
    assert result["assets"][0]["triangles"] == 724
    assert result["assets"][0]["thumbnailUrl"].endswith("painted_wooden_chair_01.png?width=256")


def test_search_needs_every_word_to_appear(library):
    proc = run_cli("search", "--query", "chair brass", "--endpoint", library.url)
    assert json.loads(proc.stdout)["assets"] == []


def test_search_with_no_query_returns_the_catalogue_cheapest_first(library):
    result = json.loads(run_cli("search", "--endpoint", library.url).stdout)
    assert result["total"] == 3
    assert [a["triangles"] for a in result["assets"]] == [10, 724, 12000]


def test_search_limit_caps_the_list_but_not_the_count(library):
    result = json.loads(run_cli("search", "--limit", "1", "--endpoint", library.url).stdout)
    assert result["total"] == 3
    assert len(result["assets"]) == 1


def test_every_request_names_this_software(library):
    """Poly Haven's terms require it, and say unnamed traffic gets blocked."""
    run_cli("search", "--endpoint", library.url)
    assert library.agents
    assert all("text-to-3d-skill" in agent for agent in library.agents)


def test_an_unreachable_library_is_reported_not_raised():
    proc = run_cli("search", "--endpoint", "http://127.0.0.1:1", "--timeout", "5")
    assert proc.returncode == 1
    assert error_envelope(proc)["code"] == "LIBRARY_UNREACHABLE"


# ---- fetch ------------------------------------------------------------------


@needs_blender
def test_fetch_downloads_the_sidecars_and_writes_one_glb(library, tmp_path):
    proc = run_cli("fetch", "--id", "painted_wooden_chair_01", "--out-dir", str(tmp_path),
                   "--endpoint", library.url)
    assert proc.returncode == 0, proc.stderr
    result = json.loads(proc.stdout)
    validate(result, FETCH_SCHEMA)

    glb = tmp_path / "painted_wooden_chair_01-polyhaven.glb"
    assert result["glb"]["uri"] == str(glb)
    assert glb.read_bytes()[:4] == b"glTF"
    assert result["license"] == "CC0"
    # The .bin was 840 bytes and the .gltf about 1.3 KB: both were fetched.
    assert result["downloadedBytes"] > 1500


@needs_blender
def test_the_fetched_glb_is_self_contained(library, tmp_path):
    """One file, buffers embedded: the preview layer lists .glb and nothing else."""
    run_cli("fetch", "--id", "painted_wooden_chair_01", "--out-dir", str(tmp_path),
            "--endpoint", library.url)
    written = sorted(p.name for p in tmp_path.iterdir())
    assert written == ["painted_wooden_chair_01-polyhaven.glb"]


def test_an_unknown_id_is_reported(library, tmp_path):
    proc = run_cli("fetch", "--id", "ghost", "--out-dir", str(tmp_path),
                   "--endpoint", library.url)
    assert proc.returncode == 1
    assert error_envelope(proc)["code"] == "ASSET_MISSING"


def test_an_asset_with_no_gltf_variant_says_so(library, tmp_path):
    proc = run_cli("fetch", "--id", "no_gltf_asset", "--out-dir", str(tmp_path),
                   "--endpoint", library.url)
    assert proc.returncode == 1
    assert error_envelope(proc)["code"] == "NO_GLTF"


def test_a_wrong_blender_path_is_named(library, tmp_path):
    proc = run_cli("fetch", "--id", "painted_wooden_chair_01", "--out-dir", str(tmp_path),
                   "--endpoint", library.url, "--blender-path", str(tmp_path / "nope"))
    assert proc.returncode == 1
    assert error_envelope(proc)["code"] == "BLENDER_MISSING"


@needs_blender
def test_a_manifest_that_escapes_its_directory_is_refused(library, tmp_path):
    """Sidecar paths come from the library and are written verbatim, so a ../
    in one would land outside the job directory."""
    proc = run_cli("fetch", "--id", "traversal", "--out-dir", str(tmp_path),
                   "--endpoint", library.url)
    assert proc.returncode == 1
    payload = error_envelope(proc)
    assert payload["code"] == "LIBRARY_ERROR"
    assert "escape" in payload["message"]
    assert not list(tmp_path.iterdir())


def test_an_unknown_request_field_is_rejected(tmp_path):
    with pytest.raises(assets_module.AssetError) as caught:
        assets_module.search({"query": "x", "sortBy": "polycount"})
    assert caught.value.code == "INVALID_REQUEST"


def test_a_bad_request_is_reported_before_a_missing_blender(library, tmp_path):
    """Ordering, not cosmetics. On a machine with no Blender this layer used to
    answer every question with BLENDER_MISSING, so a typo in an id looked like
    an environment problem and sent the caller off installing things."""
    proc = run_cli("fetch", "--id", "ghost", "--out-dir", str(tmp_path),
                   "--endpoint", library.url, "--blender-path", "/nowhere/blender")
    assert proc.returncode == 1
    assert error_envelope(proc)["code"] == "ASSET_MISSING"
