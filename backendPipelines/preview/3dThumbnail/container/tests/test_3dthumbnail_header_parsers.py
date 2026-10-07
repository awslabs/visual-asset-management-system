#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Header-only readers for the formats whose attributes come from a header, not from loading geometry.

A .ply is a mesh, a Gaussian splat or a point cloud depending on what its header declares, and the three
route to different loaders; the splat containers (.splat, .spz, .sog) carry their counts in a fixed
record size, a gzip'd header and a zipped meta.json respectively. Pure Python, so they run anywhere."""

import gzip
import io
import json
import struct
import zipfile

import pytest

from preview_pipeline.analysis import headers


def _write(tmp_path, name, data):
    path = tmp_path / name
    path.write_bytes(data)
    return str(path)


PLY_MESH = (b"ply\nformat ascii 1.0\ncomment made by a test\n"
            b"element vertex 8\nproperty float x\nproperty float y\nproperty float z\n"
            b"element face 12\nproperty list uchar int vertex_indices\nend_header\n0 0 0\n")

PLY_SPLAT = (b"ply\nformat binary_little_endian 1.0\nelement vertex 3\n"
             b"property float x\nproperty float y\nproperty float z\n"
             b"property float f_dc_0\nproperty float f_dc_1\nproperty float f_dc_2\n"
             + b"".join(b"property float f_rest_%d\n" % i for i in range(9))
             + b"property float opacity\nend_header\n")

PLY_CLOUD = (b"ply\nformat binary_big_endian 1.0\nobj_info scanner\nelement vertex 5\n"
             b"property double x\nproperty double y\nproperty double z\n"
             b"property uchar red\nproperty uchar green\nproperty uchar blue\nend_header\n")


@pytest.mark.unit
class TestPlyHeader:
    def test_parses_elements_properties_and_format(self, tmp_path):
        header = headers.read_ply_header(_write(tmp_path, "m.ply", PLY_MESH))
        assert header["format"] == "ascii"
        assert header["version"] == "1.0"
        assert header["elements"]["vertex"] == {"count": 8, "properties": ["x", "y", "z"]}
        assert header["elements"]["face"] == {"count": 12, "properties": ["vertex_indices"]}
        assert header["headerBytes"] == PLY_MESH.index(b"end_header\n") + len(b"end_header\n")

    def test_kind_is_mesh_when_faces_are_declared(self, tmp_path):
        assert headers.sniff_ply(_write(tmp_path, "m.ply", PLY_MESH)) == "mesh"

    def test_kind_is_splat_when_the_vertex_carries_sh_colour(self, tmp_path):
        path = _write(tmp_path, "s.ply", PLY_SPLAT)
        assert headers.sniff_ply(path) == "splat"
        assert headers.ply_sh_degree(headers.read_ply_header(path)["elements"]["vertex"]["properties"]) == 1

    def test_kind_is_pointcloud_otherwise(self, tmp_path):
        assert headers.sniff_ply(_write(tmp_path, "c.ply", PLY_CLOUD)) == "pointcloud"

    @pytest.mark.parametrize("rest,degree", [(0, 0), (9, 1), (24, 2), (45, 3), (7, None)])
    def test_sh_degree_from_the_rest_coefficient_count(self, rest, degree):
        props = ["x", "y", "z", "f_dc_0"] + [f"f_rest_{i}" for i in range(rest)]
        assert headers.ply_sh_degree(props) == degree

    def test_not_a_ply(self, tmp_path):
        with pytest.raises(headers.HeaderError, match="PLY"):
            headers.read_ply_header(_write(tmp_path, "x.ply", b"solid ascii\n"))

    def test_unterminated_header(self, tmp_path):
        with pytest.raises(headers.HeaderError, match="end_header"):
            headers.read_ply_header(_write(tmp_path, "x.ply", b"ply\nformat ascii 1.0\nelement vertex 1\n"))


@pytest.mark.unit
class TestSplatContainers:
    def test_splat_count_is_the_size_over_the_record_length(self, tmp_path):
        assert headers.read_splat_header(_write(tmp_path, "a.splat", b"\0" * 64)) == {
            "points": 2, "recordBytes": 32}

    def test_splat_with_a_partial_record_is_refused(self, tmp_path):
        with pytest.raises(headers.HeaderError, match="32"):
            headers.read_splat_header(_write(tmp_path, "a.splat", b"\0" * 33))

    def test_spz_header_is_read_through_gzip(self, tmp_path):
        raw = struct.pack("<IIIBBBB", headers.SPZ_MAGIC, 2, 1234, 3, 12, 1, 0) + b"\0" * 40
        assert headers.read_spz_header(_write(tmp_path, "a.spz", gzip.compress(raw))) == {
            "version": 2, "points": 1234, "shDegree": 3, "fractionalBits": 12, "antialiased": True}

    def test_spz_with_the_wrong_magic_is_refused(self, tmp_path):
        raw = struct.pack("<IIIBBBB", 0x11111111, 2, 1, 0, 12, 0, 0)
        with pytest.raises(headers.HeaderError, match="magic"):
            headers.read_spz_header(_write(tmp_path, "a.spz", gzip.compress(raw)))

    def test_sog_bundle_meta(self, tmp_path):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("meta.json", json.dumps({"version": 2, "count": 77, "means": {}, "shN": {}}))
            zf.writestr("means_l.webp", b"\0")
        assert headers.read_sog_meta(_write(tmp_path, "a.sog", buf.getvalue())) == {
            "points": 77, "version": 2, "hasHigherOrderSh": True, "members": 2}

    def test_sog_as_a_bare_meta_json(self, tmp_path):
        data = json.dumps({"version": 1, "count": 5}).encode()
        assert headers.read_sog_meta(_write(tmp_path, "meta.sog", data)) == {
            "points": 5, "version": 1, "hasHigherOrderSh": False, "members": 1}

    def test_sog_without_meta_is_refused(self, tmp_path):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("other.json", "{}")
        with pytest.raises(headers.HeaderError, match="meta.json"):
            headers.read_sog_meta(_write(tmp_path, "a.sog", buf.getvalue()))


@pytest.mark.unit
class TestPtxHeader:
    def test_columns_rows_and_point_count(self, tmp_path):
        data = b"4\n3\n0 0 0\n1 0 0\n0 1 0\n0 0 1\n1 0 0 0\n0 1 0 0\n0 0 1 0\n0 0 0 1\n1 2 3\n"
        assert headers.read_ptx_header(_write(tmp_path, "a.ptx", data)) == {
            "columns": 4, "rows": 3, "points": 12}

    def test_invalid_header(self, tmp_path):
        with pytest.raises(headers.HeaderError, match="PTX"):
            headers.read_ptx_header(_write(tmp_path, "a.ptx", b"not\na header\n"))
