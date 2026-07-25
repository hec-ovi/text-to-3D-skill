#!/usr/bin/env python3
"""Triangle quality for a GLB, as numbers rather than an impression.

    python3 quality.py out/hero-r512.glb [more.glb ...]
    python3 quality.py --json out/*.glb

A diagnostic, not a contract. Nothing in the pipeline calls it and no other
layer reads its output; it exists because "the mesh looks bad" is not a thing
anyone can act on, and because a wireframe overlay is actively misleading. Every
triangle in a wireframe is drawn, front and back, so a clean mesh two layers
deep looks like a heap of slivers. It cost an afternoon here before anyone
measured it.

What it reports, per file:

  minAngle       the smallest angle in each triangle. An equilateral triangle
                 is 60 degrees; the interesting number is the low tail, because
                 a triangle with a 2-degree corner interpolates its normals
                 badly, shades wrong, and pinches when a rig moves it.
  slivers        the share under 10 and under 20 degrees.
  radiusRatio    longest edge over the inscribed circle's diameter, scaled so
                 an equilateral triangle is 1.0. Grows without bound as a
                 triangle degenerates.
  areaSpread     the 99th percentile triangle area over the median, which says
                 whether the mesh spends its budget evenly.
  degenerate     triangles with no area at all. Should always be zero.

Stdlib only.
"""

import argparse
import json
import math
import struct
import sys

GLB_MAGIC = 0x46546C67
CHUNK_JSON = 0x4E4F534A

# glTF componentType -> (struct code, bytes)
COMPONENT = {5120: ("b", 1), 5121: ("B", 1), 5122: ("h", 2), 5123: ("H", 2),
             5125: ("I", 4), 5126: ("f", 4)}
COMPONENT_COUNT = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}


class QualityError(Exception):
    pass


def read_glb(path):
    """(gltf json, binary chunk). Raises QualityError on anything malformed."""
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError as exc:
        raise QualityError(f"cannot read {path}: {exc}")
    if len(data) < 20 or struct.unpack_from("<I", data, 0)[0] != GLB_MAGIC:
        raise QualityError(f"{path} is not a GLB")

    offset, gltf, blob = 12, None, b""
    while offset + 8 <= len(data):
        length, kind = struct.unpack_from("<II", data, offset)
        body = data[offset + 8:offset + 8 + length]
        if kind == CHUNK_JSON and gltf is None:
            gltf = json.loads(body.decode("utf-8"))
        elif kind != CHUNK_JSON and not blob:
            blob = body
        offset += 8 + length + (-length % 4)
    if gltf is None:
        raise QualityError(f"{path} has no JSON chunk")
    return gltf, blob


def accessor(gltf, blob, index):
    """One accessor as a list of tuples, honouring byteStride."""
    acc = gltf["accessors"][index]
    view = gltf["bufferViews"][acc["bufferView"]]
    code, size = COMPONENT[acc["componentType"]]
    per = COMPONENT_COUNT[acc["type"]]
    start = view.get("byteOffset", 0) + acc.get("byteOffset", 0)
    # A stride is only meaningful for interleaved data; tightly packed accessors
    # leave it out and the element size is the stride.
    stride = view.get("byteStride") or size * per
    return [struct.unpack_from("<" + code * per, blob, start + i * stride)
            for i in range(acc["count"])]


def percentile(sorted_values, fraction):
    if not sorted_values:
        return 0.0
    return sorted_values[min(len(sorted_values) - 1, int(len(sorted_values) * fraction))]


def measure(path):
    """Triangle statistics for every primitive in the file, combined."""
    gltf, blob = read_glb(path)
    min_angles, ratios, areas = [], [], []
    degenerate = 0
    triangles = 0

    for mesh in gltf.get("meshes", []):
        for prim in mesh.get("primitives", []):
            # 4 is TRIANGLES; a point cloud or a line set has no triangles to
            # judge and is skipped rather than counted as perfect.
            if prim.get("mode", 4) != 4 or "POSITION" not in prim.get("attributes", {}):
                continue
            pos = accessor(gltf, blob, prim["attributes"]["POSITION"])
            if "indices" in prim:
                idx = [v[0] for v in accessor(gltf, blob, prim["indices"])]
            else:
                idx = list(range(len(pos)))

            xs, ys, zs = zip(*pos) if pos else ((0,), (0,), (0,))
            # Areas are normalised by the model's own size so a 4 cm bolt and a
            # 3 m statue produce comparable spreads.
            extent = max(max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs)) or 1.0

            for t in range(0, len(idx) - 2, 3):
                triangles += 1
                a, b, c = (pos[idx[t + k]] for k in range(3))
                edges = [math.dist(a, b), math.dist(b, c), math.dist(c, a)]
                half = sum(edges) / 2
                under = half * (half - edges[0]) * (half - edges[1]) * (half - edges[2])
                if under <= 0 or min(edges) <= 0:
                    degenerate += 1
                    continue
                area = math.sqrt(under)
                areas.append(area / (extent * extent))
                ratios.append(max(edges) / (2 * math.sqrt(3) * area / half))
                worst = 180.0
                for i in range(3):
                    o1, o2 = edges[(i + 1) % 3], edges[(i + 2) % 3]
                    cosine = (o1 * o1 + o2 * o2 - edges[i] * edges[i]) / (2 * o1 * o2)
                    worst = min(worst, math.degrees(math.acos(max(-1.0, min(1.0, cosine)))))
                min_angles.append(worst)

    if not min_angles:
        raise QualityError(f"{path} has no triangles to measure")

    min_angles.sort()
    ratios.sort()
    areas.sort()
    n = len(min_angles)
    return {
        "file": path,
        "triangles": triangles,
        "degenerate": degenerate,
        "minAngleP01": round(percentile(min_angles, 0.01), 2),
        "minAngleP50": round(percentile(min_angles, 0.50), 2),
        "sliversUnder10Pct": round(100 * sum(1 for a in min_angles if a < 10) / n, 2),
        "sliversUnder20Pct": round(100 * sum(1 for a in min_angles if a < 20) / n, 2),
        "radiusRatioP50": round(percentile(ratios, 0.50), 2),
        "radiusRatioP99": round(percentile(ratios, 0.99), 2),
        "areaSpread": round(percentile(areas, 0.99) / max(percentile(areas, 0.50), 1e-12), 1),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="triangle quality for a GLB")
    parser.add_argument("glb", nargs="+")
    parser.add_argument("--json", action="store_true", help="one JSON array instead of a table")
    args = parser.parse_args(argv)

    rows, failed = [], False
    for path in args.glb:
        try:
            rows.append(measure(path))
        except QualityError as exc:
            print(f"{exc}", file=sys.stderr)
            failed = True

    if args.json:
        print(json.dumps(rows, indent=2))
    elif rows:
        name_width = max(len(r["file"].split("/")[-1]) for r in rows)
        print(f"{'file':<{name_width}}  {'tris':>8} {'minAng':>7} {'<10deg':>7} "
              f"{'<20deg':>7} {'ratio':>6} {'spread':>7} {'degen':>6}")
        for r in rows:
            print(f"{r['file'].split('/')[-1]:<{name_width}}  {r['triangles']:>8} "
                  f"{r['minAngleP50']:>7} {r['sliversUnder10Pct']:>6}% "
                  f"{r['sliversUnder20Pct']:>6}% {r['radiusRatioP50']:>6} "
                  f"{r['areaSpread']:>7} {r['degenerate']:>6}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
