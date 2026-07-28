"""Add a skeleton and its skinning to an existing GLB, in place.

Stdlib only. Everything here appends: no accessor is rewritten, no texture is
re-encoded, no material is touched. What comes out is the same mesh a person
already looked at, now bound to bones.
"""

from gltf import Glb  # noqa: F401  (re-exported for callers)
import skeleton as sk

ARRAY_BUFFER = 34962

FLOAT = 5126
UNSIGNED_SHORT = 5123


class SkinError(ValueError):
    """The GLB cannot carry this skeleton."""


def _mesh_nodes(gltf):
    return [i for i, node in enumerate(gltf.get("nodes", [])) if "mesh" in node]


def attach(glb, positions, parents, names, joints, weights, spans):
    """Bind `glb`'s vertices to a skeleton.

    positions/parents/names describe the skeleton in the mesh's own coordinate
    frame. joints/weights are per vertex, already pruned to four and
    normalised, in the flattened order `Glb.positions()` returns, and spans say
    which primitive each run belongs to.
    """
    gltf = glb.gltf
    if len(joints) != len(weights):
        raise SkinError("joints and weights disagree on the vertex count")
    if not (len(positions) == len(parents) == len(names)):
        raise SkinError("the skeleton's positions, parents and names disagree")

    total = sum(count for _m, _p, _first, count in spans)
    if total != len(joints):
        raise SkinError(f"the mesh has {total} vertices and {len(joints)} were skinned")

    nodes = gltf.setdefault("nodes", [])
    first_joint = len(nodes)

    # A joint node carries its transform relative to its parent, and glTF wants
    # it column-major. The rest pose is rigid, so translation alone would do,
    # but a matrix keeps this correct if the model ever predicts a rotated bone.
    globals_ = [sk.translation_matrix(p) for p in positions]
    locals_ = sk.local_matrices(globals_, parents)

    for index, name in enumerate(names):
        nodes.append({"name": name, "matrix": sk.column_major(locals_[index])})

    for index, parent in enumerate(parents):
        if parent is not None and parent >= 0:
            nodes[first_joint + parent].setdefault("children", []).append(first_joint + index)

    root = next(i for i, p in enumerate(parents) if p is None or p < 0)

    # The inverse of each joint's rest transform, which is what takes a vertex
    # out of mesh space and into the joint's own before the pose is applied.
    inverse_binds = [sk.column_major(sk.invert_rigid(globals_[i])) for i in range(len(names))]
    ibm = glb.add_accessor(inverse_binds, FLOAT, "MAT4")

    gltf.setdefault("skins", []).append({
        "joints": [first_joint + i for i in range(len(names))],
        "inverseBindMatrices": ibm,
        "skeleton": first_joint + root,
    })
    skin_index = len(gltf["skins"]) - 1

    for mesh_index, prim_index, first, count in spans:
        prim = gltf["meshes"][mesh_index]["primitives"][prim_index]
        prim["attributes"]["JOINTS_0"] = glb.add_accessor(
            joints[first:first + count], UNSIGNED_SHORT, "VEC4", target=ARRAY_BUFFER)
        prim["attributes"]["WEIGHTS_0"] = glb.add_accessor(
            weights[first:first + count], FLOAT, "VEC4", target=ARRAY_BUFFER)

    # A skinned mesh's own node transform is ignored by the spec: vertices are
    # taken to be in skin space. A mesh sitting under a transformed parent
    # would therefore jump the moment it is bound, so the skinned node is
    # reparented to the scene root with no transform of its own. The skeleton
    # was solved in this same space, so identity is the correct answer rather
    # than a convenient one.
    targets = _mesh_nodes(gltf)
    if not targets:
        raise SkinError("no node references a mesh, so there is nothing to skin")
    scene = gltf.setdefault("scenes", [{"nodes": []}])[gltf.get("scene", 0)]
    roots = scene.setdefault("nodes", [])

    for node_index in targets:
        node = gltf["nodes"][node_index]
        node["skin"] = skin_index
        for key in ("matrix", "translation", "rotation", "scale"):
            node.pop(key, None)
        if node_index not in roots:
            for other in gltf["nodes"]:
                if node_index in other.get("children", []):
                    other["children"].remove(node_index)
            roots.append(node_index)

    if first_joint + root not in roots:
        roots.append(first_joint + root)
    return skin_index, first_joint


def add_animations(glb, clips, first_joint):
    """Append clips. Each is {name, duration, channels: {joint: {path: keys}}}.

    A key is (time, value); `path` is glTF's own spelling, so "rotation" wants
    a quaternion and "translation" a vector.
    """
    gltf = glb.gltf
    animations = gltf.setdefault("animations", [])

    for clip in clips:
        samplers, channels = [], []
        for joint_index, paths in clip["channels"].items():
            for path, keys in paths.items():
                times = [t for t, _ in keys]
                values = [v for _, v in keys]
                kind = "VEC4" if path == "rotation" else "VEC3"
                sampler = {
                    "input": glb.add_accessor(times, FLOAT, "SCALAR", minmax=True),
                    "output": glb.add_accessor(values, FLOAT, kind),
                    "interpolation": "LINEAR",
                }
                samplers.append(sampler)
                channels.append({
                    "sampler": len(samplers) - 1,
                    "target": {"node": first_joint + joint_index, "path": path},
                })
        animations.append({"name": clip["name"], "samplers": samplers, "channels": channels})
    return len(animations)
