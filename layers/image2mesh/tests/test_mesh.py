"""End-to-end tests for the image2mesh blackbox.

The fast tier drives the real CLI with a stand-in engine binary, so the whole
path runs: schema validation, checksum check, flag building, subprocess, GLB
parse, result validation. The slow tier runs the actual Vulkan container and is
skipped unless T2M_RUN_GPU=1 (needs the iGPU and 20 GB of weights).
"""

import hashlib
import json
import os
import struct
import subprocess
import sys
import zlib

import pytest

LAYER = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLI = os.path.join(LAYER, "src", "mesh.py")
sys.path.insert(0, os.path.join(LAYER, "src"))

import mesh  # noqa: E402
from schema_check import load, validate  # noqa: E402

GPU = os.environ.get("T2M_RUN_GPU") == "1"
MODELS = os.environ.get("TRELLIS_MODELS", "/home/hec/models/gguf/trellis2")


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


def make_glb(triangles=2, with_uv=True, with_normals=True, materials=1, bin_chunk=True,
             meshes=1, bad_magic=False, bad_version=False, bad_length=False,
             broken_json=False):
    """A structurally valid GLB, or a deliberately broken one."""
    index_count = triangles * 3
    vertex_count = index_count
    indices = struct.pack(f"<{index_count}H", *range(index_count))
    positions = struct.pack(f"<{vertex_count * 3}f", *([0.0] * vertex_count * 3))
    blob = indices + b"\x00" * (-len(indices) % 4) + positions

    attributes = {"POSITION": 1}
    accessors = [
        {"bufferView": 0, "componentType": 5123, "count": index_count, "type": "SCALAR"},
        {"bufferView": 1, "componentType": 5126, "count": vertex_count, "type": "VEC3"},
    ]
    if with_normals:
        attributes["NORMAL"] = len(accessors)
        accessors.append({"bufferView": 1, "componentType": 5126,
                          "count": vertex_count, "type": "VEC3"})
    if with_uv:
        attributes["TEXCOORD_0"] = len(accessors)
        accessors.append({"bufferView": 1, "componentType": 5126,
                          "count": vertex_count, "type": "VEC2"})

    primitive = {"attributes": attributes, "indices": 0, "mode": 4}
    if materials:
        primitive["material"] = 0

    gltf = {
        "asset": {"version": "2.0", "generator": "t2m test"},
        "scene": 0,
        "scenes": [{"nodes": list(range(meshes))}],
        "nodes": [{"mesh": i} for i in range(meshes)],
        "meshes": [{"primitives": [primitive]} for _ in range(meshes)],
        "accessors": accessors,
        "bufferViews": [
            {"buffer": 0, "byteOffset": 0, "byteLength": len(indices)},
            {"buffer": 0, "byteOffset": len(indices) + (-len(indices) % 4),
             "byteLength": len(positions)},
        ],
        "buffers": [{"byteLength": len(blob)}],
    }
    if materials:
        gltf["materials"] = [{"pbrMetallicRoughness": {"metallicFactor": 0.0}}]

    json_bytes = b"{ broken" if broken_json else json.dumps(gltf).encode("utf-8")
    json_bytes += b" " * (-len(json_bytes) % 4)
    blob += b"\x00" * (-len(blob) % 4)

    body = struct.pack("<II", len(json_bytes), 0x4E4F534A) + json_bytes
    if bin_chunk:
        body += struct.pack("<II", len(blob), 0x004E4942) + blob

    magic = 0x11111111 if bad_magic else 0x46546C67
    version = 3 if bad_version else 2
    total = 12 + len(body) + (99 if bad_length else 0)
    return struct.pack("<III", magic, version, total) + body


def write_png(tmp_path, name="in.png"):
    data = make_png()
    path = tmp_path / name
    path.write_bytes(data)
    return str(path), hashlib.sha256(data).hexdigest()


def fake_engine(tmp_path, glb=None, exit_code=0, stderr="", write_output=True):
    """A stand-in t2m-cli that records its argv and writes a GLB."""
    payload = glb if glb is not None else make_glb()
    blob_path = tmp_path / "payload.glb"
    blob_path.write_bytes(payload)
    argv_path = tmp_path / "argv.json"
    script = tmp_path / "fake-engine.py"
    script.write_text(f"""#!/usr/bin/env python3
import json, shutil, sys
argv = sys.argv[1:]
json.dump(argv, open({str(argv_path)!r}, "w"))
sys.stderr.write({stderr!r})
if {write_output!r} and "--output" in argv:
    shutil.copyfile({str(blob_path)!r}, argv[argv.index("--output") + 1])
sys.exit({exit_code})
""")
    script.chmod(0o755)
    return str(script), argv_path


def run_cli(*args):
    return subprocess.run([sys.executable, CLI, *args], capture_output=True, text=True)


def error_envelope(proc):
    payload = json.loads(proc.stderr)
    validate(payload, load(os.path.join(LAYER, "schema", "error.json")))
    return payload


# ---- happy path through the real entry point --------------------------------


def test_cli_emits_a_valid_result_envelope(tmp_path):
    png, _ = write_png(tmp_path)
    engine, _ = fake_engine(tmp_path)
    proc = run_cli("--image", png, "--out-dir", str(tmp_path),
                   "--runner", "binary", "--binary-path", engine)

    assert proc.returncode == 0, proc.stderr
    result = json.loads(proc.stdout)
    validate(result, load(os.path.join(LAYER, "schema", "mesh_result.json")))

    glb_path = result["glb"]["uri"]
    assert os.path.isfile(glb_path)
    blob = open(glb_path, "rb").read()
    assert result["glb"]["byteSize"] == len(blob)
    assert result["glb"]["checksum"]["sha256"] == hashlib.sha256(blob).hexdigest()
    assert result["glb"]["mediaType"] == "model/gltf-binary"
    assert result["engine"]["backend"] == "vulkan"


def test_geometry_is_read_from_the_glb(tmp_path):
    png, _ = write_png(tmp_path)
    engine, _ = fake_engine(tmp_path, glb=make_glb(triangles=17, with_uv=True,
                                                   with_normals=False))
    proc = run_cli("--image", png, "--out-dir", str(tmp_path),
                   "--runner", "binary", "--binary-path", engine)

    geometry = json.loads(proc.stdout)["geometry"]
    assert geometry["triangles"] == 17
    assert geometry["meshes"] == 1
    assert geometry["primitives"] == 1
    assert geometry["hasUv"] is True
    assert geometry["hasNormals"] is False


def test_output_name_encodes_the_resolution(tmp_path):
    png, _ = write_png(tmp_path, "subject.png")
    engine, _ = fake_engine(tmp_path)
    proc = run_cli("--image", png, "--out-dir", str(tmp_path), "--res", "1024",
                   "--runner", "binary", "--binary-path", engine)
    assert os.path.basename(json.loads(proc.stdout)["glb"]["uri"]) == "subject-r1024.glb"


def test_gpu_fallback_is_always_refused(tmp_path):
    png, _ = write_png(tmp_path)
    engine, argv_path = fake_engine(tmp_path)
    run_cli("--image", png, "--out-dir", str(tmp_path),
            "--runner", "binary", "--binary-path", engine)
    assert "--require-gpu" in json.loads(argv_path.read_text())


def test_request_knobs_reach_the_engine(tmp_path):
    png, digest = write_png(tmp_path)
    engine, argv_path = fake_engine(tmp_path)
    request = {
        "image": {"uri": png, "mediaType": "image/png",
                  "byteSize": os.path.getsize(png), "checksum": {"sha256": digest}},
        "resolution": 1024, "seed": 7, "texture": False, "boxUv": True,
        "backgroundRemoval": "birefnet", "atlasPx": 4096, "decimateFaces": 0,
        "textureResolution": 512, "band": 2, "maxTokens": 8192,
        "runner": "binary", "binaryPath": engine, "outDir": str(tmp_path),
    }
    proc = subprocess.run([sys.executable, CLI, "--request", "-"],
                          input=json.dumps(request), capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr

    argv = json.loads(argv_path.read_text())
    flat = " ".join(argv)
    assert "--res 1024" in flat
    assert "--seed 7" in flat
    assert "--no-texture" in argv
    assert "--box-uv" in argv
    assert "--bg-removal birefnet" in flat
    assert "--atlas 4096" in flat
    assert "--decim 0" in flat
    assert "--tex-res 512" in flat
    assert "--band 2" in flat
    assert "--max-tokens 8192" in flat


def test_texture_on_by_default_adds_no_flag(tmp_path):
    png, _ = write_png(tmp_path)
    engine, argv_path = fake_engine(tmp_path)
    run_cli("--image", png, "--out-dir", str(tmp_path),
            "--runner", "binary", "--binary-path", engine)
    assert "--no-texture" not in json.loads(argv_path.read_text())


def test_device_name_is_lifted_from_engine_stderr(tmp_path):
    png, _ = write_png(tmp_path)
    engine, _ = fake_engine(tmp_path, stderr="vulkan: Radeon 8060S Graphics (RADV STRIX_HALO)\n")
    proc = run_cli("--image", png, "--out-dir", str(tmp_path),
                   "--runner", "binary", "--binary-path", engine)
    assert json.loads(proc.stdout)["engine"]["device"] == "Radeon 8060S Graphics (RADV STRIX_HALO)"


# ---- failure paths ----------------------------------------------------------


def test_missing_image(tmp_path):
    engine, _ = fake_engine(tmp_path)
    proc = run_cli("--image", str(tmp_path / "nope.png"), "--out-dir", str(tmp_path),
                   "--runner", "binary", "--binary-path", engine)
    assert proc.returncode == 1
    assert error_envelope(proc)["code"] == "IMAGE_MISSING"


def test_checksum_mismatch_is_refused(tmp_path):
    png, _ = write_png(tmp_path)
    engine, _ = fake_engine(tmp_path)
    request = {
        "image": {"uri": png, "mediaType": "image/png", "byteSize": os.path.getsize(png),
                  "checksum": {"sha256": "0" * 64}},
        "runner": "binary", "binaryPath": engine, "outDir": str(tmp_path),
    }
    with pytest.raises(mesh.MeshError) as exc:
        mesh.generate(request)
    assert exc.value.code == "CHECKSUM_MISMATCH"


def test_engine_nonzero_exit(tmp_path):
    png, _ = write_png(tmp_path)
    engine, _ = fake_engine(tmp_path, exit_code=3, stderr="vulkan device lost")
    proc = run_cli("--image", png, "--out-dir", str(tmp_path),
                   "--runner", "binary", "--binary-path", engine)
    payload = error_envelope(proc)
    assert payload["code"] == "ENGINE_FAILED"
    assert "vulkan device lost" in payload["detail"]


def test_engine_exits_clean_without_writing_anything(tmp_path):
    png, _ = write_png(tmp_path)
    engine, _ = fake_engine(tmp_path, write_output=False)
    proc = run_cli("--image", png, "--out-dir", str(tmp_path),
                   "--runner", "binary", "--binary-path", engine)
    assert error_envelope(proc)["code"] == "ENGINE_FAILED"


def test_server_runner_unreachable(tmp_path):
    png, _ = write_png(tmp_path)
    proc = run_cli("--image", png, "--out-dir", str(tmp_path),
                   "--runner", "server", "--endpoint", "http://127.0.0.1:1")
    assert error_envelope(proc)["code"] == "ENGINE_UNREACHABLE"


def test_binary_path_must_exist(tmp_path):
    png, digest = write_png(tmp_path)
    with pytest.raises(mesh.MeshError) as exc:
        mesh.generate({"image": {"uri": png, "mediaType": "image/png",
                                 "checksum": {"sha256": digest}},
                       "runner": "binary", "binaryPath": "/nonexistent/t2m-cli"})
    assert exc.value.code == "INVALID_REQUEST"


def test_unknown_request_field_is_rejected(tmp_path):
    png, digest = write_png(tmp_path)
    with pytest.raises(mesh.MeshError) as exc:
        mesh.generate({"image": {"uri": png, "mediaType": "image/png",
                                 "checksum": {"sha256": digest}},
                       "steps": 4})
    assert exc.value.code == "INVALID_REQUEST"


def test_unsupported_resolution_is_rejected(tmp_path):
    png, digest = write_png(tmp_path)
    with pytest.raises(mesh.MeshError) as exc:
        mesh.generate({"image": {"uri": png, "mediaType": "image/png",
                                 "checksum": {"sha256": digest}},
                       "resolution": 768})
    assert exc.value.code == "INVALID_REQUEST"


# ---- GLB validation ---------------------------------------------------------


@pytest.mark.parametrize("kwargs, why", [
    ({"bad_magic": True}, "magic"),
    ({"bad_version": True}, "version"),
    ({"bad_length": True}, "length"),
    ({"broken_json": True}, "json"),
    ({"bin_chunk": False}, "no bin chunk"),
])
def test_broken_glb_never_leaves_the_layer(tmp_path, kwargs, why):
    png, _ = write_png(tmp_path)
    engine, _ = fake_engine(tmp_path, glb=make_glb(**kwargs))
    proc = run_cli("--image", png, "--out-dir", str(tmp_path),
                   "--runner", "binary", "--binary-path", engine)
    assert proc.returncode == 1, f"{why} should have been rejected"
    assert error_envelope(proc)["code"] == "GLB_INVALID"


def test_truncated_glb_is_rejected(tmp_path):
    png, _ = write_png(tmp_path)
    engine, _ = fake_engine(tmp_path, glb=make_glb()[:40])
    proc = run_cli("--image", png, "--out-dir", str(tmp_path),
                   "--runner", "binary", "--binary-path", engine)
    assert error_envelope(proc)["code"] == "GLB_INVALID"


def test_glb_without_meshes_is_rejected(tmp_path):
    png, _ = write_png(tmp_path)
    empty = json.dumps({"asset": {"version": "2.0"}, "meshes": []}).encode()
    empty += b" " * (-len(empty) % 4)
    body = struct.pack("<II", len(empty), 0x4E4F534A) + empty
    body += struct.pack("<II", 4, 0x004E4942) + b"\x00\x00\x00\x00"
    blob = struct.pack("<III", 0x46546C67, 2, 12 + len(body)) + body

    engine, _ = fake_engine(tmp_path, glb=blob)
    proc = run_cli("--image", png, "--out-dir", str(tmp_path),
                   "--runner", "binary", "--binary-path", engine)
    assert error_envelope(proc)["code"] == "GLB_INVALID"


def test_multi_mesh_glb_counts_every_primitive(tmp_path):
    png, _ = write_png(tmp_path)
    engine, _ = fake_engine(tmp_path, glb=make_glb(triangles=5, meshes=3))
    proc = run_cli("--image", png, "--out-dir", str(tmp_path),
                   "--runner", "binary", "--binary-path", engine)
    geometry = json.loads(proc.stdout)["geometry"]
    assert geometry["meshes"] == 3
    assert geometry["primitives"] == 3
    assert geometry["triangles"] == 15


# ---- the real thing ---------------------------------------------------------


@pytest.mark.skipif(not GPU, reason="needs the iGPU and the TRELLIS.2 weights; set T2M_RUN_GPU=1")
def test_docker_runner_produces_a_loadable_glb(tmp_path):
    """The full Vulkan container on a real image. Minutes, not seconds."""
    fixture = os.path.join(LAYER, "fixtures", "bench-subject.png")
    if not os.path.isfile(fixture):
        pytest.skip("no fixtures/bench-subject.png")

    proc = run_cli("--image", fixture, "--out-dir", str(tmp_path), "--res", "512",
                   "--runner", "docker", "--models-dir", MODELS, "--timeout", "3600")
    assert proc.returncode == 0, proc.stderr

    result = json.loads(proc.stdout)
    validate(result, load(os.path.join(LAYER, "schema", "mesh_result.json")))
    assert result["geometry"]["triangles"] > 1000
    assert result["geometry"]["hasUv"]
    assert result["glb"]["byteSize"] > 10_000
