"""Turn a predicted joint tree into a named Mixamo skeleton, and its weights into glTF's.

Stdlib only, no numpy: this runs in the driver, not in the model container.

The naming is the load-bearing part. SkinTokens predicts a tree and positions
and no names at all, and a nameless skeleton cannot be animated by anything
somebody else authored. Retargeting is the usual answer and it is the thing
that goes wrong: mapping bone to bone across two rigs with different rest poses
and different axes is where a walk cycle comes out backwards or inside out. If
the joints simply carry the names the clip was authored against, there is no
mapping step to get wrong.

So the names are derived from the shape of the tree rather than guessed from an
index, because an index is only stable until the model emits a subject with one
fewer finger.
"""

import math

# Mixamo's own spelling, prefix included. A clip authored in Mixamo addresses
# these exact strings, which is the entire point of using them.
PREFIX = "mixamorig:"

SPINE_CHAIN = ["Spine", "Spine1", "Spine2"]
NECK_CHAIN = ["Neck", "Head", "HeadTop_End"]
ARM_CHAIN = ["Shoulder", "Arm", "ForeArm", "Hand"]
LEG_CHAIN = ["UpLeg", "Leg", "Foot", "ToeBase", "Toe_End"]
FINGERS = ["Thumb", "Index", "Middle", "Ring", "Pinky"]


class SkeletonError(ValueError):
    """The predicted tree is not something this namer recognises."""


def children_of(parents):
    kids = [[] for _ in parents]
    root = None
    for index, parent in enumerate(parents):
        if parent is None or parent < 0:
            if root is not None:
                raise SkeletonError("more than one root joint")
            root = index
        else:
            kids[parent].append(index)
    if root is None:
        raise SkeletonError("no root joint")
    return root, kids


def _descend(start, kids, positions, axis, sign):
    """Follow the child that keeps going in one direction. Returns the chain."""
    chain = [start]
    node = start
    while kids[node]:
        best, best_score = None, None
        for kid in kids[node]:
            score = (positions[kid][axis] - positions[node][axis]) * sign
            if best_score is None or score > best_score:
                best, best_score = kid, score
        chain.append(best)
        node = best
    return chain


def _limb(start, kids, positions, names, side, chain_names, prefix=""):
    """Name a limb down its longest path, then anything hanging off the end."""
    chain = _longest_path(start, kids)
    for position, joint in enumerate(chain):
        if position < len(chain_names):
            names[joint] = f"{PREFIX}{side}{prefix}{chain_names[position]}"
        else:
            # Past the vocabulary: keep it addressable rather than nameless.
            names[joint] = f"{PREFIX}{side}{prefix}{chain_names[-1]}{position - len(chain_names) + 1}"
    return chain


def _longest_path(start, kids):
    """The deepest path from `start`. A limb's identity is its longest run."""
    best = [start]
    stack = [(start, [start])]
    while stack:
        node, path = stack.pop()
        if not kids[node] and len(path) > len(best):
            best = path
        for kid in kids[node]:
            stack.append((kid, path + [kid]))
    return best


def _name_fingers(hand, kids, positions, names, side):
    """Digits off a hand, ordered thumb-outwards by how far out they sit.

    Mixamo names five and the model rarely predicts five. Sorting by distance
    from the hand's own axis puts the thumb first, which is what a clip that
    only animates the thumb expects.
    """
    digits = sorted(kids[hand], key=lambda j: -abs(positions[j][2]))
    for order, digit in enumerate(digits):
        label = FINGERS[order] if order < len(FINGERS) else f"Digit{order}"
        chain = _longest_path(digit, kids)
        for depth, joint in enumerate(chain):
            names[joint] = f"{PREFIX}{side}Hand{label}{depth + 1}"


def name_joints(positions, parents):
    """Mixamo names for a predicted humanoid tree.

    `positions` are joint positions in the armature's own frame, `parents` is
    one index per joint with a negative for the root. Raises SkeletonError when
    the tree does not read as a humanoid, which is the honest outcome for a
    chair or a giraffe: naming those Hips and Spine would be a lie a clip would
    then act on.
    """
    if len(positions) != len(parents):
        raise SkeletonError("positions and parents disagree on the joint count")
    if len(positions) < 6:
        raise SkeletonError(f"{len(positions)} joints is too few to be a humanoid")

    root, kids = children_of(parents)
    names = [None] * len(parents)
    names[root] = f"{PREFIX}Hips"

    # Off the hips: whatever goes up is the spine, whatever goes down are legs.
    up = [k for k in kids[root] if positions[k][1] > positions[root][1]]
    down = [k for k in kids[root] if positions[k][1] <= positions[root][1]]
    if not up:
        raise SkeletonError("nothing above the root, so this is not a standing figure")
    if len(down) != 2:
        raise SkeletonError(f"expected two legs off the root, found {len(down)}")

    # The chest is where the arms branch: walk up until a joint has more than
    # one child, which is the shoulders splitting off the spine.
    spine = _descend(up[0], kids, positions, axis=1, sign=1)
    chest = None
    for joint in spine:
        if len(kids[joint]) > 1:
            chest = joint
            break
    if chest is None:
        raise SkeletonError("no branch point above the root, so there are no arms")

    to_chest = spine[:spine.index(chest) + 1]
    for position, joint in enumerate(to_chest):
        label = SPINE_CHAIN[min(position, len(SPINE_CHAIN) - 1)]
        names[joint] = f"{PREFIX}{label}" if position < len(SPINE_CHAIN) \
            else f"{PREFIX}Spine{position}"

    # From the chest: the branch that keeps rising is the neck, the two that go
    # sideways are the arms, told apart by which side of the body they are on.
    # +X is left, which is Mixamo's convention and the one the model was
    # trained against.
    branches = kids[chest]
    neck = max(branches, key=lambda j: positions[j][1] - positions[chest][1])
    arms = [j for j in branches if j != neck]
    if len(arms) != 2:
        raise SkeletonError(f"expected two arms off the chest, found {len(arms)}")

    neck_chain = _longest_path(neck, kids)
    for position, joint in enumerate(neck_chain):
        label = NECK_CHAIN[min(position, len(NECK_CHAIN) - 1)]
        names[joint] = f"{PREFIX}{label}"

    for start in arms:
        side = "Left" if positions[start][0] > positions[chest][0] else "Right"
        chain = _limb(start, kids, positions, names, side, ARM_CHAIN)
        hand = chain[min(len(ARM_CHAIN), len(chain)) - 1]
        _name_fingers(hand, kids, positions, names, side)

    for start in down:
        side = "Left" if positions[start][0] > positions[root][0] else "Right"
        _limb(start, kids, positions, names, side, LEG_CHAIN)

    left = sum(1 for n in names if n and n.startswith(PREFIX + "Left"))
    right = sum(1 for n in names if n and n.startswith(PREFIX + "Right"))
    if not left or not right:
        raise SkeletonError("one side of the body came out unnamed")

    # Anything the walk above never reached. Leaving a None would produce a
    # glTF node with no name, which is legal and unaddressable.
    for index, name in enumerate(names):
        if name is None:
            names[index] = f"{PREFIX}Joint{index}"
    return names


def prune_and_normalize(rows, limit=4):
    """Per-vertex weights to glTF's four-influence budget.

    Two things are wrong with what comes out of the model, and both are normal.
    It writes a weight for every joint, up to nine of them above noise on a
    single vertex, where a glTF JOINTS_0/WEIGHTS_0 pair holds four and most
    engines only ever read the first set. And the row does not sum to one, so
    every vertex is slightly underweighted and the mesh would shrink towards
    the origin as soon as anything moved.

    Keeping the four largest and rescaling them to sum to one fixes both. The
    dropped influences are the smallest ones by construction, which is the same
    trade every engine's own importer makes.
    """
    joints, weights = [], []
    for row in rows:
        ranked = sorted(enumerate(row), key=lambda pair: -pair[1])[:limit]
        ranked = [(j, w) for j, w in ranked if w > 0]
        total = sum(w for _, w in ranked)
        if total <= 0:
            # A vertex the model gave nothing: bind it rigidly to the root, so
            # it travels with the character instead of being left behind at the
            # origin when the skeleton moves.
            joints.append((0, 0, 0, 0))
            weights.append((1.0, 0.0, 0.0, 0.0))
            continue
        padded = ranked + [(0, 0.0)] * (limit - len(ranked))
        joints.append(tuple(j for j, _ in padded))
        weights.append(tuple(w / total for _, w in padded))
    return joints, weights


# ---- 4x4 matrices, row-major here and column-major on the way out ------------


def identity():
    return [[1.0 if i == j else 0.0 for j in range(4)] for i in range(4)]


def multiply(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(4)) for j in range(4)] for i in range(4)]


def invert_rigid(m):
    """Inverse of a rotation-plus-translation matrix.

    Every matrix here comes from a joint's rest pose, so it is rigid and the
    inverse is the transpose of the rotation with the translation carried back
    through it. A general inverse would also work and would quietly return
    nonsense for a singular matrix instead of being obviously inapplicable.
    """
    rotation = [[m[i][j] for j in range(3)] for i in range(3)]
    translation = [m[i][3] for i in range(3)]
    inverse = identity()
    for i in range(3):
        for j in range(3):
            inverse[i][j] = rotation[j][i]
    for i in range(3):
        inverse[i][3] = -sum(rotation[k][i] * translation[k] for k in range(3))
    return inverse


def translation_matrix(offset):
    m = identity()
    for i in range(3):
        m[i][3] = offset[i]
    return m


def column_major(m):
    """glTF stores a matrix as 16 floats in column-major order."""
    return [m[row][col] for col in range(4) for row in range(4)]


def local_matrices(globals_, parents):
    """Each joint's transform relative to its parent."""
    locals_ = []
    for index, matrix in enumerate(globals_):
        parent = parents[index]
        if parent is None or parent < 0:
            locals_.append([row[:] for row in matrix])
        else:
            locals_.append(multiply(invert_rigid(globals_[parent]), matrix))
    return locals_


def quaternion_from_axis(axis, angle):
    """A rotation as glTF spells it: x, y, z, w."""
    length = math.sqrt(sum(c * c for c in axis)) or 1.0
    unit = [c / length for c in axis]
    half = angle / 2.0
    s = math.sin(half)
    return (unit[0] * s, unit[1] * s, unit[2] * s, math.cos(half))
