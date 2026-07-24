#!/usr/bin/env python3
"""mcp blackbox: the whole toolkit as an MCP server over stdio.

    python3 src/server.py --out-dir out

Speaks JSON-RPC 2.0 on stdin and stdout, protocol revision 2025-11-25. No SDK:
the stdio transport is newline-delimited JSON-RPC and the surface this server
needs is four methods, so the layer stays stdlib-only like every other one here.

Every tool returns a handle, never bytes. A 20 MB GLB base64-encodes to about
28 M characters against a 25,000-token default result cap in Claude Code, so the
result carries an id, a path, a resource link and a preview URL, and the bytes
stay on disk where a loader can read them.

See ../CONTRACT.md.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from schema_check import load, validate  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
LAYER = os.path.dirname(HERE)
LAYERS = os.path.dirname(LAYER)
TOOLS_SCHEMA = os.path.join(LAYER, "schema", "tool_result.json")

# The layer CLIs this server fronts. Overridable so the tests can stand in for
# the two that need a GPU without faking the protocol as well.
PIPELINE = os.environ.get("T2M_PIPELINE") or os.path.join(LAYERS, "pipeline", "src", "pipeline.py")
TEXT2IMAGE = os.environ.get("T2M_TEXT2IMAGE") or os.path.join(LAYERS, "text2image", "src", "klein.py")
RIG = os.environ.get("T2M_RIG") or os.path.join(LAYERS, "rig", "src", "rig.py")
ASSETS = os.environ.get("T2M_ASSETS") or os.path.join(LAYERS, "assets", "src", "assets.py")

PROTOCOL_VERSION = "2025-11-25"
CONTRACT_VERSION = "1.0"
SERVER_INFO = {"name": "text-to-3d", "title": "text to 3D", "version": "1.0.0"}

ID_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def asset_id(name):
    """Same rule the preview layer uses, so one id addresses both."""
    return ID_UNSAFE.sub("-", os.path.splitext(os.path.basename(name))[0]).strip("-.")


class ToolError(Exception):
    """A failure the model should see and can act on, not a protocol error."""


# ---- the tools --------------------------------------------------------------


TOOLS = [
    {
        "name": "generate_model",
        "title": "Generate a 3D model",
        "description": (
            "Turn a description of one object into a textured GLB, locally: FLUX.2 klein "
            "draws a reference image, TRELLIS.2 reconstructs it. Takes about two to four "
            "minutes. Returns an id, the file path and a preview URL, never the bytes. "
            "Set targetFaces for a low-poly, game-ready mesh (4000 gives roughly 3800 "
            "triangles in 300 KB); omit it for the full 150K-face default."
        ),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["prompt"],
            "properties": {
                "prompt": {"type": "string", "description": "What the object is. One subject."},
                "targetFaces": {"type": "integer", "minimum": 100, "maximum": 2000000},
                "resolution": {"type": "integer", "enum": [512, 1024, 1536], "default": 512},
                "seed": {"type": "integer", "minimum": 0},
                "rig": {
                    "type": "string",
                    "enum": ["none", "humanoid", "prop"],
                    "default": "none",
                    "description": "Rig the result after generating it. humanoid adds a "
                                   "skeleton, skinning and idle/walk/run/jump.",
                },
            },
        },
        "annotations": {"readOnlyHint": False, "destructiveHint": False,
                        "idempotentHint": False, "openWorldHint": False},
    },
    {
        "name": "generate_image",
        "title": "Generate a reference image",
        "description": ("Run only the image half: a description in, a PNG on disk out. "
                        "Useful to check the framing before paying for a reconstruction."),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["prompt"],
            "properties": {
                "prompt": {"type": "string"},
                "size": {"type": "integer", "minimum": 256, "maximum": 2048, "default": 1024},
                "steps": {"type": "integer", "minimum": 1, "maximum": 50, "default": 4},
                "seed": {"type": "integer", "minimum": 0},
            },
        },
        "annotations": {"readOnlyHint": False, "destructiveHint": False,
                        "idempotentHint": False, "openWorldHint": False},
    },
    {
        "name": "rig_model",
        "title": "Rig a model",
        "description": ("Give an existing GLB a skeleton and standard clips. humanoid fits "
                        "19 Mixamo-named bones to the mesh and authors idle, walk, run and "
                        "jump; prop adds no armature, only node clips and an optional socket. "
                        "Rig after decimating, never before."),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["id", "subject"],
            "properties": {
                "id": {"type": "string", "description": "Model id from generate_model or list_models."},
                "subject": {"type": "string", "enum": ["humanoid", "prop"]},
                "animations": {"type": "array", "items": {"type": "string"}},
                "socket": {"type": "string", "description": "prop only: name an attachment empty."},
            },
        },
        "annotations": {"readOnlyHint": False, "destructiveHint": False,
                        "idempotentHint": False, "openWorldHint": False},
    },
    {
        "name": "list_models",
        "title": "List generated models",
        "description": "Every GLB in the output directory, newest first, with its id, triangle count, clips and preview URL.",
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {"filter": {"type": "string", "description": "Substring of the name."}},
        },
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
    {
        "name": "get_preview",
        "title": "Preview a model",
        "description": ("Where to look at one asset: the viewer URL for its id, the path of "
                        "the source image it was reconstructed from, and what the file holds. "
                        "Start the viewer with layers/preview/src/serve.py if it is not up."),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["id"],
            "properties": {"id": {"type": "string"}},
        },
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
    {
        "name": "search_assets",
        "title": "Search the CC0 asset library",
        "description": ("Look for an existing model before generating one. Poly Haven, a few "
                        "hundred CC0 models, no attribution required and redistribution "
                        "allowed. Returns ids to pass to fetch_asset. Generating takes "
                        "minutes; fetching takes seconds, so search first when a stock prop "
                        "would do."),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "query": {"type": "string", "description": "Words matched against name, tags and categories."},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 20},
            },
        },
        "annotations": {"readOnlyHint": True, "openWorldHint": True},
    },
    {
        "name": "fetch_asset",
        "title": "Fetch a library asset",
        "description": ("Download one asset by its library id and convert it to a single GLB "
                        "in the output directory, where it behaves like anything generated "
                        "here: same id rules, same viewer, riggable as a prop."),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["id"],
            "properties": {
                "id": {"type": "string", "description": "Asset id from search_assets."},
                "resolution": {"type": "string", "enum": ["1k", "2k", "4k"], "default": "1k"},
            },
        },
        "annotations": {"readOnlyHint": False, "destructiveHint": False,
                        "idempotentHint": True, "openWorldHint": True},
    },
    {
        "name": "download_glb",
        "title": "Copy a GLB somewhere",
        "description": ("Copy an asset to a path you name, for dropping into a project. "
                        "Returns the destination path and its sha256. Never returns bytes: "
                        "a GLB is megabytes and a tool result is model context."),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["id", "destination"],
            "properties": {
                "id": {"type": "string"},
                "destination": {"type": "string", "description": "File path, or a directory to copy into."},
            },
        },
        "annotations": {"readOnlyHint": False, "destructiveHint": True,
                        "idempotentHint": True, "openWorldHint": False},
    },
]


class Toolkit:
    """Runs the layer CLIs and turns their envelopes into tool results."""

    def __init__(self, out_dir, preview_url, python=None, timeout=2400):
        self.out_dir = os.path.abspath(out_dir)
        self.preview_url = preview_url.rstrip("/")
        self.python = python or sys.executable
        self.timeout = timeout

    # -- helpers --

    def _run(self, script, args, timeout=None):
        proc = subprocess.run([self.python, script, *args], capture_output=True, text=True,
                              timeout=timeout or self.timeout)
        if proc.returncode != 0:
            detail = proc.stderr.strip() or proc.stdout.strip()
            try:
                envelope = json.loads(detail)
                raise ToolError(f"{envelope.get('code', 'FAILED')}: {envelope.get('message', '')}")
            except ValueError:
                raise ToolError(detail[-800:] or f"{os.path.basename(script)} exited "
                                                 f"{proc.returncode}")
        try:
            return json.loads(proc.stdout)
        except ValueError:
            raise ToolError(f"{os.path.basename(script)} returned no envelope")

    def models(self):
        out = []
        if not os.path.isdir(self.out_dir):
            return out
        for name in sorted(os.listdir(self.out_dir)):
            if not name.lower().endswith(".glb"):
                continue
            path = os.path.join(self.out_dir, name)
            out.append({"id": asset_id(name), "name": name, "path": path,
                        "byteSize": os.path.getsize(path),
                        "modifiedAt": int(os.path.getmtime(path)),
                        "previewUrl": f"{self.preview_url}/?id={asset_id(name)}"})
        out.sort(key=lambda m: m["modifiedAt"], reverse=True)
        return out

    def resolve(self, ident):
        for model in self.models():
            if ident in (model["id"], model["name"], model["path"]):
                return model
        raise ToolError(f"no model with id {ident}. Call list_models to see what is there.")

    def _describe(self, path):
        """Triangles, materials and clip names, read out of the file."""
        import struct
        with open(path, "rb") as fh:
            data = fh.read()
        offset, gltf = 12, None
        while offset + 8 <= len(data):
            length, kind = struct.unpack_from("<II", data, offset)
            if kind == 0x4E4F534A:
                gltf = json.loads(data[offset + 8: offset + 8 + length].decode("utf-8"))
                break
            offset += 8 + length + (-length % 4)
        if gltf is None:
            return {}
        accessors = gltf.get("accessors", [])
        triangles = 0
        for mesh in gltf.get("meshes", []):
            for prim in mesh.get("primitives", []):
                index = prim.get("indices")
                if index is not None and index < len(accessors):
                    triangles += accessors[index].get("count", 0) // 3
        described = {"triangles": triangles, "materials": len(gltf.get("materials", []))}
        clips = [a.get("name", "") for a in gltf.get("animations", [])]
        if clips:
            described["animations"] = clips
        if gltf.get("skins"):
            described["joints"] = len(gltf["skins"][0].get("joints", []))
        return described

    def _asset(self, path, extra=None):
        ident = asset_id(path)
        payload = {
            "id": ident,
            "path": path,
            "byteSize": os.path.getsize(path),
            "previewUrl": f"{self.preview_url}/?id={ident}",
        }
        payload.update(self._describe(path))
        payload.update(extra or {})
        return payload

    # -- the tools themselves --

    def generate_model(self, args):
        cmd = ["--prompt", args["prompt"], "--out-dir", self.out_dir,
               "--res", str(args.get("resolution", 512))]
        if args.get("targetFaces"):
            cmd += ["--target-faces", str(args["targetFaces"])]
        if args.get("seed") is not None:
            cmd += ["--seed", str(args["seed"])]
        result = self._run(PIPELINE, cmd)
        asset = self._asset(result["glb"]["uri"], {"prompt": args["prompt"],
                                                   "timings": result.get("timings", {})})
        rig_kind = args.get("rig", "none")
        if rig_kind != "none":
            rigged = self.rig_model({"id": asset["id"], "subject": rig_kind})
            rigged["source"] = asset["id"]
            return rigged
        return asset

    def generate_image(self, args):
        cmd = ["--prompt", args["prompt"], "--out-dir", self.out_dir,
               "--width", str(args.get("size", 1024)), "--height", str(args.get("size", 1024)),
               "--steps", str(args.get("steps", 4))]
        if args.get("seed") is not None:
            cmd += ["--seed", str(args["seed"])]
        result = self._run(TEXT2IMAGE, cmd)
        image = result["image"]
        return {"id": asset_id(image["uri"]), "path": image["uri"],
                "byteSize": image.get("byteSize", 0), "mediaType": image.get("mediaType"),
                "seed": result.get("seed"), "prompt": args["prompt"]}

    def rig_model(self, args):
        model = self.resolve(args["id"])
        cmd = ["--glb", model["path"], "--subject", args["subject"], "--out-dir", self.out_dir]
        if args.get("animations"):
            cmd += ["--animations", ",".join(args["animations"])]
        if args.get("socket"):
            cmd += ["--socket", args["socket"]]
        result = self._run(RIG, cmd, timeout=1200)
        return self._asset(result["glb"]["uri"],
                           {"subject": result["subject"],
                            "bones": len(result["skeleton"]["bones"]),
                            "clips": [a["name"] for a in result["animations"]]})

    def search_assets(self, args):
        cmd = ["search", "--limit", str(args.get("limit", 20))]
        if args.get("query"):
            cmd += ["--query", args["query"]]
        result = self._run(ASSETS, cmd, timeout=120)
        return {"source": result["source"], "query": result.get("query", ""),
                "total": result["total"], "assets": result["assets"]}

    def fetch_asset(self, args):
        cmd = ["fetch", "--id", args["id"], "--out-dir", self.out_dir,
               "--resolution", args.get("resolution", "1k")]
        result = self._run(ASSETS, cmd, timeout=600)
        return self._asset(result["glb"]["uri"],
                           {"source": result["source"], "license": result["license"],
                            "libraryId": result["id"]})

    def list_models(self, args):
        needle = (args.get("filter") or "").lower()
        models = [m for m in self.models() if needle in m["name"].lower()]
        for model in models:
            model.update(self._describe(model["path"]))
        return {"dir": self.out_dir, "count": len(models), "models": models}

    def get_preview(self, args):
        model = self.resolve(args["id"])
        payload = self._asset(model["path"])
        stem = re.sub(r"-r\d+$", "", re.sub(r"-rigged$", "",
                                            os.path.splitext(model["name"])[0]))
        for ext in (".png", ".jpg", ".jpeg", ".webp"):
            candidate = os.path.join(self.out_dir, stem + ext)
            if os.path.isfile(candidate):
                payload["sourceImage"] = candidate
                break
        payload["viewer"] = f"{self.preview_url}/?id={model['id']}"
        return payload

    def download_glb(self, args):
        import hashlib
        import shutil
        model = self.resolve(args["id"])
        destination = os.path.abspath(os.path.expanduser(args["destination"]))
        if os.path.isdir(destination):
            destination = os.path.join(destination, model["name"])
        parent = os.path.dirname(destination) or "."
        if not os.path.isdir(parent):
            raise ToolError(f"no directory at {parent}")
        shutil.copyfile(model["path"], destination)
        with open(destination, "rb") as fh:
            digest = hashlib.sha256(fh.read()).hexdigest()
        return {"id": model["id"], "path": destination,
                "byteSize": os.path.getsize(destination), "sha256": digest}


# ---- the protocol -----------------------------------------------------------


def tool_result(payload, uri=None):
    """structuredContent plus a mirrored text block plus a link to the bytes.

    The text mirror is not redundancy for its own sake: client support for
    structuredContent is uneven, and the spec's own backwards-compatibility note
    says to serialize the same JSON into a content block.
    """
    content = [{"type": "text", "text": json.dumps(payload, indent=2)}]
    if uri:
        content.append({
            "type": "resource_link",
            "uri": f"file://{uri}",
            "name": os.path.basename(uri),
            "mimeType": "model/gltf-binary" if uri.endswith(".glb") else "image/png",
        })
    result = {"content": content, "structuredContent": payload}
    validate(result, load(TOOLS_SCHEMA))
    return result


class Server:
    def __init__(self, toolkit, stdin=None, stdout=None):
        self.toolkit = toolkit
        self.stdin = stdin or sys.stdin
        self.stdout = stdout or sys.stdout
        self.lock = threading.Lock()

    def send(self, message):
        with self.lock:
            self.stdout.write(json.dumps(message) + "\n")
            self.stdout.flush()

    def progress(self, token, progress, message):
        if token is None:
            return
        self.send({"jsonrpc": "2.0", "method": "notifications/progress",
                   "params": {"progressToken": token, "progress": progress, "message": message}})

    # -- methods --

    def initialize(self, params):
        return {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": SERVER_INFO,
            "instructions": (
                "Generation takes minutes and every result is a handle, not bytes: an id, a "
                "path on disk and a viewer URL. Pass targetFaces for a game-ready low-poly "
                "asset, and rig it after generating, never before decimating."
            ),
        }

    def list_tools(self, params):
        return {"tools": TOOLS}

    def call_tool(self, params, meta):
        name = params.get("name")
        args = params.get("arguments") or {}
        handler = getattr(self.toolkit, name, None) if name in {t["name"] for t in TOOLS} else None
        if handler is None:
            raise ToolError(f"no tool named {name}")

        token = (meta or {}).get("progressToken")
        stop = threading.Event()
        if token is not None:
            def heartbeat():
                # A generation runs for minutes with nothing to say. The idle
                # timer on the client side is what kills it, and a progress
                # notification is what keeps that timer alive.
                started, step = time.monotonic(), 0
                while not stop.wait(5.0):
                    step += 1
                    self.progress(token, step,
                                  f"{name}: {int(time.monotonic() - started)}s elapsed")
            threading.Thread(target=heartbeat, daemon=True).start()

        try:
            payload = handler(args)
        finally:
            stop.set()

        uri = payload.get("path") if isinstance(payload, dict) else None
        return tool_result(payload, uri if uri and os.path.isfile(uri) else None)

    def handle(self, message):
        """One request in, one response out, or None for a notification."""
        method = message.get("method")
        params = message.get("params") or {}
        request_id = message.get("id")

        if request_id is None:                       # a notification: nothing comes back
            return None
        try:
            if method == "initialize":
                result = self.initialize(params)
            elif method == "tools/list":
                result = self.list_tools(params)
            elif method == "tools/call":
                result = self.call_tool(params, params.get("_meta"))
            elif method == "ping":
                result = {}
            else:
                return {"jsonrpc": "2.0", "id": request_id,
                        "error": {"code": -32601, "message": f"method not found: {method}"}}
        except ToolError as exc:
            # A tool that failed is a result the model can read and retry from,
            # not a protocol error that ends the conversation.
            return {"jsonrpc": "2.0", "id": request_id,
                    "result": {"content": [{"type": "text", "text": str(exc)}], "isError": True}}
        except subprocess.TimeoutExpired:
            return {"jsonrpc": "2.0", "id": request_id,
                    "result": {"content": [{"type": "text", "text": "the run timed out"}],
                               "isError": True}}
        except Exception as exc:                     # never take the server down
            return {"jsonrpc": "2.0", "id": request_id,
                    "error": {"code": -32603, "message": f"{type(exc).__name__}: {exc}"}}
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    def serve_forever(self):
        for line in self.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except ValueError:
                self.send({"jsonrpc": "2.0", "id": None,
                           "error": {"code": -32700, "message": "parse error"}})
                continue
            response = self.handle(message)
            if response is not None:
                self.send(response)


def main(argv=None):
    parser = argparse.ArgumentParser(description="text-to-3D as an MCP server (stdio)")
    parser.add_argument("--out-dir", default=os.path.join(os.getcwd(), "out"))
    parser.add_argument("--preview-url", default="http://127.0.0.1:8190")
    parser.add_argument("--timeout", type=int, default=2400)
    parser.add_argument("--list-tools", action="store_true",
                        help="print the tool table and exit, for a sanity check")
    args = parser.parse_args(argv)

    toolkit = Toolkit(args.out_dir, args.preview_url, timeout=args.timeout)
    if args.list_tools:
        print(json.dumps({"tools": [{"name": t["name"], "title": t["title"]} for t in TOOLS]},
                         indent=2))
        return 0
    # Anything written to stdout that is not a JSON-RPC message breaks the
    # transport, so the banner goes to stderr.
    print(f"text-to-3d MCP server on stdio, protocol {PROTOCOL_VERSION}, "
          f"serving {toolkit.out_dir}", file=sys.stderr)
    Server(toolkit).serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
