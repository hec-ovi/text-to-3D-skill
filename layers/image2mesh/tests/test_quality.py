"""Tests for the triangle-quality diagnostic.

Synthetic meshes with known geometry, because the point of the tool is that its
numbers can be trusted: an equilateral triangle has to come out at 60 degrees
and a ratio of 1.0, or nothing it says about a real asset means anything.
"""

import json
import math
import os
import struct
import subprocess
import sys

import pytest

LAYER = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(LAYER, "src"))

import quality  # noqa: E402

CLI = os.path.join(LAYER, "src", "quality.py")


def glb(positions, indices):
    """A minimal GLB holding one triangle primitive."""
    pos_bytes = b"".join(struct.pack("<fff", *p) for p in positions)
    idx_bytes = b"".join(struct.pack("<I", i) for i in indices)
    pos_bytes += b"\x00" * (-len(pos_bytes) % 4)
    blob = pos_bytes + idx_bytes

    xs = [p[0] for p in positions]
    ys = [p[1] for p in positions]
    zs = [p[2] for p in positions]
    gltf = {
        "asset": {"version": "2.0"},
        "buffers": [{"byteLength": len(blob)}],
        "bufferViews": [
            {"buffer": 0, "byteOffset": 0, "byteLength": len(pos_bytes)},
            {"buffer": 0, "byteOffset": len(pos_bytes), "byteLength": len(idx_bytes)},
        ],
        "accessors": [
            {"bufferView": 0, "componentType": 5126, "count": len(positions), "type": "VEC3",
             "min": [min(xs), min(ys), min(zs)], "max": [max(xs), max(ys), max(zs)]},
            {"bufferView": 1, "componentType": 5125, "count": len(indices), "type": "SCALAR"},
        ],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}, "indices": 1}]}],
        "nodes": [{"mesh": 0}],
        "scenes": [{"nodes": [0]}],
        "scene": 0,
    }
    chunk = json.dumps(gltf).encode()
    chunk += b" " * (-len(chunk) % 4)
    return (struct.pack("<III", 0x46546C67, 2, 12 + 8 + len(chunk) + 8 + len(blob))
            + struct.pack("<II", len(chunk), 0x4E4F534A) + chunk
            + struct.pack("<II", len(blob), 0x004E4942) + blob)


def write(tmp_path, positions, indices, name="mesh.glb"):
    path = tmp_path / name
    path.write_bytes(glb(positions, indices))
    return str(path)


EQUILATERAL = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.5, 0.0, math.sqrt(3) / 2)]
SLIVER = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.5, 0.0, 0.01)]


def test_equilateral_reads_sixty_degrees_and_ratio_one(tmp_path):
    """The reference point. Every other number the tool prints is read against
    these two, so if this drifts nothing else means anything."""
    report = quality.measure(write(tmp_path, EQUILATERAL, [0, 1, 2]))
    assert report["triangles"] == 1
    assert report["minAngleP50"] == pytest.approx(60.0, abs=0.05)
    assert report["radiusRatioP50"] == pytest.approx(1.0, abs=0.02)
    assert report["sliversUnder10Pct"] == 0
    assert report["degenerate"] == 0


def test_a_sliver_is_caught_by_the_angle_and_the_ratio(tmp_path):
    """A triangle 1 unit long and 0.01 thick. Both measures have to see it, or
    a mesh full of them reads as clean."""
    report = quality.measure(write(tmp_path, SLIVER, [0, 1, 2]))
    assert report["minAngleP50"] < 3
    assert report["sliversUnder10Pct"] == 100
    assert report["radiusRatioP99"] > 20


def test_a_zero_area_triangle_is_counted_not_averaged_in(tmp_path):
    """Three collinear points have no angles at all. Folding a NaN into the
    median would poison every other number in the row."""
    flat = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (2.0, 0.0, 0.0)]
    report = quality.measure(write(tmp_path, EQUILATERAL + flat, [0, 1, 2, 3, 4, 5]))
    assert report["triangles"] == 2
    assert report["degenerate"] == 1
    assert report["minAngleP50"] == pytest.approx(60.0, abs=0.05)


def test_an_unindexed_primitive_is_read_as_a_triangle_soup(tmp_path):
    """glTF lets a primitive leave out its indices, and three consecutive
    positions are then one triangle."""
    gltf_path = write(tmp_path, EQUILATERAL, [0, 1, 2])
    gltf, blob = quality.read_glb(gltf_path)
    del gltf["meshes"][0]["primitives"][0]["indices"]
    chunk = json.dumps(gltf).encode()
    chunk += b" " * (-len(chunk) % 4)
    out = tmp_path / "soup.glb"
    out.write_bytes(struct.pack("<III", 0x46546C67, 2, 12 + 8 + len(chunk) + 8 + len(blob))
                    + struct.pack("<II", len(chunk), 0x4E4F534A) + chunk
                    + struct.pack("<II", len(blob), 0x004E4942) + blob)
    assert quality.measure(str(out))["triangles"] == 1


def test_a_file_that_is_not_a_glb_is_reported_not_crashed(tmp_path):
    bad = tmp_path / "not.glb"
    bad.write_bytes(b"this is not a GLB at all")
    with pytest.raises(quality.QualityError):
        quality.measure(str(bad))


def test_the_cli_prints_a_table_and_json(tmp_path):
    path = write(tmp_path, EQUILATERAL, [0, 1, 2])
    table = subprocess.run([sys.executable, CLI, path], capture_output=True, text=True)
    assert table.returncode == 0
    assert "minAng" in table.stdout
    assert "mesh.glb" in table.stdout

    js = subprocess.run([sys.executable, CLI, "--json", path], capture_output=True, text=True)
    assert js.returncode == 0
    rows = json.loads(js.stdout)
    assert rows[0]["minAngleP50"] == pytest.approx(60.0, abs=0.05)


def test_the_cli_reports_a_bad_file_and_still_measures_the_good_ones(tmp_path):
    good = write(tmp_path, EQUILATERAL, [0, 1, 2], "good.glb")
    bad = tmp_path / "bad.glb"
    bad.write_bytes(b"nope")
    proc = subprocess.run([sys.executable, CLI, "--json", good, str(bad)],
                          capture_output=True, text=True)
    assert proc.returncode == 1
    assert "not a GLB" in proc.stderr
    assert len(json.loads(proc.stdout)) == 1
