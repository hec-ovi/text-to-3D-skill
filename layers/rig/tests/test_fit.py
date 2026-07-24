"""Tests for the limb measurement, with no Blender in sight.

Every case here is a synthetic body built out of point clouds, posed the way a
real generation poses one: legs apart, a leg forward in depth, arms hanging at
45 degrees. The point of keeping this module free of bpy is that these run in
milliseconds on any machine.
"""

import math
import os
import sys

import pytest

LAYER = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(LAYER, "src"))

import fit  # noqa: E402


# ---- synthetic bodies -------------------------------------------------------


def capsule(a, b, radius, count=400):
    """A cloud of points along the segment a-b, roughly `radius` thick."""
    points = []
    for i in range(count):
        t = i / (count - 1)
        centre = [a[k] + (b[k] - a[k]) * t for k in range(3)]
        for j in range(16):
            angle = 2 * math.pi * j / 16
            points.append((centre[0] + radius * math.cos(angle),
                           centre[1],
                           centre[2] + radius * math.sin(angle)))
    return points


def body(left_foot=(-0.15, 0.0, 0.0), right_foot=(0.15, 0.0, 0.0),
         left_hand=(-0.55, 0.95, 0.0), right_hand=(0.55, 0.95, 0.0)):
    """A 1.8 m figure: torso, head, two legs to the given feet, two arms."""
    points = []
    points += capsule((0, 0.9, 0), (0, 1.45, 0), 0.16, 300)      # torso
    points += capsule((0, 1.45, 0), (0, 1.75, 0), 0.10, 120)     # neck and head
    points += capsule((-0.09, 0.9, 0), left_foot, 0.07, 300)     # left leg
    points += capsule((0.09, 0.9, 0), right_foot, 0.07, 300)     # right leg
    points += capsule((-0.18, 1.40, 0), left_hand, 0.05, 250)    # left arm
    points += capsule((0.18, 1.40, 0), right_hand, 0.05, 250)    # right arm
    return points


def axis_end(chain):
    return chain["axis"][-1]


# ---- the pieces -------------------------------------------------------------


def test_islands_separate_two_legs_that_sorted_x_would_merge():
    """One leg in front of the other overlaps in X and is a single cluster to
    any X-only measurement. In the plane they are two."""
    points = [(-0.1 + 0.01 * i, 0.0) for i in range(10)]          # left leg, behind
    points += [(-0.08 + 0.01 * i, 0.40) for i in range(10)]       # right leg, forward
    found = fit.islands(points, cell=0.05)
    assert len(found) == 2


def test_islands_close_a_ring_of_surface_vertices():
    """A slice through a limb is a ring, not a disc. If the ring does not close
    into one island the limb shatters and nothing downstream finds it."""
    ring = [(0.07 * math.cos(2 * math.pi * i / 12), 0.07 * math.sin(2 * math.pi * i / 12))
            for i in range(12)]
    assert len(fit.islands(ring, cell=0.026)) == 1


def test_resample_keeps_the_ends_and_spaces_the_middle():
    axis = [(0, 0, 0), (0, 1, 0), (0, 2, 0)]
    out = fit.resample(axis, 5)
    assert out[0] == (0, 0, 0)
    assert out[-1] == (0, 2, 0)
    assert out[2][1] == pytest.approx(1.0, abs=1e-6)


def test_bend_finds_a_knee_and_ignores_a_straight_limb():
    straight = [(0, 1.0, 0), (0, 0.5, 0), (0, 0.0, 0)]
    _, deviation = fit.bend(straight)
    assert deviation < 1e-6

    bent = [(0, 1.0, 0), (0, 0.5, 0.25), (0, 0.0, 0)]
    where, deviation = fit.bend(bent)
    assert deviation > 0.1
    assert 0.35 < where < 0.65


# ---- whole bodies -----------------------------------------------------------


def test_a_plain_standing_figure_measures_four_limbs():
    marks = fit.limbs(body())
    assert set(marks["chains"]) == {"LeftLeg", "RightLeg", "LeftArm", "RightArm"}
    assert marks["height"] == pytest.approx(1.75, abs=0.05)
    # The crotch is where the legs fuse into the torso, near 0.9 m here.
    assert 0.75 < marks["crotch"] < 1.05
    assert 1.2 < marks["shoulderY"] < 1.6


def test_a_leg_that_swung_forward_is_followed_into_depth():
    """The failure that started this: an X-only measurement cannot see a stride,
    so both feet land at the same place and the rig binds a splayed pose."""
    marks = fit.limbs(body(left_foot=(-0.12, 0.0, 0.45), right_foot=(0.12, 0.0, -0.35)))
    left, right = marks["chains"]["LeftLeg"], marks["chains"]["RightLeg"]
    assert axis_end(left)[2] > 0.25            # forward
    assert axis_end(right)[2] < -0.15          # back
    assert axis_end(left)[2] - axis_end(right)[2] > 0.5


def test_arms_hanging_down_are_followed_down_not_sideways():
    """An A-posed character is widest at the hands, well below the shoulders. A
    horizontal arm bone from the shoulder to that width misses the arm."""
    marks = fit.limbs(body(left_hand=(-0.42, 0.55, 0.0), right_hand=(0.42, 0.55, 0.0)))
    left = marks["chains"]["LeftArm"]
    top, bottom = left["axis"][0], axis_end(left)
    assert top[1] - bottom[1] > 0.4            # the axis descends
    assert abs(bottom[0]) > abs(top[0])        # and moves outwards


def test_a_figure_with_no_measurable_arms_still_measures_its_legs():
    """Arms pressed against the body do not separate. That limb gets no chain
    and the caller falls back to the template for it alone."""
    points = []
    points += capsule((0, 0.9, 0), (0, 1.75, 0), 0.22, 400)      # one fat torso
    points += capsule((-0.09, 0.9, 0), (-0.15, 0.0, 0.0), 0.07, 300)
    points += capsule((0.09, 0.9, 0), (0.15, 0.0, 0.0), 0.07, 300)
    marks = fit.limbs(points)
    assert "LeftLeg" in marks["chains"] and "RightLeg" in marks["chains"]


def test_a_mesh_with_no_height_is_refused_rather_than_divided_by_zero():
    flat = [(x / 100.0, 0.0, 0.0) for x in range(50)]
    with pytest.raises(ValueError):
        fit.limbs(flat)


def test_measuring_is_linear_enough_to_run_on_a_real_mesh():
    """A generated character is tens of thousands of vertices; the fitter runs
    once per rig and must not be quadratic in them."""
    import time
    dense = body() * 30                                   # ~90k points
    started = time.monotonic()
    fit.limbs(dense)
    assert time.monotonic() - started < 5.0
