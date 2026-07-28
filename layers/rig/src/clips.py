"""Animation clips generated from the skeleton, not shipped alongside it.

Why generate rather than retarget a library clip. Mixamo's own clips cannot be
redistributed as standalone assets, so they cannot ship in this repository, and
the CC0 packs that can are authored against their own skeletons, which puts a
retarget step back in the path. Retargeting across two rigs with different rest
poses is exactly where a walk comes out backwards. A clip solved against the
skeleton in front of it has no mapping step to get wrong.

What this is not: hand-animated. These are sine curves on the right joints,
which reads as a walk at gameplay distance and does not survive a close look.
A named Mixamo skeleton is the point of the naming pass, so a real clip pack
can be dropped in later and play with no retargeting at all.

Convention, read off the model's own output: +X is left, +Y is up, +Z is
forward, which is Mixamo's. A rotation about +X takes -Y towards -Z, so a
negative angle about X swings a downward-pointing limb forward.
"""

import math

from skeleton import quaternion_from_axis

X, Y, Z = (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)

SAMPLES = 17          # per cycle; linear interpolation between them
IDENTITY = (0.0, 0.0, 0.0, 1.0)


def _index(names):
    return {name: i for i, name in enumerate(names)}


def _curve(joint, axis, amplitude, phase, duration, samples=SAMPLES, offset=0.0):
    """One joint swinging on a sine, as rotation keyframes over one loop."""
    keys = []
    for step in range(samples):
        t = duration * step / (samples - 1)
        angle = offset + amplitude * math.sin(2 * math.pi * (step / (samples - 1)) + phase)
        keys.append((t, quaternion_from_axis(axis, angle)))
    return keys


def _bob(joint_position, amplitude, duration, cycles=2, samples=SAMPLES):
    """Vertical translation keys for the hips: two dips per stride."""
    keys = []
    for step in range(samples):
        t = duration * step / (samples - 1)
        y = -amplitude * abs(math.sin(math.pi * cycles * step / (samples - 1)))
        keys.append((t, (joint_position[0], joint_position[1] + y, joint_position[2])))
    return keys


def _has(index, *names):
    return all(name in index for name in names)


def walk(names, positions, duration=1.0):
    """A one-second stride: legs opposed, arms counter-swinging, hips dipping."""
    index = _index(names)
    required = ("mixamorig:LeftUpLeg", "mixamorig:RightUpLeg",
                "mixamorig:LeftLeg", "mixamorig:RightLeg")
    if not _has(index, *required):
        return None

    channels = {}

    # Thighs lead the stride, half a cycle apart. The knee only ever folds
    # backwards, so its curve is offset to stay on one side of straight.
    channels[index["mixamorig:LeftUpLeg"]] = {
        "rotation": _curve(None, X, -0.55, 0.0, duration)}
    channels[index["mixamorig:RightUpLeg"]] = {
        "rotation": _curve(None, X, -0.55, math.pi, duration)}
    channels[index["mixamorig:LeftLeg"]] = {
        "rotation": _curve(None, X, 0.42, math.pi / 2, duration, offset=0.45)}
    channels[index["mixamorig:RightLeg"]] = {
        "rotation": _curve(None, X, 0.42, -math.pi / 2, duration, offset=0.45)}

    for side, phase in (("Left", math.pi / 2), ("Right", -math.pi / 2)):
        foot = f"mixamorig:{side}Foot"
        if foot in index:
            channels[index[foot]] = {"rotation": _curve(None, X, 0.22, phase, duration)}

    # Arms counter the legs: the left arm goes with the right leg. Without
    # this a walk reads as a shuffle, because the counter-swing is what a
    # person actually recognises.
    for side, phase in (("Left", math.pi), ("Right", 0.0)):
        arm = f"mixamorig:{side}Arm"
        if arm in index:
            channels[index[arm]] = {"rotation": _curve(None, X, -0.38, phase, duration)}
        fore = f"mixamorig:{side}ForeArm"
        if fore in index:
            channels[index[fore]] = {
                "rotation": _curve(None, X, 0.14, phase, duration, offset=-0.25)}

    hips = index.get("mixamorig:Hips")
    if hips is not None:
        channels[hips] = {"translation": _bob(positions[hips], 0.035, duration)}

    spine = index.get("mixamorig:Spine")
    if spine is not None:
        channels[spine] = {"rotation": _curve(None, Y, 0.06, 0.0, duration)}

    return {"name": "walk", "duration": duration, "channels": channels}


def idle(names, positions, duration=3.0):
    """Breathing, and a little weight shift. What a character does when nothing happens."""
    index = _index(names)
    channels = {}

    hips = index.get("mixamorig:Hips")
    if hips is not None:
        channels[hips] = {"translation": _bob(positions[hips], 0.012, duration, cycles=1)}

    for name, amplitude, axis in (("mixamorig:Spine1", 0.035, X),
                                  ("mixamorig:Neck", 0.03, X),
                                  ("mixamorig:Head", 0.025, Y)):
        if name in index:
            channels[index[name]] = {"rotation": _curve(None, axis, amplitude, 0.0, duration)}

    for side in ("Left", "Right"):
        arm = f"mixamorig:{side}Arm"
        if arm in index:
            channels[index[arm]] = {"rotation": _curve(None, Z, 0.03, 0.0, duration)}

    if not channels:
        return None
    return {"name": "idle", "duration": duration, "channels": channels}


# There was a third clip here, a full turn on the spot, and it was wrong in a
# way worth leaving a note about. A 360 degree spin ends on the quaternion
# (0, 0, 0, -1), which is the same orientation as (0, 0, 0, 1) with every
# component negated. glTF's LINEAR interpolation blends the components, so the
# last step would have driven the rotation through zero and folded the
# character inside out for a frame. Any full revolution has this problem, and
# the viewer's turntable already spins the model, so the clip was deleted
# rather than fixed.


def build(names, positions):
    """Every clip that applies to this skeleton, in the order a viewer lists them."""
    return [clip for clip in (idle(names, positions), walk(names, positions)) if clip]
