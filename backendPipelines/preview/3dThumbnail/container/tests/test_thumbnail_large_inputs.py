#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Point clouds and large inputs (#467, and the container half of #470).

* Budgets come from the cgroup memory limit and CPU quota; ``VAMS_COMPUTE_TIER`` is only logged.
* The 100 GB constant is replaced by a free-disk check before the download, and the download runs as
  parallel ranged GETs.
* FARO (.fls/.fws) is removed from every list: Open3D has no FARO reader, so each such run failed.
* E57 reads every scan in fixed buffers with its pose applied and its colours scaled by the declared
  limits (the old reader took scan 0 only, unposed, and cast colours straight to uint8).
* PTX parses every scan's header and applies its transform (the old reader parsed the next scan's ten
  header lines as points) and streams from S3 with no local copy.
* Each phase's duration is logged.
* The disk refusal never suggests another tier (every tier has the same work volume); the memory
  refusal suggests the large tier only on the standard tier.

The E57 and decimation tests need the real pye57 / pyvista / VTK and run inside the pipeline image.
"""

import json
import logging
import os
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from conftest import requires_real
from preview_pipeline import core, resources
from preview_pipeline.format_handlers import point_streams, pointcloud_handler
from preview_pipeline.utils import s3_utils
from preview_pipeline.utils.pipeline.objects import (
    PipelineStage,
    PipelineStatus,
    PipelineType,
    StageOutput,
)

_PIPELINE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_REPO = os.path.dirname(os.path.dirname(os.path.dirname(_PIPELINE_DIR)))
GIB = 1024 ** 3


# --------------------------------------------------------------------------------------------------
# Resources and budgets
# --------------------------------------------------------------------------------------------------

def _cgroup(tmp_path, files):
    for name, text in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return str(tmp_path)


@pytest.mark.unit
class TestResources:
    def test_the_cgroup_v2_memory_limit_is_read(self, tmp_path):
        root = _cgroup(tmp_path, {"memory.max": str(16 * GIB)})
        with patch("os.sysconf", side_effect=lambda name: {"SC_PAGE_SIZE": 4096,
                                                           "SC_PHYS_PAGES": 64 * GIB // 4096}[name]):
            assert resources.memory_limit_bytes(root) == 16 * GIB

    def test_an_unlimited_cgroup_reads_as_physical_memory_and_v1_is_a_fallback(self, tmp_path):
        physical = {"SC_PAGE_SIZE": 4096, "SC_PHYS_PAGES": 32 * GIB // 4096}
        with patch("os.sysconf", side_effect=lambda name: physical[name]):
            assert resources.memory_limit_bytes(_cgroup(tmp_path / "a", {"memory.max": "max"})) == 32 * GIB
            v1 = _cgroup(tmp_path / "b", {"memory/memory.limit_in_bytes": str(8 * GIB)})
            assert resources.memory_limit_bytes(v1) == 8 * GIB
            unlimited_v1 = _cgroup(tmp_path / "c", {"memory/memory.limit_in_bytes": str(2 ** 63 - 4096)})
            assert resources.memory_limit_bytes(unlimited_v1) == 32 * GIB

    def test_the_cpu_quota_caps_the_cpu_count(self, tmp_path):
        with patch("os.sched_getaffinity", return_value=set(range(16))):
            assert resources.cpu_count(_cgroup(tmp_path / "a", {"cpu.max": "400000 100000"})) == 4
            assert resources.cpu_count(_cgroup(tmp_path / "b", {"cpu.max": "max 100000"})) == 16
            assert resources.cpu_count(_cgroup(tmp_path / "c", {"cpu.max": "150000 100000"})) == 2
            v1 = _cgroup(tmp_path / "d", {"cpu/cpu.cfs_quota_us": "800000", "cpu/cpu.cfs_period_us": "100000"})
            assert resources.cpu_count(v1) == 8

    def test_budgets_scale_with_cpus_and_memory(self):
        standard = resources.compute_budgets(memory_bytes=16 * GIB, cpus=4)
        assert standard.points == 20_000_000 and standard.triangles == 20_000_000
        large = resources.compute_budgets(memory_bytes=120 * GIB, cpus=16)
        assert large.points == resources.MAX_POINTS and large.triangles == resources.MAX_TRIANGLES
        small = resources.compute_budgets(memory_bytes=2 * GIB, cpus=4)
        assert small.points == int(2 * GIB * resources.GEOMETRY_MEMORY_FRACTION / resources.BYTES_PER_POINT)
        assert small.points < standard.points
        tiny = resources.compute_budgets(memory_bytes=256 * 1024 ** 2, cpus=1)
        assert tiny.points == resources.MIN_POINTS and tiny.triangles == resources.MIN_TRIANGLES

    def test_the_rasteriser_thread_count_follows_the_cpus_without_overriding_a_set_value(self, monkeypatch):
        monkeypatch.delenv("LP_NUM_THREADS", raising=False)
        monkeypatch.delenv("GALLIUM_DRIVER", raising=False)
        resources.configure_rasteriser_threads(4)
        assert os.environ["LP_NUM_THREADS"] == "4" and os.environ["GALLIUM_DRIVER"] == "llvmpipe"
        resources.configure_rasteriser_threads(16)
        assert os.environ["LP_NUM_THREADS"] == "4"

    def test_an_input_larger_than_the_free_disk_is_refused(self, tmp_path):
        with patch("shutil.disk_usage", return_value=MagicMock(free=10 * GIB)):
            assert resources.ensure_disk_space(str(tmp_path), 8 * GIB) == 10 * GIB
            with pytest.raises(resources.InsufficientDiskError, match="does not fit"):
                resources.ensure_disk_space(str(tmp_path), 9.5 * GIB)


# --------------------------------------------------------------------------------------------------
# S3: transfer config and resumable stream
# --------------------------------------------------------------------------------------------------

@pytest.mark.unit
class TestS3Transfers:
    def test_the_download_uses_parallel_ranged_gets(self, tmp_path):
        client = MagicMock()
        with patch.object(s3_utils, "client", client), \
                patch("preview_pipeline.resources.cpu_count", return_value=4):
            assert s3_utils.download("b", "k", str(tmp_path / "f")) == str(tmp_path / "f")
        config = client.download_file.call_args.kwargs["Config"]
        assert config.max_concurrency == 16 and config.multipart_chunksize == 64 * 1024 ** 2
        client.download_fileobj.assert_not_called()

    def test_a_dropped_stream_resumes_at_its_offset(self):
        from botocore.exceptions import ReadTimeoutError

        class Body:
            def __init__(self, chunks, fail_after=None):
                self.chunks, self.fail_after = chunks, fail_after

            def iter_chunks(self, size):
                for index, chunk in enumerate(self.chunks):
                    if self.fail_after is not None and index == self.fail_after:
                        raise ReadTimeoutError(endpoint_url="s3")
                    yield chunk

            def close(self):
                pass

        client = MagicMock()
        client.get_object.side_effect = [{"Body": Body([b"abc", b"def", b"x"], fail_after=2)},
                                         {"Body": Body([b"ghi"])}]
        with patch.object(s3_utils, "client", client):
            data = b"".join(s3_utils.iter_object_bytes("b", "k"))
        assert data == b"abcdefghi"
        assert client.get_object.call_args_list[1].kwargs["Range"] == "bytes=6-"


# --------------------------------------------------------------------------------------------------
# Sampling and PTX
# --------------------------------------------------------------------------------------------------

@pytest.mark.unit
class TestReservoirSampler:
    def test_the_sample_is_bounded_uniform_and_keeps_colours(self):
        sampler = point_streams.ReservoirSampler(1000, seed=1)
        for start in range(0, 100_000, 7_000):
            index = np.arange(start, min(start + 7_000, 100_000))
            sampler.add(np.column_stack([index, index, index]).astype(float),
                        np.column_stack([index % 256] * 3).astype(np.uint8))
        points, colors = sampler.result()
        assert len(points) == 1000 and len(colors) == 1000 and sampler.seen == 100_000
        # Uniform over the stream: about a tenth of the sample from each tenth of the input.
        counts = np.histogram(points[:, 0], bins=10, range=(0, 100_000))[0]
        assert counts.min() > 60, counts
        assert np.array_equal(colors[:, 0], (points[:, 0] % 256).astype(np.uint8))

    def test_a_small_input_is_kept_whole_and_missing_colours_drop_all_colours(self):
        sampler = point_streams.ReservoirSampler(10_000)
        sampler.add(np.ones((5, 3)), np.ones((5, 3), dtype=np.uint8))
        sampler.add(np.ones((5, 3)), None)
        points, colors = sampler.result()
        assert len(points) == 10 and colors is None


def _ptx_scan(points, translation=(0.0, 0.0, 0.0), rotation=None, columns=None, rgb=True):
    rotation = np.eye(3) if rotation is None else np.asarray(rotation, dtype=float)
    columns = columns or len(points)
    rows = len(points) // columns
    matrix = np.eye(4)
    matrix[:3, :3] = rotation
    matrix[3, :3] = translation
    lines = [str(columns), str(rows), "0 0 0", "1 0 0", "0 1 0", "0 0 1"]
    lines += [" ".join(f"{v:g}" for v in row) for row in matrix]
    for x, y, z in points:
        lines.append(f"{x:g} {y:g} {z:g} 0.5" + (" 200 100 50" if rgb else ""))
    return "\n".join(lines) + "\n"


def _chunks(text, size):
    data = text.encode()
    return [data[i:i + size] for i in range(0, len(data), size)]


@pytest.mark.unit
class TestPtx:
    def test_every_scan_is_read_with_its_transform_and_no_header_is_parsed_as_points(self):
        first = [(1, 0, 0), (0, 0, 0), (2, 0, 0), (3, 0, 0)]  # 0 0 0 is a missing return
        second = [(1, 0, 0), (2, 0, 0)]
        rot_z_90 = [[0, 1, 0], [-1, 0, 0], [0, 0, 1]]  # row-vector convention: x -> y
        text = _ptx_scan(first, columns=2) + _ptx_scan(second, translation=(100, 0, 0), rotation=rot_z_90)
        # Tiny chunks split lines and numbers across reads.
        points, colors = point_streams.read_ptx(_chunks(text, 7), max_points=1000)
        got = sorted(map(tuple, np.round(points, 6)))
        assert got == sorted([(1, 0, 0), (2, 0, 0), (3, 0, 0), (100, 1, 0), (100, 2, 0)]), got
        assert colors.shape == (5, 3) and tuple(colors[0]) == (200, 100, 50)

    def test_a_scan_is_sampled_to_the_budget_while_it_streams(self):
        pts = [(float(i), 1.0, 2.0) for i in range(1, 5001)]
        points, colors = point_streams.read_ptx(_chunks(_ptx_scan(pts, rgb=False), 4096),
                                                max_points=500, chunk_lines=300)
        assert len(points) == 500 and colors is None
        assert points[:, 0].max() > 4000, "the sample must reach the end of the stream"

    def test_a_malformed_first_header_fails_and_trailing_garbage_after_a_scan_is_ignored(self):
        with pytest.raises(ValueError, match="PTX"):
            point_streams.read_ptx([b"not a ptx\n"], max_points=10)
        text = _ptx_scan([(1, 2, 3)]) + "garbage here\n"
        points, _ = point_streams.read_ptx([text.encode()], max_points=10)
        assert points.tolist() == [[1.0, 2.0, 3.0]]

    def test_a_local_ptx_goes_through_the_same_streamed_reader(self, tmp_path):
        path = tmp_path / "scan.ptx"
        path.write_text(_ptx_scan([(1, 0, 0)]) + _ptx_scan([(1, 0, 0)], translation=(0, 5, 0)))
        fake_pv = MagicMock(side_effect=lambda pts: MagicMock(points=pts, point_data={}))
        with patch.object(pointcloud_handler.pv, "PolyData", fake_pv):
            cloud = pointcloud_handler.load(str(path), max_points=100)
        assert sorted(map(tuple, cloud.points)) == [(1.0, 0.0, 0.0), (1.0, 5.0, 0.0)]


# --------------------------------------------------------------------------------------------------
# E57
# --------------------------------------------------------------------------------------------------

@pytest.mark.unit
class TestE57Colours:
    def test_colours_are_scaled_by_the_declared_limits(self):
        values = np.array([[0, 32768, 65535], [65535, 0, 0]], dtype=float)
        scaled = point_streams.scale_colours(values, [(0, 65535)] * 3)
        assert scaled.tolist() == [[0, 128, 255], [255, 0, 0]]
        floats = point_streams.scale_colours(np.array([[0.0, 0.5, 1.0]]), [(0.0, 1.0)] * 3)
        assert floats.tolist() == [[0, 128, 255]]


@pytest.mark.unit
@requires_real("pye57")
class TestE57AllScans:
    def test_every_scan_is_read_in_buffers_with_its_pose(self, tmp_path):
        import pye57

        path = str(tmp_path / "two.e57")
        writer = pye57.E57(path, mode="w")
        xyz = np.column_stack([np.arange(1, 251, dtype=float), np.zeros(250), np.zeros(250)])
        colour = {name: np.full(250, value, np.uint8)
                  for name, value in (("colorRed", 200), ("colorGreen", 100), ("colorBlue", 50))}
        scan = {"cartesianX": xyz[:, 0], "cartesianY": xyz[:, 1], "cartesianZ": xyz[:, 2], **colour}
        writer.write_scan_raw(scan, rotation=np.array([1.0, 0, 0, 0]), translation=np.array([0.0, 0, 0]))
        # 90 degrees about Z (w, x, y, z): x -> y, then translated by 1000 in x.
        writer.write_scan_raw(scan, rotation=np.array([0.70710678, 0, 0, 0.70710678]),
                              translation=np.array([1000.0, 0, 0]))
        writer.close()

        points, colors = point_streams.read_e57(path, max_points=10_000, chunk_points=64)
        assert len(points) == 500, "both scans, read across several 64-point buffers"
        posed = points[points[:, 0] > 500]
        assert len(posed) == 250 and np.allclose(posed[:, 0], 1000.0, atol=1e-6)
        assert np.allclose(np.sort(posed[:, 1]), np.arange(1, 251), atol=1e-6)
        assert tuple(colors[0]) == (200, 100, 50)

    def test_the_point_budget_bounds_the_result(self, tmp_path):
        import pye57

        path = str(tmp_path / "big.e57")
        writer = pye57.E57(path, mode="w")
        xyz = np.random.default_rng(0).normal(size=(5000, 3))
        writer.write_scan_raw({"cartesianX": xyz[:, 0], "cartesianY": xyz[:, 1], "cartesianZ": xyz[:, 2]})
        writer.close()
        points, colors = point_streams.read_e57(path, max_points=300, chunk_points=1000)
        assert len(points) == 300 and colors is None


# --------------------------------------------------------------------------------------------------
# Mesh decimation
# --------------------------------------------------------------------------------------------------

@pytest.mark.unit
@requires_real("pyvista", "vtk")
class TestTriangleBudget:
    def test_a_mesh_above_the_budget_is_decimated_and_keeps_its_colours(self):
        import pyvista as pv

        from preview_pipeline import geometry_budget

        mesh = pv.Plane(i_resolution=400, j_resolution=400).triangulate()
        mesh.point_data["RGB"] = np.tile(np.array([[220, 40, 30]], dtype=np.uint8), (mesh.n_points, 1))
        assert mesh.n_cells > 300_000
        reduced = geometry_budget.limit_triangles(mesh, 20_000)
        assert 2_000 < reduced.n_cells <= 20_000
        assert "RGB" in reduced.cell_data
        assert np.allclose(np.asarray(reduced.cell_data["RGB"]).mean(axis=0), (220, 40, 30), atol=1)
        bounds = np.array(reduced.bounds) - np.array(mesh.bounds)
        assert np.abs(bounds).max() < 0.05, "decimation must keep the mesh's extent"

    def test_a_mesh_within_the_budget_is_returned_unchanged(self):
        import pyvista as pv

        from preview_pipeline import geometry_budget

        mesh = pv.Sphere()
        assert geometry_budget.limit_triangles(mesh, 10 ** 6) is mesh


# --------------------------------------------------------------------------------------------------
# The stage: disk check, streaming, timings, tier, FARO
# --------------------------------------------------------------------------------------------------

def _stage(key, ext):
    output = StageOutput(bucketName="run-bucket", objectDir="pipelines/p/job/output/e1/files/")
    return PipelineStage(type=PipelineType.PREVIEW_3D_THUMBNAIL,
                         inputFile={"bucketName": "asset-bucket", "objectKey": key, "fileExtension": ext},
                         outputFiles=output.__dict__, outputMetadata=output.__dict__,
                         temporaryFiles=output.__dict__)


def _patches(tmp_path, size, download=None, stream=None):
    def _create_dir(parts):
        path = tmp_path / "-".join(parts)
        path.mkdir(parents=True, exist_ok=True)
        return str(path)

    def _writer(frames, path, **kwargs):
        open(path, "wb").write(b"gif")
        return path

    return [
        patch.object(core, "_create_dir", _create_dir),
        patch.object(core, "_check_existing_preview", MagicMock(return_value=None)),
        patch.object(core.s3, "get_object_size", MagicMock(return_value=size)),
        patch.object(core.s3, "download", download or MagicMock(return_value=None)),
        patch.object(core.s3, "iter_object_bytes", stream or MagicMock(return_value=iter(()))),
        patch.object(core.s3, "upload", MagicMock(side_effect=lambda bucket, key, path: key)),
        patch.object(core.renderer, "generate_rotating_frames", MagicMock(return_value=[1, 2])),
        patch.object(core.image_utils, "ensure_under_size_limit", MagicMock(side_effect=_writer)),
        patch.object(core.geometry_budget, "apply_budgets", lambda data, budgets: data),
        patch.object(core, "_normalize_up_axis", lambda data, ext: data),
    ]


def _run(patches, stage):
    for one in patches:
        one.start()
    try:
        return core._run_preview_pipeline(stage, {}, {}, False, "asset1")
    finally:
        for one in reversed(patches):
            one.stop()


@pytest.mark.unit
class TestStage:
    def test_an_input_larger_than_the_free_disk_fails_before_the_download(self, tmp_path):
        download = MagicMock()
        patches = _patches(tmp_path, 300 * GIB, download=download)
        with patch("shutil.disk_usage", return_value=MagicMock(free=200 * GIB)):
            result = _run(patches, _stage("asset1/scan.e57", ".e57"))
        assert result.status == PipelineStatus.FAILED
        assert "does not fit on the work volume" in result.errorMessage
        download.assert_not_called()

    def test_a_ptx_streams_from_s3_with_no_download_and_no_disk_check(self, tmp_path):
        download = MagicMock()
        stream = MagicMock(return_value=iter([_ptx_scan([(1, 2, 3)]).encode()]))
        loaded = MagicMock()
        patches = _patches(tmp_path, 300 * GIB, download=download, stream=stream)
        patches.append(patch.object(core.pointcloud_handler, "load_stream", MagicMock(return_value=loaded)))
        with patch("shutil.disk_usage", return_value=MagicMock(free=1 * GIB)):
            result = _run(patches, _stage("asset1/sub/scan.ptx", ".ptx"))
        assert result.status == PipelineStatus.COMPLETE, result.errorMessage
        download.assert_not_called()
        stream.assert_called_once_with("asset-bucket", "asset1/sub/scan.ptx")

    def test_the_tier_budgets_and_phase_timings_are_logged(self, tmp_path, monkeypatch, caplog):
        monkeypatch.setenv("VAMS_COMPUTE_TIER", "large")

        def _download(bucket, key, path):
            open(path, "wb").write(b"x")
            return path

        patches = _patches(tmp_path, 1024, download=_download)
        patches.append(patch.object(core, "_load_file", MagicMock(return_value=MagicMock())))
        with caplog.at_level(logging.INFO):
            result = _run(patches, _stage("asset1/model.glb", ".glb"))
        assert result.status == PipelineStatus.COMPLETE, result.errorMessage
        text = caplog.text
        assert "VAMS_COMPUTE_TIER=large" in text
        assert "Render budgets: memory=" in text
        for phase in ("download", "load", "render", "encode", "upload"):
            assert f"phase={phase} seconds=" in text, phase
        assert "phase timings:" in text and "Input size: 0.000 GiB" in text

    def test_no_tier_is_logged_when_none_is_set(self, tmp_path, monkeypatch, caplog):
        monkeypatch.delenv("VAMS_COMPUTE_TIER", raising=False)
        with caplog.at_level(logging.INFO):
            _run(_patches(tmp_path, 1024), _stage("asset1/model.glb", ".glb"))
        assert "VAMS_COMPUTE_TIER" not in caplog.text


def _set_tier(monkeypatch, tier):
    if tier is None:
        monkeypatch.delenv("VAMS_COMPUTE_TIER", raising=False)
    else:
        monkeypatch.setenv("VAMS_COMPUTE_TIER", tier)


def _assert_generic(message, tmp_path):
    """Refusals reach the execution record, so they name no bucket, key or local path."""
    assert "asset-bucket" not in message and "run-bucket" not in message
    assert "asset1/" not in message and str(tmp_path) not in message


@pytest.mark.unit
class TestRefusalAdvice:
    """The refusals' advice holds on the tier the job ran on: every tier has the same work volume,
    and only the standard tier has a larger tier to move to."""

    @pytest.mark.parametrize("tier", ["standard", "large", None])
    def test_the_disk_refusal_advises_reducing_the_input_on_every_tier(self, tmp_path, monkeypatch, tier):
        _set_tier(monkeypatch, tier)
        with patch("shutil.disk_usage", return_value=MagicMock(free=200 * GIB)):
            result = _run(_patches(tmp_path, 300 * GIB, download=MagicMock()),
                          _stage("asset1/scan.e57", ".e57"))
        assert result.status == PipelineStatus.FAILED
        message = result.errorMessage
        assert "reduce the input size or split it into smaller files" in message
        assert "tier" not in message.lower()
        assert "300.00 GiB" in message and "200.00 GiB free" in message
        _assert_generic(message, tmp_path)

    @pytest.mark.parametrize("tier, advice", [
        ("standard", "Run the large compute tier."),
        ("large", "The input exceeds the memory of the largest compute tier; reduce the input size."),
        (None, "Reduce the input size, or use a larger compute size if the deployment offers one."),
    ])
    def test_the_memory_refusal_advice_follows_the_tier(self, tmp_path, monkeypatch, tier, advice):
        _set_tier(monkeypatch, tier)

        def _download(bucket, key, path):
            open(path, "wb").write(b"x")
            return path

        patches = _patches(tmp_path, 1024, download=_download)
        patches.append(patch.object(core, "_load_file", MagicMock(side_effect=MemoryError)))
        result = _run(patches, _stage("asset1/model.glb", ".glb"))
        assert result.status == PipelineStatus.FAILED
        message = result.errorMessage
        assert "Out of memory" in message and advice in message
        if tier != "standard":
            assert "Run the large compute tier" not in message
        if tier is None:
            assert "tier" not in message.lower()
        _assert_generic(message, tmp_path)


@pytest.mark.unit
class TestFaroIsNotOffered:
    def test_no_list_names_a_faro_extension(self):
        assert not {".fls", ".fws"} & core._all_extensions()
        for relative in ("vamsSchema/pipeline.json", "vamsSchema/workflow.json"):
            with open(os.path.join(_PIPELINE_DIR, relative)) as handle:
                text = handle.read()
            assert ".fls" not in text and ".fws" not in text, relative
            json.loads(text)
        construct = os.path.join(_REPO, "infra", "lib", "nestedStacks", "pipelines", "preview",
                                 "3dThumbnail", "constructs", "preview3dThumbnail-construct.ts")
        if os.path.isfile(construct):
            with open(construct) as handle:
                text = handle.read()
            assert ".fls" not in text and ".fws" not in text
