#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Real-scene splat thumbnails and XGRIDS LCC / LCC2 scenes.

* A captured scene surrounds its subject with background made of large splats. The splat handler
  keeps the scene's core (``focus_scene``): a compact object loses nothing, a far background shell
  is dropped, oversized splats in the core are dropped, and a flat, slightly tilted ground plane is
  levelled. Each splat is drawn as a flat, unlit disc with the coverage of its Gaussian.
* An ``.lcc``/``.lcc2`` scene is read at one level of detail: the finest within twice the splat
  budget. For ``.lcc2`` only that level's chunk files are fetched, and every chunk file name the
  description lists must stay inside the asset before splat-transform is started (ARCC BSC1).

Render and conversion tests need the real pyvista/VTK and the image's Node.js + splat-transform.
"""

import json
import os
import subprocess
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from conftest import requires_real
from preview_pipeline import core
from preview_pipeline.format_handlers import lcc_meta, splat_handler
from test_thumbnail_splats import C0, _PROPS, _fake_polydata

_HAS_CLI = os.path.isfile(splat_handler.SPLAT_TRANSFORM_CLI) and os.path.isfile(splat_handler.NODE_BINARY)


def write_ply(path, points, rgb, scales, opacity=3.0):
    n = len(points)
    f_dc = ((np.asarray(rgb, np.float64) - 0.5) / C0).astype(np.float32)
    scales = np.broadcast_to(np.asarray(scales, np.float64), (n, 3))
    data = np.hstack([np.asarray(points, np.float32), np.zeros((n, 3), np.float32), f_dc,
                      np.broadcast_to(np.float32(opacity), (n, 1)), np.log(scales).astype(np.float32),
                      np.tile(np.array([1, 0, 0, 0], np.float32), (n, 1))]).astype("<f4")
    with open(path, "wb") as handle:
        handle.write(f"ply\nformat binary_little_endian 1.0\nelement vertex {n}\n".encode())
        handle.write("".join(f"property float {p}\n" for p in _PROPS).encode() + b"end_header\n")
        handle.write(data.tobytes())
    return str(path)


def _ball(rng, n=6000, radius=1.0, scale=0.05):
    v = rng.normal(size=(n, 3))
    v /= np.linalg.norm(v, axis=1, keepdims=True)
    rgb = np.where(v[:, 1:2] > 0, [[0.9, 0.2, 0.15]], [[0.15, 0.3, 0.9]])
    return v * radius, rgb, np.full((n, 3), scale)


def _load(path):
    with patch.object(splat_handler.pv, "PolyData", _fake_polydata()):
        return splat_handler.load_ply(str(path), max_splats=10 ** 6)


# --------------------------------------------------------------------------------------------------
# Scene core
# --------------------------------------------------------------------------------------------------

@pytest.mark.unit
class TestSceneFocus:
    def test_a_far_background_shell_is_dropped(self, tmp_path):
        rng = np.random.default_rng(1)
        ball, rgb, scales = _ball(rng)
        shell = rng.normal(size=(3000, 3))
        shell = shell / np.linalg.norm(shell, axis=1, keepdims=True) * 25.0
        path = write_ply(tmp_path / "scene.ply", np.vstack([ball, shell]),
                         np.vstack([rgb, np.full((3000, 3), 0.1)]), np.vstack([scales, np.full((3000, 3), 1.5)]))
        cloud = _load(path)
        distance = np.linalg.norm(cloud.points, axis=1)
        assert len(cloud.points) == len(ball), "every ball splat is kept and the shell is not"
        assert distance.max() < 1.01

    @pytest.mark.parametrize("shape", ["rod", "slab", "ball"])
    def test_a_compact_object_loses_nothing(self, tmp_path, shape):
        rng = np.random.default_rng(2)
        n = 20000
        if shape == "rod":
            points = np.column_stack([rng.uniform(-20, 20, n), rng.uniform(-1, 1, n), rng.uniform(-1, 1, n)])
        elif shape == "slab":
            points = np.column_stack([rng.uniform(-5, 5, n), rng.uniform(-0.05, 0.05, n), rng.uniform(-5, 5, n)])
        else:
            points = rng.uniform(-1, 1, (n, 3))
        cloud = _load(write_ply(tmp_path / f"{shape}.ply", points, np.full((n, 3), 0.5), 0.02))
        assert len(cloud.points) == n

    def test_oversized_splats_in_the_core_are_dropped(self, tmp_path):
        rng = np.random.default_rng(3)
        ball, rgb, scales = _ball(rng)
        fog = rng.uniform(-0.8, 0.8, (40, 3))
        path = write_ply(tmp_path / "fog.ply", np.vstack([ball, fog]), np.vstack([rgb, np.full((40, 3), 0.9)]),
                         np.vstack([scales, np.full((40, 3), 0.6)]))
        cloud = _load(path)
        assert len(cloud.points) == len(ball)
        assert np.asarray(cloud.point_data["splat_radius"]).max() < 0.1

    def test_a_tilted_ground_plane_is_levelled(self, tmp_path):
        rng = np.random.default_rng(4)
        n = 20000
        plane = np.column_stack([rng.uniform(-5, 5, n), rng.normal(0, 0.02, n), rng.uniform(-5, 5, n)])
        tilt = np.radians(30.0)
        rotation = np.array([[1, 0, 0], [0, np.cos(tilt), -np.sin(tilt)], [0, np.sin(tilt), np.cos(tilt)]])
        cloud = _load(write_ply(tmp_path / "ground.ply", plane @ rotation.T, np.full((n, 3), 0.4), 0.02))
        spread = np.ptp(np.asarray(cloud.points), axis=0)
        assert spread[1] < 0.3, f"levelled plane is flat in Y: {spread}"
        assert spread[0] > 8 and spread[2] > 8

    def test_an_upright_or_steep_plane_is_not_turned(self, tmp_path):
        rng = np.random.default_rng(5)
        n = 20000
        wall = np.column_stack([rng.uniform(-5, 5, n), rng.uniform(-5, 5, n), rng.normal(0, 0.02, n)])
        cloud = _load(write_ply(tmp_path / "wall.ply", wall, np.full((n, 3), 0.4), 0.02))
        assert np.ptp(np.asarray(cloud.points)[:, 2]) < 0.3, "a wall stays a wall"

    def test_a_small_scene_is_drawn_whole(self):
        xyz = np.vstack([np.zeros((10, 3)), np.full((2, 3), 500.0)])
        keep, _, rotation = splat_handler.focus_scene(xyz, np.full(12, 0.1), np.full(12, 0.9), np.full(12, 0.1))
        assert keep.all() and rotation is None


# --------------------------------------------------------------------------------------------------
# LCC levels of detail
# --------------------------------------------------------------------------------------------------

def _lcc2(files, levels, counts=True, legacy=False):
    total = max(levels) + 1
    root = {"childNum": 0}
    parents = [root]
    for depth in range(1, total + 1):
        level = total - depth
        children = []
        for parent in parents:
            parent["child"] = {}
            for slot, index in enumerate(levels.get(level, [])):
                gs = {"name": index, "start": 0}
                if counts:
                    gs["count"] = 1000 * (level + 1)
                child = {"data": {"3dgs": gs}, "childNum": 0}
                parent["child"][str(slot)] = child
                children.append(child)
        parents = children or parents
    if legacy:
        root["files"] = files
        return {"total_splats": 1, "lod_3dgs_info": list(range(total, 0, -1)), "lod_level": total, "root": root}
    root["splatFiles"] = files
    return {"version": "0.0.3", "totalLevels": total, "totalSplats": 1,
            "lodSplats": [7] * total, "splatType": ".sog", "root": root}


@pytest.mark.unit
class TestLccLevels:
    @pytest.mark.parametrize("counts,budget,level", [
        ([6_019_032, 2_995_625, 1_494_160], 4_000_000, 0),   # 6.0 M <= 2 x 4 M: the finest level
        ([6_019_032, 2_995_625, 1_494_160], 1_000_000, 2),
        ([6_019_032, 2_995_625, 1_494_160], 1_500_000, 1),   # 3.0 M <= 2 x 1.5 M
        ([6_019_032, 2_995_625], 100, 1),                     # nothing fits: the coarsest
        ([], 100, 0),
    ])
    def test_the_finest_level_within_twice_the_budget(self, counts, budget, level):
        assert lcc_meta.select_level(counts, budget) == level

    def test_an_lcc_scene_reads_the_chosen_level(self, tmp_path):
        meta = tmp_path / "meta.lcc"
        meta.write_text(json.dumps({"splats": [6_019_032, 2_995_625, 1_494_160], "totalLevel": 3}))
        assert splat_handler.select_lcc_level(str(meta), 1_000_000) == 2
        meta.write_text(json.dumps({"version": "5.0"}))
        assert splat_handler.select_lcc_level(str(meta), 1_000_000) is None

    def test_the_lcc2_octree_gives_each_level_its_files(self):
        meta = _lcc2(["a.sog", "b.sog", "c.sog"], {0: [0, 1], 1: [2]})
        layout = lcc_meta.lcc2_layout(meta)
        assert layout["levels"] == {0: {0: 1000, 1: 1000}, 1: {2: 2000}}
        assert lcc_meta.lcc2_level_counts(meta) == [2000, 2000]
        assert lcc_meta.lcc2_level_files(meta, 1000) == (0, ["a.sog", "b.sog"])
        assert lcc_meta.lcc2_level_files(meta, 10) == (1, ["c.sog"])

    def test_counts_fall_back_to_lod_splats_and_the_older_protocol(self):
        meta = _lcc2(["a", "/b"], {0: [0], 1: [1]}, counts=False, legacy=True)
        layout = lcc_meta.lcc2_layout(meta)
        assert layout["splat_files"] == ["a.sog", "b.sog"]
        assert lcc_meta.lcc2_level_counts(meta) == [1, 2], "lod_3dgs_info is listed coarsest first"

    def test_trailing_commas_are_read(self, tmp_path):
        path = tmp_path / "scene.lcc2"
        text = json.dumps(_lcc2(["a.sog"], {0: [0]}), indent=1)
        path.write_text(text.replace('"childNum": 0\n', '"childNum": 0,\n').replace("]\n", "],\n"))
        assert lcc_meta.lcc2_layout(lcc_meta.read_meta(str(path)))["splat_files"] == ["a.sog"]
        assert lcc_meta._strip_trailing_commas('{"a": "x,}", "b": [1, 2,],}') == '{"a": "x,}", "b": [1, 2]}'

    @pytest.mark.parametrize("meta", [
        {"totalLevels": 1, "root": {"splatFiles": ["a.sog"], "child": {"0": {"data": {"3dgs": {"name": 3}}}}}},
        {"totalLevels": 1, "root": {"child": {}}},
        {"totalLevels": 0, "root": {"splatFiles": []}},
        {"totalLevels": 1},
        {"totalLevels": 1, "root": {"splatFiles": [7]}},
    ])
    def test_a_malformed_description_is_refused(self, meta):
        with pytest.raises(lcc_meta.LccMetaError):
            lcc_meta.lcc2_level_files(meta, 1000)

    def test_an_oversized_description_is_refused(self, tmp_path, monkeypatch):
        path = tmp_path / "big.lcc2"
        path.write_text(" " * 2048)
        monkeypatch.setattr(lcc_meta, "MAX_META_BYTES", 1024)
        with pytest.raises(lcc_meta.LccMetaError, match="larger than"):
            lcc_meta.read_meta(str(path))


# --------------------------------------------------------------------------------------------------
# LCC2 chunk confinement
# --------------------------------------------------------------------------------------------------

def _lcc2_on_disk(tmp_path, files, levels, present=None):
    root = tmp_path / "work"
    scene = root / "scan"
    (scene / "data" / "3dgs").mkdir(parents=True, exist_ok=True)
    path = scene / "scene.lcc2"
    path.write_text(json.dumps(_lcc2(files, levels)))
    for name in present if present is not None else files:
        target = scene / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"chunk")
    return str(path), str(root)


@pytest.mark.unit
class TestLcc2Confinement:
    def test_a_scene_inside_the_asset_is_accepted(self, tmp_path):
        files = ["data/3dgs/l0.sog", "data/3dgs/l1.sog"]
        path, root = _lcc2_on_disk(tmp_path, files, {0: [0], 1: [1]})
        assert splat_handler.prepare_lcc2(path, root, 10 ** 6) == 0
        assert splat_handler.prepare_lcc2(path, root, 10) == 1

    @pytest.mark.parametrize("bad", [
        "../../../etc/passwd", "../outside.sog", "/etc/passwd", "https://example.invalid/a.sog",
        "s3://other-bucket/a.sog", "C:/chunks/a.sog", "data\\3dgs\\a.sog", "data/3dgs/a\x00.sog", "",
    ])
    def test_a_name_outside_the_asset_refuses_the_conversion(self, tmp_path, bad):
        # The bad name is on the level that is NOT read: it is refused all the same.
        files = ["data/3dgs/l0.sog", bad]
        path, root = _lcc2_on_disk(tmp_path, files, {0: [0], 1: [1]}, present=["data/3dgs/l0.sog"])
        with pytest.raises(splat_handler.SplatConversionError, match="not a path inside the asset"):
            splat_handler.prepare_lcc2(path, root, 10 ** 6)

    def test_a_symlink_out_of_the_work_directory_is_refused(self, tmp_path):
        outside = tmp_path / "secret.sog"
        outside.write_bytes(b"secret")
        path, root = _lcc2_on_disk(tmp_path, ["data/3dgs/link.sog"], {0: [0]}, present=[])
        os.symlink(outside, os.path.join(os.path.dirname(path), "data", "3dgs", "link.sog"))
        with pytest.raises(splat_handler.SplatConversionError, match="not a path inside the asset"):
            splat_handler.prepare_lcc2(path, root, 10 ** 6)

    def test_a_missing_chunk_of_the_chosen_level_refuses_the_conversion(self, tmp_path):
        files = ["data/3dgs/l0a.sog", "data/3dgs/l0b.sog", "data/3dgs/l1.sog"]
        path, root = _lcc2_on_disk(tmp_path, files, {0: [0, 1], 1: [2]}, present=["data/3dgs/l0a.sog"])
        with pytest.raises(splat_handler.SplatConversionError, match="1 of 2 LCC2 chunk files for LOD 0"):
            splat_handler.prepare_lcc2(path, root, 10 ** 6)

    def test_the_converter_is_told_the_level(self, tmp_path):
        files = ["data/3dgs/l0.sog", "data/3dgs/l1.sog"]
        path, root = _lcc2_on_disk(tmp_path, files, {0: [0], 1: [1]})
        commands = []

        def fake_run(command, **kwargs):
            commands.append(command)
            open(command[-1], "wb").write(b"ply")
            return subprocess.CompletedProcess(command, 0, b"", b"")

        with patch.object(splat_handler.subprocess, "run", MagicMock(side_effect=fake_run)), \
                patch.object(splat_handler, "load_ply", MagicMock(return_value="cloud")):
            assert splat_handler.load(path, max_splats=10, work_dir=root) == "cloud"
        command = commands[0]
        assert command[command.index("--select-lod") + 1] == "1"
        assert command[-2] == os.path.realpath(path)

    def test_lcc2_is_an_allowlisted_conversion_input_and_a_y_up_format(self, tmp_path):
        assert ".lcc2" in core._all_extensions() and ".lcc2" in core._Y_UP_FORMATS
        (tmp_path / "s.lcc2").write_text("{}")
        assert splat_handler.validate_conversion_input(str(tmp_path / "s.lcc2"), root=str(tmp_path))


# --------------------------------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------------------------------

@pytest.mark.unit
@requires_real("pyvista", "vtk")
class TestSplatSceneRendering:
    def test_a_splat_is_a_flat_unlit_disc(self):
        import pyvista as pv
        from preview_pipeline import renderer

        cloud = pv.PolyData(np.zeros((1, 3)))
        cloud.point_data["RGB"] = np.array([[200, 100, 50]], np.uint8)
        cloud.point_data["splat_radius"] = np.array([1.0], np.float32)
        plotter = pv.Plotter(off_screen=True, window_size=(200, 200))
        renderer._add_point_cloud(plotter, cloud)
        plotter.camera.position, plotter.camera.focal_point, plotter.camera.up = (0, 0, 10), (0, 0, 0), (0, 1, 0)
        plotter.camera.parallel_projection = True
        plotter.camera.parallel_scale = 2.0
        plotter.render()
        frame = renderer._capture_rgba(plotter)
        plotter.close()
        row = frame[100]
        assert row[100].tolist() == [200, 100, 50, 255], "the disc centre is the splat's own colour"
        assert row[140].tolist() == [200, 100, 50, 255], "no darkening toward the edge"
        assert row[199][3] == 0, "outside the disc is background"

    def test_a_scene_inside_a_background_shell_renders_the_subject(self, tmp_path):
        from preview_pipeline import renderer

        rng = np.random.default_rng(6)
        ball, rgb, scales = _ball(rng)
        shell = rng.normal(size=(20000, 3))
        shell = shell / np.linalg.norm(shell, axis=1, keepdims=True) * rng.uniform(4, 30, (20000, 1))
        path = write_ply(tmp_path / "room.ply", np.vstack([ball, shell]),
                         np.vstack([rgb, np.full((20000, 3), 0.05)]), np.vstack([scales, np.full((20000, 3), 0.8)]))
        data = core._load_file(path, ".ply", budgets=MagicMock(points=10 ** 6, splats=10 ** 6))
        for frame in (renderer.generate_static_frame(data, resolution=(200, 150)),
                      renderer.generate_rotating_frames(data, n_frames=4, resolution=(200, 150))[2]):
            alpha = frame[..., 3]
            assert [alpha[0, 0], alpha[0, -1], alpha[-1, 0], alpha[-1, -1]] == [0, 0, 0, 0]
            subject = frame[..., :3][alpha == 255].astype(int)
            assert len(subject) > 1500, "the ball fills the frame, not the shell"
            dark = np.sum(subject.max(axis=1) < 40)
            assert dark < 0.05 * len(subject), "no dark background shell in front of the ball"

    @pytest.mark.skipif(not _HAS_CLI, reason="needs Node.js and splat-transform (pipeline image)")
    def test_an_lcc2_scene_converts_at_the_chosen_level(self, tmp_path):
        rng = np.random.default_rng(7)
        scene = tmp_path / "work" / "scan"
        chunks = scene / "data" / "3dgs"
        chunks.mkdir(parents=True)
        ball, rgb, scales = _ball(rng, n=4000)
        write_ply(tmp_path / "l0.ply", ball, rgb, scales)
        write_ply(tmp_path / "l1.ply", ball[::4], rgb[::4], scales[::4] * 2)
        for name in ("l0", "l1"):
            subprocess.run([splat_handler.NODE_BINARY, splat_handler.SPLAT_TRANSFORM_CLI, "-q", "-w", "-g", "cpu",
                            str(tmp_path / f"{name}.ply"), str(chunks / f"{name}.spz")], check=True, timeout=300)
        meta = _lcc2(["data/3dgs/l0.spz", "data/3dgs/l1.spz"], {0: [0], 1: [1]})
        meta["root"]["child"]["0"]["data"]["3dgs"]["count"] = 1000
        meta["root"]["child"]["0"]["child"]["0"]["data"]["3dgs"]["count"] = 4000
        meta["splatType"] = ".spz"
        (scene / "scene.lcc2").write_text(json.dumps(meta))
        root = str(tmp_path / "work")
        fine = core._load_file(str(scene / "scene.lcc2"), ".lcc2", asset_root=root,
                               budgets=MagicMock(points=10 ** 6, splats=10 ** 6))
        coarse = core._load_file(str(scene / "scene.lcc2"), ".lcc2", asset_root=root,
                                 budgets=MagicMock(points=10 ** 6, splats=400))
        assert splat_handler.is_splat(fine) and 3500 <= fine.n_points <= 4000
        assert splat_handler.is_splat(coarse) and coarse.n_points <= 1000
