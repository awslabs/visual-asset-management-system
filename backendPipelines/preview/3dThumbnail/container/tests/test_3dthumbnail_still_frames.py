#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Still frames for the analysis model: N views at evenly spaced orbit angles, RGB, no alpha step.

The thumbnail generators return RGBA frames whose alpha comes from the depth buffer; a still meant as a
vision-model input keeps the renderer's neutral background instead, so the depth read and the scipy
erosion are skipped. Camera framing is the existing one, so the two paths frame a model identically."""

import math
from types import SimpleNamespace
from unittest.mock import patch

import pytest

np = pytest.importorskip("numpy", reason="camera framing is numpy arithmetic over the point array")

from preview_pipeline import renderer  # noqa: E402


class _FakeCamera:
    def __init__(self):
        self.position = None
        self.focal_point = None
        self.up = None
        self.clipping_range = None


class _FakePlotter:
    instances = []

    def __init__(self, off_screen=False, window_size=(800, 600)):
        self.off_screen = off_screen
        self.window_size = tuple(window_size)
        self.camera = _FakeCamera()
        self.rendered_positions = []
        self.meshes = []
        self.lights = 0
        self.closed = False
        self.depth_reads = 0
        _FakePlotter.instances.append(self)

    def set_background(self, colour):
        self.background = colour

    def add_mesh(self, data, **kwargs):
        self.meshes.append(kwargs)

    def add_light(self, light):
        self.lights += 1

    def render(self):
        self.rendered_positions.append(tuple(float(v) for v in self.camera.position))

    def screenshot(self, return_img=True):
        width, height = self.window_size
        return np.full((height, width, 3), 128, dtype=np.uint8)

    def get_image_depth(self):
        self.depth_reads += 1
        width, height = self.window_size
        return np.ones((height, width), dtype=np.float32)

    def close(self):
        self.closed = True


def _box_points():
    return np.array([[x, y, z] for x in (0.0, 2.0) for y in (0.0, 1.0) for z in (0.0, 4.0)])


def _mesh(points=None):
    points = _box_points() if points is None else points
    return SimpleNamespace(points=points, n_points=len(points), n_cells=12, point_data={})


def _cloud():
    points = _box_points()
    return SimpleNamespace(points=points, n_points=len(points), n_cells=0, point_data={})


def _yaw_degrees(position, focal_point):
    return math.degrees(math.atan2(position[2] - focal_point[2], position[0] - focal_point[0])) % 360.0


@pytest.fixture
def fake_plotter():
    _FakePlotter.instances = []
    with patch.object(renderer.pv, "Plotter", _FakePlotter):
        yield _FakePlotter


@pytest.mark.unit
class TestGenerateStillFrames:
    def test_defaults_are_four_square_views(self):
        assert renderer.DEFAULT_STILL_VIEWS == 4
        assert renderer.MAX_STILL_VIEWS == 8
        assert renderer.DEFAULT_STILL_RESOLUTION == (768, 768)

    def test_renders_n_rgb_frames_at_the_requested_resolution(self, fake_plotter):
        frames = renderer.generate_still_frames(_mesh(), n_views=4)
        assert len(frames) == 4
        for frame in frames:
            assert frame.shape == (768, 768, 3)
            assert frame.dtype == np.uint8
        plotter = fake_plotter.instances[-1]
        assert plotter.off_screen is True
        assert plotter.window_size == (768, 768)
        assert plotter.closed is True
        assert plotter.depth_reads == 0, "a still is not alpha-masked, so the depth buffer is never read"

    def test_views_are_evenly_spaced_around_the_orbit_from_the_start_angle(self, fake_plotter):
        renderer.generate_still_frames(_mesh(), n_views=4, start_angle_deg=45.0)
        plotter = fake_plotter.instances[-1]
        focal = np.array(plotter.camera.focal_point)
        yaws = [round(_yaw_degrees(p, focal)) for p in plotter.rendered_positions]
        assert yaws == [45, 135, 225, 315]
        elevations = {round(p[1], 6) for p in plotter.rendered_positions}
        assert len(elevations) == 1, "every view shares the same elevation above the orbit plane"

    def test_eight_views_and_the_clamp(self, fake_plotter):
        assert len(renderer.generate_still_frames(_mesh(), n_views=8)) == 8
        assert len(renderer.generate_still_frames(_mesh(), n_views=50)) == renderer.MAX_STILL_VIEWS
        assert len(renderer.generate_still_frames(_mesh(), n_views=0)) == 1

    def test_point_clouds_and_meshes_take_their_own_styling(self, fake_plotter):
        renderer.generate_still_frames(_cloud(), n_views=1)
        cloud_plotter = fake_plotter.instances[-1]
        assert cloud_plotter.meshes[0].get("render_points_as_spheres") is True
        renderer.generate_still_frames(_mesh(), n_views=1)
        mesh_plotter = fake_plotter.instances[-1]
        assert mesh_plotter.meshes[0].get("smooth_shading") is True
        assert mesh_plotter.lights == 1

    def test_full_bounds_framing_is_passed_through(self, fake_plotter):
        with patch.object(renderer, "_compute_camera_framing",
                          wraps=renderer._compute_camera_framing) as framing:
            renderer.generate_still_frames(_mesh(), n_views=1, use_full_bounds=True)
        assert framing.call_args.kwargs["use_full_bounds"] is True
        assert framing.call_args.kwargs["resolution"] == (768, 768)


@pytest.mark.unit
class TestThumbnailGeneratorsKeepTheirContract:
    def test_static_frame_is_still_alpha_masked(self, fake_plotter):
        """Control: the thumbnail path keeps its RGBA output and its depth-based transparency."""
        def _alpha(plotter, img):
            return np.dstack([img, np.full(img.shape[:2], 255, dtype=np.uint8)])

        with patch.object(renderer, "_add_alpha_from_depth", side_effect=_alpha) as alpha:
            frame = renderer.generate_static_frame(_mesh())
        assert frame.shape == (600, 800, 4)
        assert alpha.call_count == 1

    def test_rotating_frames_still_orbit_the_full_circle(self, fake_plotter):
        with patch.object(renderer, "_add_alpha_from_depth", side_effect=lambda p, img: img):
            frames = renderer.generate_rotating_frames(_mesh(), n_frames=6)
        plotter = fake_plotter.instances[-1]
        focal = np.array(plotter.camera.focal_point)
        yaws = [round(_yaw_degrees(p, focal)) for p in plotter.rendered_positions]
        assert len(frames) == 6
        assert yaws == [0, 60, 120, 180, 240, 300]
