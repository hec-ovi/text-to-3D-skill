"""The ComfyUI half: an IMAGE tensor in, a GLB path out.

Everything that knows what a tensor is lives here, and nothing else does.
`client.py` is stdlib, so the contract tests run without torch, without numpy
and without ComfyUI; this file is the adapter between that and a graph.

torch, numpy and PIL are imported inside the call rather than at module import.
ComfyUI imports every custom node at startup, and a node that raises on import
takes the whole node list with it.
"""

import base64
import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from client import NodeError, generate  # noqa: E402

# ComfyUI's own output directory is the one place a graph can write that the
# rest of the stack already serves. It is overridable because the preview layer
# watches whatever directory init was pointed at.
DEFAULT_OUT = os.environ.get("T2M_OUT_DIR", "/app/ComfyUI/output")
DEFAULT_ENGINE = os.environ.get("T2M_ENGINE", "http://127.0.0.1:8189")


def _png_bytes(image):
    """First frame of a ComfyUI IMAGE batch as PNG bytes.

    A batch arrives as [B, H, W, C] in 0..1. TRELLIS reconstructs one subject
    from one view, so a batch of four is four separate runs; taking the first
    frame and saying so beats silently reconstructing a collage of them.
    """
    import numpy as np
    from PIL import Image

    frame = image[0] if len(getattr(image, "shape", ())) == 4 else image
    array = frame.detach().cpu().numpy() if hasattr(frame, "detach") else np.asarray(frame)
    array = np.clip(array * 255.0 + 0.5, 0, 255).astype(np.uint8)
    if array.ndim == 3 and array.shape[2] == 4:
        mode = "RGBA"
    elif array.ndim == 3 and array.shape[2] == 3:
        mode = "RGB"
    else:
        raise NodeError("INVALID_REQUEST",
                        f"expected an RGB or RGBA image, got shape {getattr(array, 'shape', None)}")
    buffer = io.BytesIO()
    Image.fromarray(array, mode).save(buffer, format="PNG")
    return buffer.getvalue()


class TextTo3DMesh:
    """Reconstruct one rendered image into a textured GLB on the Vulkan engine."""

    CATEGORY = "text-to-3d"
    FUNCTION = "run"
    RETURN_TYPES = ("STRING", "STRING")
    RETURN_NAMES = ("glb_path", "result_json")
    OUTPUT_NODE = True

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image": ("IMAGE",),
                "name": ("STRING", {"default": "comfy", "multiline": False}),
                "resolution": ([512, 1024, 1536], {"default": 512}),
                "seed": ("INT", {"default": 42, "min": 0, "max": 2147483647}),
            },
            "optional": {
                # 0 means "the engine's own default", because a ComfyUI widget
                # cannot be left empty the way a JSON field can be omitted.
                "target_faces": ("INT", {"default": 0, "min": 0, "max": 2000000, "step": 1000}),
                "background_removal": (["auto", "threshold", "birefnet"], {"default": "auto"}),
                "engine": ("STRING", {"default": DEFAULT_ENGINE, "multiline": False}),
                "out_dir": ("STRING", {"default": DEFAULT_OUT, "multiline": False}),
                "timeout_seconds": ("INT", {"default": 1800, "min": 10, "max": 7200}),
            },
        }

    def run(self, image, name, resolution, seed, target_faces=0,
            background_removal="auto", engine=DEFAULT_ENGINE, out_dir=DEFAULT_OUT,
            timeout_seconds=1800):
        try:
            png = _png_bytes(image)
            request = {
                "image": {
                    "data": base64.b64encode(png).decode("ascii"),
                    "contentEncoding": "base64",
                    "contentMediaType": "image/png",
                    "byteSize": len(png),
                },
                "name": name or "comfy",
                "resolution": int(resolution),
                "seed": int(seed),
                "backgroundRemoval": background_removal,
                "endpoint": engine,
                "outDir": out_dir,
                "timeoutSeconds": int(timeout_seconds),
            }
            if target_faces:
                request["targetFaces"] = int(target_faces)
            result = generate(request)
        except NodeError as exc:
            # ComfyUI shows the exception text to the person who pressed Run, so
            # the envelope goes in it rather than into a log they will not read.
            raise RuntimeError(json.dumps(exc.envelope())) from exc
        return (result["glb"]["uri"], json.dumps(result))


NODE_CLASS_MAPPINGS = {"TextTo3DMesh": TextTo3DMesh}
NODE_DISPLAY_NAME_MAPPINGS = {"TextTo3DMesh": "Image to GLB (TRELLIS.2 Vulkan)"}
