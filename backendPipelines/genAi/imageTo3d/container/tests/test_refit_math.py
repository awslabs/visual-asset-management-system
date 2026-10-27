# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The refit arithmetic without Blender: the outline comparison, the outline fit, the relief from a depth
estimate and the metaball sizing. The Blender side (`bl_refit.py`) is exercised in
`test_blender_tools_live.py`."""

import os
import sys

import numpy as np
import pytest

_TOOLS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "blenderagent_tools")
sys.path.insert(0, _TOOLS_DIR)
import photo_projection  # noqa: E402
import refit_math  # noqa: E402

pytestmark = pytest.mark.unit


def _edges(triangles):
    pairs = np.sort(np.vstack([triangles[:, [0, 1]], triangles[:, [1, 2]], triangles[:, [2, 0]]]), axis=1)
    return np.unique(pairs, axis=0)


def _cylinder_along_y(radius=0.5, length=0.6, segments=96, rings=9):
    """A closed cylinder whose axis is Y, so the camera sees a disc; Z runs 0..1."""
    angles = np.linspace(0.0, 2.0 * np.pi, segments, endpoint=False)
    ys = np.linspace(-length / 2, length / 2, rings)
    side = np.array([[radius * np.cos(a), y, 0.5 + radius * np.sin(a)] for y in ys for a in angles])
    vertices = np.vstack([side, [[0.0, -length / 2, 0.5], [0.0, length / 2, 0.5]]])
    triangles = []
    for i in range(rings - 1):
        for j in range(segments):
            a, b = i * segments + j, i * segments + (j + 1) % segments
            triangles += [[a, b + segments, b], [a, a + segments, b + segments]]
    front, back, last = len(side), len(side) + 1, (rings - 1) * segments
    for j in range(segments):
        triangles.append([front, j, (j + 1) % segments])
        triangles.append([back, last + (j + 1) % segments, last + j])
    triangles = np.array(triangles)
    return vertices, triangles, _edges(triangles)


def _square(size=128, margin=24):
    mask = np.zeros((size, size), dtype=bool)
    mask[margin:size - margin, margin:size - margin] = True
    return mask


class TestTheOutlineComparison:
    def test_iou_of_identical_disjoint_and_half_overlapping_masks(self):
        a = np.zeros((10, 10), dtype=bool)
        a[:, :5] = True
        assert refit_math.iou(a, a) == 1.0
        assert refit_math.iou(a, ~a) == 0.0
        b = np.zeros((10, 10), dtype=bool)
        b[:5, :] = True
        assert refit_math.iou(a, b) == pytest.approx(25 / 75)

    def test_the_distance_transform_matches_brute_force(self):
        rng = np.random.default_rng(3)
        features = rng.random((23, 31)) > 0.97
        features[0, 0] = True
        dist, near_r, near_c = refit_math.edt(features)
        fr, fc = np.nonzero(features)
        for r in range(features.shape[0]):
            for c in range(features.shape[1]):
                brute = np.sqrt(((fr - r) ** 2 + (fc - c) ** 2).min())
                assert dist[r, c] == pytest.approx(brute)
                assert np.hypot(near_r[r, c] - r, near_c[r, c] - c) == pytest.approx(brute)
                assert features[near_r[r, c], near_c[r, c]]

    def test_the_signed_field_is_negative_inside_positive_outside_and_zero_on_the_outline(self):
        mask = _square(64, 16)
        sdf, _, _ = refit_math.outline_field(mask)
        assert sdf[32, 32] == pytest.approx(-15.0), "row 47 is the nearest edge"
        assert sdf[32, 4] == pytest.approx(12.0)
        assert sdf[16, 32] == 0.0 and sdf[47, 32] == 0.0

    def test_a_mask_resamples_to_the_working_grid_and_reports_its_factor(self):
        mask = np.zeros((1000, 500), dtype=bool)
        mask[100:900, 100:400] = True
        work, k = refit_math.resample_mask(mask, long_side=512)
        assert work.shape == (512, 256) and k == pytest.approx(0.512)
        assert work.sum() == pytest.approx(mask.sum() * k * k, rel=0.02)

    def test_the_rasteriser_covers_exactly_the_pixel_centres_inside_a_square(self):
        u = np.array([2.0, 6.0, 6.0, 2.0])
        v = np.array([2.0, 2.0, 6.0, 6.0])
        depth = np.ones(4)
        zbuf = refit_math.rasterise_depth(u, v, depth, np.array([[0, 1, 2], [0, 2, 3]]), 8, 8)
        covered = np.isfinite(zbuf)
        expected = np.zeros((8, 8), dtype=bool)
        expected[2:6, 2:6] = True
        assert (covered == expected).all()

    def test_the_nearest_triangle_wins_each_pixel(self):
        u = np.array([0.0, 8.0, 0.0, 0.0, 8.0, 8.0])
        v = np.array([0.0, 0.0, 8.0, 0.0, 0.0, 8.0])
        depth = np.array([5.0, 5.0, 5.0, 1.0, 1.0, 1.0])
        zbuf = refit_math.rasterise_depth(u, v, depth, np.array([[0, 1, 2], [3, 4, 5]]), 8, 8)
        assert zbuf[0, 7] == pytest.approx(1.0) and zbuf[7, 0] == pytest.approx(5.0)
        assert zbuf[1, 2] == pytest.approx(1.0), "where both cover, the nearer one is in the buffer"

    def test_a_large_and_a_tiny_triangle_land_in_the_same_buffer(self):
        u = np.array([0.0, 300.0, 0.0, 310.2, 310.8, 310.5])
        v = np.array([0.0, 0.0, 300.0, 310.2, 310.2, 310.9])
        zbuf = refit_math.rasterise_depth(u, v, np.ones(6), np.array([[0, 1, 2], [3, 4, 5]]), 320, 320)
        assert np.isfinite(zbuf[10, 10]) and np.isfinite(zbuf[310, 310])

    def test_the_frame_lays_the_box_of_a_correctly_proportioned_mesh_onto_the_subject(self):
        vertices, triangles, _ = _cylinder_along_y()
        target = _square()
        frame = refit_math.fit_frame(target, vertices)
        u, v = refit_math.project(vertices, frame)
        assert u.min() == pytest.approx(24.0) and u.max() == pytest.approx(104.0)
        assert v.min() == pytest.approx(24.0) and v.max() == pytest.approx(104.0)
        rendered = refit_math.front_mask(vertices, triangles, frame)
        assert refit_math.iou(rendered, target) == pytest.approx(np.pi / 4, abs=0.02)


class TestTheOutlineFit:
    def test_a_disc_fitted_to_a_square_gains_iou_on_every_accepted_iteration(self):
        vertices, triangles, _ = _cylinder_along_y()
        target = _square()
        fitted, record = refit_math.fit_outline(vertices, triangles, target, height=1.0, max_shift=0.3)
        history = record["iou"]
        assert len(history) >= 3, record
        assert all(b >= a for a, b in zip(history, history[1:])), history
        assert history[-1] > history[0] + 0.03, history
        assert np.array_equal(fitted[:, 1], vertices[:, 1]), "depth is never moved"
        assert record["maxGradient"] <= 0.5

    def test_an_iteration_that_would_lower_the_iou_is_refused_and_leaves_the_mesh_alone(self):
        vertices, triangles, _ = _cylinder_along_y()
        target = _square()

        def squash(rendered, target_sdf, sigma, step_cap):
            """Pull every pixel 40% of the way to the middle row: the disc becomes a flat ellipse."""
            rows = np.arange(rendered.shape[0], dtype=float)[:, None] + 0.5
            return np.zeros(rendered.shape), np.broadcast_to(-(rows - rendered.shape[0] / 2) * 0.4, rendered.shape)

        fitted, record = refit_math.fit_outline(vertices, triangles, target, height=1.0, max_shift=0.3,
                                                warp=squash)
        assert record["stop"] == "reverted" and record["refused"] < record["iou"][0]
        assert np.array_equal(fitted, vertices)

    def test_no_vertex_moves_further_than_the_limit(self):
        vertices, triangles, _ = _cylinder_along_y()
        target = _square()
        fitted, record = refit_math.fit_outline(vertices, triangles, target, height=1.0, max_shift=0.01)
        assert np.linalg.norm(fitted - vertices, axis=1).max() <= 0.01 + 1e-9
        assert record["maxShift"] <= 0.01 + 1e-9

    def test_the_fit_is_the_same_at_any_scale(self):
        """A reconstruction in metres and one in millimetres are the same shape: every length is a fraction
        of the height, so the fit takes the same steps."""
        vertices, triangles, _ = _cylinder_along_y()
        target = _square()
        _, small = refit_math.fit_outline(vertices, triangles, target, height=1.0, max_shift=0.3)
        _, large = refit_math.fit_outline(vertices * 1000.0, triangles, target, height=1000.0, max_shift=0.3)
        assert small["iou"] == large["iou"], (small, large)
        assert small["maxShift"] == pytest.approx(large["maxShift"], abs=1e-4)

    def test_the_warp_pushes_a_too_small_outline_out_and_leaves_far_pixels_alone(self):
        rows, cols = np.mgrid[0:128, 0:128]
        r = np.hypot(rows + 0.5 - 64, cols + 0.5 - 64)
        rendered, target = r < 30, r < 40
        du, dv = refit_math.warp_field(rendered, refit_math.outline_field(target)[0], sigma=4.0, step_cap=3.0)
        assert du[64, 93] > 1.0 and du[64, 34] < -1.0, "the right edge moves right, the left edge left"
        assert abs(du[64, 64]) < 1e-3 and abs(du[2, 2]) < 1e-3, "the centre and the far corner stay"


class TestTheRelief:
    def _plane(self, n=101):
        """A unit square facing -Y, the camera's way; Z runs 0..1."""
        xs = np.linspace(0.0, 1.0, n)
        grid_x, grid_z = np.meshgrid(xs, xs)
        vertices = np.column_stack([grid_x.ravel(), np.zeros(n * n), grid_z.ravel()])
        triangles = []
        for i in range(n - 1):
            for j in range(n - 1):
                a, b, c, d = i * n + j, i * n + j + 1, (i + 1) * n + j, (i + 1) * n + j + 1
                triangles += [[a, b, d], [a, d, c]]
        return vertices, np.array(triangles), _edges(np.array(triangles))

    def _bump(self, size=200, amplitude=0.02, width=3.0):
        rows, cols = np.mgrid[0:size, 0:size]
        r2 = (rows - size / 2 + 0.5) ** 2 + (cols - size / 2 + 0.5) ** 2
        return 2.0 - amplitude * np.exp(-r2 / (2 * width ** 2))

    def test_a_bump_in_the_depth_estimate_survives_the_high_pass_and_its_surroundings_do_not(self):
        depth = self._bump() + np.linspace(0.0, 0.5, 200)[None, :]
        detail = refit_math.depth_detail(depth, np.ones_like(depth, dtype=bool), sigma=6.0)
        assert detail[100, 100] < -0.012, "the bump comes through"
        assert abs(detail[100, 30]) < 1e-3, "a smooth slope is not relief"

    def test_the_bump_moves_the_vertices_it_lands_on_towards_the_camera_and_nothing_at_the_outline(self):
        vertices, triangles, _ = self._plane()
        normals = photo_projection.vertex_normals(vertices, triangles)
        if normals[:, 1].mean() > 0:
            triangles = triangles[:, ::-1]
            normals = photo_projection.vertex_normals(vertices, triangles)
        work = np.ones((200, 200), dtype=bool)
        frame = refit_math.fit_frame(work, vertices)
        inside = np.maximum(-refit_math.outline_field(work)[0], 0.0)
        depth = self._bump()
        offsets, summary = refit_math.relief_offsets(
            vertices, normals, triangles, frame, 1.0, depth, np.ones_like(depth, dtype=bool), inside,
            metric_width=1.0, cap=0.05)
        centre = np.argmin(np.hypot(vertices[:, 0] - 0.5, vertices[:, 2] - 0.5))
        assert offsets[centre] > 0.01, "the bump nearer the camera raises the surface towards it"
        corner = np.argmin(np.hypot(vertices[:, 0], vertices[:, 2]))
        assert offsets[corner] == 0.0, "the outline is pinned"
        assert summary["vertices"] > 0 and np.abs(offsets).max() <= 0.05

    def test_a_surface_seen_edge_on_takes_no_relief(self):
        vertices, triangles, _ = self._plane()
        normals = np.tile([0.0, -0.1, 0.995], (len(vertices), 1))
        work = np.ones((200, 200), dtype=bool)
        frame = refit_math.fit_frame(work, vertices)
        inside = np.maximum(-refit_math.outline_field(work)[0], 0.0)
        depth = self._bump()
        offsets, _ = refit_math.relief_offsets(vertices, normals, triangles, frame, 1.0, depth,
                                               np.ones_like(depth, dtype=bool), inside, 1.0, 0.05)
        assert not offsets.any()


class TestTheMetaballSizing:
    def test_the_threshold_solve_lands_within_two_percent_of_the_volume(self):
        threshold, volume, how, tried = refit_math.solve_threshold(lambda t: 10.0 / t, target=4.0)
        assert how == "solved" and abs(volume - 4.0) <= 0.08 and threshold == pytest.approx(2.5, rel=0.03)

    def test_a_volume_out_of_range_is_reported_rather_than_forced(self):
        assert refit_math.solve_threshold(lambda t: 1.0 / t, target=100.0)[2] == "below"
        assert refit_math.solve_threshold(lambda t: 1000.0 / t, target=1.0)[2] == "above"

    def test_the_stride_keeps_the_element_count_under_the_cap(self):
        assert refit_math.choose_stride(50_000, 60_000) == 1
        assert refit_math.choose_stride(4_600_000, 60_000) == 5

    def test_parity_along_a_line_marks_the_inside_of_two_shells(self):
        inside = refit_math.inside_along_line([7.0, 1.0, 5.0, 3.0], np.array([0.0, 2.0, 4.0, 6.0, 8.0]))
        assert inside.tolist() == [False, True, False, True, False]

    def test_a_unit_cube_encloses_unit_volume(self):
        vertices = np.array([[x, y, z] for x in (0, 1) for y in (0, 1) for z in (0, 1)], dtype=float)
        faces = np.array([[0, 2, 3], [0, 3, 1], [4, 5, 7], [4, 7, 6], [0, 1, 5], [0, 5, 4],
                          [2, 6, 7], [2, 7, 3], [0, 4, 6], [0, 6, 2], [1, 3, 7], [1, 7, 5]])
        assert abs(refit_math.mesh_volume(vertices, faces)) == pytest.approx(1.0)


class TestTheColourCarryAndTheOverlay:
    def test_a_point_on_a_corner_takes_that_corner_alone(self):
        corners = np.array([[[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]] * 3)
        points = np.array([[1.0, 0.0, 0.0], [1 / 3, 1 / 3, 0.0], [2.0, 2.0, 0.0]])
        weights = refit_math.barycentric(points, corners)
        assert weights[0] == pytest.approx([0.0, 1.0, 0.0])
        assert weights[1] == pytest.approx([1 / 3, 1 / 3, 1 / 3])
        assert (weights[2] >= 0).all() and weights[2].sum() == pytest.approx(1.0), "outside is clipped in"

    def test_a_degenerate_triangle_shares_its_weight_evenly(self):
        corners = np.array([[[0.0, 0.0, 0.0], [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]]])
        assert refit_math.barycentric(np.zeros((1, 3)), corners)[0] == pytest.approx([1 / 3, 1 / 3, 1 / 3])

    def test_the_overlay_colours_agreement_and_each_kind_of_miss(self):
        rendered = np.zeros((8, 8), dtype=bool)
        target = np.zeros((8, 8), dtype=bool)
        rendered[2:6, 1:5] = True
        target[2:6, 3:7] = True
        image = refit_math.overlay_image(rendered, target)
        assert image[4, 1] == pytest.approx([0.2, 0.4, 1.0]), "mesh only is blue"
        assert image[0, 0] == pytest.approx([1.0, 1.0, 1.0]), "neither is white"
        assert image[2, 3] == pytest.approx([0.0, 0.0, 0.0]), "the photograph's outline is black"
        assert image[3, 4] == pytest.approx([0.75, 0.75, 0.75]), "inside both is grey"
        assert image[3, 5] == pytest.approx([1.0, 0.55, 0.1]), "photograph only is orange"


class TestTheViewSearch:
    def _box(self):
        corners = np.array([[x, y, z] for x in (-0.5, 0.5) for y in (-0.2, 0.2) for z in (-0.3, 0.3)])
        faces = np.array([[0, 2, 3], [0, 3, 1], [4, 5, 7], [4, 7, 6], [0, 1, 5], [0, 5, 4],
                          [2, 6, 7], [2, 7, 3], [0, 4, 6], [0, 6, 2], [1, 3, 7], [1, 7, 5]])
        return corners, faces

    def test_a_photograph_from_above_and_round_is_matched_at_its_view(self):
        box, faces = self._box()
        seen = box @ refit_math.view_rotation(45, 30).T
        canvas = np.zeros((300, 300), dtype=bool)
        canvas[50:250, 50:250] = True
        photograph = refit_math.front_mask(seen, faces, refit_math.fit_frame(canvas, seen))
        azimuth, elevation, score = refit_math.choose_view(box, faces, photograph)
        assert elevation == 30 and azimuth in (45, 225) and score > 0.95

    def test_a_level_photograph_keeps_the_mesh_level(self):
        box, faces = self._box()
        canvas = np.zeros((300, 300), dtype=bool)
        canvas[50:250, 50:250] = True
        photograph = refit_math.front_mask(box, faces, refit_math.fit_frame(canvas, box))
        azimuth, elevation, score = refit_math.choose_view(box, faces, photograph)
        assert (azimuth, elevation) in ((0, 0), (180, 0)) and score > 0.95

    def test_the_view_rotation_turns_then_tips(self):
        assert refit_math.view_rotation(90, 0) @ np.array([1.0, 0.0, 0.0]) == pytest.approx([0.0, 1.0, 0.0])
        tipped = refit_math.view_rotation(0, 90) @ np.array([0.0, 0.0, 1.0])
        assert tipped == pytest.approx([0.0, -1.0, 0.0]), "the top tips towards a camera on -Y"
