#!/usr/bin/env python3
"""Bundled skill launcher for the text-to-3D toolkit."""

import os
import subprocess
import sys

REPOSITORY = "https://github.com/hec-ovi/text-to-3D-skill.git"


def _has_toolkit(path):
    return os.path.isfile(os.path.join(path, "layers", "init", "src", "init.py"))


def _argument_value(arguments, name):
    try:
        return arguments[arguments.index(name) + 1]
    except (ValueError, IndexError):
        return None


def toolkit_dir(arguments):
    explicit = _argument_value(arguments, "--toolkit-dir")
    if explicit:
        return os.path.abspath(os.path.expanduser(explicit)), False

    configured = os.environ.get("TEXT_TO_3D_TOOLKIT")
    if configured:
        return os.path.abspath(os.path.expanduser(configured)), False

    candidates = (
        os.getcwd(),
        os.path.abspath(os.path.join(os.path.dirname(__file__), "..")),
    )
    for candidate in candidates:
        if _has_toolkit(candidate):
            return candidate, False

    return os.path.expanduser("~/.local/share/text-to-3d-toolkit"), True


def main(arguments=None):
    arguments = list(sys.argv[1:] if arguments is None else arguments)
    path, may_clone = toolkit_dir(arguments)
    if not _has_toolkit(path):
        if not may_clone:
            print(f"text-to-3d toolkit not found at {path}", file=sys.stderr)
            return 1
        os.makedirs(os.path.dirname(path), exist_ok=True)
        print(f"cloning toolkit into {path}", file=sys.stderr)
        completed = subprocess.run(["git", "clone", REPOSITORY, path], check=False)
        if completed.returncode:
            return completed.returncode

    entry = os.path.join(path, "layers", "init", "src", "init.py")
    if "--toolkit-dir" not in arguments:
        arguments = ["--toolkit-dir", path, *arguments]
    os.execv(sys.executable, [sys.executable, entry, *arguments])


if __name__ == "__main__":
    raise SystemExit(main())
