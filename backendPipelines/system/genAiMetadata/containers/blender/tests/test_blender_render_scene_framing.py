# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The camera framing and view table of `renderScene.py`.

The framing helpers are pure functions over tuples and `math` -- no `bpy`, no `mathutils` -- precisely
so they can be executed here (through `ast`, because the module imports `bpy` at the top). What they pin:
eight views (six orthographic + two perspective) in a fixed order, deterministic file names per view, and
a frame computed over the union of EVERY object's bounds. The legacy script framed only the last selected
mesh and scaled by its Y extent, so a multi-part model showed one part and a flat model divided by zero.
"""

import math

import pytest

from test_blender_render_scene_dispatch import exec_named_nodes

_FRAMING_NAMES = [
    "RENDER_FILE_PREFIX", "PERSPECTIVE_LENS_MM", "SENSOR_WIDTH_MM", "FRAMING_PADDING",
    "ORTHO_DISTANCE_FACTOR", "VIEW_DIRECTIONS", "VIEW_ORDER", "select_views", "render_file_name",
    "normalized", "combine_bounds", "camera_placement",
]


@pytest.fixture
def scene():
    return exec_named_nodes(_FRAMING_NAMES, {"math": math})


def _cube_corners(center, half=0.5):
    cx, cy, cz = center
    return [(cx + sx * half, cy + sy * half, cz + sz * half) for sx in (-1, 1) for sy in (-1, 1) for sz in (-1, 1)]


@pytest.mark.unit
def test_eight_views_six_orthographic_two_perspective(scene):
    views = scene["VIEW_DIRECTIONS"]
    assert len(views) == 8
    perspective = [name for name, (_direction, is_persp) in views.items() if is_persp]
    assert sorted(perspective) == ["perspective_back", "perspective_front"]
    assert scene["VIEW_ORDER"] == tuple(views)
    assert scene["VIEW_ORDER"][0] == "perspective_front", "RENDER_VIEWS=1 must yield the most informative view"
    assert len(set(scene["VIEW_ORDER"])) == 8


@pytest.mark.unit
def test_orthographic_directions_are_the_six_axis_views(scene):
    views = scene["VIEW_DIRECTIONS"]
    assert views["front"][0] == (0.0, -1.0, 0.0)
    assert views["back"][0] == (0.0, 1.0, 0.0)
    assert views["right"][0] == (1.0, 0.0, 0.0)
    assert views["left"][0] == (-1.0, 0.0, 0.0)
    assert views["top"][0] == (0.0, 0.0, 1.0)
    assert views["bottom"][0] == (0.0, 0.0, -1.0)


@pytest.mark.unit
def test_select_views_takes_the_first_n_in_order_and_clamps(scene):
    order = scene["VIEW_ORDER"]
    assert scene["select_views"](8) == order
    assert scene["select_views"](2) == order[:2]
    assert scene["select_views"](0) == order[:1]
    assert scene["select_views"](-5) == order[:1]
    assert scene["select_views"](99) == order
    assert scene["select_views"]("3") == order[:3]


@pytest.mark.unit
def test_render_file_names_are_deterministic_per_view(scene):
    assert scene["RENDER_FILE_PREFIX"] == "render_"
    assert scene["render_file_name"]("perspective_front") == "render_perspective_front.png"
    names = [scene["render_file_name"](v) for v in scene["VIEW_ORDER"]]
    assert len(set(names)) == 8


@pytest.mark.unit
def test_bounds_are_the_union_over_all_objects_not_the_last_one(scene):
    corners = _cube_corners((0.0, 0.0, 0.0)) + _cube_corners((5.0, 0.0, 0.0))
    center, size = scene["combine_bounds"](corners)
    assert center == (2.5, 0.0, 0.0)
    assert size == (6.0, 1.0, 1.0)
    last_only_center, _ = scene["combine_bounds"](_cube_corners((5.0, 0.0, 0.0)))
    assert last_only_center == (5.0, 0.0, 0.0), "control: the last object alone frames differently"


@pytest.mark.unit
def test_bounds_with_no_corners_raise(scene):
    with pytest.raises(ValueError):
        scene["combine_bounds"]([])


@pytest.mark.unit
def test_orthographic_placement_covers_the_two_visible_axes(scene):
    center, size = (1.0, 2.0, 3.0), (2.0, 1.0, 3.0)
    placement = scene["camera_placement"](center, size, (0.0, -1.0, 0.0), False)
    # Looking along Y: the visible extents are X (2) and Z (3); the larger one, padded, is the ortho scale.
    assert placement["ortho_scale"] == pytest.approx(3.0 * scene["FRAMING_PADDING"])
    assert placement["distance"] == pytest.approx(3.0 * scene["ORTHO_DISTANCE_FACTOR"])
    assert placement["location"] == pytest.approx((1.0, 2.0 - placement["distance"], 3.0))
    assert placement["clip_end"] > placement["distance"] + math.sqrt(2.0 ** 2 + 1.0 + 3.0 ** 2)


@pytest.mark.unit
def test_perspective_placement_fits_the_bounding_sphere(scene):
    center, size = (0.0, 0.0, 0.0), (2.0, 2.0, 2.0)
    direction = (1.0, -1.0, 0.75)
    placement = scene["camera_placement"](center, size, direction, True)
    radius = 0.5 * math.sqrt(12.0)
    half_fov = math.atan(scene["SENSOR_WIDTH_MM"] / (2.0 * scene["PERSPECTIVE_LENS_MM"]))
    assert placement["ortho_scale"] is None
    # The sphere of radius r sits inside the cone when distance * sin(half_fov) >= r (times the padding).
    assert placement["distance"] * math.sin(half_fov) == pytest.approx(radius * scene["FRAMING_PADDING"])
    unit = scene["normalized"](direction)
    assert placement["location"] == pytest.approx(tuple(unit[i] * placement["distance"] for i in range(3)))
    assert placement["clip_end"] > placement["distance"] + 2.0 * radius


@pytest.mark.unit
def test_flat_and_tiny_objects_still_get_a_positive_frame(scene):
    flat = scene["camera_placement"]((0.0, 0.0, 0.0), (2.0, 2.0, 0.0), (0.0, 0.0, 1.0), False)
    assert flat["ortho_scale"] > 0 and flat["distance"] > 0
    point = scene["camera_placement"]((0.0, 0.0, 0.0), (0.0, 0.0, 0.0), (1.0, -1.0, 0.75), True)
    assert point["distance"] > 0 and point["clip_end"] > point["distance"]
    with pytest.raises(ValueError):
        scene["normalized"]((0.0, 0.0, 0.0))
