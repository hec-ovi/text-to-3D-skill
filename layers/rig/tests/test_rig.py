"""Contract tests for the rig layer.

The real CLI is driven against a stub rig server over real HTTP. No GPU, no
model, no torch: the model's job is to predict a skeleton, and every failure
this layer can have on its own is downstream of that prediction.

The skeleton fixture is not invented. `fixtures/skeleton-viking.json` is what
SkinTokens actually predicted for a generated viking on the Radeon 8060S, so
the naming pass is tested against the shape the model really emits rather than
against one written to make it pass.
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
CLI = os.path.join(LAYER, "src", "rig.py")
FIXTURES = os.path.join(LAYER, "fixtures")

sys.path.insert(0, os.path.join(LAYER, "src"))

import clips as clip_builder        # noqa: E402
import skeleton as sk               # noqa: E402
from gltf import Glb                # noqa: E402


def viking():
    with open(os.path.join(FIXTURES, "skeleton-viking.json"), encoding="utf-8") as handle:
        return json.load(handle)


# ---- fixtures ---------------------------------------------------------------


def make_glb(vertices=8, triangles=4, node_transform=None, nested=False):
    """A small but structurally real GLB: one mesh, one primitive, indexed."""
    positions = [(float(i), float(i) * 0.5, 0.0) for i in range(vertices)]
    indices = [(i % vertices, (i + 1) % vertices, (i + 2) % vertices) for i in range(triangles)]

    blob = b"".join(struct.pack("<3f", *p) for p in positions)
    index_offset = len(blob)
    blob += b"".join(struct.pack("<3H", *t) for t in indices)
    blob += b"\x00" * (-len(blob) % 4)

    node = {"mesh": 0}
    if node_transform:
        node["translation"] = node_transform
    nodes = [node]
    scene_nodes = [0]
    if nested:
        nodes = [{"children": [1], "translation": [5.0, 0.0, 0.0]}, node]
        scene_nodes = [0]

    gltf = {
        "asset": {"version": "2.0", "generator": "rig test"},
        "scene": 0,
        "scenes": [{"nodes": scene_nodes}],
        "nodes": nodes,
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}, "indices": 1, "mode": 4}]}],
        "accessors": [
            {"bufferView": 0, "componentType": 5126, "count": vertices, "type": "VEC3"},
            {"bufferView": 1, "componentType": 5123, "count": triangles * 3, "type": "SCALAR"},
        ],
        "bufferViews": [
            {"buffer": 0, "byteOffset": 0, "byteLength": index_offset},
            {"buffer": 0, "byteOffset": index_offset, "byteLength": triangles * 6},
        ],
        "buffers": [{"byteLength": len(blob)}],
    }
    payload = json.dumps(gltf).encode("utf-8")
    payload += b" " * (-len(payload) % 4)
    body = struct.pack("<II", len(payload), 0x4E4F534A) + payload
    body += struct.pack("<II", len(blob), 0x004E4942) + blob
    return struct.pack("<III", 0x46546C67, 2, 12 + len(body)) + body


class StubServer:
    """A rig server that answers with a skeleton the test chose."""

    def __init__(self, skeleton=None, status=200, body=None, vertex_count=8):
        self.calls = []
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                request = json.loads(self.rfile.read(length))
                stub.calls.append(request)

                if body is not None:
                    payload = body
                else:
                    data = skeleton if skeleton is not None else viking()
                    joints = len(data["parents"])
                    # One row per vertex, weighted onto the root and its first
                    # child so the numbers are checkable by hand.
                    rows = []
                    for _ in range(vertex_count):
                        row = [0.0] * joints
                        row[0] = 0.6
                        if joints > 1:
                            row[1] = 0.3
                        rows.append(row)
                    flat = [w for row in rows for w in row]
                    payload = json.dumps({
                        "parents": data["parents"],
                        "positions": data["positions"],
                        "skin": base64.b64encode(
                            struct.pack(f"<{len(flat)}f", *flat)).decode("ascii"),
                    }).encode("utf-8")

                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @property
    def endpoint(self):
        return f"http://127.0.0.1:{self.server.server_port}"

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def server():
    made = []

    def make(**kwargs):
        stub = StubServer(**kwargs)
        made.append(stub)
        return stub

    yield make
    for stub in made:
        stub.close()


def run(args, stdin=None):
    return subprocess.run([sys.executable, CLI] + args, input=stdin,
                          capture_output=True, text=True)


def write_glb(tmp_path, name="static.glb", **kwargs):
    path = tmp_path / name
    path.write_bytes(make_glb(**kwargs))
    return str(path)


# ---- naming, against what the model really predicted -------------------------


def test_the_predicted_tree_names_out_as_a_mixamo_skeleton():
    data = viking()
    names = sk.name_joints(data["positions"], data["parents"])

    assert names[0] == "mixamorig:Hips"
    assert names[5] == "mixamorig:Head"
    # +X is left, which is the convention the model was trained against.
    assert names[6] == "mixamorig:LeftShoulder"
    assert names[16] == "mixamorig:RightShoulder"
    assert names[26] == "mixamorig:LeftUpLeg"
    assert names[30] == "mixamorig:RightUpLeg"
    assert names[29] == "mixamorig:LeftToeBase"
    assert len(set(names)) == len(names), "two joints came out with the same name"


def test_the_arms_and_legs_are_told_apart_by_side():
    data = viking()
    names = sk.name_joints(data["positions"], data["parents"])
    for index, (name, position) in enumerate(zip(names, data["positions"])):
        if "Left" in name and "Hips" not in name:
            assert position[0] > 0, f"{name} at index {index} is on the right of the origin"
        if "Right" in name:
            assert position[0] < 0, f"{name} at index {index} is on the left of the origin"


def test_a_tree_that_is_not_a_humanoid_is_refused():
    # A chair: a root and four legs, nothing above it.
    positions = [(0, 0, 0), (1, -1, 1), (-1, -1, 1), (1, -1, -1), (-1, -1, -1)]
    parents = [-1, 0, 0, 0, 0]
    with pytest.raises(sk.SkeletonError):
        sk.name_joints(positions, parents)


def test_a_figure_with_one_leg_is_refused():
    positions = [(0, 0, 0), (0, 1, 0), (0, 2, 0), (1, 1.8, 0), (-1, 1.8, 0), (0, -1, 0)]
    parents = [-1, 0, 1, 2, 2, 0]
    with pytest.raises(sk.SkeletonError):
        sk.name_joints(positions, parents)


# ---- weights ----------------------------------------------------------------


def test_weights_are_cut_to_four_and_made_to_sum_to_one():
    # Nine influences above noise, summing to less than one: exactly what the
    # model returned on the real warrior.
    row = [0.30, 0.20, 0.15, 0.12, 0.08, 0.05, 0.03, 0.01, 0.005] + [0.0] * 25
    joints, weights = sk.prune_and_normalize([row])

    assert len(joints[0]) == 4 and len(weights[0]) == 4
    assert joints[0] == (0, 1, 2, 3), "the four largest influences were not the ones kept"
    assert abs(sum(weights[0]) - 1.0) < 1e-6


def test_a_vertex_the_model_ignored_is_bound_to_the_root():
    """Otherwise it stays at the origin while the rest of the character moves."""
    joints, weights = sk.prune_and_normalize([[0.0] * 12])
    assert joints[0] == (0, 0, 0, 0)
    assert weights[0] == (1.0, 0.0, 0.0, 0.0)


def test_the_influence_budget_is_honoured():
    row = [0.4, 0.3, 0.2, 0.1]
    joints, weights = sk.prune_and_normalize([row], limit=2)
    assert joints[0][:2] == (0, 1)
    assert abs(sum(weights[0]) - 1.0) < 1e-6


# ---- clips ------------------------------------------------------------------


def test_the_walk_moves_the_legs_in_opposition():
    data = viking()
    names = sk.name_joints(data["positions"], data["parents"])
    clip = clip_builder.walk(names, data["positions"])

    left = names.index("mixamorig:LeftUpLeg")
    right = names.index("mixamorig:RightUpLeg")
    assert left in clip["channels"] and right in clip["channels"]

    # Half a cycle apart: at the moment the left thigh is furthest forward the
    # right is furthest back, which is what makes it a stride and not a hop.
    left_keys = clip["channels"][left]["rotation"]
    right_keys = clip["channels"][right]["rotation"]
    quarter = len(left_keys) // 4
    assert left_keys[quarter][1][0] * right_keys[quarter][1][0] < 0


def test_the_arms_counter_swing_against_the_legs():
    data = viking()
    names = sk.name_joints(data["positions"], data["parents"])
    clip = clip_builder.walk(names, data["positions"])

    leg = clip["channels"][names.index("mixamorig:LeftUpLeg")]["rotation"]
    arm = clip["channels"][names.index("mixamorig:LeftArm")]["rotation"]
    quarter = len(leg) // 4
    assert leg[quarter][1][0] * arm[quarter][1][0] < 0, "the left arm swings with the left leg"


def test_every_clip_starts_and_ends_on_the_same_pose():
    """A loop that does not close reads as a stutter once a second."""
    data = viking()
    names = sk.name_joints(data["positions"], data["parents"])
    for clip in clip_builder.build(names, data["positions"]):
        for channels in clip["channels"].values():
            for keys in channels.values():
                first, last = keys[0][1], keys[-1][1]
                assert all(abs(a - b) < 1e-6 for a, b in zip(first, last)), \
                    f"{clip['name']} does not loop"


def test_a_skeleton_with_no_legs_gets_no_walk():
    names = ["mixamorig:Hips", "mixamorig:Spine"]
    assert clip_builder.walk(names, [(0, 0, 0), (0, 1, 0)]) is None


# ---- the whole layer, over HTTP ---------------------------------------------


def test_a_static_glb_comes_back_skinned_and_animated(server, tmp_path):
    stub = server(vertex_count=8)
    path = write_glb(tmp_path)

    proc = run(["--glb", path, "--out-dir", str(tmp_path), "--endpoint", stub.endpoint])
    assert proc.returncode == 0, proc.stderr

    result = json.loads(proc.stdout)
    assert result["skeleton"]["joints"] == 34
    assert result["skeleton"]["root"] == "mixamorig:Hips"
    assert result["skeleton"]["convention"] == "mixamo"
    assert result["animations"] == ["idle", "walk"]

    out = result["glb"]["uri"]
    assert os.path.isfile(out)
    assert out.endswith("-rigged.glb")

    with open(out, "rb") as handle:
        rigged = Glb.parse(handle.read())
    assert len(rigged.gltf["skins"]) == 1
    assert len(rigged.gltf["skins"][0]["joints"]) == 34
    assert len(rigged.gltf["animations"]) == 2
    prim = rigged.gltf["meshes"][0]["primitives"][0]
    assert "JOINTS_0" in prim["attributes"] and "WEIGHTS_0" in prim["attributes"]


def test_the_original_mesh_survives_untouched(server, tmp_path):
    """The point of appending rather than rebuilding."""
    stub = server(vertex_count=8)
    path = write_glb(tmp_path)
    before = Glb.parse(open(path, "rb").read())

    proc = run(["--glb", path, "--out-dir", str(tmp_path), "--endpoint", stub.endpoint])
    assert proc.returncode == 0, proc.stderr
    after = Glb.parse(open(json.loads(proc.stdout)["glb"]["uri"], "rb").read())

    assert after.positions()[0] == before.positions()[0]
    assert after.triangles() == before.triangles()
    assert after.gltf["meshes"][0]["primitives"][0]["indices"] == \
        before.gltf["meshes"][0]["primitives"][0]["indices"]


def test_the_skinned_node_loses_a_transform_the_spec_would_ignore(server, tmp_path):
    """A skinned mesh is in skin space, so a leftover transform makes it jump."""
    stub = server(vertex_count=8)
    path = write_glb(tmp_path, node_transform=[3.0, 1.0, -2.0])

    proc = run(["--glb", path, "--out-dir", str(tmp_path), "--endpoint", stub.endpoint])
    assert proc.returncode == 0, proc.stderr

    rigged = Glb.parse(open(json.loads(proc.stdout)["glb"]["uri"], "rb").read())
    node = next(n for n in rigged.gltf["nodes"] if "mesh" in n)
    assert "skin" in node
    for key in ("translation", "rotation", "scale", "matrix"):
        assert key not in node


def test_a_nested_mesh_node_is_lifted_to_the_scene_root(server, tmp_path):
    stub = server(vertex_count=8)
    path = write_glb(tmp_path, nested=True)

    proc = run(["--glb", path, "--out-dir", str(tmp_path), "--endpoint", stub.endpoint])
    assert proc.returncode == 0, proc.stderr

    rigged = Glb.parse(open(json.loads(proc.stdout)["glb"]["uri"], "rb").read())
    mesh_node = next(i for i, n in enumerate(rigged.gltf["nodes"]) if "mesh" in n)
    scene = rigged.gltf["scenes"][0]["nodes"]
    assert mesh_node in scene
    for node in rigged.gltf["nodes"]:
        assert mesh_node not in node.get("children", [])


def test_joint_nodes_use_trs_so_they_can_be_animated(server, tmp_path):
    """glTF forbids animating the TRS of a node that defines a matrix.

    Writing joints as matrices parses back fine and looks correct until a clip
    targets one, and then the Khronos validator reports one error per animated
    channel. It found eighteen here before this was fixed, which is exactly the
    class of thing a round-trip through our own reader cannot catch.
    """
    stub = server(vertex_count=8)
    path = write_glb(tmp_path)

    proc = run(["--glb", path, "--out-dir", str(tmp_path), "--endpoint", stub.endpoint])
    assert proc.returncode == 0, proc.stderr

    rigged = Glb.parse(open(json.loads(proc.stdout)["glb"]["uri"], "rb").read())
    joint_nodes = set(rigged.gltf["skins"][0]["joints"])
    for index in joint_nodes:
        assert "matrix" not in rigged.gltf["nodes"][index], \
            "a joint written as a matrix cannot carry a clip"

    animated = {channel["target"]["node"]
                for animation in rigged.gltf["animations"]
                for channel in animation["channels"]}
    assert animated, "nothing is animated"
    assert animated <= joint_nodes, "a clip targets something that is not a joint"


def test_no_animate_skins_and_stops(server, tmp_path):
    stub = server(vertex_count=8)
    path = write_glb(tmp_path)

    proc = run(["--glb", path, "--out-dir", str(tmp_path),
                "--endpoint", stub.endpoint, "--no-animate"])
    assert proc.returncode == 0, proc.stderr
    result = json.loads(proc.stdout)
    assert result["animations"] == []

    rigged = Glb.parse(open(result["glb"]["uri"], "rb").read())
    assert rigged.gltf.get("skins")
    assert not rigged.gltf.get("animations")


def test_the_mesh_reaches_the_model_in_its_own_vertex_order(server, tmp_path):
    """Weights come back per vertex, so the orders have to be the same one."""
    stub = server(vertex_count=8)
    path = write_glb(tmp_path, vertices=8, triangles=4)

    proc = run(["--glb", path, "--out-dir", str(tmp_path), "--endpoint", stub.endpoint])
    assert proc.returncode == 0, proc.stderr

    sent = stub.calls[0]
    assert sent["vertexCount"] == 8
    assert sent["faceCount"] == 4
    points = struct.unpack("<24f", base64.b64decode(sent["vertices"]))
    assert points[:3] == (0.0, 0.0, 0.0)
    assert points[3:6] == (1.0, 0.5, 0.0)


# ---- the closed error set ---------------------------------------------------


def test_a_missing_glb_is_reported(tmp_path):
    proc = run(["--glb", str(tmp_path / "nope.glb"), "--out-dir", str(tmp_path)])
    assert proc.returncode == 1
    assert json.loads(proc.stderr)["code"] == "GLB_MISSING"


def test_nothing_listening_is_model_unreachable(tmp_path):
    path = write_glb(tmp_path)
    proc = run(["--glb", path, "--out-dir", str(tmp_path), "--endpoint", "http://127.0.0.1:1"])
    assert proc.returncode == 1
    assert json.loads(proc.stderr)["code"] == "MODEL_UNREACHABLE"


def test_a_server_error_is_model_failed(server, tmp_path):
    stub = server(status=500, body=b"out of memory")
    path = write_glb(tmp_path)
    proc = run(["--glb", path, "--out-dir", str(tmp_path), "--endpoint", stub.endpoint])
    assert proc.returncode == 1
    error = json.loads(proc.stderr)
    assert error["code"] == "MODEL_FAILED"
    assert "out of memory" in error["detail"]


def test_a_prop_is_refused_rather_than_given_hips(server, tmp_path):
    """A treasure chest with a predicted tree is still not a character."""
    chest = {"parents": [-1, 0, 0, 0, 0],
             "positions": [[0, 0, 0], [1, -1, 1], [-1, -1, 1], [1, -1, -1], [-1, -1, -1]]}
    stub = server(skeleton=chest, vertex_count=8)
    path = write_glb(tmp_path)

    proc = run(["--glb", path, "--out-dir", str(tmp_path), "--endpoint", stub.endpoint])
    assert proc.returncode == 1
    error = json.loads(proc.stderr)
    assert error["code"] == "NOT_A_CHARACTER"
    assert list(tmp_path.glob("*-rigged.glb")) == []


def test_a_skeleton_in_the_wrong_coordinate_frame_is_refused(server, tmp_path):
    """The bug this exists for: the model normalises, so its skeleton is scaled.

    Left unmapped, a character 0.98 units tall got a skeleton 1.69 tall, and
    binding a mesh to a skeleton bigger than itself squashes it. What came out
    were short, wide characters with folded knees, and nothing said so.
    """
    data = viking()
    oversized = {"parents": data["parents"],
                 "positions": [[c * 40 for c in p] for p in data["positions"]]}
    stub = server(skeleton=oversized, vertex_count=8)
    path = write_glb(tmp_path)

    proc = run(["--glb", path, "--out-dir", str(tmp_path), "--endpoint", stub.endpoint])

    assert proc.returncode == 1
    error = json.loads(proc.stderr)
    assert error["code"] == "MODEL_FAILED"
    assert "coordinate frame" in error["message"]
    assert list(tmp_path.glob("*-rigged.glb")) == [], "a squashed rig was written anyway"


def test_a_skeleton_that_fits_its_mesh_is_accepted(server, tmp_path):
    """A bone may sit slightly outside the surface; the guard is not anatomy police."""
    data = viking()
    # The fixture's skeleton spans about 1.16 x 1.69 x 0.34, and the test mesh
    # is built to be comfortably larger than that.
    stub = server(skeleton=data, vertex_count=8)
    path = write_glb(tmp_path, vertices=8, triangles=4)

    proc = run(["--glb", path, "--out-dir", str(tmp_path), "--endpoint", stub.endpoint])
    assert proc.returncode == 0, proc.stderr


def test_weights_for_the_wrong_vertex_count_are_refused(server, tmp_path):
    stub = server(vertex_count=3)
    path = write_glb(tmp_path, vertices=8)

    proc = run(["--glb", path, "--out-dir", str(tmp_path), "--endpoint", stub.endpoint])
    assert proc.returncode == 1
    error = json.loads(proc.stderr)
    assert error["code"] == "MODEL_FAILED"
    assert "3 vertices" in error.get("detail", "") + error["message"]


def test_a_checksum_that_disagrees_is_refused(server, tmp_path):
    stub = server(vertex_count=8)
    path = write_glb(tmp_path)
    request = {
        "glb": {"uri": path, "mediaType": "model/gltf-binary",
                "checksum": {"sha256": "0" * 64}},
        "endpoint": stub.endpoint,
        "outDir": str(tmp_path),
    }
    proc = run(["--request", "-"], stdin=json.dumps(request))
    assert proc.returncode == 1
    assert json.loads(proc.stderr)["code"] == "CHECKSUM_MISMATCH"


def test_something_that_is_not_a_glb_is_refused(server, tmp_path):
    stub = server()
    path = tmp_path / "notaglb.glb"
    path.write_bytes(b"this is not a GLB at all, not even close")
    proc = run(["--glb", str(path), "--out-dir", str(tmp_path), "--endpoint", stub.endpoint])
    assert proc.returncode == 1
    assert json.loads(proc.stderr)["code"] == "GLB_INVALID"
    assert stub.calls == [], "a non-GLB was sent to the model anyway"


def test_an_off_contract_request_is_refused(server, tmp_path):
    stub = server()
    path = write_glb(tmp_path)
    request = {"glb": {"uri": path}, "endpoint": stub.endpoint,
               "outDir": str(tmp_path), "maxInfluences": 9}
    proc = run(["--request", "-"], stdin=json.dumps(request))
    assert proc.returncode == 1
    assert json.loads(proc.stderr)["code"] == "INVALID_REQUEST"
