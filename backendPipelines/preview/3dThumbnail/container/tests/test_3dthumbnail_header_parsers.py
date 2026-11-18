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


# headers.SOG_META_MAX_BYTES, written out so a test of the limit does not move with it.
_SOG_META_LIMIT_BYTES = 16 * 1024 ** 2


def _write(tmp_path, name, data):
    path = tmp_path / name
    path.write_bytes(data)
    return str(path)


# SPZ fixtures in both layouts (nianticlabs/spz README, "File Format"). The payload after the header is not
# real splat data: the reader takes the header only and never decompresses a stream.
_ZSTD_FRAME_MAGIC = struct.pack("<I", 0xFD2FB528)


def spz_v3_bytes(points, sh_degree, flags=0, fractional_bits=12):
    """A version 3 .spz: one gzip stream holding the 16-byte header and the attribute arrays."""
    header = struct.pack("<IIIBBBB", 0x5053474E, 3, points, sh_degree, fractional_bits, flags, 0)
    return gzip.compress(header + b"\x11" * (points % 97 + 32), mtime=0)


def spz_v4_bytes(points, sh_degree, flags=0, fractional_bits=12, extension=b""):
    """A version 4 .spz: the 32-byte plaintext NgspFileHeader, optional extension records, a TOC of
    (compressedSize u64, uncompressedSize u64) per stream, then one ZSTD frame per stream."""
    streams = 6 if sh_degree else 5
    toc_offset = 32 + len(extension)
    header = struct.pack("<IIIBBBBI12s", 0x5053474E, 4, points, sh_degree, fractional_bits, flags, streams,
                         toc_offset, b"\0" * 12)
    frames = [_ZSTD_FRAME_MAGIC + bytes([n]) * 8 for n in range(streams)]
    toc = b"".join(struct.pack("<QQ", len(f), 64) for f in frames)
    return header + extension + toc + b"".join(frames)


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

    def test_spz_v3_gzip_layout(self, tmp_path):
        """The legacy layout (versions 1-3): the whole file is one gzip stream, header first."""
        path = _write(tmp_path, "unicorn-v3.spz", spz_v3_bytes(points=49602, sh_degree=3))
        with open(path, "rb") as handle:
            assert handle.read(2) == b"\x1f\x8b"  # control: the fixture is a gzip stream
        assert headers.read_spz_header(path) == {
            "version": 3, "points": 49602, "shDegree": 3, "fractionalBits": 12, "antialiased": False}

    def test_spz_v4_plaintext_ngsp_layout(self, tmp_path):
        """Version 4 (D131): a 32-byte plaintext NgspFileHeader, then the TOC and the ZSTD streams. The
        file starts with the bytes NGSP, not gzip, so a gzip read fails with BadGzipFile."""
        path = _write(tmp_path, "unicorn-v4.spz", spz_v4_bytes(points=49602, sh_degree=3, flags=1))
        with open(path, "rb") as handle:
            assert handle.read(8) == b"NGSP\x04\x00\x00\x00"  # control: the layout the live file had
        with pytest.raises(gzip.BadGzipFile):
            with gzip.open(path, "rb") as handle:
                handle.read(16)
        assert headers.read_spz_header(path) == {
            "version": 4, "points": 49602, "shDegree": 3, "fractionalBits": 12, "antialiased": True}

    def test_spz_v4_with_header_extensions_still_reads_the_fixed_header(self, tmp_path):
        # flags 0x2: extension records sit between the header and the TOC; the fixed header is unchanged.
        data = spz_v4_bytes(points=7, sh_degree=0, flags=2, extension=b"\x01\x00\x00\x00\x04\x00\x00\x00abcd")
        assert headers.read_spz_header(_write(tmp_path, "ext.spz", data))["points"] == 7

    @pytest.mark.parametrize("data,needle", [
        (b"NGSP\x04\x00\x00\x00\x07", "shorter than its version 4 header"),
        (struct.pack("<IIIBBBBI12s", headers.SPZ_MAGIC, 3, 1, 0, 12, 0, 6, 32, b"\0" * 12), "versions 1-3"),
        (b"PK\x03\x04" + b"\0" * 40, "neither a gzip stream"),
        (b"", "neither a gzip stream"),
        (gzip.compress(b"\0" * 64, mtime=0)[:12], "gzip stream could not be read"),
    ], ids=["v4-truncated", "ngsp-naming-v3", "zip-bytes", "empty", "gzip-truncated"])
    def test_a_malformed_spz_is_a_header_error(self, tmp_path, data, needle):
        with pytest.raises(headers.HeaderError, match=needle):
            headers.read_spz_header(_write(tmp_path, "bad.spz", data))

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

    def test_a_sog_bundle_meta_over_the_size_limit_is_not_read(self, tmp_path):
        # Valid JSON padded past the limit: checked from the central directory, refused before decompressing.
        meta = '{"version": 2, "count": 77}' + " " * _SOG_META_LIMIT_BYTES
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("meta.json", meta)
        with pytest.raises(headers.HeaderError, match="meta.json was not read"):
            headers.read_sog_meta(_write(tmp_path, "a.sog", buf.getvalue()))

    def test_a_bare_sog_meta_over_the_size_limit_is_not_read(self, tmp_path):
        data = b'{"version": 1, "count": 5}' + b" " * _SOG_META_LIMIT_BYTES
        with pytest.raises(headers.HeaderError, match="meta.json was not read"):
            headers.read_sog_meta(_write(tmp_path, "meta.sog", data))

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
