#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""CPU thumbnails for 3D Gaussian splats (#469), and PLY routing by content.

* A ``.ply`` is routed by its header: 3DGS properties -> splats, faces -> mesh, vertices only -> point
  cloud (with the point budget). A 3DGS PLY used to go to the mesh handler as an uncoloured blob.
* Splat colour is the degree-0 SH term, opacity ``sigmoid(opacity)``, size ``exp(max(scale))``; faint
  splats are dropped and the rest sampled by opacity to the splat budget.
* ``.spz``/``.sog``/``.ksplat``/``.lcc``/``.compressed.ply`` go through ``splat-transform``, which is
  only given an allowlisted local file inside the work directory, with an argument list, no shell
  and a timeout -- never a URL and never an ``.mjs`` script (it fetches the one and runs the other).

Render and conversion tests need the real pyvista/VTK and the image's Node.js + splat-transform.
"""

import os
import subprocess
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from conftest import requires_real
from preview_pipeline import core
from preview_pipeline.format_handlers import ply_header, splat_handler

C0 = 0.28209479177387814
_PROPS = ["x", "y", "z", "nx", "ny", "nz", "f_dc_0", "f_dc_1", "f_dc_2", "opacity",
          "scale_0", "scale_1", "scale_2", "rot_0", "rot_1", "rot_2", "rot_3"]


def write_splat_ply(path, n=4000, floaters=400, seed=3):
    """A red-top / blue-bottom unit sphere of opaque splats plus faint far floaters (3DGS layout)."""
    rng = np.random.default_rng(seed)
    v = rng.normal(size=(n, 3))
    v /= np.linalg.norm(v, axis=1, keepdims=True)
    pts = np.vstack([v, rng.normal(size=(floaters, 3)) * 12.0]).astype(np.float32)
    rgb = np.vstack([np.where(v[:, 1:2] > 0, [[0.9, 0.2, 0.15]], [[0.15, 0.3, 0.9]]),
                     np.full((floaters, 3), 0.5)])
    f_dc = ((rgb - 0.5) / C0).astype(np.float32)
    opacity = np.concatenate([np.full(n, 3.0), np.full(floaters, -2.5)]).astype(np.float32)
    scale = np.log(np.concatenate([np.full((n, 3), 0.05), np.full((floaters, 3), 0.4)])).astype(np.float32)
    rot = np.tile(np.array([1, 0, 0, 0], np.float32), (n + floaters, 1))
    data = np.hstack([pts, np.zeros_like(pts), f_dc, opacity[:, None], scale, rot]).astype("<f4")
    with open(path, "wb") as handle:
        handle.write(f"ply\nformat binary_little_endian 1.0\nelement vertex {len(data)}\n".encode())
        handle.write("".join(f"property float {p}\n" for p in _PROPS).encode() + b"end_header\n")
        handle.write(data.tobytes())
    return str(path)


def _fake_polydata():
    """A stand-in for pv.PolyData that records points and point_data, for runs outside the image."""
    def make(points):
        return MagicMock(points=np.asarray(points), point_data={}, n_points=len(points))
    return make


@pytest.mark.unit
class TestPlyRouting:
    def test_the_header_decides_splat_mesh_or_points(self, tmp_path):
        assert ply_header.ply_kind(write_splat_ply(tmp_path / "s.ply", n=10, floaters=0)) == "splat"
        mesh = tmp_path / "m.ply"
        mesh.write_text("ply\nformat ascii 1.0\nelement vertex 3\nproperty float x\nproperty float y\n"
                        "property float z\nelement face 1\nproperty list uchar int vertex_indices\n"
                        "end_header\n0 0 0\n1 0 0\n0 1 0\n3 0 1 2\n")
        assert ply_header.ply_kind(str(mesh)) == "mesh"
        points = tmp_path / "p.ply"
        points.write_text("ply\nformat ascii 1.0\nelement vertex 2\nproperty float x\nproperty float y\n"
                          "property float z\nelement face 0\nproperty list uchar int vertex_indices\n"
                          "end_header\n0 0 0\n1 1 1\n")
        assert ply_header.ply_kind(str(points)) == "points"
        compressed = tmp_path / "c.ply"
        compressed.write_bytes(b"ply\nformat binary_little_endian 1.0\nelement chunk 1\nproperty float min_x\n"
                               b"element vertex 1\nproperty uint packed_position\nend_header\n")
        assert ply_header.ply_kind(str(compressed)) == "compressed_splat"
        assert ply_header.ply_kind(str(tmp_path / "missing.ply")) == "mesh"

    @pytest.mark.parametrize("fmt", ["ascii", "binary"])
    def test_a_vertex_only_ply_is_a_budgeted_point_cloud(self, tmp_path, fmt):
        from preview_pipeline.format_handlers import pointcloud_handler

        xyz = np.column_stack([np.arange(5000), np.zeros(5000), np.ones(5000)]).astype(np.float32)
        colours = np.tile(np.array([[255, 128, 0]], dtype=np.uint8), (5000, 1))
        header = ("ply\nformat {} 1.0\nelement vertex 5000\nproperty float x\nproperty float y\n"
                  "property float z\nproperty uchar red\nproperty uchar green\nproperty uchar blue\nend_header\n")
        path = tmp_path / f"cloud_{fmt}.ply"
        if fmt == "ascii":
            body = "".join(f"{x:g} {y:g} {z:g} 255 128 0\n" for x, y, z in xyz)
            path.write_text(header.format("ascii") + body)
        else:
            rows = np.zeros(5000, dtype=[("x", "<f4"), ("y", "<f4"), ("z", "<f4"),
                                         ("red", "u1"), ("green", "u1"), ("blue", "u1")])
            rows["x"], rows["y"], rows["z"] = xyz.T
            rows["red"], rows["green"], rows["blue"] = colours.T
            path.write_bytes(header.format("binary_little_endian").encode() + rows.tobytes())
        with patch.object(pointcloud_handler.pv, "PolyData", _fake_polydata()):
            cloud = core._load_file(str(path), ".ply", budgets=MagicMock(points=1000, splats=10))
        assert len(cloud.points) == 1000
        assert tuple(cloud.point_data["RGB"][0]) == (255, 128, 0)
        assert np.allclose(cloud.points[:, 2], 1.0)


@pytest.mark.unit
class TestSplatReading:
    def test_colour_opacity_size_floaters_and_orientation(self, tmp_path):
        path = write_splat_ply(tmp_path / "ball.ply")
        with patch.object(splat_handler.pv, "PolyData", _fake_polydata()):
            cloud = splat_handler.load_ply(path, max_splats=10_000)
        assert len(cloud.points) == 4000, "the faint floaters are dropped"
        radius = np.asarray(cloud.point_data["splat_radius"])
        assert np.allclose(radius, 0.05, rtol=1e-4)
        rgb = np.asarray(cloud.point_data["RGB"]).astype(int)
        # 180 degrees about Z: the red top (+Y in the file) is at -Y before... and +Y after the turn.
        top = rgb[cloud.points[:, 1] < 0]
        bottom = rgb[cloud.points[:, 1] > 0]
        assert np.allclose(top.mean(axis=0), (230, 51, 38), atol=2), "file +Y (red) maps to -Y"
        assert np.allclose(bottom.mean(axis=0), (38, 77, 230), atol=2)

    def test_the_sample_respects_the_budget_and_prefers_opaque_splats(self):
        alpha = np.concatenate([np.full(10_000, 0.95), np.full(10_000, 0.25), np.full(10_000, 0.1)])
        chosen = splat_handler.opacity_weighted_sample(alpha, 6_000)
        assert len(chosen) == 6_000 and np.all(np.diff(chosen) > 0)
        assert np.all(alpha[chosen] >= splat_handler.OPACITY_THRESHOLD)
        opaque = np.sum(chosen < 10_000)
        assert opaque > 3 * (len(chosen) - opaque), "opaque splats dominate the sample"

    def test_a_dot_splat_file_is_decoded(self, tmp_path):
        records = np.zeros(3, dtype=splat_handler.SPLAT_RECORD)
        records["position"] = [[1, 2, 3], [4, 5, 6], [7, 8, 9]]
        records["scale"] = [[0.1, 0.2, 0.3]] * 3
        records["rgba"] = [[255, 0, 0, 255], [0, 255, 0, 200], [0, 0, 255, 10]]
        path = tmp_path / "s.splat"
        path.write_bytes(records.tobytes())
        with patch.object(splat_handler.pv, "PolyData", _fake_polydata()):
            cloud = splat_handler.load_splat(str(path))
        assert cloud.points.tolist() == [[-1, -2, 3], [-4, -5, 6]], "transparent splat dropped, turned about Z"
        assert np.asarray(cloud.point_data["RGB"]).tolist() == [[255, 0, 0], [0, 255, 0]]
        assert np.allclose(cloud.point_data["splat_radius"], 0.3)
        (tmp_path / "bad.splat").write_bytes(b"x" * 33)
        with pytest.raises(ValueError, match="multiple of 32"):
            splat_handler.load_splat(str(tmp_path / "bad.splat"))


@pytest.mark.unit
class TestSplatTransformIsConfined:
    @pytest.mark.parametrize("name", ["scene.mjs", "scene.spz.mjs", "scene.ply", "scene.txt", "scene"])
    def test_only_allowlisted_extensions_are_converted(self, tmp_path, name):
        (tmp_path / name).write_bytes(b"x")
        with pytest.raises(splat_handler.SplatConversionError):
            splat_handler.validate_conversion_input(str(tmp_path / name), root=str(tmp_path))

    @pytest.mark.parametrize("value", ["https://example.invalid/a.spz", "http://h/a.sog", "s3://b/a.spz",
                                       "a\x00.spz", ""])
    def test_urls_and_malformed_paths_are_refused(self, value):
        with pytest.raises(splat_handler.SplatConversionError):
            splat_handler.validate_conversion_input(value)

    def test_a_file_outside_the_work_directory_or_not_a_file_is_refused(self, tmp_path):
        work = tmp_path / "work"
        work.mkdir()
        outside = tmp_path / "other.spz"
        outside.write_bytes(b"x")
        with pytest.raises(splat_handler.SplatConversionError, match="outside"):
            splat_handler.validate_conversion_input(str(outside), root=str(work))
        os.symlink(outside, work / "link.spz")
        with pytest.raises(splat_handler.SplatConversionError, match="outside"):
            splat_handler.validate_conversion_input(str(work / "link.spz"), root=str(work))
        (work / "dir.spz").mkdir()
        with pytest.raises(splat_handler.SplatConversionError, match="regular file"):
            splat_handler.validate_conversion_input(str(work / "dir.spz"), root=str(work))
        (work / "ok.compressed.ply").write_bytes(b"x")
        assert splat_handler.validate_conversion_input(str(work / "ok.compressed.ply"), root=str(work))

    def test_the_cli_runs_with_an_argument_list_no_shell_a_timeout_and_cpu_only(self, tmp_path):
        source = tmp_path / "scene.spz"
        source.write_bytes(b"x")

        def fake_run(command, **kwargs):
            open(command[-1], "wb").write(b"ply")
            return subprocess.CompletedProcess(command, 0, b"", b"")

        with patch.object(splat_handler.subprocess, "run", MagicMock(side_effect=fake_run)) as run:
            output = splat_handler.convert_to_ply(str(source), str(tmp_path), root=str(tmp_path), timeout=77)
        command = run.call_args.args[0]
        kwargs = run.call_args.kwargs
        assert isinstance(command, list) and kwargs.get("shell", False) is False
        assert kwargs["timeout"] == 77 and kwargs["cwd"] == str(tmp_path)
        assert command[command.index("--gpu") + 1] == "cpu"
        assert command[-2] == os.path.realpath(str(source)) and output == command[-1]
        assert set(kwargs["env"]) <= {"PATH", "HOME", "NODE_OPTIONS"} and kwargs["env"]["NODE_OPTIONS"] == ""

    def test_a_timeout_or_failure_is_reported(self, tmp_path):
        source = tmp_path / "scene.sog"
        source.write_bytes(b"x")
        with patch.object(splat_handler.subprocess, "run",
                          MagicMock(side_effect=subprocess.TimeoutExpired("node", 5))):
            with pytest.raises(splat_handler.SplatConversionError, match="did not finish"):
                splat_handler.convert_to_ply(str(source), str(tmp_path), root=str(tmp_path))
        failed = subprocess.CompletedProcess([], 1, b"", b"bad input")
        with patch.object(splat_handler.subprocess, "run", MagicMock(return_value=failed)):
            with pytest.raises(splat_handler.SplatConversionError, match="bad input"):
                splat_handler.convert_to_ply(str(source), str(tmp_path), root=str(tmp_path))

    def test_no_list_offers_mjs(self):
        assert ".mjs" not in core._all_extensions()
        assert not any(suffix.endswith(".mjs") for suffix in splat_handler.CONVERTED_SUFFIXES)


_HAS_CLI = os.path.isfile(splat_handler.SPLAT_TRANSFORM_CLI) and os.path.isfile(splat_handler.NODE_BINARY)


@pytest.mark.unit
@requires_real("pyvista", "vtk")
class TestSplatRendering:
    def _subject(self, frame):
        return frame[..., :3][frame[..., 3] == 255].astype(float)

    def test_a_splat_ply_renders_coloured_with_a_transparent_background(self, tmp_path):
        from preview_pipeline import renderer

        data = core._load_file(write_splat_ply(tmp_path / "ball.ply"), ".ply",
                               budgets=MagicMock(points=10 ** 6, splats=10 ** 6))
        assert splat_handler.is_splat(data)
        frame = renderer.generate_static_frame(data, resolution=(200, 150))
        subject = self._subject(frame)
        assert len(subject) > 2000, "the sphere fills a good part of the frame"
        assert frame[0, 0, 3] == 0 and frame[-1, -1, 3] == 0
        reds = np.sum((subject[:, 0] > subject[:, 2] + 60))
        blues = np.sum((subject[:, 2] > subject[:, 0] + 60))
        assert reds > 200 and blues > 200, (reds, blues)

    @pytest.mark.skipif(not _HAS_CLI, reason="needs Node.js and splat-transform (pipeline image)")
    @pytest.mark.parametrize("suffix", [".spz", ".sog", ".compressed.ply"])
    def test_converted_formats_render_like_the_ply(self, tmp_path, suffix):
        source = write_splat_ply(tmp_path / "ball.ply")
        target = str(tmp_path / f"ball{suffix}")
        subprocess.run([splat_handler.NODE_BINARY, splat_handler.SPLAT_TRANSFORM_CLI, "-q", "-w", "-g", "cpu",
                        source, target], check=True, timeout=300)
        ext = ".ply" if suffix == ".compressed.ply" else suffix
        data = core._load_file(target, ext, asset_root=str(tmp_path),
                               budgets=MagicMock(points=10 ** 6, splats=10 ** 6))
        assert splat_handler.is_splat(data) and 3500 <= data.n_points <= 4400
        rgb = np.asarray(data.point_data["RGB"]).astype(int)
        top = rgb[np.asarray(data.points)[:, 1] < -0.5]
        assert top[:, 0].mean() > 180 and top[:, 2].mean() < 90, "the red half keeps its colour"
