#!/usr/bin/env python3
"""Resident rig server: a mesh in, a skeleton and its weights out.

Runs SkinTokens on the Vulkan-less half of this box, ROCm, and is the only
process in the toolkit that imports torch. It is resident for the same reason
the mesh engine is: the checkpoints take about twenty seconds to load and a
process per request would pay that every time.

Two things stand between SkinTokens and an AMD card, and both are handled here
without editing a line of their source:

  1. Four of their modules open with `from flash_attn_interface import
     flash_attn_func` and fall through to `flash_attn` with no third branch, so
     on a machine without either the import fails outright. `shim/` supplies
     that module backed by torch SDPA. The implementation is not invented: their
     own `attention_processor.py` already carries it as a fallback.

  2. `tokenrig.py` builds the Qwen3 backbone with attn_implementation hardcoded
     to "flash_attention_2", and transformers raises rather than falling back.
     The patch below rewrites that one argument to "sdpa".

Neither changes what is computed. SDPA is the same attention.
"""

import base64
import json
import os
import struct
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np
import torch

SKINTOKENS = os.environ.get("T2M_SKINTOKENS", "/opt/skintokens")
MODELS = os.environ.get("T2M_RIG_MODELS", "/models")
CKPT = os.environ.get("T2M_RIG_CKPT",
                      "/models/articulation_xl_quantization_256_token_4/grpo_1400.ckpt")
PORT = int(os.environ.get("T2M_RIG_PORT", "8191"))

sys.path.insert(0, SKINTOKENS)
os.chdir(SKINTOKENS)

# The rig checkpoint names its VAE checkpoint by a path relative to the working
# directory, `experiments/skin_vae_2_10_32768/last.ckpt`, so loading the outer
# file by an absolute path is not enough: the inner one is looked up under the
# source tree. Pointing `experiments` at the read-only mount is what makes both
# resolve, and it is a link rather than a copy so the 1.6 GiB stays outside the
# image.
_experiments = os.path.join(SKINTOKENS, "experiments")
if not os.path.exists(_experiments) and os.path.isdir(MODELS):
    os.symlink(MODELS, _experiments)

from transformers import AutoModelForCausalLM  # noqa: E402

_from_config = AutoModelForCausalLM.from_config.__func__


def _sdpa_from_config(cls, config, **kwargs):
    if kwargs.get("attn_implementation") == "flash_attention_2":
        kwargs["attn_implementation"] = "sdpa"
    return _from_config(cls, config, **kwargs)


AutoModelForCausalLM.from_config = classmethod(_sdpa_from_config)

from src.data.dataset import DatasetConfig, RigDatasetModule   # noqa: E402
from src.data.transform import Transform                        # noqa: E402
from src.server.spec import get_model                           # noqa: E402
from src.tokenizer.parse import get_tokenizer                   # noqa: E402

STATE = {}


def load():
    started = time.monotonic()
    model = get_model(CKPT, hf_path=None)
    STATE["model"] = model
    STATE["tokenizer"] = get_tokenizer(**model.tokenizer_config)
    STATE["transform"] = Transform.parse(**model.transform_config["predict_transform"])
    attn = getattr(model.transformer.model.config, "_attn_implementation", "unknown")
    print(f"[rig] model loaded in {time.monotonic() - started:.1f}s, attention {attn}",
          flush=True)
    print(f"[rig] device {torch.cuda.get_device_name(0)}", flush=True)


def predict(vertices, faces):
    """One mesh through the model. Returns (positions, parents, skin)."""
    scratch = "/tmp/rig-request.npz"
    np.savez(scratch, vertices=vertices.astype(np.float32), faces=faces.astype(np.int64))

    config = DatasetConfig.parse(
        shuffle=False, batch_size=1, num_workers=0, pin_memory=False,
        persistent_workers=False,
        datapath={"data_name": None, "loader": "npz",
                  "filepaths": {"articulation": [scratch]}},
    ).split_by_cls()

    module = RigDatasetModule(predict_dataset_config=config,
                              predict_transform=STATE["transform"],
                              tokenizer=STATE["tokenizer"],
                              process_fn=STATE["model"]._process_fn)

    for batch in module.predict_dataloader()["articulation"]:
        batch = {k: (v.to("cuda") if isinstance(v, torch.Tensor) else v)
                 for k, v in batch.items()}
        batch.pop("skeleton_tokens", None)
        batch.pop("skeleton_mask", None)
        batch["generate_kwargs"] = dict(max_length=2048, top_k=10, top_p=0.95,
                                        temperature=0.5, repetition_penalty=1.0,
                                        num_return_sequences=1, num_beams=1, do_sample=True)
        asset = STATE["model"].predict_step(batch, make_asset=True)["results"][0].asset

        # The model normalises the mesh before it predicts, and the skeleton it
        # returns is in that normalised frame, not the one the mesh arrived in.
        # Handing those joints straight to a skinning pass puts every bone in
        # the wrong place: on a character 0.98 units tall the skeleton came back
        # 1.69 tall, and binding a mesh to a skeleton larger than itself
        # squashes it. That looked like a short, wide character with broken
        # knees, which is a hard symptom to trace back to a coordinate frame.
        #
        # `asset.vertices` is the same mesh in the frame the model worked in, so
        # the two bounding boxes recover the transform exactly, provided it is a
        # uniform scale and a translation. The per-axis scales are logged so
        # that assumption is checked on every call rather than trusted.
        source = np.asarray(vertices, dtype=np.float64)
        seen = np.asarray(asset.vertices, dtype=np.float64)
        span = np.maximum(seen.max(0) - seen.min(0), 1e-9)
        per_axis = (source.max(0) - source.min(0)) / span
        scale = float(np.median(per_axis))
        offset = source.min(0) - seen.min(0) * scale
        spread = float(per_axis.max() / max(per_axis.min(), 1e-9))
        print(f"[rig] frame: scale {scale:.5f} per-axis {np.round(per_axis, 5).tolist()} "
              f"spread {spread:.4f}", flush=True)
        if spread > 1.02:
            print("[rig] WARNING: the model's normalisation is not a uniform scale; "
                  "the skeleton will not map back exactly", flush=True)

        positions = [(np.asarray(m[:3, 3], dtype=np.float64) * scale + offset).tolist()
                     for m in asset.matrix_local]
        return positions, [int(p) for p in asset.parents], np.asarray(asset.skin, dtype=np.float32)
    raise RuntimeError("the dataloader yielded nothing")


class Handler(BaseHTTPRequestHandler):
    server_version = "t2m-rig"

    def log_message(self, fmt, *args):
        sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))

    def _json(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.rstrip("/") == "/health":
            ready = "model" in STATE
            self._json(200 if ready else 503, {"ready": ready})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):
        if self.path.rstrip("/") != "/rig":
            return self._json(404, {"error": "not found"})
        try:
            length = int(self.headers.get("Content-Length", 0))
            request = json.loads(self.rfile.read(length))
            vertices = np.frombuffer(base64.b64decode(request["vertices"]),
                                     dtype="<f4").reshape(-1, 3)
            faces = np.frombuffer(base64.b64decode(request["faces"]),
                                  dtype="<i4").reshape(-1, 3)
        except (ValueError, KeyError, TypeError) as exc:
            return self._json(400, {"error": f"bad request: {exc}"})

        started = time.monotonic()
        try:
            positions, parents, skin = predict(vertices, faces)
        except Exception as exc:                       # noqa: BLE001
            # Whatever the model does wrong, the caller gets one line it can
            # put in an envelope rather than a stack trace in a container log.
            return self._json(500, {"error": f"{type(exc).__name__}: {exc}"})

        print(f"[rig] {len(vertices)} vertices -> {len(parents)} joints "
              f"in {time.monotonic() - started:.1f}s", flush=True)
        self._json(200, {
            "parents": parents,
            "positions": positions,
            "skin": base64.b64encode(
                struct.pack(f"<{skin.size}f", *skin.reshape(-1).tolist())).decode("ascii"),
        })


def main():
    load()
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"[rig] listening on http://0.0.0.0:{PORT}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
