#!/usr/bin/env python3
"""preview blackbox: serve a directory of GLBs and a three.js page that shows them.

    python3 src/serve.py --dir ../../out
    python3 src/serve.py --dir ../../out --port 8190 --open

Routes:
    GET /                 the viewer page
    GET /api/models       ModelList envelope, newest first
    GET /models/<name>    the GLB bytes, as model/gltf-binary
    GET /<asset>          the page's own js/css and the vendored three.js

Stdlib only. See ../CONTRACT.md.
"""

import argparse
import datetime
import json
import mimetypes
import os
import posixpath
import re
import struct
import sys
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
LAYER = os.path.dirname(HERE)
WEB = os.path.join(LAYER, "web")

CONTRACT_VERSION = "1.0"

# Browsers refuse ES modules served as text/plain, and a GLB served as
# octet-stream is fine but the correct type makes the network tab readable.
TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".glb": "model/gltf-binary",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
}

GLB_MAGIC = 0x46546C67
CHUNK_JSON = 0x4E4F534A

# image2mesh names its output <image-stem>-r<resolution>.glb, so the picture a
# mesh came from is derivable rather than something to guess at.
SOURCE_SUFFIX = re.compile(r"-r\d+$")
IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg",
               ".jpeg": "image/jpeg", ".webp": "image/webp"}


def png_size(path):
    """(width, height) from a PNG IHDR, or None for anything else."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(24)
    except OSError:
        return None
    if len(head) < 24 or head[:8] != b"\x89PNG\r\n\x1a\n" or head[12:16] != b"IHDR":
        return None
    return struct.unpack(">II", head[16:24])


def find_source(directory, glb_name):
    """The image `glb_name` was reconstructed from, if it sits in the same folder.

    Only the exact stem matches, which is what keeps the engine's own
    `<stem>-r<res>_base.png` texture atlas out of this: that file is an output,
    and showing it as the source would misrepresent what the mesh came from.
    """
    stem = SOURCE_SUFFIX.sub("", os.path.splitext(glb_name)[0])
    if not stem:
        return None
    for ext, media in IMAGE_TYPES.items():
        candidate = stem + ext
        path = os.path.join(directory, candidate)
        if not os.path.isfile(path):
            continue
        entry = {
            "name": candidate,
            "uri": "/images/" + urllib.parse.quote(candidate),
            "byteSize": os.path.getsize(path),
            "mediaType": media,
        }
        size = png_size(path)
        if size:
            entry["width"], entry["height"] = size
        return entry
    return None


def glb_stats(path):
    """Triangle and material counts straight out of the file.

    Deliberately a second, smaller GLB reader than the one in image2mesh: this
    layer does not import that one, and only needs enough to label a dropdown.
    Returns None when the file is not a GLB this reader understands.
    """
    try:
        with open(path, "rb") as fh:
            head = fh.read(12)
            if len(head) < 12:
                return None
            magic, version, _length = struct.unpack("<III", head)
            if magic != GLB_MAGIC or version != 2:
                return None
            # Walk chunks until the JSON one; it is required to come first.
            while True:
                header = fh.read(8)
                if len(header) < 8:
                    return None
                chunk_len, chunk_type = struct.unpack("<II", header)
                body = fh.read(chunk_len)
                if len(body) != chunk_len:
                    return None
                if chunk_type == CHUNK_JSON:
                    gltf = json.loads(body.decode("utf-8"))
                    break
                fh.seek(-chunk_len % 4, os.SEEK_CUR)
    except (OSError, ValueError, UnicodeDecodeError):
        return None

    accessors = gltf.get("accessors", [])
    triangles = 0
    for mesh in gltf.get("meshes", []):
        for prim in mesh.get("primitives", []):
            if prim.get("mode", 4) != 4:
                continue
            index = prim.get("indices", prim.get("attributes", {}).get("POSITION"))
            if index is not None and index < len(accessors):
                triangles += accessors[index].get("count", 0) // 3
    return {"triangles": triangles, "materials": len(gltf.get("materials", []))}


def list_models(directory):
    """ModelList envelope for `directory`, newest first."""
    models = []
    try:
        names = os.listdir(directory)
    except OSError:
        names = []

    for name in names:
        if not name.lower().endswith(".glb"):
            continue
        path = os.path.join(directory, name)
        if not os.path.isfile(path):
            continue
        stat = os.stat(path)
        entry = {
            "name": name,
            "uri": "/models/" + urllib.parse.quote(name),
            "byteSize": stat.st_size,
            "modifiedAt": datetime.datetime.fromtimestamp(
                stat.st_mtime, datetime.timezone.utc).replace(microsecond=0).isoformat(),
        }
        stats = glb_stats(path)
        if stats is None:
            entry["readable"] = False
        else:
            entry["readable"] = True
            entry.update(stats)
        source = find_source(directory, name)
        if source:
            entry["source"] = source
        models.append(entry)

    models.sort(key=lambda m: m["modifiedAt"], reverse=True)
    return {"contractVersion": CONTRACT_VERSION,
            "dir": os.path.abspath(directory),
            "models": models}


def make_handler(models_dir):
    class Handler(BaseHTTPRequestHandler):
        server_version = "t2m-preview"

        def log_message(self, fmt, *args):
            if os.environ.get("T2M_PREVIEW_QUIET") != "1":
                sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))

        # ---- helpers ----

        def _send(self, status, body, ctype, extra=None):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            # The viewer refetches a model after a regeneration; a cached 200
            # would show the old mesh and look like the engine did nothing.
            self.send_header("Cache-Control", "no-store")
            for key, value in (extra or {}).items():
                self.send_header(key, value)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, status, payload):
            self._send(status, json.dumps(payload, indent=2).encode("utf-8"),
                       "application/json; charset=utf-8")

        def _error(self, status, code, message, detail=""):
            payload = {"contractVersion": CONTRACT_VERSION, "code": code, "message": message}
            if detail:
                payload["detail"] = detail
            self._json(status, payload)

        def _file(self, root, relative):
            """Serve `relative` under `root`, refusing anything that escapes it."""
            safe = posixpath.normpath("/" + relative).lstrip("/")
            path = os.path.abspath(os.path.join(root, safe))
            if os.path.commonpath([path, os.path.abspath(root)]) != os.path.abspath(root):
                return self._error(403, "FORBIDDEN", "path escapes the served directory")
            if not os.path.isfile(path):
                return self._error(404, "NOT_FOUND", f"no such file: {safe}")
            ext = os.path.splitext(path)[1].lower()
            ctype = TYPES.get(ext) or mimetypes.guess_type(path)[0] or "application/octet-stream"
            with open(path, "rb") as fh:
                self._send(200, fh.read(), ctype)

        # ---- routes ----

        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            path = urllib.parse.urlparse(self.path).path

            if path == "/api/models":
                if not os.path.isdir(models_dir):
                    return self._error(404, "DIR_MISSING",
                                       f"no directory at {models_dir}",
                                       "pass --dir with a path that exists")
                return self._json(200, list_models(models_dir))

            if path.startswith("/models/"):
                return self._file(models_dir, urllib.parse.unquote(path[len("/models/"):]))

            if path.startswith("/images/"):
                name = urllib.parse.unquote(path[len("/images/"):])
                if os.path.splitext(name)[1].lower() not in IMAGE_TYPES:
                    return self._error(404, "NOT_FOUND", f"not an image: {name}")
                return self._file(models_dir, name)

            if path == "/":
                return self._file(WEB, "index.html")

            return self._file(WEB, urllib.parse.unquote(path.lstrip("/")))

    return Handler


def serve(models_dir, host="127.0.0.1", port=8190, open_browser=False):
    handler = make_handler(os.path.abspath(models_dir))
    try:
        httpd = ThreadingHTTPServer((host, port), handler)
    except OSError as exc:
        payload = {"contractVersion": CONTRACT_VERSION, "code": "PORT_IN_USE",
                   "message": f"cannot bind {host}:{port}", "detail": str(exc)}
        print(json.dumps(payload, indent=2), file=sys.stderr)
        raise SystemExit(1)

    url = f"http://{host}:{httpd.server_port}/"
    count = len(list_models(models_dir)["models"]) if os.path.isdir(models_dir) else 0
    print(f"preview: {url}")
    print(f"serving {count} GLB{'' if count == 1 else 's'} from {os.path.abspath(models_dir)}")
    if not count:
        print("  (none yet: generate one with layers/pipeline/src/pipeline.py)")
    if open_browser:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        httpd.server_close()


def main(argv=None):
    parser = argparse.ArgumentParser(description="three.js preview for generated GLBs")
    parser.add_argument("--dir", default=os.path.join(os.getcwd(), "out"),
                        help="directory of .glb files to serve (default ./out)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8190)
    parser.add_argument("--open", action="store_true", help="open a browser window")
    args = parser.parse_args(argv)
    serve(args.dir, args.host, args.port, args.open)


if __name__ == "__main__":
    main()
