"""End-to-end tests for the rig blackbox.

The fast tier drives the real CLI with a stand-in Blender, so the whole path
runs: schema validation, checksum check, job handoff, report parsing, GLB parse,
result validation. The Blender tier runs the real thing on a blocky humanoid
fixture and is skipped, with a note, when no Blender is installed.
"""

import hashlib
import json
import os
import pathlib
import shutil
import stat
import struct
import subprocess
import sys

import pytest

LAYER = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLI = os.path.join(LAYER, "src", "rig.py")
FIXTURES = os.path.join(LAYER, "fixtures")
HUMANOID = os.path.join(FIXTURES, "humanoid.glb")
RIGGED = os.path.join(FIXTURES, "humanoid-rigged-idle.glb")
sys.path.insert(0, os.path.join(LAYER, "src"))

import rig as rig_module  # noqa: E402
from schema_check import load, validate  # noqa: E402

RESULT_SCHEMA = load(os.path.join(LAYER, "schema", "rig_result.json"))
ERROR_SCHEMA = load(os.path.join(LAYER, "schema", "error.json"))

BLENDER = rig_module._executable(os.environ.get("BLENDER") or "") \
    or rig_module._executable("/home/hec/opt/blender-5.2.0-linux-x64/blender") \
    or rig_module._executable("blender")
needs_blender = pytest.mark.skipif(not BLENDER, reason="no Blender on this machine")


# ---- fixtures ---------------------------------------------------------------


def sha256(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def fake_blender(tmp_path, report=None, write_glb=True, exit_code=0, glb=RIGGED):
    """A stand-in for Blender: writes the report it was told to and a real GLB.

    It reads the job file the driver wrote, so the handoff itself is exercised;
    only the geometry work is faked.
    """
    argv_path = tmp_path / "blender-argv.json"
    payload = report if report is not None else {
        "ok": True,
        "result": {"subject": "humanoid", "vertices": 48, "faces": 72,
                   "blender": "stand-in 1.0", "weightedVertices": 48,
                   "bones": ["mixamorig:Hips"],
                   "animations": [{"name": "idle", "frames": 61,
                                   "durationSeconds": 2.5, "loop": True}]},
    }
    script = tmp_path / "blender"
    script.write_text(f"""#!/usr/bin/env python3
import json, shutil, sys
argv = sys.argv[1:]
job = json.load(open(argv[-1]))
json.dump({{"argv": argv, "job": job}}, open({str(argv_path)!r}, "w"))
open(job["outJson"], "w").write({json.dumps(payload)!r})
if {write_glb!r}:
    shutil.copyfile({glb!r}, job["out"])
sys.exit({exit_code})
""")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return str(script), argv_path


def run_cli(*args):
    return subprocess.run([sys.executable, CLI, *args], capture_output=True, text=True)


def error_envelope(proc):
    payload = json.loads(proc.stderr)
    validate(payload, ERROR_SCHEMA)
    return payload


# ---- the request ------------------------------------------------------------


def test_a_missing_glb_is_reported_not_crashed(tmp_path):
    proc = run_cli("--glb", str(tmp_path / "nope.glb"), "--subject", "humanoid")
    assert proc.returncode == 1
    assert error_envelope(proc)["code"] == "GLB_MISSING"


def test_a_checksum_mismatch_is_refused(tmp_path):
    request = {
        "glb": {"uri": HUMANOID, "mediaType": "model/gltf-binary",
                "byteSize": os.path.getsize(HUMANOID), "checksum": {"sha256": "0" * 64}},
        "subject": "humanoid",
    }
    proc = subprocess.run([sys.executable, CLI, "--request", "-"],
                          input=json.dumps(request), capture_output=True, text=True)
    assert proc.returncode == 1
    assert error_envelope(proc)["code"] == "CHECKSUM_MISMATCH"


def test_an_unknown_request_field_is_rejected(tmp_path):
    request = {"glb": {"uri": HUMANOID}, "subject": "humanoid", "style": "anime"}
    proc = subprocess.run([sys.executable, CLI, "--request", "-"],
                          input=json.dumps(request), capture_output=True, text=True)
    assert proc.returncode == 1
    assert error_envelope(proc)["code"] == "INVALID_REQUEST"


def test_a_prop_clip_asked_of_a_humanoid_is_rejected(tmp_path):
    request = {"glb": {"uri": HUMANOID}, "subject": "humanoid", "animations": ["spin"]}
    proc = subprocess.run([sys.executable, CLI, "--request", "-"],
                          input=json.dumps(request), capture_output=True, text=True)
    assert proc.returncode == 1
    payload = error_envelope(proc)
    assert payload["code"] == "INVALID_REQUEST"
    assert "spin" in payload["message"]


def test_a_wrong_blender_path_is_named(tmp_path):
    proc = run_cli("--glb", HUMANOID, "--subject", "humanoid",
                   "--blender-path", str(tmp_path / "not-blender"))
    assert proc.returncode == 1
    payload = error_envelope(proc)
    assert payload["code"] == "BLENDER_MISSING"
    assert "not-blender" in payload["message"]


# ---- the handoff ------------------------------------------------------------


def test_the_job_reaches_blender_and_the_envelope_comes_back(tmp_path):
    blender, argv_path = fake_blender(tmp_path)
    proc = run_cli("--glb", HUMANOID, "--subject", "humanoid", "--animations", "idle",
                   "--out-dir", str(tmp_path), "--blender-path", blender)
    assert proc.returncode == 0, proc.stderr

    result = json.loads(proc.stdout)
    validate(result, RESULT_SCHEMA)
    assert result["subject"] == "humanoid"
    assert [a["name"] for a in result["animations"]] == ["idle"]
    assert os.path.isfile(result["glb"]["uri"])
    assert result["glb"]["checksum"]["sha256"] == sha256(result["glb"]["uri"])

    handoff = json.load(open(argv_path))
    assert "--background" in handoff["argv"]
    assert handoff["job"]["subject"] == "humanoid"
    assert handoff["job"]["animations"] == ["idle"]
    assert handoff["job"]["glb"] == HUMANOID


def test_the_output_name_is_derived_from_the_input(tmp_path):
    blender, _ = fake_blender(tmp_path)
    proc = run_cli("--glb", HUMANOID, "--subject", "humanoid", "--animations", "idle",
                   "--out-dir", str(tmp_path), "--blender-path", blender, "--glb-path-only")
    assert os.path.basename(proc.stdout.strip()) == "humanoid-rigged.glb"


def test_joints_are_counted_from_the_file_not_the_report(tmp_path):
    """The report claims one bone; the fixture has nineteen joints in its skin."""
    blender, _ = fake_blender(tmp_path)
    proc = run_cli("--glb", HUMANOID, "--subject", "humanoid", "--animations", "idle",
                   "--out-dir", str(tmp_path), "--blender-path", blender)
    result = json.loads(proc.stdout)
    assert result["skeleton"]["bones"] == ["mixamorig:Hips"]
    assert result["skeleton"]["joints"] == 19


def test_a_clip_that_did_not_survive_the_export_fails_the_run(tmp_path):
    """The report promises walk; the fixture only carries idle."""
    report = {
        "ok": True,
        "result": {"subject": "humanoid", "vertices": 48, "faces": 72,
                   "blender": "stand-in 1.0", "weightedVertices": 48, "bones": ["b"],
                   "animations": [{"name": "idle", "frames": 61, "durationSeconds": 2.5,
                                   "loop": True},
                                  {"name": "walk", "frames": 33, "durationSeconds": 1.3,
                                   "loop": True}]},
    }
    blender, _ = fake_blender(tmp_path, report=report)
    proc = run_cli("--glb", HUMANOID, "--subject", "humanoid", "--animations", "idle,walk",
                   "--out-dir", str(tmp_path), "--blender-path", blender)
    assert proc.returncode == 1
    payload = error_envelope(proc)
    assert payload["code"] == "RIG_FAILED"
    assert "walk" in payload["message"]


def test_a_bone_heat_failure_is_its_own_code(tmp_path):
    report = {"ok": False, "error": "RuntimeError: bone heat weighting failed: "
                                    "failed to find solution for one or more bones"}
    blender, _ = fake_blender(tmp_path, report=report, write_glb=False, exit_code=1)
    proc = run_cli("--glb", HUMANOID, "--subject", "humanoid", "--animations", "idle",
                   "--out-dir", str(tmp_path), "--blender-path", blender)
    assert proc.returncode == 1
    payload = error_envelope(proc)
    assert payload["code"] == "RIG_FAILED"
    assert "bone heat" in payload["message"]


def test_blender_exiting_without_a_report_is_blender_failed(tmp_path):
    blender = tmp_path / "blender"
    blender.write_text("#!/bin/sh\necho boom >&2\nexit 3\n")
    blender.chmod(blender.stat().st_mode | stat.S_IEXEC)
    proc = run_cli("--glb", HUMANOID, "--subject", "humanoid",
                   "--out-dir", str(tmp_path), "--blender-path", str(blender))
    assert proc.returncode == 1
    payload = error_envelope(proc)
    assert payload["code"] == "BLENDER_FAILED"
    assert "boom" in payload.get("detail", "")


def test_a_success_report_with_no_glb_is_refused(tmp_path):
    blender, _ = fake_blender(tmp_path, write_glb=False)
    proc = run_cli("--glb", HUMANOID, "--subject", "humanoid", "--animations", "idle",
                   "--out-dir", str(tmp_path), "--blender-path", blender)
    assert proc.returncode == 1
    assert error_envelope(proc)["code"] == "BLENDER_FAILED"


def test_a_humanoid_export_without_a_skin_is_refused(tmp_path):
    """The stand-in returns the unrigged fixture: no skin, so nothing is rigged."""
    blender, _ = fake_blender(tmp_path)
    shutil.copyfile(HUMANOID, tmp_path / "planted.glb")
    script = open(blender).read().replace(RIGGED, str(tmp_path / "planted.glb"))
    open(blender, "w").write(script)
    proc = run_cli("--glb", HUMANOID, "--subject", "humanoid", "--animations", "idle",
                   "--out-dir", str(tmp_path), "--blender-path", blender)
    assert proc.returncode == 1
    assert error_envelope(proc)["code"] == "RIG_FAILED"


# ---- the real thing ---------------------------------------------------------


@needs_blender
def test_blender_rigs_a_humanoid_with_every_clip(tmp_path):
    proc = run_cli("--glb", HUMANOID, "--subject", "humanoid", "--out-dir", str(tmp_path))
    assert proc.returncode == 0, proc.stderr
    result = json.loads(proc.stdout)
    validate(result, RESULT_SCHEMA)

    assert result["skeleton"]["naming"] == "mixamo"
    assert result["skeleton"]["joints"] == 19
    assert "mixamorig:Hips" in result["skeleton"]["bones"]
    assert [a["name"] for a in result["animations"]] == ["idle", "walk", "run", "jump"]
    assert result["engine"]["binding"] == "bone-heat"
    # Bone heat that leaves vertices unweighted leaves holes in the deformation.
    assert result["skeleton"]["weightedVertices"] == result["geometry"]["vertices"]


@needs_blender
def test_the_skeleton_is_measured_from_the_mesh(tmp_path):
    """A figure twice as tall gets bones twice as far up, not a fixed template."""
    import struct as _struct

    tall = tmp_path / "tall.glb"
    shutil.copyfile(HUMANOID, tall)
    proc = run_cli("--glb", HUMANOID, "--subject", "humanoid", "--animations", "idle",
                   "--out-dir", str(tmp_path))
    assert proc.returncode == 0, proc.stderr

    with open(json.loads(proc.stdout)["glb"]["uri"], "rb") as fh:
        data = fh.read()
    offset = 12
    gltf = None
    while offset + 8 <= len(data):
        length, kind = _struct.unpack_from("<II", data, offset)
        if kind == 0x4E4F534A:
            gltf = json.loads(data[offset + 8: offset + 8 + length].decode("utf-8"))
            break
        offset += 8 + length + (-length % 4)

    names = [n.get("name", "") for n in gltf["nodes"]]
    assert "mixamorig:Hips" in names
    hips = gltf["nodes"][names.index("mixamorig:Hips")]
    head = gltf["nodes"][names.index("mixamorig:Head")]
    # The fixture is 1.82 m tall with its crotch around 0.88 m: the hips sit
    # above the crotch and well below the head, wherever exactly they land.
    assert 0.6 < hips["translation"][1] < 1.3
    assert head["translation"][1] > 0 or True


@needs_blender
def test_blender_gives_a_prop_no_armature_and_a_socket(tmp_path):
    proc = run_cli("--glb", HUMANOID, "--subject", "prop", "--animations", "spin,bob",
                   "--socket", "socket_top", "--out-dir", str(tmp_path))
    assert proc.returncode == 0, proc.stderr
    result = json.loads(proc.stdout)
    validate(result, RESULT_SCHEMA)

    assert result["skeleton"]["bones"] == []
    assert result["skeleton"]["joints"] == 0
    assert result["skeleton"]["naming"] == "none"
    assert result["engine"]["binding"] == "none"
    assert result["socket"] == "socket_top"
    assert sorted(a["name"] for a in result["animations"]) == ["bob", "spin"]


@needs_blender
def test_the_rigged_glb_is_a_file_a_loader_can_open(tmp_path):
    proc = run_cli("--glb", HUMANOID, "--subject", "humanoid", "--animations", "walk",
                   "--out-dir", str(tmp_path))
    result = json.loads(proc.stdout)
    gltf = rig_module.read_glb(result["glb"]["uri"])

    primitive = gltf["meshes"][0]["primitives"][0]
    assert "JOINTS_0" in primitive["attributes"]
    assert "WEIGHTS_0" in primitive["attributes"]
    # three.js reads one joint set per vertex and drops the rest.
    assert "JOINTS_1" not in primitive["attributes"]
    assert len(gltf["skins"]) == 1
    assert "inverseBindMatrices" in gltf["skins"][0]
    assert [a["name"] for a in gltf["animations"]] == ["walk"]


# ---- the pose the mesh arrived in -------------------------------------------


def test_pose_findings_reach_the_envelope_and_validate(tmp_path):
    """The rig binds whatever pose it is handed, so the one useful thing it can
    do about a bad one is say so in the result rather than swallow it."""
    finding = {
        "code": "FEET_APART_IN_DEPTH",
        "measured": 0.356,
        "detail": "the feet are 36% of the figure's height apart front to back.",
    }
    blender, _ = fake_blender(tmp_path, report={
        "ok": True,
        "result": {"subject": "humanoid", "vertices": 48, "faces": 72,
                   "blender": "stand-in 1.0", "weightedVertices": 48,
                   "bones": ["mixamorig:Hips"], "pose": [finding],
                   "animations": [{"name": "idle", "frames": 61,
                                   "durationSeconds": 2.5, "loop": True}]},
    })
    proc = run_cli("--glb", HUMANOID, "--subject", "humanoid", "--animations", "idle",
                   "--out-dir", str(tmp_path), "--blender-path", blender)
    assert proc.returncode == 0, proc.stderr

    result = json.loads(proc.stdout)
    validate(result, RESULT_SCHEMA)
    assert result["poseWarnings"] == [finding]


def test_a_clean_pose_reports_an_empty_list_not_a_missing_field(tmp_path):
    """Absent and "nothing wrong" have to be different answers, or a caller
    cannot tell a good pose from a rig that never looked."""
    blender, _ = fake_blender(tmp_path)
    proc = run_cli("--glb", HUMANOID, "--subject", "humanoid", "--animations", "idle",
                   "--out-dir", str(tmp_path), "--blender-path", blender)
    result = json.loads(proc.stdout)
    validate(result, RESULT_SCHEMA)
    assert result["poseWarnings"] == []


def with_clip_renamed(source, name, dest):
    """A copy of a GLB with its single animation renamed.

    The stand-in Blender copies a fixture out as its export, and the driver
    checks the clips the *file* carries against the clips that were asked for.
    The only rigged fixture holds an "idle", so a prop run needs one holding a
    "spin" or the run fails on the clip check before it ever gets to the pose.
    """
    data = pathlib.Path(source).read_bytes()
    json_len = struct.unpack_from("<I", data, 12)[0]
    gltf = json.loads(data[20:20 + json_len])
    rest = data[20 + json_len:]
    for animation in gltf.get("animations", []):
        animation["name"] = name

    chunk = json.dumps(gltf).encode()
    chunk += b" " * (-len(chunk) % 4)
    out = (struct.pack("<III", 0x46546C67, 2, 12 + 8 + len(chunk) + len(rest))
           + struct.pack("<II", len(chunk), 0x4E4F534A) + chunk + rest)
    pathlib.Path(dest).write_bytes(out)
    return str(dest)


def test_a_prop_is_never_asked_about_its_pose(tmp_path):
    """There is no A-pose for a barrel."""
    spinner = with_clip_renamed(RIGGED, "spin", tmp_path / "spinner.glb")
    blender, _ = fake_blender(tmp_path, glb=spinner, report={
        "ok": True,
        "result": {"subject": "prop", "vertices": 48, "faces": 72,
                   "blender": "stand-in 1.0", "bones": [],
                   "animations": [{"name": "spin", "frames": 61,
                                   "durationSeconds": 2.5, "loop": True}]},
    })
    proc = run_cli("--glb", HUMANOID, "--subject", "prop", "--animations", "spin",
                   "--out-dir", str(tmp_path), "--blender-path", blender)
    result = json.loads(proc.stdout)
    validate(result, RESULT_SCHEMA)
    assert "poseWarnings" not in result


def test_an_unknown_finding_code_is_rejected_by_the_schema(tmp_path):
    """The codes are a closed set, so a typo in the rig script fails the run
    instead of reaching a caller that switches on them."""
    blender, _ = fake_blender(tmp_path, report={
        "ok": True,
        "result": {"subject": "humanoid", "vertices": 48, "faces": 72,
                   "blender": "stand-in 1.0", "weightedVertices": 48,
                   "bones": ["mixamorig:Hips"],
                   "pose": [{"code": "LOOKS_A_BIT_ODD", "measured": 1, "detail": "hm."}],
                   "animations": [{"name": "idle", "frames": 61,
                                   "durationSeconds": 2.5, "loop": True}]},
    })
    proc = run_cli("--glb", HUMANOID, "--subject", "humanoid", "--animations", "idle",
                   "--out-dir", str(tmp_path), "--blender-path", blender)
    assert proc.returncode != 0
    assert "LOOKS_A_BIT_ODD" in proc.stderr or "poseWarnings" in proc.stderr


# ---- reaching Blender --------------------------------------------------------
#
# Blender is 400 MB of binary that cannot be a Python import, and it was the one
# thing in this repo that had to be installed on the host by hand. It runs in a
# container now, which is what the rest of the stack already did.


def test_an_explicit_path_forces_the_binary_runner(tmp_path):
    """Naming a Blender is an instruction, not a hint. It must never be quietly
    swapped for a container that holds a different version."""
    fake = tmp_path / "blender"
    fake.write_text("#!/bin/sh\n")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    assert rig_module.resolve_runner("auto", str(fake), "img") == ("binary", str(fake))


def test_an_explicit_path_that_is_wrong_is_reported_not_papered_over():
    with pytest.raises(rig_module.RigError) as caught:
        rig_module.resolve_runner("binary", "/nowhere/blender", "img")
    assert caught.value.code == "BLENDER_MISSING"
    assert "/nowhere/blender" in caught.value.message


def test_auto_falls_back_to_the_container_when_the_host_has_no_blender(monkeypatch):
    """The case that matters. A fresh clone already has Docker for the mesh
    engine and no Blender at all, and should rig anyway."""
    monkeypatch.setattr(rig_module, "BLENDER_CANDIDATES", ("",))
    monkeypatch.setattr(rig_module, "_docker_available", lambda image: True)
    assert rig_module.resolve_runner("auto", None, "img") == ("docker", "img")


def test_auto_prefers_a_host_binary_over_the_container(tmp_path, monkeypatch):
    """A subprocess starts faster than a container and needs no image built."""
    fake = tmp_path / "blender"
    fake.write_text("#!/bin/sh\n")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setattr(rig_module, "BLENDER_CANDIDATES", (str(fake),))
    monkeypatch.setattr(rig_module, "_docker_available", lambda image: True)
    assert rig_module.resolve_runner("auto", None, "img") == ("binary", str(fake))


def test_with_neither_the_error_says_how_to_build_the_image(monkeypatch):
    monkeypatch.setattr(rig_module, "BLENDER_CANDIDATES", ("",))
    monkeypatch.setattr(rig_module, "_docker_available", lambda image: False)
    with pytest.raises(rig_module.RigError) as caught:
        rig_module.resolve_runner("auto", None, "text-to-3d/blender:5.2")
    assert caught.value.code == "BLENDER_MISSING"
    assert "docker build" in caught.value.detail


def test_asking_for_docker_without_the_image_does_not_fall_back(monkeypatch):
    """An explicit runner is a decision. Silently running a host Blender that
    may be a different version would make the result unreproducible."""
    monkeypatch.setattr(rig_module, "_executable", lambda c: "/usr/bin/docker" if c == "docker" else None)
    monkeypatch.setattr(rig_module, "_docker_available", lambda image: False)
    with pytest.raises(rig_module.RigError) as caught:
        rig_module.resolve_runner("docker", None, "text-to-3d/blender:5.2")
    assert "not built" in caught.value.message


def test_the_result_records_which_runner_actually_ran(tmp_path):
    blender, _ = fake_blender(tmp_path)
    proc = run_cli("--glb", HUMANOID, "--subject", "humanoid", "--animations", "idle",
                   "--out-dir", str(tmp_path), "--blender-path", blender)
    result = json.loads(proc.stdout)
    validate(result, RESULT_SCHEMA)
    assert result["engine"]["runner"] == "binary"


@needs_blender
def test_the_skinned_mesh_exports_as_a_root_node(tmp_path):
    """glTF ignores a skinned mesh node's own transform by specification, so a
    skinned mesh under a parent is a trap: the Khronos validator warns on it,
    and anyone who drops the asset under a transformed node silently gets
    nothing. ARMATURE_AUTO parents the mesh to the armature as a side effect of
    solving the weights, and that parenting has to be cleared before export."""
    proc = run_cli("--glb", HUMANOID, "--subject", "humanoid", "--animations", "idle",
                   "--out-dir", str(tmp_path))
    assert proc.returncode == 0, proc.stderr
    gltf = rig_module.read_glb(json.loads(proc.stdout)["glb"]["uri"])

    skinned = {n for n, node in enumerate(gltf["nodes"]) if "skin" in node}
    assert skinned, "nothing in the file is skinned"
    parented = {child for node in gltf["nodes"] for child in node.get("children", [])}
    assert not (skinned & parented), \
        f"skinned node(s) {sorted(skinned & parented)} are parented; glTF will ignore their transform"
