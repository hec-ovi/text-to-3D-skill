"""The single-container supervisor: two processes, one fate.

Compose gave this policy away for free. Inside one container it is a shell
script, and a shell script nobody tests is where a container ends up answering
on 8188 with a dead mesh engine. Every external command it calls is overridable
through a T2M_* variable for exactly this reason, so these run with no ROCm, no
Vulkan and no ComfyUI.
"""

import os
import subprocess
import sys
import time

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
LAYER = os.path.dirname(HERE)
SUPERVISE = os.path.join(LAYER, "docker", "supervise.sh")

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX shell script")


def _stub(path, body):
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("#!/bin/sh\n" + body)
    os.chmod(path, 0o755)
    return path


@pytest.fixture
def world(tmp_path):
    """A fake container: a health file, a stub engine, a stub ComfyUI."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    comfy_dir = tmp_path / "comfy"
    comfy_dir.mkdir()
    (comfy_dir / "main.py").write_text("", encoding="utf-8")

    # `curl` answers once the engine stub has dropped its ready file, which is
    # how the script's real wait loop is exercised without a socket.
    ready = tmp_path / "ready"
    _stub(bin_dir / "curl", f'[ -f "{ready}" ] || exit 1\nexit 0\n')

    def run(engine_body, comfy_body, env=None, timeout=25):
        _stub(bin_dir / "engine", engine_body)
        _stub(bin_dir / "comfy", comfy_body)
        environment = dict(
            os.environ,
            PATH=f"{bin_dir}:{os.environ['PATH']}",
            T2M_SKIP_CHECKS="1",
            T2M_ENGINE_BIN=str(bin_dir / "engine"),
            T2M_COMFY_BIN=str(bin_dir / "comfy"),
            T2M_COMFY_DIR=str(comfy_dir),
            T2M_OUT_DIR=str(tmp_path / "out"),
            T2M_READY=str(ready),
            T2M_MARK=str(tmp_path / "mark"),
        )
        environment.update(env or {})
        # By path, not through an explicit interpreter: the shebang is part of
        # what is being tested, and running it under /bin/sh is what hid a
        # bashism here in the first place.
        return subprocess.run([SUPERVISE], capture_output=True, text=True,
                              timeout=timeout, env=environment)

    run.tmp_path = tmp_path
    run.ready = ready
    return run


READY_THEN_WAIT = 'touch "$T2M_READY"\nwhile true; do sleep 0.2; done\n'


def test_the_engine_comes_up_before_comfyui(world):
    """ComfyUI reads its node list at startup and the node's default is the engine."""
    proc = world(READY_THEN_WAIT, 'echo "comfy started" >> "$T2M_MARK"\nexit 3\n')

    assert "[supervise] engine ready" in proc.stderr
    order = proc.stderr.index("starting t2m-server"), proc.stderr.index("engine ready")
    assert order[0] < order[1] < proc.stderr.index("starting ComfyUI")
    assert os.path.isfile(world.tmp_path / "mark")


def test_comfyui_dying_takes_the_container_down(world):
    proc = world(READY_THEN_WAIT, "exit 3\n")

    assert proc.returncode != 0, "the container survived a dead ComfyUI"
    assert "ComfyUI exited" in proc.stderr


def test_the_engine_dying_takes_the_container_down(world):
    """The failure that would otherwise look healthy from outside."""
    proc = world(f'touch "$T2M_READY"\nsleep 1\nexit 4\n',
                 'while true; do sleep 0.2; done\n')

    assert proc.returncode != 0, "the container survived a dead mesh engine"
    assert "mesh engine exited" in proc.stderr


def test_an_engine_that_never_starts_is_not_waited_on_forever(world):
    """Without this the script sits in its health loop for the full ten minutes."""
    started = time.monotonic()
    proc = world("exit 78\n", 'while true; do sleep 0.2; done\n')

    assert proc.returncode != 0
    assert "exited during startup" in proc.stderr
    assert time.monotonic() - started < 20, "the startup loop did not notice a dead child"
    assert "starting ComfyUI" not in proc.stderr


def test_a_missing_vulkan_device_fails_before_anything_starts(world):
    """78 is the code the two-container entrypoint uses for the same thing."""
    proc = world(READY_THEN_WAIT, 'while true; do sleep 0.2; done\n',
                 env={"T2M_SKIP_CHECKS": "0", "T2M_MODELS": "/nonexistent"})

    assert proc.returncode == 78
    assert "no Vulkan device" in proc.stderr
    assert "starting t2m-server" not in proc.stderr


def test_the_output_directory_is_made_before_a_graph_needs_it(world):
    world(READY_THEN_WAIT, "exit 0\n")

    assert os.path.isdir(world.tmp_path / "out")
