#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Point-cloud readers that need no native library beyond numpy: PCD in all three encodings, ASCII XYZ,
the cap applied by `load_points`, and the FARO path that names its conversion route. Also pins that
Open3D is imported nowhere in the package except the FBX fallback of the mesh handler, which is what
lets the Lambda image omit it."""

import os
import struct
import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest

np = pytest.importorskip("numpy", reason="the readers build numpy arrays")

from preview_pipeline.format_handlers import pcd_reader, pointcloud_handler  # noqa: E402

PACKAGE_DIR = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "preview_pipeline"))


def _write(tmp_path, name, data):
    path = tmp_path / name
    path.write_bytes(data)
    return str(path)


def _pcd_header(fields, sizes, types, counts, points, data):
    return (f"# .PCD v0.7 - Point Cloud Data file format\nVERSION 0.7\nFIELDS {fields}\nSIZE {sizes}\n"
            f"TYPE {types}\nCOUNT {counts}\nWIDTH {points}\nHEIGHT 1\nVIEWPOINT 0 0 0 1 0 0 0\n"
            f"POINTS {points}\nDATA {data}\n").encode("ascii")


def _packed_rgb(r, g, b):
    return struct.unpack("<f", struct.pack("<I", (r << 16) | (g << 8) | b))[0]


def _lzf_literals(data):
    """A valid LZF stream made only of literal runs (control byte < 32 = run length - 1)."""
    out = bytearray()
    for start in range(0, len(data), 32):
        chunk = data[start:start + 32]
        out.append(len(chunk) - 1)
        out += chunk
    return bytes(out)


@pytest.mark.unit
class TestLzf:
    def test_literal_runs(self):
        payload = bytes(range(70))
        assert pcd_reader.lzf_decompress(_lzf_literals(payload), len(payload)) == payload

    def test_back_reference(self):
        # literal "abc" (ctrl 0x02), then a back-reference of length 6 (ctrl len field 4 -> 4+2) at
        # offset 3 (ctrl high bits 0, low byte 2 -> offset 2+1).
        stream = bytes([0x02]) + b"abc" + bytes([0x80, 0x02])
        assert pcd_reader.lzf_decompress(stream, 9) == b"abcabcabc"

    def test_long_back_reference_uses_the_extension_byte(self):
        # len field 7 means "add the next byte": 7 + 3 + 2 = 12 bytes copied from offset 1. The
        # reference overlaps the bytes being produced, so this is the byte-at-a-time branch.
        stream = bytes([0x00]) + b"z" + bytes([0xE0, 0x03, 0x00])
        assert pcd_reader.lzf_decompress(stream, 13) == b"z" * 13

    def test_a_non_overlapping_back_reference_after_a_large_literal_is_copied_as_one_slice(self):
        # 1 MiB of literal runs, then the longest back-reference LZF can encode (len field 7 + 255 +
        # 2 = 264 bytes) at offset 4096: it lies entirely inside the output already produced, which is
        # the slice branch. A binary_compressed PCD body at the point cap is ~240 MB of such tokens, so
        # copying them per byte would take minutes; this pins that the reader takes the slice path by
        # producing the right bytes for the largest non-overlapping token.
        payload = bytes((i * 7919) & 0xFF for i in range(1 << 20))
        offset = 4096
        stream = _lzf_literals(payload) + bytes([0xE0 | ((offset - 1) >> 8), 0xFF, (offset - 1) & 0xFF])
        expected = payload + payload[len(payload) - offset:len(payload) - offset + 264]
        assert pcd_reader.lzf_decompress(stream, len(expected)) == expected


@pytest.mark.unit
class TestPcdHeader:
    def test_fields_sizes_types_counts_and_data(self, tmp_path):
        path = _write(tmp_path, "a.pcd", _pcd_header("x y z rgb", "4 4 4 4", "F F F F", "1 1 1 1", 3, "ascii"))
        header = pcd_reader.read_pcd_header(path)
        assert header["fields"] == ["x", "y", "z", "rgb"]
        assert header["sizes"] == [4, 4, 4, 4]
        assert header["types"] == ["F", "F", "F", "F"]
        assert header["counts"] == [1, 1, 1, 1]
        assert header["points"] == 3
        assert header["width"] == 3 and header["height"] == 1
        assert header["data"] == "ascii"
        assert header["version"] == "0.7"

    def test_missing_data_line(self, tmp_path):
        path = _write(tmp_path, "a.pcd", b"VERSION 0.7\nFIELDS x y z\nSIZE 4 4 4\nTYPE F F F\n")
        with pytest.raises(pcd_reader.PcdError, match="DATA"):
            pcd_reader.read_pcd_header(path)


@pytest.mark.unit
class TestPcdBody:
    def test_ascii_with_packed_rgb(self, tmp_path):
        body = (f"0 0 0 {_packed_rgb(255, 0, 0)!r}\n1 2 3 {_packed_rgb(0, 128, 0)!r}\n"
                f"nan nan nan 0\n").encode("ascii")
        path = _write(tmp_path, "a.pcd", _pcd_header("x y z rgb", "4 4 4 4", "F F F F", "1 1 1 1", 3, "ascii") + body)
        points, colors, header = pcd_reader.read_pcd(path)
        assert points.shape == (2, 3), "the NaN row is dropped"
        assert points.dtype == np.float64
        assert points[1].tolist() == [1.0, 2.0, 3.0]
        assert colors.dtype == np.uint8
        assert colors.tolist() == [[255, 0, 0], [0, 128, 0]]
        assert header["points"] == 3

    def test_binary_with_intensity_and_no_colour(self, tmp_path):
        rows = [(0.0, 0.0, 0.0, 10.0), (4.0, 5.0, 6.0, 20.0)]
        body = b"".join(struct.pack("<ffff", *row) for row in rows)
        path = _write(tmp_path, "a.pcd", _pcd_header("x y z intensity", "4 4 4 4", "F F F F", "1 1 1 1", 2, "binary") + body)
        points, colors, _ = pcd_reader.read_pcd(path)
        assert points.tolist() == [[0.0, 0.0, 0.0], [4.0, 5.0, 6.0]]
        assert colors is None

    def test_binary_compressed_is_field_major(self, tmp_path):
        xs, ys, zs = (1.0, 2.0), (3.0, 4.0), (5.0, 6.0)
        raw = struct.pack("<ff", *xs) + struct.pack("<ff", *ys) + struct.pack("<ff", *zs)
        compressed = _lzf_literals(raw)
        body = struct.pack("<II", len(compressed), len(raw)) + compressed
        path = _write(tmp_path, "a.pcd", _pcd_header("x y z", "4 4 4", "F F F", "1 1 1", 2, "binary_compressed") + body)
        points, colors, _ = pcd_reader.read_pcd(path)
        assert points.tolist() == [[1.0, 3.0, 5.0], [2.0, 4.0, 6.0]]
        assert colors is None

    def test_separate_rgb_byte_fields(self, tmp_path):
        body = b"".join(struct.pack("<fffBBB", 0.0, 0.0, float(i), 10 * i, 20 * i, 30 * i) for i in range(2))
        path = _write(tmp_path, "a.pcd", _pcd_header("x y z r g b", "4 4 4 1 1 1", "F F F U U U", "1 1 1 1 1 1", 2, "binary") + body)
        points, colors, _ = pcd_reader.read_pcd(path)
        assert points.shape == (2, 3)
        assert colors.tolist() == [[0, 0, 0], [10, 20, 30]]

    def test_a_cloud_without_xyz_is_refused(self, tmp_path):
        path = _write(tmp_path, "a.pcd", _pcd_header("u v", "4 4", "F F", "1 1", 1, "ascii") + b"0 0\n")
        with pytest.raises(pcd_reader.PcdError, match="x, y, z"):
            pcd_reader.read_pcd(path)


@pytest.mark.unit
class TestXyz:
    def test_reads_xyz_with_integer_colours(self, tmp_path):
        data = b"# comment\n0 0 0 255 0 0\n1, 1, 1, 0, 255, 0\n\n2 2 2 0 0 255\n"
        points, colors = pointcloud_handler._load_xyz(_write(tmp_path, "a.xyz", data))
        assert points.tolist() == [[0, 0, 0], [1, 1, 1], [2, 2, 2]]
        assert colors.dtype == np.uint8
        assert colors.tolist() == [[255, 0, 0], [0, 255, 0], [0, 0, 255]]

    def test_normals_are_not_mistaken_for_colours(self, tmp_path):
        data = b"0 0 0 0.0 0.0 1.0\n1 1 1 0.0 1.0 0.0\n"
        points, colors = pointcloud_handler._load_xyz(_write(tmp_path, "a.xyz", data))
        assert len(points) == 2
        assert colors is None

    def test_is_strided_to_the_cap(self, tmp_path):
        data = b"".join(b"%d 0 0\n" % i for i in range(1000))
        points, _ = pointcloud_handler._load_xyz(_write(tmp_path, "a.xyz", data), max_points=100)
        assert len(points) == 100
        assert points[0, 0] == 0.0 and points[-1, 0] == 990.0

    def test_empty_file_is_refused(self, tmp_path):
        with pytest.raises(ValueError, match="No points"):
            pointcloud_handler._load_xyz(_write(tmp_path, "a.xyz", b"# nothing\n"))


@pytest.mark.unit
class TestLoadPoints:
    def test_dispatches_xyz_and_caps(self, tmp_path):
        data = b"".join(b"%d 0 0\n" % i for i in range(50))
        path = _write(tmp_path, "a.xyz", data)
        points, colors = pointcloud_handler.load_points(path, max_points=10)
        assert len(points) == 10
        assert colors is None

    def test_the_cap_subsamples_colours_with_points(self):
        points = np.arange(300, dtype=np.float64).reshape(100, 3)
        colors = np.arange(300, dtype=np.uint8).reshape(100, 3)
        capped_points, capped_colors = pointcloud_handler.cap_points(points, colors, 10)
        assert len(capped_points) == len(capped_colors) == 10
        # the colour rows follow their points
        for p, c in zip(capped_points, capped_colors):
            assert c.tolist() == [int(v) % 256 for v in p]

    def test_load_builds_polydata_with_rgb(self, tmp_path):
        data = b"0 0 0 1 2 3\n1 1 1 4 5 6\n"
        path = _write(tmp_path, "a.xyz", data)
        made = {}

        def _polydata(points):
            made["points"] = points
            return SimpleNamespace(points=points, point_data={})

        with patch.object(pointcloud_handler, "pv", SimpleNamespace(PolyData=_polydata)):
            cloud = pointcloud_handler.load(path)
        assert made["points"].shape == (2, 3)
        assert cloud.point_data["RGB"].tolist() == [[1, 2, 3], [4, 5, 6]]

    def test_supported_extensions_and_loader_names(self):
        assert ".xyz" in pointcloud_handler.SUPPORTED_EXTENSIONS
        assert ".pcd" in pointcloud_handler.SUPPORTED_EXTENSIONS
        assert pointcloud_handler.LOADERS[".pcd"] == "pcd_reader"
        assert pointcloud_handler.LOADERS[".las"] == pointcloud_handler.LOADERS[".laz"] == "laspy"
        assert pointcloud_handler.LOADERS[".e57"] == "pye57"
        assert set(pointcloud_handler.LOADERS) == pointcloud_handler.SUPPORTED_EXTENSIONS


@pytest.mark.unit
class TestFaro:
    def test_names_the_conversion_route_without_open3d(self, tmp_path, monkeypatch):
        monkeypatch.setitem(sys.modules, "open3d", None)  # an import would raise ImportError
        path = _write(tmp_path, "scan.fls", b"\0" * 16)
        with pytest.raises(ValueError, match="proprietary conversion"):
            pointcloud_handler.load_points(path)


@pytest.mark.unit
class TestOpen3dConfinement:
    def test_open3d_is_imported_only_by_the_fbx_fallback(self):
        """The Lambda image ships no Open3D; every other loader must not reach for it."""
        hits = []
        for root, _dirs, files in os.walk(PACKAGE_DIR):
            for name in files:
                if not name.endswith(".py"):
                    continue
                path = os.path.join(root, name)
                with open(path, encoding="utf-8") as handle:
                    for number, line in enumerate(handle, start=1):
                        if "open3d" in line and not line.lstrip().startswith("#"):
                            hits.append((os.path.relpath(path, PACKAGE_DIR).replace("\\", "/"), number, line.strip()))
        files_hit = {h[0] for h in hits}
        assert files_hit == {"format_handlers/mesh_handler.py"}, hits
        for _file, _number, line in hits:
            assert "fbx" in line.lower() or "import open3d" in line or "Open3D" in line, line
