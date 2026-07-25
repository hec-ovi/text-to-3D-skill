"""End-to-end tests for the pipeline blackbox.

These run the real thing across all three layers: the pipeline CLI invokes the
real text2image CLI (against a stub ComfyUI) and the real image2mesh CLI (with
a stand-in engine binary). Nothing internal is mocked; the only substitutions
are at the two external boundaries, the GPU and the diffusion backend.
"""

import json
import os
import subprocess
import sys

import pytest

LAYER = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LAYERS = os.path.dirname(LAYER)
CLI = os.path.join(LAYER, "src", "pipeline.py")
sys.path.insert(0, os.path.join(LAYER, "src"))
sys.path.insert(0, os.path.join(LAYERS, "text2image", "tests"))
sys.path.insert(0, os.path.join(LAYERS, "image2mesh", "tests"))

import pipeline  # noqa: E402
from schema_check import load, validate  # noqa: E402
from test_klein import StubComfy, make_png  # noqa: E402
from test_mesh import fake_engine, make_glb  # noqa: E402


def run_cli(*args):
    return subprocess.run([sys.executable, CLI, *args], capture_output=True, text=True)


def error_envelope(proc):
    payload = json.loads(proc.stderr)
    validate(payload, load(os.path.join(LAYER, "schema", "error.json")))
    return payload


def base_args(tmp_path, comfy, engine):
    return ("--out-dir", str(tmp_path), "--comfy", comfy.url,
            "--runner", "binary", "--engine-path", engine)


# ---- happy path -------------------------------------------------------------


def test_prompt_to_glb(tmp_path):
    engine, _ = fake_engine(tmp_path, glb=make_glb(triangles=1234))
    with StubComfy(png=make_png(512, 512)) as comfy:
        proc = run_cli("--prompt", "a brass diving helmet",
                       *base_args(tmp_path, comfy, engine))

    assert proc.returncode == 0, proc.stderr
    result = json.loads(proc.stdout)
    validate(result, load(os.path.join(LAYER, "schema", "text_to_mesh_result.json")))

    assert result["prompt"] == "a brass diving helmet"
    assert result["triangles"] == 1234
    assert result["glb"]["mediaType"] == "model/gltf-binary"
    assert os.path.isfile(result["glb"]["uri"])
    assert os.path.isfile(result["stages"]["text2image"]["image"]["uri"])


def test_glb_path_only_prints_one_line(tmp_path):
    engine, _ = fake_engine(tmp_path)
    with StubComfy() as comfy:
        proc = run_cli("--prompt", "a lantern", "--glb-path-only",
                       *base_args(tmp_path, comfy, engine))
    lines = proc.stdout.strip().splitlines()
    assert len(lines) == 1
    assert lines[0].endswith(".glb")
    assert os.path.isfile(lines[0])


def test_both_stage_envelopes_are_carried_through(tmp_path):
    engine, _ = fake_engine(tmp_path)
    with StubComfy() as comfy:
        proc = run_cli("--prompt", "a kettle", *base_args(tmp_path, comfy, engine))

    stages = json.loads(proc.stdout)["stages"]
    validate(stages["text2image"],
             load(os.path.join(LAYERS, "text2image", "schema", "image_result.json")))
    validate(stages["image2mesh"],
             load(os.path.join(LAYERS, "image2mesh", "schema", "mesh_result.json")))


def test_the_image_handed_to_stage_two_is_the_one_stage_one_wrote(tmp_path):
    """The seam: no re-hashing, no path rewriting between the stages."""
    engine, argv_path = fake_engine(tmp_path)
    with StubComfy() as comfy:
        proc = run_cli("--prompt", "a stone idol", *base_args(tmp_path, comfy, engine))

    result = json.loads(proc.stdout)
    png = result["stages"]["text2image"]["image"]["uri"]
    argv = json.loads(argv_path.read_text())
    assert argv[argv.index("--image") + 1] == png


def test_prompt_seeds_the_image_deterministically(tmp_path):
    engine, _ = fake_engine(tmp_path)
    with StubComfy() as comfy:
        first = json.loads(run_cli("--prompt", "a rusty anchor",
                                   *base_args(tmp_path, comfy, engine)).stdout)
        second = json.loads(run_cli("--prompt", "a rusty anchor",
                                    *base_args(tmp_path, comfy, engine)).stdout)
    assert first["stages"]["text2image"]["seed"] == second["stages"]["text2image"]["seed"]
    assert first["glb"]["checksum"] == second["glb"]["checksum"]


def test_resolution_and_texture_reach_the_engine(tmp_path):
    engine, argv_path = fake_engine(tmp_path)
    with StubComfy() as comfy:
        run_cli("--prompt", "a chair", "--res", "1024", "--no-texture",
                "--bg-removal", "birefnet", *base_args(tmp_path, comfy, engine))
    flat = " ".join(json.loads(argv_path.read_text()))
    assert "--res 1024" in flat
    assert "--no-texture" in flat
    assert "--bg-removal birefnet" in flat


def test_target_faces_reaches_the_engine(tmp_path):
    engine, argv_path = fake_engine(tmp_path)
    with StubComfy() as comfy:
        run_cli("--prompt", "a wooden crate", "--target-faces", "3000",
                *base_args(tmp_path, comfy, engine))
    assert "--target-faces 3000" in " ".join(json.loads(argv_path.read_text()))


def test_without_target_faces_the_engine_picks_its_own(tmp_path):
    engine, argv_path = fake_engine(tmp_path)
    with StubComfy() as comfy:
        run_cli("--prompt", "a wooden crate", *base_args(tmp_path, comfy, engine))
    assert "--target-faces" not in json.loads(argv_path.read_text())


def test_texture_knobs_reach_the_engine(tmp_path):
    engine, argv_path = fake_engine(tmp_path)
    with StubComfy() as comfy:
        run_cli("--prompt", "a knight", "--res", "1024", "--tex-res", "1024",
                "--atlas", "4096", *base_args(tmp_path, comfy, engine))
    flat = " ".join(json.loads(argv_path.read_text()))
    assert "--tex-res 1024" in flat
    assert "--atlas 4096" in flat


def test_a_character_prompt_reaches_comfy_in_a_rig_ready_pose(tmp_path):
    engine, _ = fake_engine(tmp_path)
    with StubComfy() as comfy:
        run_cli("--prompt", "a female elf archer", *base_args(tmp_path, comfy, engine))
        sent = comfy.graphs[0]["4"]["inputs"]["text"]
    assert "neutral A-pose" in sent
    assert "product photograph" not in sent


def test_framing_can_be_forced_from_the_pipeline(tmp_path):
    engine, _ = fake_engine(tmp_path)
    with StubComfy() as comfy:
        run_cli("--prompt", "a knight helmet on a stand", "--framing", "object",
                *base_args(tmp_path, comfy, engine))
        sent = comfy.graphs[0]["4"]["inputs"]["text"]
    assert "product photograph" in sent
    assert "A-pose" not in sent


def test_a_character_renders_portrait_by_default(tmp_path):
    """A standing figure in a square frame spends half its pixels on backdrop,
    and the face reaches the reconstructor smaller for it."""
    engine, _ = fake_engine(tmp_path)
    with StubComfy() as comfy:
        run_cli("--prompt", "a female elf archer", *base_args(tmp_path, comfy, engine))
        graph = comfy.graphs[0]
    assert (graph["6"]["inputs"]["width"], graph["6"]["inputs"]["height"]) == (832, 1216)


def test_a_prop_still_renders_square(tmp_path):
    engine, _ = fake_engine(tmp_path)
    with StubComfy() as comfy:
        run_cli("--prompt", "a wooden barrel", *base_args(tmp_path, comfy, engine))
        graph = comfy.graphs[0]
    assert (graph["6"]["inputs"]["width"], graph["6"]["inputs"]["height"]) == (1024, 1024)


def test_an_explicit_size_wins_over_the_character_default(tmp_path):
    engine, _ = fake_engine(tmp_path)
    with StubComfy() as comfy:
        run_cli("--prompt", "a viking warrior", "--image-size", "1024",
                *base_args(tmp_path, comfy, engine))
        square = comfy.graphs[0]
    with StubComfy() as comfy:
        run_cli("--prompt", "a viking warrior", "--image-width", "768",
                "--image-height", "1280", *base_args(tmp_path, comfy, engine))
        tall = comfy.graphs[0]
    assert (square["6"]["inputs"]["width"], square["6"]["inputs"]["height"]) == (1024, 1024)
    assert (tall["6"]["inputs"]["width"], tall["6"]["inputs"]["height"]) == (768, 1280)


def test_steps_and_image_size_reach_comfy(tmp_path):
    engine, _ = fake_engine(tmp_path)
    with StubComfy() as comfy:
        run_cli("--prompt", "a mug", "--steps", "8", "--image-size", "768",
                *base_args(tmp_path, comfy, engine))
        graph = comfy.graphs[0]
    assert graph["7"]["inputs"]["steps"] == 8
    assert graph["6"]["inputs"]["width"] == 768


def test_drop_image_removes_the_intermediate(tmp_path):
    engine, _ = fake_engine(tmp_path)
    with StubComfy() as comfy:
        proc = run_cli("--prompt", "a bowl", "--drop-image",
                       *base_args(tmp_path, comfy, engine))
    result = json.loads(proc.stdout)
    assert not os.path.exists(result["stages"]["text2image"]["image"]["uri"])
    assert os.path.isfile(result["glb"]["uri"])


def test_timings_are_reported_per_stage(tmp_path):
    engine, _ = fake_engine(tmp_path)
    with StubComfy() as comfy:
        proc = run_cli("--prompt", "a vase", *base_args(tmp_path, comfy, engine))
    result = json.loads(proc.stdout)
    assert result["timings"]["imageMs"] >= 0
    assert result["timings"]["meshMs"] >= 0
    assert result["elapsedMs"] >= result["timings"]["meshMs"]


# ---- failure propagation ----------------------------------------------------


def test_image_stage_failure_is_wrapped_with_its_cause(tmp_path):
    engine, _ = fake_engine(tmp_path)
    proc = run_cli("--prompt", "x", "--out-dir", str(tmp_path),
                   "--comfy", "http://127.0.0.1:1",
                   "--runner", "binary", "--engine-path", engine)
    assert proc.returncode == 1
    payload = error_envelope(proc)
    assert payload["code"] == "TEXT2IMAGE_FAILED"
    assert payload["stage"] == "text2image"
    assert payload["cause"]["code"] == "BACKEND_UNREACHABLE"


def test_mesh_stage_failure_is_wrapped_with_its_cause(tmp_path):
    engine, _ = fake_engine(tmp_path, exit_code=9, stderr="device lost")
    with StubComfy() as comfy:
        proc = run_cli("--prompt", "x", *base_args(tmp_path, comfy, engine))
    payload = error_envelope(proc)
    assert payload["code"] == "IMAGE2MESH_FAILED"
    assert payload["stage"] == "image2mesh"
    assert payload["cause"]["code"] == "ENGINE_FAILED"


def test_broken_glb_fails_the_pipeline(tmp_path):
    engine, _ = fake_engine(tmp_path, glb=make_glb(bad_magic=True))
    with StubComfy() as comfy:
        proc = run_cli("--prompt", "x", *base_args(tmp_path, comfy, engine))
    payload = error_envelope(proc)
    assert payload["code"] == "IMAGE2MESH_FAILED"
    assert payload["cause"]["code"] == "GLB_INVALID"


def test_empty_prompt_is_invalid_request():
    with pytest.raises(pipeline.PipelineError) as exc:
        pipeline.generate({"prompt": ""})
    assert exc.value.code == "INVALID_REQUEST"


def test_unknown_field_is_rejected():
    with pytest.raises(pipeline.PipelineError) as exc:
        pipeline.generate({"prompt": "x", "cfg": 3.5})
    assert exc.value.code == "INVALID_REQUEST"


def test_unsupported_resolution_is_rejected():
    with pytest.raises(pipeline.PipelineError) as exc:
        pipeline.generate({"prompt": "x", "resolution": 2048})
    assert exc.value.code == "INVALID_REQUEST"


def test_a_character_gets_the_texels_a_face_needs_without_being_asked(tmp_path):
    """A head is about an eighth of a standing figure and the atlas hands out
    texels by surface area, so on the engine's default atlas a face gets an
    eighth of them. Both knobs existed; neither was anybody's default."""
    engine, argv_path = fake_engine(tmp_path)
    with StubComfy() as comfy:
        run_cli("--prompt", "a female elf archer", "--res", "1024",
                *base_args(tmp_path, comfy, engine))
    flat = " ".join(json.loads(argv_path.read_text()))
    assert "--atlas 4096" in flat
    assert "--tex-res 1024" in flat


def test_the_character_atlas_follows_the_reconstruction_resolution(tmp_path):
    """One step above the engine's own default, not a fixed number: 4096 texels
    wrapped around a res-512 reconstruction is atlas for detail that is not
    there."""
    engine, argv_path = fake_engine(tmp_path)
    with StubComfy() as comfy:
        run_cli("--prompt", "a female elf archer", "--res", "512",
                *base_args(tmp_path, comfy, engine))
    assert "--atlas 2048" in " ".join(json.loads(argv_path.read_text()))


def test_a_prop_is_left_on_the_engine_defaults(tmp_path):
    """A barrel has no face to starve, and a bigger atlas would only cost UV
    packing time and file size."""
    engine, argv_path = fake_engine(tmp_path)
    with StubComfy() as comfy:
        run_cli("--prompt", "a wooden barrel", "--res", "1024",
                *base_args(tmp_path, comfy, engine))
    argv = json.loads(argv_path.read_text())
    assert "--atlas" not in argv
    assert "--tex-res" not in argv


def test_an_explicit_atlas_wins_over_the_character_default(tmp_path):
    engine, argv_path = fake_engine(tmp_path)
    with StubComfy() as comfy:
        run_cli("--prompt", "a viking warrior", "--res", "1024", "--atlas", "1024",
                "--tex-res", "512", *base_args(tmp_path, comfy, engine))
    flat = " ".join(json.loads(argv_path.read_text()))
    assert "--atlas 1024" in flat
    assert "--tex-res 512" in flat


def test_framing_object_opts_a_person_out_of_the_character_defaults(tmp_path):
    """The escape hatch: a stone statue of a warrior is a prop, and the caller
    is allowed to say so."""
    engine, argv_path = fake_engine(tmp_path)
    with StubComfy() as comfy:
        run_cli("--prompt", "a viking warrior", "--res", "1024", "--framing", "object",
                *base_args(tmp_path, comfy, engine))
    argv = json.loads(argv_path.read_text())
    assert "--atlas" not in argv
    assert "--tex-res" not in argv
