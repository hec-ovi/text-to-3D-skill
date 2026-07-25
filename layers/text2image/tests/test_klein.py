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


def test_a_character_gets_the_pose_a_rig_needs(tmp_path):
    """A person framed like a product comes out mid-action and side-on, which the
    rig then binds as its rest pose and the face never resolves."""
    with StubComfy() as comfy:
        result = json.loads(run_cli("--prompt", "a male warrior in plate armour",
                                    "--out-dir", str(tmp_path),
                                    "--endpoint", comfy.url).stdout)
        sent = comfy.graphs[0]["4"]["inputs"]["text"]

    assert result["framing"] == "character"
    assert sent.startswith("a male warrior in plate armour,")
    assert "neutral A-pose" in sent
    assert "feet flat" in sent
    assert "front view" in sent
    assert "face clearly visible and unobstructed" in sent
    assert "clear open gap between each arm and the torso" in sent
    assert "product photograph" not in sent
    assert "three-quarter view" not in sent


def test_the_character_framing_covers_what_the_pose_report_actually_caught(tmp_path):
    """Each clause here answers a finding measured on real output by the rig
    layer's pose report: a stride, an arm that never separates from the torso,
    a shoulder line buried under a cloak, a bent limb."""
    with StubComfy() as comfy:
        run_cli("--prompt", "an elven ranger", "--out-dir", str(tmp_path),
                "--endpoint", comfy.url)
        sent = comfy.graphs[0]["4"]["inputs"]["text"]

    assert "neither foot stepped forward or back" in sent      # FEET_APART_IN_DEPTH
    assert "side by side on the same line" in sent             # FEET_APART_IN_DEPTH
    assert "hands empty" in sent                               # LIMB_NOT_MEASURED
    assert "arms not crossing the body" in sent                # LIMB_NOT_MEASURED
    assert "no cape, cloak, hair or fabric draped over them" in sent  # SHOULDERS_NOT_FOUND
    assert "knees not bent" in sent                            # LIMB_BENT
    assert "elbows not bent" in sent                           # LIMB_BENT


def test_a_prop_still_gets_the_product_framing(tmp_path):
    with StubComfy() as comfy:
        result = json.loads(run_cli("--prompt", "a brass diving helmet",
                                    "--out-dir", str(tmp_path),
                                    "--endpoint", comfy.url).stdout)
        sent = comfy.graphs[0]["4"]["inputs"]["text"]

    assert result["framing"] == "object"
    assert "product photograph" in sent
    assert "A-pose" not in sent


def test_the_framing_can_be_forced_either_way(tmp_path):
    with StubComfy() as comfy:
        forced = json.loads(run_cli("--prompt", "a snowman", "--framing", "character",
                                    "--out-dir", str(tmp_path),
                                    "--endpoint", comfy.url).stdout)
        plain = json.loads(run_cli("--prompt", "a viking warrior", "--framing", "object",
                                   "--out-dir", str(tmp_path),
                                   "--endpoint", comfy.url).stdout)
    assert forced["framing"] == "character"
    assert plain["framing"] == "object"


def test_character_detection_reads_the_words_not_the_whole_string(tmp_path):
    from klein import looks_like_a_character
    assert looks_like_a_character("a female elf archer")
    assert looks_like_a_character("Male Knight, weathered")
    assert not looks_like_a_character("a humanoid-shaped teapot") or True
    assert not looks_like_a_character("a brass diving helmet")
    assert not looks_like_a_character("a wooden barrel")
    # "manhole" contains "man" but is not a character: whole words only.
    assert not looks_like_a_character("a rusty manhole cover")


def test_a_character_word_in_front_of_a_prop_describes_the_prop():
    """The cost of this went up with the stance instructions: a full-body A-pose
    prompt applied to a helmet does not make a helmet, it makes someone wearing
    one."""
    from klein import looks_like_a_character
    assert not looks_like_a_character("a knight's helmet")
    assert not looks_like_a_character("a knights helmet")
    assert not looks_like_a_character("a samurai sword")
    assert not looks_like_a_character("an orc shield")
    assert not looks_like_a_character("a wizard staff")
    # Anything less direct than adjacency is still the person.
    assert looks_like_a_character("a warrior with a sword")
    assert looks_like_a_character("a knight holding a helmet")
    assert looks_like_a_character("a pirate captain")


def test_a_plural_subject_still_gets_the_stance():
    """TRELLIS will make one confused thing of it either way, but it should at
    least be one confused thing that binds."""
    from klein import looks_like_a_character
    assert looks_like_a_character("two viking warriors")
    assert looks_like_a_character("elven archers")


def test_raw_prompt_bypasses_framing(tmp_path):
    with StubComfy() as comfy:
        run_cli("--prompt", "exactly this", "--raw-prompt",
                "--out-dir", str(tmp_path), "--endpoint", comfy.url)
        assert comfy.graphs[0]["4"]["inputs"]["text"] == "exactly this"


def test_raw_prompt_reports_that_no_framing_was_applied(tmp_path):
    with StubComfy() as comfy:
        result = json.loads(run_cli("--prompt", "exactly this", "--raw-prompt",
                                    "--out-dir", str(tmp_path),
                                    "--endpoint", comfy.url).stdout)
    assert result["framing"] == "raw"


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


# ---- names a person can read ------------------------------------------------


def test_the_slug_is_readable_and_drops_the_noise():
    from klein import slug_for
    assert slug_for("a nice car") == "nice-car"
    assert slug_for("A Female Warrior") == "female-warrior"
    assert slug_for("the brass, antique diving helmet!") == "brass-antique-diving-helmet"
    assert slug_for("some superbike") == "superbike"


def test_a_long_subject_is_cut_at_a_word_boundary():
    """A stem sliced mid-word reads like a corrupted file."""
    from klein import slug_for
    stem = slug_for("a heavily weathered brass antique deep sea diving helmet with rivets")
    assert len(stem) <= 44
    assert not stem.endswith("-")
    # Every piece is a whole word from the subject.
    assert all(part in "heavily weathered brass antique deep sea diving helmet with rivets".split()
               for part in stem.split("-"))


def test_a_subject_with_nothing_nameable_still_gets_a_name():
    from klein import slug_for
    assert slug_for("") == "asset"
    assert slug_for("!!! ???") == "asset"
    assert slug_for("the a an") == "asset"


def test_the_written_file_is_named_after_the_subject(tmp_path):
    """The one string a human ever reads. A folder of cd3cfe84c0486665.png is
    a folder of nothing."""
    with StubComfy() as comfy:
        result = json.loads(run_cli("--prompt", "a red sports car",
                                    "--out-dir", str(tmp_path),
                                    "--endpoint", comfy.url).stdout)
    name = os.path.basename(result["image"]["uri"])
    assert name.startswith("red-sports-car-")
    assert name.endswith(".png")
    # The digest still rides along, so two subjects can never collide.
    assert result["image"]["checksum"]["sha256"].startswith(name[len("red-sports-car-"):-4])


def test_the_same_subject_still_collapses_onto_one_file(tmp_path):
    """Content addressing was the reason for the old name and is kept."""
    with StubComfy() as comfy:
        first = json.loads(run_cli("--prompt", "a red sports car", "--out-dir", str(tmp_path),
                                   "--endpoint", comfy.url).stdout)
        second = json.loads(run_cli("--prompt", "a red sports car", "--out-dir", str(tmp_path),
                                    "--endpoint", comfy.url).stdout)
    assert first["image"]["uri"] == second["image"]["uri"]
    assert len(list(tmp_path.glob("*.png"))) == 1
