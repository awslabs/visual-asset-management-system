#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""An input whose text is not UTF-8 fails with a message saying it could not be read (#910).

trimesh reads the text of an OBJ, ASCII STL, XYZ or glTF file, and a GLB's JSON chunk, as UTF-8, and
falls back to charset_normalizer, which the image does not install, so such an input used to end the
run with "No module named 'charset_normalizer'". ``check_input_text`` reads that text as UTF-8 before
anything is fetched or loaded. A binary STL, whose header trimesh only reads as metadata, is not
checked, nor is a GLB's binary chunk or any other format.

``test_3dbasic_real_conversions.py`` runs the same inputs through trimesh itself.
"""

import io
import json
import os
import struct
import sys
import types
import importlib.util
from unittest.mock import MagicMock, patch

import pytest

_LAMBDA_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _LAMBDA_DIR not in sys.path:
    sys.path.insert(0, _LAMBDA_DIR)

if "common" not in sys.modules:
    _common_pkg = types.ModuleType("common")
    _common_logger = types.ModuleType("common.logger")
    _common_logger.safeLogger = lambda **kw: MagicMock()
    _common_pkg.logger = _common_logger
    sys.modules["common"] = _common_pkg
    sys.modules["common.logger"] = _common_logger
if "trimesh" not in sys.modules:
    sys.modules["trimesh"] = MagicMock()

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_REGION", "us-east-1")


def _load():
    spec = importlib.util.spec_from_file_location(
        "trimesh_pipeline_lambda_input_text", os.path.join(_LAMBDA_DIR, "lambda.py"))
    module = importlib.util.module_from_spec(spec)
    with patch("boto3.client", MagicMock()), patch("boto3.resource", MagicMock()):
        spec.loader.exec_module(module)
    return module


FACES = "v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n"
# 'Würfel' in Windows-1252: the 'ü' is the single byte 0xFC, which no UTF-8 sequence starts with.
LATIN1_OBJ = ("# W\u00fcrfel\no W\u00fcrfel\n" + FACES).encode("cp1252")
LATIN1_OFFSET = len(b"# W")
NOISE = bytes(range(256)) * 8


def _binary_stl(header=b"", triangles=1):
    return header.ljust(80, b" ") + struct.pack("<I", triangles) + b"\x00" * (50 * triangles)


def _glb(document_bytes, binary=b""):
    """A GLB whose JSON chunk holds ``document_bytes`` as given, plus an optional binary chunk."""
    json_chunk = document_bytes + b" " * (-len(document_bytes) % 4)
    body = struct.pack("<I4s", len(json_chunk), b"JSON") + json_chunk
    if binary:
        binary_chunk = binary + b"\x00" * (-len(binary) % 4)
        body += struct.pack("<I4s", len(binary_chunk), b"BIN\x00") + binary_chunk
    return struct.pack("<4sII", b"glTF", 2, 12 + len(body)) + body


def _write(tmp_path, name, data):
    path = tmp_path / name
    path.write_bytes(data)
    return str(path)


@pytest.mark.unit
class TestFirstNonUtf8Byte:
    def test_ascii_and_utf8_text_pass(self):
        mod = _load()
        assert mod.first_non_utf8_byte(io.BytesIO(FACES.encode("ascii"))) is None
        assert mod.first_non_utf8_byte(io.BytesIO("o W\u00fcrfel \u7acb\u65b9\u4f53\n".encode("utf-8"))) is None

    def test_the_first_byte_that_is_not_utf8_is_named(self):
        assert _load().first_non_utf8_byte(io.BytesIO(LATIN1_OBJ)) == (LATIN1_OFFSET, 0xFC)

    @pytest.mark.parametrize("chunk_size", [1, 2, 3, 4, 5, 7])
    def test_a_character_split_between_reads_is_decoded_whole(self, chunk_size):
        mod = _load()
        text = "a\u00e9\u20ac\U0001f600b".encode("utf-8")
        assert mod.first_non_utf8_byte(io.BytesIO(text), chunk_size=chunk_size) is None
        assert mod.first_non_utf8_byte(io.BytesIO(text + b"\xff"), chunk_size=chunk_size) == (len(text), 0xFF)

    def test_a_character_cut_off_at_the_end_is_not_utf8(self):
        assert _load().first_non_utf8_byte(io.BytesIO(b"ab\xc3")) == (2, 0xC3)

    def test_only_the_given_length_is_read(self):
        mod = _load()
        handle = io.BytesIO(b"ok\xff")
        assert mod.first_non_utf8_byte(handle, length=2) is None
        assert handle.tell() == 2

    def test_an_empty_input_passes(self):
        assert _load().first_non_utf8_byte(io.BytesIO(b"")) is None


@pytest.mark.unit
class TestCheckInputText:
    def test_the_error_is_a_value_error(self):
        assert issubclass(_load().InputTextError, ValueError)

    @pytest.mark.parametrize("data", [LATIN1_OBJ, NOISE, ("# x\n" + FACES).encode("utf-16")],
                             ids=["latin1", "binary", "utf16"])
    def test_an_obj_that_is_not_utf8_text_fails_naming_the_file(self, tmp_path, data):
        mod = _load()
        path = _write(tmp_path, "w.obj", data)
        with pytest.raises(mod.InputTextError, match=r"^w\.obj could not be read as OBJ text: byte 0x[0-9A-F]{2} "
                                                     r"at offset \d+ is not UTF-8\. .*UTF-8 OBJ text") as raised:
            mod.check_input_text(path, ".obj", "w.obj")
        assert "charset" not in str(raised.value)

    def test_the_offset_and_byte_are_the_first_that_is_not_utf8(self, tmp_path):
        mod = _load()
        with pytest.raises(mod.InputTextError, match=f"byte 0xFC at offset {LATIN1_OFFSET} is not UTF-8"):
            mod.check_input_text(_write(tmp_path, "w.obj", LATIN1_OBJ), ".obj", "w.obj")

    def test_an_obj_in_utf8_with_non_ascii_names_passes(self, tmp_path):
        data = ("# W\u00fcrfel\no W\u00fcrfel\n" + FACES).encode("utf-8")
        _load().check_input_text(_write(tmp_path, "w.obj", data), ".obj", "w.obj")

    def test_xyz_and_gltf_text_are_checked(self, tmp_path):
        mod = _load()
        xyz = _write(tmp_path, "c.xyz", "# W\u00fcrfel\n0 0 0\n".encode("cp1252"))
        with pytest.raises(mod.InputTextError, match=r"^c\.xyz could not be read as XYZ text"):
            mod.check_input_text(xyz, ".xyz", "c.xyz")
        gltf = _write(tmp_path, "m.gltf", json.dumps({"asset": {"version": "2.0", "generator": "W\u00fcrfel"}},
                                                     ensure_ascii=False).encode("cp1252"))
        with pytest.raises(mod.InputTextError, match=r"^m\.gltf could not be read as glTF JSON"):
            mod.check_input_text(gltf, ".gltf", "m.gltf")

    def test_a_binary_stl_with_a_header_that_is_not_utf8_passes(self, tmp_path):
        data = _binary_stl("W\u00fcrfel".encode("cp1252"), triangles=2)
        _load().check_input_text(_write(tmp_path, "b.stl", data), ".stl", "b.stl")

    @pytest.mark.parametrize("data", [
        NOISE,
        "solid W\u00fcrfel\nendsolid W\u00fcrfel\n".encode("cp1252"),
        b"\xff" * 10,
        # Laid out as binary but one byte short, so trimesh reads it as ASCII text.
        _binary_stl(b"\xff", triangles=1)[:-1],
    ], ids=["binary-noise", "latin1-ascii", "shorter-than-a-header", "binary-length-mismatch"])
    def test_an_stl_that_is_neither_binary_nor_utf8_text_fails(self, tmp_path, data):
        mod = _load()
        with pytest.raises(mod.InputTextError, match=r"^s\.stl could not be read as STL: it is not a binary STL"):
            mod.check_input_text(_write(tmp_path, "s.stl", data), ".stl", "s.stl")

    def test_an_ascii_stl_in_utf8_passes(self, tmp_path):
        data = b"solid s\nfacet normal 0 0 1\nouter loop\nvertex 0 0 0\nvertex 1 0 0\nvertex 0 1 0\n" \
               b"endloop\nendfacet\nendsolid s\n"
        _load().check_input_text(_write(tmp_path, "s.stl", data), ".stl", "s.stl")

    def test_a_glb_json_chunk_that_is_not_utf8_fails_with_its_file_offset(self, tmp_path):
        mod = _load()
        document = '{"asset":{"version":"2.0","generator":"W\u00fcrfel"}}'.encode("cp1252")
        offset = 20 + document.index(b"\xfc")
        with pytest.raises(mod.InputTextError, match=rf"^g\.glb could not be read as GLB: in its JSON chunk, "
                                                     rf"byte 0xFC at offset {offset} is not UTF-8"):
            mod.check_input_text(_write(tmp_path, "g.glb", _glb(document)), ".glb", "g.glb")

    def test_a_glb_binary_chunk_is_not_checked(self, tmp_path):
        data = _glb(b'{"asset":{"version":"2.0"}}', binary=NOISE)
        _load().check_input_text(_write(tmp_path, "g.glb", data), ".glb", "g.glb")

    @pytest.mark.parametrize("data", [NOISE, b"glTF", struct.pack("<4sIII4s", b"glTF", 1, 0, 4, b"JSON") + b"\xff" * 4])
    def test_a_file_without_a_glb_header_is_left_to_the_loader(self, tmp_path, data):
        _load().check_input_text(_write(tmp_path, "g.glb", data), ".glb", "g.glb")

    def test_a_ply_is_left_to_the_loader(self, tmp_path):
        _load().check_input_text(_write(tmp_path, "p.ply", NOISE), ".ply", "p.ply")


@pytest.mark.unit
class TestTheHandlerChecksBeforeFetchingOrLoading:
    def test_a_latin1_obj_fails_before_its_references_are_fetched_or_it_is_loaded(self):
        mod = _load()

        def _download(bucket, key, path):
            with open(path, "wb") as handle:
                handle.write(b"mtllib w.mtl\n" + LATIN1_OBJ)
            return path

        trimesh_stub = MagicMock()
        fetch = MagicMock()
        upload = MagicMock()
        with patch.object(mod, "download", MagicMock(side_effect=_download)), \
                patch.object(mod, "uploadV2", upload), \
                patch.object(mod, "trimesh", trimesh_stub), \
                patch.object(mod.referenced_files, "fetch_referenced_files", fetch):
            with pytest.raises(mod.InputTextError, match=r"^w\.obj could not be read as OBJ text"):
                mod.convert_input_output("s3://abkt/xid/w.obj", "s3://obkt/files/", ".glb")
        fetch.assert_not_called()
        trimesh_stub.load.assert_not_called()
        upload.assert_not_called()
