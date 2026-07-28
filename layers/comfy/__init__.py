"""ComfyUI entry point.

ComfyUI imports `custom_nodes/<dir>/__init__.py` and reads two names off it.
Mount this folder there and the node appears under `text-to-3d`.

The implementation is in `src/`, which is also the CLI and is what the contract
tests drive. Nothing here does work.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from node import NODE_CLASS_MAPPINGS, NODE_DISPLAY_NAME_MAPPINGS  # noqa: E402,F401

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]
