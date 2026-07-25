"""Runs inside Blender: GLB in, rigged and animated GLB out.

Invoked by rig.py as

    blender --background --python blender_rig.py -- <job.json>

and it talks back through the job file's `outJson` path, never through stdout,
because Blender prints its own banner and add-on chatter on stdout and a JSON
document sharing that stream is a parse away from a bad day.

Two subjects, two different answers:

    humanoid   fit a skeleton to the mesh by measuring it, bind with bone heat,
               author locomotion clips on the bones
    prop       no armature at all. A node the caller can animate, TRS clips on
               that node, and a named socket empty for attachment

The humanoid skeleton is measured, not guessed at from a canon of proportions:
the mesh is sliced along its up axis, and where a slice splits into two islands
the legs begin, where it is widest the shoulders are. That fits a stocky dwarf
and a tall elf from the same code, which fixed ratios do not.
"""

import json
import math
import os
import sys

import bpy
import mathutils

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fit  # noqa: E402


# ---- small geometry helpers -------------------------------------------------


def clusters(values, gap):
    """Split a sorted list of numbers wherever the step exceeds `gap`."""
    out, run = [], []
    for v in values:
        if run and v - run[-1] > gap:
            out.append(run)
            run = []
        run.append(v)
    if run:
        out.append(run)
    return out


def slice_report(verts, y0, y1, gap):
    """What the mesh looks like in one horizontal band: islands across X."""
    xs = sorted(v.x for v in verts if y0 <= v.y < y1)
    if not xs:
        return {"count": 0, "islands": [], "width": 0.0}
    islands = [(c[0], c[-1]) for c in clusters(xs, gap)]
    return {"count": len(xs), "islands": islands, "width": xs[-1] - xs[0]}


def measure(mesh_object, slices=48):
    """Landmarks in world space, in metres, read off the mesh itself.

    Y is up: the GLB importer brings a glTF Y-up scene into Blender's Z-up
    world, so this runs after the mesh has been read in Blender's frame, where
    up is Z. The caller passes vertices already mapped so that `.y` is up.
    """
    verts = [mesh_object.matrix_world @ v.co for v in mesh_object.data.vertices]
    verts = [mathutils.Vector((v.x, v.z, -v.y)) for v in verts]   # Blender Z-up -> Y-up

    ys = [v.y for v in verts]
    xs = [v.x for v in verts]
    zs = [v.z for v in verts]
    low, high = min(ys), max(ys)
    height = high - low
    if height <= 0:
        raise RuntimeError("the mesh has no vertical extent")

    gap = height * 0.02          # two islands are separate if this far apart
    step = height / slices
    bands = []
    for i in range(slices):
        y0 = low + i * step
        bands.append((y0 + step / 2, slice_report(verts, y0, y0 + step, gap)))

    # Legs: scan up from the floor while the band is two islands. The first band
    # that fuses into one is the crotch, and the hips sit a little above it.
    crotch = low + height * 0.5
    for centre, band in bands:
        if band["count"] < 8:
            continue
        if len(band["islands"]) >= 2 and centre < low + height * 0.6:
            crotch = centre
        elif centre > low + height * 0.25 and len(band["islands"]) == 1:
            break

    # Feet: the two islands nearest the floor give each foot its own centre.
    floor_band = next((b for _, b in bands if b["count"] >= 8), bands[0][1])
    if len(floor_band["islands"]) >= 2:
        left_foot = sum(floor_band["islands"][0]) / 2
        right_foot = sum(floor_band["islands"][-1]) / 2
    else:
        spread = (max(xs) - min(xs)) * 0.18
        left_foot, right_foot = -spread, spread

    # Shoulders: the widest band in the upper half. An A-posed character is
    # widest at the hands, so the search stops above the elbows.
    upper = [(c, b) for c, b in bands if c > low + height * 0.62 and b["count"] >= 8]
    shoulder_y = low + height * 0.82
    if upper:
        shoulder_y = max(upper, key=lambda cb: cb[1]["width"])[0]

    reach = max(abs(min(xs)), abs(max(xs)))
    measured = fit.limbs([(v.x, v.y, v.z) for v in verts], slices)
    return {
        "chains": measured["chains"],
        # What is wrong with the pose the mesh arrived in. Carried out to the
        # caller rather than acted on here: the rig binds whatever it is given,
        # and the caller is the one who can decide to regenerate.
        "pose": fit.pose_report(measured),
        "low": low, "high": high, "height": height,
        "centreX": (min(xs) + max(xs)) / 2,
        "centreZ": (min(zs) + max(zs)) / 2,
        "crotch": measured["crotch"],
        "shoulderY": measured["shoulderY"],
        "shoulderX": max(height * 0.08, (max(xs) - min(xs)) * 0.13),
        "reach": reach,
        "leftFootX": left_foot,
        "rightFootX": right_foot,
        "depth": max(zs) - min(zs),
    }


# ---- the skeleton -----------------------------------------------------------

# Mixamo naming, because it is what the free CC0 clip libraries and every
# retargeting tool already speak. See ../CONTRACT.md.
def canonical_bones(m):
    """The templated skeleton: legs straight down, arms straight out.

    Two jobs. It is the fallback for a limb the measurement could not find, and
    it is the neutral target every clip is corrected towards, so a character
    bound mid-stride still starts its walk cycle standing still.
    """
    h = m["height"]
    cx, cz = m["centreX"], m["centreZ"]
    hip_y = m["crotch"] + h * 0.06
    sh_y = m["shoulderY"]
    neck_y = sh_y + h * 0.04
    head_y = neck_y + h * 0.05
    top_y = m["high"]
    sx = m["shoulderX"]
    arm = max(m["reach"] - sx, h * 0.14)
    foot_z = cz + m["depth"] * 0.25

    def v(x, y, z=cz):
        return (x, y, z)

    bones = [
        ("mixamorig:Hips", None, v(cx, hip_y), v(cx, hip_y + h * 0.08)),
        ("mixamorig:Spine", "mixamorig:Hips", v(cx, hip_y + h * 0.08), v(cx, hip_y + h * 0.16)),
        ("mixamorig:Spine1", "mixamorig:Spine", v(cx, hip_y + h * 0.16), v(cx, sh_y)),
        ("mixamorig:Neck", "mixamorig:Spine1", v(cx, sh_y), v(cx, neck_y)),
        ("mixamorig:Head", "mixamorig:Neck", v(cx, neck_y), v(cx, top_y)),
    ]

    for side, sign in (("Left", -1.0), ("Right", 1.0)):
        # Arms hang from the shoulder line out to the widest point of the mesh.
        bones += [
            (f"mixamorig:{side}Shoulder", "mixamorig:Spine1",
             v(cx, sh_y), v(cx + sign * sx, sh_y)),
            (f"mixamorig:{side}Arm", f"mixamorig:{side}Shoulder",
             v(cx + sign * sx, sh_y), v(cx + sign * (sx + arm * 0.45), sh_y - h * 0.06)),
            (f"mixamorig:{side}ForeArm", f"mixamorig:{side}Arm",
             v(cx + sign * (sx + arm * 0.45), sh_y - h * 0.06),
             v(cx + sign * (sx + arm * 0.82), sh_y - h * 0.12)),
            (f"mixamorig:{side}Hand", f"mixamorig:{side}ForeArm",
             v(cx + sign * (sx + arm * 0.82), sh_y - h * 0.12),
             v(cx + sign * (sx + arm), sh_y - h * 0.15)),
        ]

    for side, foot_x in (("Left", m["leftFootX"]), ("Right", m["rightFootX"])):
        hip_x = cx + (foot_x - cx) * 0.55
        bones += [
            (f"mixamorig:{side}UpLeg", "mixamorig:Hips",
             v(hip_x, hip_y), v(foot_x, m["low"] + h * 0.28)),
            (f"mixamorig:{side}Leg", f"mixamorig:{side}UpLeg",
             v(foot_x, m["low"] + h * 0.28), v(foot_x, m["low"] + h * 0.06)),
            (f"mixamorig:{side}Foot", f"mixamorig:{side}Leg",
             v(foot_x, m["low"] + h * 0.06), (foot_x, m["low"] + h * 0.01, foot_z)),
        ]
    return bones


def chain_bones(names, parent, axis, split, min_len):
    """A bone per segment along a measured axis, head to tail, no gaps.

    `split` is the arclength fraction where the chain bends (knee, elbow). A
    zero-length bone is silently dropped by Blender and would then be missing
    from the rig without anything failing, so every segment is given a floor.
    """
    cuts = [0.0, split, 1.0] if len(names) == 2 else [0.0, split, (1.0 + split) / 2, 1.0]
    points = [fit.sample_at(axis, u) for u in cuts]
    out, previous = [], parent
    for name, head, tail in zip(names, points, points[1:]):
        length = math.dist(head, tail)
        if length < min_len:
            direction = [(tail[i] - head[i]) / (length or 1.0) for i in range(3)]
            if length == 0:
                direction = [0.0, -1.0, 0.0]
            tail = tuple(head[i] + direction[i] * min_len for i in range(3))
        full = f"mixamorig:{name}"
        out.append((full, previous, tuple(head), tuple(tail)))
        previous = full
    return out


def humanoid_bones(m):
    """The canonical skeleton, with every limb the measurement found replacing
    its templated stand-in. Per limb, not all or nothing: a measured leg and a
    templated arm is a better rig than reverting both."""
    bones = canonical_bones(m)
    chains = m.get("chains") or {}
    min_len = m["height"] * 0.01
    replaced = []

    for side in ("Left", "Right"):
        leg = chains.get(f"{side}Leg")
        if leg and len(leg["axis"]) >= 3:
            names = [f"{side}UpLeg", f"{side}Leg", f"{side}Foot"]
            split, deviation = leg["bend"]
            if deviation < min_len:
                split = 0.55
            bones = [b for b in bones if b[0] not in {f"mixamorig:{n}" for n in names}]
            bones += chain_bones(names, "mixamorig:Hips", leg["axis"], split, min_len)
            replaced.append(names[0])

        arm = chains.get(f"{side}Arm")
        if arm and len(arm["axis"]) >= 3:
            names = [f"{side}Arm", f"{side}ForeArm", f"{side}Hand"]
            split, deviation = arm["bend"]
            if deviation < min_len:
                split = 0.5
            bones = [b for b in bones if b[0] not in {f"mixamorig:{n}" for n in names}]
            bones += chain_bones(names, f"mixamorig:{side}Shoulder", arm["axis"], split, min_len)
            replaced.append(names[0])

    m["measuredLimbs"] = replaced
    # Parents must exist before their children, and the replacements were
    # appended at the end.
    order = {name: i for i, (name, *_rest) in enumerate(bones)}

    def depth(bone):
        steps, parent = 0, bone[1]
        while parent is not None and steps < 10:
            steps += 1
            parent = next((b[1] for b in bones if b[0] == parent), None)
        return steps

    return sorted(bones, key=lambda b: (depth(b), order[b[0]]))


def build_armature(bones, name="Armature"):
    armature = bpy.data.armatures.new(name)
    rig = bpy.data.objects.new(name, armature)
    bpy.context.collection.objects.link(rig)
    bpy.context.view_layer.objects.active = rig
    bpy.ops.object.mode_set(mode="EDIT")

    made = {}
    for bone_name, parent, head, tail in bones:
        bone = armature.edit_bones.new(bone_name)
        # Back to Blender's Z-up frame: (x, y, z)_gltf -> (x, -z, y)_blender.
        bone.head = (head[0], -head[2], head[1])
        bone.tail = (tail[0], -tail[2], tail[1])
        # Roll decides what a rotation about the bone's local X actually does.
        # Left to itself, a leg fitted pointing down AND outwards gets a local
        # frame tilted with it, and a walk cycle authored as an X swing throws
        # the leg sideways instead of forward. Aligning every bone's roll to
        # world forward gives the whole skeleton one sagittal plane to swing in,
        # whatever pose it was measured from.
        bone.align_roll(mathutils.Vector((0.0, -1.0, 0.0)))
        if parent:
            bone.parent = made[parent]
            bone.use_connect = False
        made[bone_name] = bone

    bpy.ops.object.mode_set(mode="OBJECT")
    return rig


# ---- animation --------------------------------------------------------------

# Clips are authored here rather than imported. Every free humanoid clip library
# is either licence-encumbered for redistribution (Mixamo) or authored for a
# different skeleton, and a retarget needs the same rest pose to look right.
# Bones we control, in a rest pose we chose, take a curve directly.

def _euler(degrees):
    return mathutils.Euler([math.radians(d) for d in degrees], "XYZ").to_quaternion()


IDENTITY = mathutils.Quaternion((1.0, 0.0, 0.0, 0.0))

# How far a single bone may be rotated to reach the neutral stance. A stride is
# tens of degrees; anything past these caps means the fit went wrong, and
# straightening that far would tear the mesh rather than pose it.
CORRECTION_CAPS = {"UpLeg": 55.0, "Leg": 55.0, "Foot": 40.0, "Arm": 70.0,
                   "ForeArm": 70.0, "Hand": 40.0, "Shoulder": 30.0,
                   "Spine": 25.0, "Spine1": 25.0, "Neck": 25.0, "Head": 25.0,
                   "Hips": 20.0}


def rest_rotations(rig):
    """Each bone's rest orientation relative to its parent, as a quaternion."""
    out = {}
    for bone in rig.data.bones:
        local = bone.matrix_local.to_3x3()
        if bone.parent:
            local = bone.parent.matrix_local.to_3x3().inverted() @ local
        out[bone.name] = local.to_quaternion()
    return out


def bind_corrections(fitted, canonical_list):
    """Per bone, the rotation that takes the fitted rest pose to the canonical one.

    Blender composes a pose bone as rest_local @ basis, and the clips write the
    basis. Pre-multiplying this correction into every key gives
    rest_fit @ (C @ B) = rest_canonical @ B, so an authored key lands in the pose
    the canonical skeleton would have held. An identity key is then a neutral
    stance, whatever pose the mesh was captured in: the stride is cancelled
    before the walk's swing is added rather than stacking on top of it.

    The canonical rest set is read from a real armature built by the same code
    with the same roll convention, because deriving it by hand is how the two
    frames silently drift apart.
    """
    probe = build_armature(canonical_list, name="__canonical")
    canonical = rest_rotations(probe)
    bpy.data.objects.remove(probe, do_unlink=True)

    fit_rest = rest_rotations(fitted)
    corrections, report = {}, {}
    for name, rest in fit_rest.items():
        if name not in canonical:
            continue
        turn = rest.inverted() @ canonical[name]
        angle = math.degrees(min(abs(turn.angle), 2 * math.pi - abs(turn.angle)))
        cap = next((v for k, v in CORRECTION_CAPS.items() if name.endswith(k)), 45.0)
        if angle > cap:
            # Clamp rather than drop: half a correction still beats none, and a
            # dropped one leaves that limb posed in the stride it was bound in.
            turn = IDENTITY.slerp(turn, cap / angle)
            angle = cap
        corrections[name] = turn
        if angle >= 1.0:
            report[name] = round(angle, 1)
    return corrections, report


def keyframe(rig, frame, pose, corrections=None):
    corrections = corrections or {}
    for bone_name, angles in pose.items():
        bone = rig.pose.bones.get(bone_name)
        if not bone:
            continue
        bone.rotation_mode = "QUATERNION"
        # Pre-multiply. The other order applies the straightening inside the
        # already-swung frame and puts the stride back into the swing axis.
        bone.rotation_quaternion = corrections.get(bone_name, IDENTITY) @ _euler(angles)
        bone.keyframe_insert("rotation_quaternion", frame=frame)


def swing(amount, phase):
    return amount * math.sin(phase * 2 * math.pi)


def clip_poses(kind, t):
    """The pose at phase t in [0,1) for one clip, as degrees per bone."""
    if kind == "idle":
        breathe = swing(2.5, t)
        return {
            "mixamorig:Spine": (breathe * 0.4, 0, 0),
            "mixamorig:Spine1": (breathe * 0.3, 0, 0),
            "mixamorig:Head": (breathe * 0.5, swing(3.0, t) * 0.5, 0),
            "mixamorig:LeftArm": (0, 0, swing(2.0, t)),
            "mixamorig:RightArm": (0, 0, -swing(2.0, t)),
        }
    if kind in ("walk", "run"):
        big = 1.0 if kind == "walk" else 1.9
        lift = 8 if kind == "walk" else 22
        return {
            "mixamorig:Hips": (swing(3 * big, t * 2), 0, 0),
            "mixamorig:Spine": (2 * big, 0, swing(2, t)),
            "mixamorig:LeftUpLeg": (swing(-25 * big, t), 0, 0),
            "mixamorig:LeftLeg": (max(0.0, swing(35 * big, t + 0.25)), 0, 0),
            "mixamorig:LeftFoot": (swing(lift, t + 0.5) * 0.4, 0, 0),
            "mixamorig:RightUpLeg": (swing(-25 * big, t + 0.5), 0, 0),
            "mixamorig:RightLeg": (max(0.0, swing(35 * big, t + 0.75)), 0, 0),
            "mixamorig:RightFoot": (swing(lift, t) * 0.4, 0, 0),
            # Arms oppose the leg on the same side. A negative X rotation swings
            # a limb forward on this skeleton, so the arm takes the same phase as
            # its own leg with the opposite sign, not a half-cycle offset: that
            # offset cancels against the leg's negative amplitude and marches the
            # character with its arm and leg swinging together.
            "mixamorig:LeftArm": (swing(22 * big, t), 0, 4),
            "mixamorig:LeftForeArm": (-abs(swing(18 * big, t)), 0, 0),
            "mixamorig:RightArm": (swing(22 * big, t + 0.5), 0, -4),
            "mixamorig:RightForeArm": (-abs(swing(18 * big, t + 0.5)), 0, 0),
        }
    if kind == "jump":
        # crouch, extend, tuck, land: a single shot, not a cycle
        crouch = max(0.0, math.sin(t * math.pi)) if t < 0.25 else 0.0
        air = math.sin(min(1.0, max(0.0, (t - 0.25) / 0.5)) * math.pi)
        land = max(0.0, (t - 0.85) / 0.15) if t > 0.85 else 0.0
        bend = crouch * 45 + land * 30
        return {
            "mixamorig:Hips": (bend * 0.2, 0, 0),
            "mixamorig:LeftUpLeg": (-bend + air * 20, 0, 0),
            "mixamorig:LeftLeg": (bend * 1.6 + air * 25, 0, 0),
            "mixamorig:RightUpLeg": (-bend + air * 20, 0, 0),
            "mixamorig:RightLeg": (bend * 1.6 + air * 25, 0, 0),
            "mixamorig:LeftArm": (-air * 60 + bend * 0.6, 0, 6),
            "mixamorig:RightArm": (-air * 60 + bend * 0.6, 0, -6),
            "mixamorig:Spine": (bend * 0.25, 0, 0),
        }
    raise RuntimeError(f"unknown clip {kind}")


CLIPS = {
    "idle": {"frames": 60, "loop": True},
    "walk": {"frames": 32, "loop": True},
    "run": {"frames": 24, "loop": True},
    "jump": {"frames": 40, "loop": False},
}


def author_clips(rig, wanted, corrections=None, fps=24):
    """One glTF animation per clip, each an action stashed on its own NLA track."""
    corrections = corrections or {}
    made = []
    rig.animation_data_create()
    for kind in wanted:
        spec = CLIPS[kind]
        action = bpy.data.actions.new(kind)
        action.use_fake_user = True
        rig.animation_data.action = action
        # Every bone is keyed with its correction, not only the ones this clip
        # poses. Idle moves five bones; without this the legs would keep the
        # stride they were bound in while the corrected arms straightened.
        for bone in rig.pose.bones:
            bone.rotation_mode = "QUATERNION"
            bone.rotation_quaternion = corrections.get(bone.name, IDENTITY)
            bone.keyframe_insert("rotation_quaternion", frame=1)

        frames = spec["frames"]
        for frame in range(frames + 1):
            t = (frame % frames) / frames if spec["loop"] else frame / frames
            keyframe(rig, frame + 1, clip_poses(kind, t), corrections)

        track = rig.animation_data.nla_tracks.new()
        track.name = kind
        track.strips.new(kind, 1, action)
        rig.animation_data.action = None
        made.append({"name": kind, "frames": frames + 1,
                     "durationSeconds": round((frames) / fps, 3),
                     "loop": spec["loop"]})
    return made


def prop_clips(node, wanted, fps=24):
    """Props are animated on the node, not on bones: TRS channels only."""
    made = []
    node.animation_data_create()
    base = node.location.copy()
    for kind in wanted:
        spec = {"spin": 48, "bob": 48}[kind]
        action = bpy.data.actions.new(kind)
        action.use_fake_user = True
        node.animation_data.action = action
        for frame in range(spec + 1):
            t = frame / spec
            if kind == "spin":
                node.rotation_mode = "QUATERNION"
                node.rotation_quaternion = mathutils.Euler(
                    (0, 0, t * 2 * math.pi), "XYZ").to_quaternion()
                node.keyframe_insert("rotation_quaternion", frame=frame + 1)
            else:
                node.location = base + mathutils.Vector((0, 0, math.sin(t * 2 * math.pi) * 0.05))
                node.keyframe_insert("location", frame=frame + 1)
        track = node.animation_data.nla_tracks.new()
        track.name = kind
        track.strips.new(kind, 1, action)
        node.animation_data.action = None
        made.append({"name": kind, "frames": spec + 1,
                     "durationSeconds": round(spec / fps, 3), "loop": True})
    node.location = base
    return made


# ---- the job ----------------------------------------------------------------


def clean_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)


def import_glb(path):
    bpy.ops.import_scene.gltf(filepath=path)
    meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    if not meshes:
        raise RuntimeError("the GLB holds no mesh")
    for obj in bpy.context.scene.objects:
        obj.select_set(obj.type == "MESH")
    bpy.context.view_layer.objects.active = meshes[0]
    if len(meshes) > 1:
        bpy.ops.object.join()
    mesh = bpy.context.view_layer.objects.active
    bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)
    return mesh


def prepare(mesh, merge_distance=0.0005):
    """Bone heat solves a Laplacian over the surface and refuses to solve it on
    duplicate vertices or degenerate faces, which is what a marching-cubes mesh
    arrives full of. This is the difference between a rig and an abort."""
    bpy.context.view_layer.objects.active = mesh
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.mesh.remove_doubles(threshold=merge_distance)
    bpy.ops.mesh.delete_loose()
    bpy.ops.mesh.dissolve_degenerate()
    bpy.ops.mesh.normals_make_consistent(inside=False)
    bpy.ops.object.mode_set(mode="OBJECT")
    return len(mesh.data.vertices), len(mesh.data.polygons)


def bind(mesh, rig):
    bpy.ops.object.select_all(action="DESELECT")
    mesh.select_set(True)
    rig.select_set(True)
    bpy.context.view_layer.objects.active = rig
    try:
        bpy.ops.object.parent_set(type="ARMATURE_AUTO")
    except RuntimeError as exc:
        raise RuntimeError(f"bone heat weighting failed: {exc}")
    if not any(m.type == "ARMATURE" for m in mesh.modifiers):
        raise RuntimeError("bone heat weighting produced no armature modifier")
    weighted = sum(1 for v in mesh.data.vertices if v.groups)
    if weighted < len(mesh.data.vertices) * 0.5:
        raise RuntimeError(
            f"only {weighted} of {len(mesh.data.vertices)} vertices took a weight")
    return weighted


def run(job):
    clean_scene()
    mesh = import_glb(job["glb"])
    verts, faces = prepare(mesh)

    result = {"subject": job["subject"], "vertices": verts, "faces": faces,
              "blender": bpy.app.version_string}

    if job["subject"] == "humanoid":
        marks = measure(mesh)
        bones = humanoid_bones(marks)
        rig = build_armature(bones)
        result["weightedVertices"] = bind(mesh, rig)
        result["bones"] = [name for name, *_ in bones]
        corrections, turned = bind_corrections(rig, canonical_bones(marks))
        result["animations"] = author_clips(rig, job["animations"], corrections)
        result["measuredLimbs"] = marks.get("measuredLimbs", [])
        result["bindCorrection"] = turned
        result["measurements"] = {k: round(float(v), 5) for k, v in marks.items()
                                  if isinstance(v, (int, float))}
        result["pose"] = marks.get("pose", [])
    else:
        mesh.name = job.get("nodeName", "prop")
        result["animations"] = prop_clips(mesh, job["animations"])
        result["bones"] = []
        if job.get("socket"):
            socket = bpy.data.objects.new(job["socket"], None)
            socket.empty_display_size = 0.05
            bpy.context.collection.objects.link(socket)
            socket.parent = mesh
            socket.location = (0, 0, max(v.co.z for v in mesh.data.vertices))
            result["socket"] = job["socket"]

    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.export_scene.gltf(
        filepath=job["out"],
        export_format="GLB",
        export_skins=True,
        export_animations=True,
        export_animation_mode="NLA_TRACKS",
        export_rest_position_armature=True,
        export_yup=True,
        export_apply=False,
    )
    return result


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    if not argv:
        raise SystemExit("usage: blender --background --python blender_rig.py -- <job.json>")
    job = json.loads(open(argv[0], encoding="utf-8").read())
    try:
        payload = {"ok": True, "result": run(job)}
    except Exception as exc:                                  # reported, never raised
        payload = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    with open(job["outJson"], "w", encoding="utf-8") as fh:
        json.dump(payload, fh)
    if not payload["ok"]:
        sys.stderr.write(payload["error"] + "\n")


main()
