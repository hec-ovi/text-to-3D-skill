"""Where the limbs actually are, measured from the mesh. No bpy, no mathutils.

The rig used to assume a figure standing with its feet together and its arms
straight out: legs were placed by the X of the two lowest islands, arms by the
widest X in the upper half. Neither survives contact with a real generation. An
A-posed character has its arms hanging out and *down*, so the widest X is at the
hands and an arm bone drawn horizontally from the shoulder misses the arm
entirely. A character caught mid-stride has one foot forward in depth, which an
X-only measurement cannot see at all.

So: slice the mesh into horizontal bands, find the connected islands in each
band, follow each island from one band to the next, and take the chain of island
centres as the limb's axis. That is a limb's medial axis, near enough, and it
follows the limb wherever the pose put it.

Kept free of Blender imports on purpose: this is the part worth testing without
a GPU, a display or a 400 MB dependency.
"""

import math


def bucket(verts, low, step, slices):
    """One pass: every vertex into its horizontal band, as (x, z)."""
    bands = [[] for _ in range(slices)]
    for x, y, z in verts:
        index = int((y - low) / step) if step else 0
        bands[min(slices - 1, max(0, index))].append((x, z))
    return bands


def islands(points, cell, link=2):
    """Connected components of `points` in the XZ plane, by grid adjacency.

    Two points belong together when their cells are within `link` cells of each
    other, which keeps this O(n) and makes the threshold a distance rather than
    a count. The alternative, a gap in sorted X, cannot tell a leg in front of
    another leg from one leg.

    `link` matters more than it looks. A horizontal slice through a limb is a
    *ring* of surface vertices, not a disc, and on a decimated mesh those sit
    centimetres apart. Linking only touching cells shatters one limb into a
    dozen islands; linking across two closes the ring while still leaving two
    legs a hand's width apart as two islands.
    """
    if not points:
        return []
    cells = {}
    for index, (x, z) in enumerate(points):
        cells.setdefault((int(math.floor(x / cell)), int(math.floor(z / cell))), []).append(index)

    parent = {key: key for key in cells}

    def find(key):
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    span = range(-link, link + 1)
    for (i, j) in cells:
        for di in span:
            for dj in span:
                neighbour = (i + di, j + dj)
                if neighbour in cells:
                    union((i, j), neighbour)

    groups = {}
    for key, members in cells.items():
        groups.setdefault(find(key), []).extend(members)

    out = []
    for members in groups.values():
        xs = [points[i][0] for i in members]
        zs = [points[i][1] for i in members]
        out.append({
            "count": len(members),
            "centre": (sum(xs) / len(xs), sum(zs) / len(zs)),
            "extent": (min(xs), max(xs), min(zs), max(zs)),
            "radius": max(max(xs) - min(xs), max(zs) - min(zs)) / 2 or cell,
        })
    out.sort(key=lambda island: -island["count"])
    return out


def band_profile(verts, slices=48):
    """(low, high, step, [(y, [island, ...]), ...]) bottom band first."""
    ys = [v[1] for v in verts]
    low, high = min(ys), max(ys)
    height = high - low
    if height <= 0:
        raise ValueError("the mesh has no vertical extent")
    step = height / slices
    cell = height * 0.015
    profile = []
    for index, points in enumerate(bucket(verts, low, step, slices)):
        profile.append((low + (index + 0.5) * step, islands(points, cell)))
    return low, high, step, profile


def follow(profile, start_band, direction, seed_centre, cell, floor_y=None):
    """Follow one island from `start_band` in `direction` (+1 up, -1 down).

    Returns the chain of (x, y, z) centres. At each step it takes the island
    whose centre is nearest the last one, and stops when the nearest is further
    than a limb could plausibly have moved between two bands. That is what keeps
    a leg from jumping to the other leg, and an arm from jumping into the hip.
    """
    axis = []
    centre = seed_centre
    index = start_band
    while 0 <= index < len(profile):
        y, found = profile[index]
        if not found:
            break
        near = min(found, key=lambda island: math.dist(island["centre"], centre))
        reach = max(cell * 2.5, near["radius"] * 1.6)
        if math.dist(near["centre"], centre) > reach:
            break
        centre = near["centre"]
        axis.append((centre[0], y, centre[1]))
        index += direction
    if floor_y is not None and axis and direction < 0:
        # A foot's last band sits half a band above the floor; extend to it so the
        # ankle-to-toe bone is not left hanging in the air.
        axis.append((axis[-1][0], floor_y, axis[-1][2]))
    return axis


def resample(axis, count):
    """`count` points evenly along the polyline, endpoints included."""
    if len(axis) < 2:
        return [axis[0]] * count if axis else []
    lengths = [0.0]
    for a, b in zip(axis, axis[1:]):
        lengths.append(lengths[-1] + math.dist(a, b))
    total = lengths[-1]
    if total <= 0:
        return [axis[0]] * count
    out = []
    for i in range(count):
        target = total * i / (count - 1)
        j = max(1, min(len(axis) - 1,
                       next((k for k in range(1, len(lengths)) if lengths[k] >= target),
                            len(axis) - 1)))
        span = lengths[j] - lengths[j - 1] or 1.0
        t = (target - lengths[j - 1]) / span
        a, b = axis[j - 1], axis[j]
        out.append(tuple(a[k] + (b[k] - a[k]) * t for k in range(3)))
    return out


def sample_at(axis, u):
    """The point at arclength fraction `u` along a polyline."""
    return resample(axis, 2)[0] if u <= 0 else resample(axis, 2)[1] if u >= 1 else \
        resample(axis, 21)[int(round(u * 20))]


def bend(axis):
    """(fraction, deviation) of the point furthest off the chord, in [0.3, 0.7].

    A bent knee bows forward and this finds it. A straight leg deviates by
    nothing, and the caller falls back to the middle rather than trusting noise.
    """
    if len(axis) < 3:
        return 0.5, 0.0
    dense = resample(axis, 21)
    a, b = dense[0], dense[-1]
    span = math.dist(a, b) or 1.0
    best, best_dev = 0.5, 0.0
    for i in range(6, 15):
        p = dense[i]
        # perpendicular distance from p to the line a-b
        ab = [b[k] - a[k] for k in range(3)]
        ap = [p[k] - a[k] for k in range(3)]
        t = sum(ab[k] * ap[k] for k in range(3)) / (span * span)
        proj = [a[k] + ab[k] * t for k in range(3)]
        dev = math.dist(p, proj)
        if dev > best_dev:
            best, best_dev = i / 20.0, dev
    return best, best_dev


def limbs(verts, slices=48):
    """Measure a humanoid. Returns landmarks plus a measured axis per limb.

    Every value is in the same Y-up metres the caller passes in. `chains` is
    empty for a limb that could not be measured, and the caller falls back to
    the templated placement for that limb alone.
    """
    low, high, step, profile = band_profile(verts, slices)
    height = high - low
    cell = height * 0.015
    xs = [v[0] for v in verts]
    zs = [v[2] for v in verts]
    centre_x = (min(xs) + max(xs)) / 2
    centre_z = (min(zs) + max(zs)) / 2

    # The crotch: scanning up from the floor, the last band that still holds
    # exactly two separate islands before they fuse into one body. Exactly two,
    # not two or more: an A-posed figure's arms show up as extra islands well
    # below the shoulder, and counting those as legs puts the hips in the chest.
    crotch_band = None
    for index, (y, found) in enumerate(profile):
        if y > low + height * 0.6:
            break
        big = [island for island in found if island["count"] >= 8]
        if len(big) == 2:
            crotch_band = index
        elif crotch_band is not None and len(big) == 1 and y > low + height * 0.3:
            break
    crotch = profile[crotch_band][0] if crotch_band is not None else low + height * 0.5

    # The shoulder line: scanning down from the head, the first band where the
    # body has become three islands, which is a torso with an arm either side.
    # Not the widest band: on an A-pose the widest band is at the hands, halfway
    # down the body, and a shoulder placed there puts the whole arm chain in the
    # character's stomach.
    shoulder_band, shoulder_y = None, low + height * 0.82
    for index in range(len(profile) - 1, -1, -1):
        y, found = profile[index]
        if y < low + height * 0.55:
            break
        if len([i for i in found if i["count"] >= 4]) >= 3:
            shoulder_band, shoulder_y = index, y
            break

    chains = {}

    # Legs: seed from the two biggest islands one band under the crotch and walk
    # down. Left and right are decided at the hip, where a stride has not yet
    # displaced the limb sideways.
    if crotch_band is not None and crotch_band > 0:
        seeds = [i for i in profile[max(0, crotch_band - 1)][1] if i["count"] >= 6][:2]
        if len(seeds) == 2:
            seeds.sort(key=lambda island: island["centre"][0])
            for side, seed in zip(("Left", "Right"), seeds):
                axis = follow(profile, max(0, crotch_band - 1), -1, seed["centre"], cell,
                              floor_y=low)
                if len(axis) >= 3:
                    axis = [(seed["centre"][0], crotch + height * 0.06, seed["centre"][1])] + axis
                    chains[f"{side}Leg"] = {"axis": axis, "bend": bend(axis)}

    # Arms: seed from the outermost islands at the shoulder band and walk down.
    # An A-posed arm leaves the torso there; an arm pressed to the body does not
    # separate at all, and that limb simply has no chain.
    if shoulder_band is not None:
        found = [i for i in profile[shoulder_band][1] if i["count"] >= 4]
        if len(found) >= 3:
            found.sort(key=lambda island: island["centre"][0])
            for side, seed in zip(("Left", "Right"), (found[0], found[-1])):
                axis = follow(profile, shoulder_band, -1, seed["centre"], cell)
                if len(axis) >= 3:
                    chains[f"{side}Arm"] = {"axis": axis, "bend": bend(axis)}
        else:
            # One island at the shoulder: the arms are still attached to the
            # torso in cross-section. Fall back to the side lobes of that island.
            for side, sign in (("Left", -1.0), ("Right", 1.0)):
                lobe = _side_lobe(profile, shoulder_band, centre_x, sign, cell)
                if len(lobe) >= 3:
                    chains[f"{side}Arm"] = {"axis": lobe, "bend": bend(lobe)}

    return {
        "low": low, "high": high, "height": height,
        "centreX": centre_x, "centreZ": centre_z,
        "crotch": crotch, "shoulderY": shoulder_y,
        "chains": chains,
    }


def _side_lobe(profile, shoulder_band, centre_x, sign, cell):
    """The outer third of each band below the shoulder, as an axis.

    A crude arm when the arm never separates from the torso: the centroid of
    whatever sits furthest out on that side. Better than a horizontal guess,
    worse than a tracked island, and the caller can tell the difference by the
    deviation the axis reports.
    """
    axis = []
    for index in range(shoulder_band, -1, -1):
        y, found = profile[index]
        outer = []
        for island in found:
            x0, x1, z0, z1 = island["extent"]
            edge = x1 if sign > 0 else x0
            if abs(edge - centre_x) > cell * 3:
                outer.append((edge, (z0 + z1) / 2))
        if not outer:
            break
        pick = max(outer, key=lambda p: sign * p[0])
        axis.append((pick[0] - sign * cell, y, pick[1]))
    return axis
