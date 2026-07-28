"""Enough glTF to add a skeleton to a GLB without rebuilding it.

Stdlib only. This is not a glTF library: it reads the handful of things a
skinning pass needs and appends the handful it produces, leaving every byte it
does not understand exactly where it found it.

That constraint is the whole design. The alternative, decoding a GLB into some
intermediate mesh and writing a fresh one, means re-encoding the textures, the
materials and the tangents that TRELLIS baked, and every one of those is a
chance to lose something. Appending buffer views to the BIN chunk touches none
of it: the mesh a person already looked at in the viewer is the same mesh
afterwards, with two more vertex attributes on it.
"""

import base64
import json
import struct

GLB_MAGIC = 0x46546C67
CHUNK_JSON = 0x4E4F534A
CHUNK_BIN = 0x004E4942

# glTF component types, and how struct spells them.
COMPONENT = {
    5120: ("b", 1),   # BYTE
    5121: ("B", 1),   # UNSIGNED_BYTE
    5122: ("h", 2),   # SHORT
    5123: ("H", 2),   # UNSIGNED_SHORT
    5125: ("I", 4),   # UNSIGNED_INT
    5126: ("f", 4),   # FLOAT
}

COUNTS = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}


class GltfError(ValueError):
    """The file is not a GLB this module can work with."""


class Glb:
    """A parsed GLB: its JSON tree, and its binary blob."""

    def __init__(self, gltf, blob):
        self.gltf = gltf
        self.blob = bytearray(blob)

    # ---- reading ----

    @classmethod
    def parse(cls, data):
        if len(data) < 20:
            raise GltfError(f"{len(data)} bytes is too short to be a GLB")
        magic, version, length = struct.unpack_from("<III", data, 0)
        if magic != GLB_MAGIC:
            raise GltfError("missing the glTF magic; this is not a GLB")
        if version != 2:
            raise GltfError(f"glTF container version {version}, expected 2")
        if length != len(data):
            raise GltfError(f"header declares {length} bytes, file is {len(data)}")

        offset, gltf, blob = 12, None, b""
        while offset + 8 <= len(data):
            chunk_len, chunk_type = struct.unpack_from("<II", data, offset)
            body = data[offset + 8: offset + 8 + chunk_len]
            if len(body) != chunk_len:
                raise GltfError("a chunk runs past the end of the file")
            if chunk_type == CHUNK_JSON and gltf is None:
                try:
                    gltf = json.loads(body.decode("utf-8"))
                except (ValueError, UnicodeDecodeError) as exc:
                    raise GltfError(f"the JSON chunk does not parse: {exc}")
            elif chunk_type == CHUNK_BIN and not blob:
                blob = bytes(body)
            offset += 8 + chunk_len + (-chunk_len % 4)

        if gltf is None:
            raise GltfError("no JSON chunk")
        if not gltf.get("meshes"):
            raise GltfError("the glTF declares no meshes")
        return cls(gltf, blob)

    def read_accessor(self, index):
        """One accessor as a list of tuples (or scalars for SCALAR).

        Honours byteStride, because an interleaved GLB is legal and reading it
        as if it were tightly packed returns plausible nonsense rather than an
        error, which is the worst kind of bug to find later.
        """
        accessor = self.gltf["accessors"][index]
        fmt, size = COMPONENT[accessor["componentType"]]
        per = COUNTS[accessor["type"]]
        count = accessor["count"]

        if "bufferView" not in accessor:
            # A sparse or zero-filled accessor. Neither appears in what this
            # toolkit produces, and guessing is worse than refusing.
            raise GltfError("accessor without a bufferView is not supported")

        view = self.gltf["bufferViews"][accessor["bufferView"]]
        base = view.get("byteOffset", 0) + accessor.get("byteOffset", 0)
        stride = view.get("byteStride") or size * per

        values = []
        for i in range(count):
            chunk = self.blob[base + i * stride: base + i * stride + size * per]
            item = struct.unpack("<" + fmt * per, chunk)
            values.append(item[0] if per == 1 else item)
        return values

    def primitives(self):
        """(mesh index, primitive index, primitive) for every primitive."""
        for m, mesh in enumerate(self.gltf.get("meshes", [])):
            for p, prim in enumerate(mesh.get("primitives", [])):
                yield m, p, prim

    def positions(self):
        """Every vertex position, in file order, flattened across primitives.

        Returns (list of (x, y, z), list of (mesh, prim, first, count)) so the
        weights that come back can be sliced onto the primitive they belong to.
        """
        points, spans = [], []
        for m, p, prim in self.primitives():
            attribute = prim.get("attributes", {}).get("POSITION")
            if attribute is None:
                continue
            first = len(points)
            values = self.read_accessor(attribute)
            points.extend(values)
            spans.append((m, p, first, len(values)))
        if not points:
            raise GltfError("no POSITION attribute anywhere in the file")
        return points, spans

    def triangles(self):
        """Index triples, offset into the flattened vertex list from positions()."""
        faces, base = [], 0
        for _m, _p, prim in self.primitives():
            attribute = prim.get("attributes", {}).get("POSITION")
            if attribute is None:
                continue
            count = self.gltf["accessors"][attribute]["count"]
            if prim.get("mode", 4) == 4:
                index = prim.get("indices")
                order = (self.read_accessor(index) if index is not None
                         else list(range(count)))
                for i in range(0, len(order) - 2, 3):
                    faces.append((order[i] + base, order[i + 1] + base, order[i + 2] + base))
            base += count
        return faces

    # ---- writing ----

    def add_accessor(self, values, component_type, kind, target=None, minmax=False):
        """Append data to the BIN chunk and return the new accessor's index."""
        fmt, size = COMPONENT[component_type]
        per = COUNTS[kind]

        # Every accessor's byteOffset must be a multiple of its component size,
        # and a viewer that trusts the spec will read garbage rather than
        # complain if it is not.
        while len(self.blob) % max(size, 4):
            self.blob.append(0)
        offset = len(self.blob)

        for value in values:
            item = (value,) if per == 1 else tuple(value)
            self.blob += struct.pack("<" + fmt * per, *item)

        view = {"buffer": 0, "byteOffset": offset, "byteLength": len(self.blob) - offset}
        if target:
            view["target"] = target
        self.gltf.setdefault("bufferViews", []).append(view)

        accessor = {
            "bufferView": len(self.gltf["bufferViews"]) - 1,
            "componentType": component_type,
            "count": len(values),
            "type": kind,
        }
        if minmax and values:
            columns = list(zip(*[(v,) if per == 1 else tuple(v) for v in values]))
            accessor["min"] = [min(c) for c in columns]
            accessor["max"] = [max(c) for c in columns]
        self.gltf.setdefault("accessors", []).append(accessor)
        return len(self.gltf["accessors"]) - 1

    def to_bytes(self):
        """Serialise back to a GLB, with both chunks padded as the spec requires."""
        self.gltf.setdefault("asset", {}).setdefault("version", "2.0")
        if self.blob:
            self.gltf["buffers"] = [{"byteLength": len(self.blob)}]

        payload = json.dumps(self.gltf, separators=(",", ":")).encode("utf-8")
        payload += b" " * (-len(payload) % 4)          # JSON pads with spaces
        blob = bytes(self.blob) + b"\x00" * (-len(self.blob) % 4)   # BIN pads with zeros

        body = struct.pack("<II", len(payload), CHUNK_JSON) + payload
        if blob:
            body += struct.pack("<II", len(blob), CHUNK_BIN) + blob
        return struct.pack("<III", GLB_MAGIC, 2, 12 + len(body)) + body


def data_uri(blob, media_type="application/octet-stream"):
    return f"data:{media_type};base64," + base64.b64encode(blob).decode("ascii")
