#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""A mesh input whose text is not UTF-8 fails with a message naming the file (#1044).

trimesh reads the text of an OBJ, ASCII STL, XYZ or glTF file, and a GLB's JSON chunk, as UTF-8, and
falls back to charset_normalizer, which the image does not install, so such an input used to end the
run with "No module named 'charset_normalizer'". ``check_input_text`` reads that text as UTF-8 after the
download and before trimesh loads the file. A binary STL, whose header trimesh only reads as metadata, is
not checked, nor is a GLB's binary chunk, a PLY or a CAD file.

``TestRealTrimesh`` runs the inputs through the pinned trimesh in a child interpreter, since sibling
modules stub ``trimesh`` in this process.
"""

import base64
import importlib.util
import io
import json
import os
import struct
import subprocess
import sys
import types
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
if "metadata_extractors" not in sys.modules:
    _extractors = types.ModuleType("metadata_extractors")
    _extractors.extract_cad_metadata = MagicMock(return_value={})
    _extractors.extract_mesh_metadata = MagicMock(return_value={"volume": 1.5})
    _extractors.get_handler_for_format = MagicMock(return_value="mesh")
    sys.modules["metadata_extractors"] = _extractors
if "cadquery" not in sys.modules:
    sys.modules["cadquery"] = MagicMock()

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_REGION", "us-east-1")


def _load_input_text():
    spec = importlib.util.spec_from_file_location(
        "meshcad_input_text", os.path.join(_LAMBDA_DIR, "input_text.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_handler():
    spec = importlib.util.spec_from_file_location(
        "meshcad_pipeline_lambda_input_text", os.path.join(_LAMBDA_DIR, "lambda.py"))
    module = importlib.util.module_from_spec(spec)
    with patch("boto3.client", MagicMock()), patch("boto3.resource", MagicMock()):
        spec.loader.exec_module(module)
    return module


FACES = "v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n"
# 'Würfel' in Latin-1: the 'ü' is the single byte 0xFC, which no UTF-8 sequence starts with.
LATIN1_OBJ = ("# W\u00fcrfel\no W\u00fcrfel\n" + FACES).encode("latin-1")
LATIN1_OFFSET = len(b"# W")
UTF8_OBJ = ("# W\u00fcrfel \u7acb\u65b9\u4f53\no W\u00fcrfel\n" + FACES).encode("utf-8")
_STL_FACET = ("facet normal 0 0 1\nouter loop\nvertex 0 0 0\nvertex 1 0 0\nvertex 0 1 0\nendloop\n"
              "endfacet\n")
LATIN1_STL = ("solid W\u00fcrfel\n" + _STL_FACET + "endsolid W\u00fcrfel\n").encode("latin-1")
UTF8_STL = ("solid W\u00fcrfel\n" + _STL_FACET + "endsolid W\u00fcrfel\n").encode("utf-8")
LATIN1_XYZ = "# W\u00fcrfel\n0 0 0\n1 0 0\n0 1 0\n".encode("latin-1")
# Random bytes under a mesh name; 0x80 is the first byte that cannot start a UTF-8 sequence.
NOISE = bytes(range(256)) * 8
NOISE_OFFSET = 0x80


def _binary_stl(header=b"", triangles=1):
    facet = struct.pack("<12fH", 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0)
    return header.ljust(80, b" ") + struct.pack("<I", triangles) + facet * triangles


def _gltf(name):
    """A one-triangle glTF document whose node is called ``name``, with its buffer embedded."""
    positions = struct.pack("<9f", 0, 0, 0, 1, 0, 0, 0, 1, 0)
    return {
        "asset": {"version": "2.0"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0, "name": name}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}}]}],
        "accessors": [{"bufferView": 0, "componentType": 5126, "count": 3, "type": "VEC3",
                       "min": [0, 0, 0], "max": [1, 1, 0]}],
        "bufferViews": [{"buffer": 0, "byteLength": len(positions)}],
        "buffers": [{"byteLength": len(positions),
                     "uri": "data:application/octet-stream;base64," + base64.b64encode(positions).decode()}],
    }


def _gltf_bytes(encoding):
    return json.dumps(_gltf("W\u00fcrfel"), ensure_ascii=False).encode(encoding)


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
        mod = _load_input_text()
        assert mod.first_non_utf8_byte(io.BytesIO(FACES.encode("ascii"))) is None
        assert mod.first_non_utf8_byte(io.BytesIO(UTF8_OBJ)) is None

    def test_the_first_byte_that_is_not_utf8_is_named(self):
        assert _load_input_text().first_non_utf8_byte(io.BytesIO(LATIN1_OBJ)) == (LATIN1_OFFSET, 0xFC)

    @pytest.mark.parametrize("chunk_size", [1, 2, 3, 4, 5, 7])
    def test_a_character_split_between_reads_is_decoded_whole(self, chunk_size):
        mod = _load_input_text()
        text = "a\u00e9\u20ac\U0001f600b".encode("utf-8")
        assert mod.first_non_utf8_byte(io.BytesIO(text), chunk_size=chunk_size) is None
        assert mod.first_non_utf8_byte(io.BytesIO(text + b"\xff"), chunk_size=chunk_size) == (len(text), 0xFF)

    def test_a_character_cut_off_at_the_end_is_not_utf8(self):
        assert _load_input_text().first_non_utf8_byte(io.BytesIO(b"ab\xc3")) == (2, 0xC3)

    def test_only_the_given_length_is_read(self):
        mod = _load_input_text()
        handle = io.BytesIO(b"ok\xff")
        assert mod.first_non_utf8_byte(handle, length=2) is None
        assert handle.tell() == 2

    def test_an_empty_input_passes(self):
        assert _load_input_text().first_non_utf8_byte(io.BytesIO(b"")) is None


@pytest.mark.unit
class TestCheckInputText:
    def test_the_error_is_a_value_error(self):
        assert issubclass(_load_input_text().InputTextError, ValueError)

    @pytest.mark.parametrize("name,data,offset,byte,phrase", [
        ("model.obj", LATIN1_OBJ, LATIN1_OFFSET, 0xFC, "could not be read as OBJ text"),
        ("noise.obj", NOISE, NOISE_OFFSET, 0x80, "could not be read as OBJ text"),
        ("model.stl", LATIN1_STL, len(b"solid W"), 0xFC, "it is not a binary STL"),
        ("noise.stl", NOISE, NOISE_OFFSET, 0x80, "it is not a binary STL"),
        ("cloud.xyz", LATIN1_XYZ, LATIN1_OFFSET, 0xFC, "could not be read as XYZ text"),
        ("noise.xyz", NOISE, NOISE_OFFSET, 0x80, "could not be read as XYZ text"),
    ], ids=["latin1-obj", "random-obj", "latin1-ascii-stl", "random-stl", "latin1-xyz", "random-xyz"])
    def test_text_that_is_not_utf8_fails_naming_the_file(self, tmp_path, name, data, offset, byte, phrase):
        mod = _load_input_text()
        path = _write(tmp_path, "input" + os.path.splitext(name)[1], data)
        with pytest.raises(mod.InputTextError) as raised:
            mod.check_input_text(path, os.path.splitext(name)[1], name)
        message = str(raised.value)
        assert message.startswith(name + " ")
        assert phrase in message
        assert f"byte 0x{byte:02X} at offset {offset} is not UTF-8" in message
        assert "charset_normalizer" not in message
        assert "UTF-8" in message and "run the extraction again" in message

    def test_a_gltf_that_is_not_utf8_fails(self, tmp_path):
        mod = _load_input_text()
        path = _write(tmp_path, "input.gltf", _gltf_bytes("latin-1"))
        with pytest.raises(mod.InputTextError, match=r"^scene\.gltf could not be read as glTF JSON: byte 0xFC"):
            mod.check_input_text(path, ".gltf", "scene.gltf")

    def test_a_glb_whose_json_is_not_utf8_fails_at_the_offset_in_the_file(self, tmp_path):
        mod = _load_input_text()
        document = _gltf_bytes("latin-1")
        path = _write(tmp_path, "input.glb", _glb(document))
        expected = 20 + document.index(b"\xfc")
        with pytest.raises(mod.InputTextError, match=rf"^scene\.glb could not be read as GLB: in its JSON "
                                                     rf"chunk, byte 0xFC at offset {expected} "):
            mod.check_input_text(path, ".glb", "scene.glb")

    def test_the_extension_is_matched_without_regard_to_case(self, tmp_path):
        mod = _load_input_text()
        path = _write(tmp_path, "input.obj", LATIN1_OBJ)
        with pytest.raises(mod.InputTextError, match="MODEL.OBJ could not be read as OBJ text"):
            mod.check_input_text(path, ".OBJ", "MODEL.OBJ")

    @pytest.mark.parametrize("extension,data", [
        (".obj", UTF8_OBJ),
        (".obj", FACES.encode("ascii")),
        (".stl", UTF8_STL),
        (".xyz", "0 0 0\n1 0 0\n".encode("ascii")),
        (".gltf", _gltf_bytes("utf-8")),
        (".glb", _glb(_gltf_bytes("utf-8"))),
    ], ids=["utf8-obj", "ascii-obj", "utf8-ascii-stl", "ascii-xyz", "utf8-gltf", "utf8-glb"])
    def test_utf8_text_passes(self, tmp_path, extension, data):
        mod = _load_input_text()
        assert mod.check_input_text(_write(tmp_path, "input" + extension, data), extension, "m" + extension) is None

    def test_a_binary_stl_is_not_checked_even_with_a_header_that_is_not_utf8(self, tmp_path):
        mod = _load_input_text()
        path = _write(tmp_path, "input.stl", _binary_stl(header=b"W\xfcrfel \xff\xfe"))
        assert mod.check_input_text(path, ".stl", "part.stl") is None

    def test_a_glb_binary_chunk_is_not_checked(self, tmp_path):
        mod = _load_input_text()
        path = _write(tmp_path, "input.glb", _glb(_gltf_bytes("utf-8"), binary=NOISE))
        assert mod.check_input_text(path, ".glb", "scene.glb") is None

    def test_a_file_that_is_not_a_glb_is_left_to_the_loader(self, tmp_path):
        mod = _load_input_text()
        assert mod.check_input_text(_write(tmp_path, "input.glb", NOISE), ".glb", "scene.glb") is None

    @pytest.mark.parametrize("extension", [".ply", ".step", ".stp", ".dxf"])
    def test_other_formats_are_left_to_their_loader(self, tmp_path, extension):
        mod = _load_input_text()
        path = _write(tmp_path, "input" + extension, NOISE)
        assert mod.check_input_text(path, extension, "m" + extension) is None


_INPUT_BUCKET = "abkt"
_OUTPUT_PATH = "s3://obkt/pipelines/metaExtract/JOB/output/E1/metadata/"


def _extract(mod, relative_path, data, handler_type="mesh"):
    """Run extract_metadata on ``data`` stored under ``relative_path``; return the extractor mocks."""

    def _download(bucket, key, path):
        with open(path, "wb") as handle:
            handle.write(data)
        return path

    mesh = MagicMock(return_value={"volume": 1.5})
    cad = MagicMock(return_value={"volume": 1.5})
    with patch.object(mod, "download", MagicMock(side_effect=_download)), \
            patch.object(mod, "upload", MagicMock()), \
            patch.object(mod, "extract_mesh_metadata", mesh), \
            patch.object(mod, "extract_cad_metadata", cad), \
            patch.object(mod, "get_handler_for_format", MagicMock(return_value=handler_type)):
        mod.extract_metadata(relative_path, f"s3://{_INPUT_BUCKET}/xasset/{relative_path}", _OUTPUT_PATH)
    return mesh, cad


@pytest.mark.unit
class TestTheExtractionFailsBeforeTheLoad:
    @pytest.mark.parametrize("relative_path,data", [
        ("models/W\u00fcrfel.obj", LATIN1_OBJ),
        ("models/part.stl", LATIN1_STL),
        ("models/noise.obj", NOISE),
    ], ids=["latin1-obj", "latin1-ascii-stl", "random-bytes-obj"])
    def test_the_run_fails_naming_the_input_file(self, relative_path, data):
        with pytest.raises(Exception) as raised:
            _extract(_load_handler(), relative_path, data)
        message = str(raised.value)
        name = os.path.basename(relative_path)
        assert message.startswith(f"Attribute extraction failed: {name} could not be read as ")
        assert "is not UTF-8" in message
        assert "input." not in message, "the message names the working copy, not the user's file"

    def test_the_mesh_extractor_is_not_called(self):
        mod = _load_handler()
        mesh = MagicMock()

        def _download(bucket, key, path):
            with open(path, "wb") as handle:
                handle.write(LATIN1_OBJ)

        with patch.object(mod, "download", MagicMock(side_effect=_download)), \
                patch.object(mod, "upload", MagicMock()), \
                patch.object(mod, "extract_mesh_metadata", mesh), \
                patch.object(mod, "get_handler_for_format", MagicMock(return_value="mesh")):
            with pytest.raises(Exception, match="could not be read as OBJ text"):
                mod.extract_metadata("m.obj", f"s3://{_INPUT_BUCKET}/xasset/m.obj", _OUTPUT_PATH)
        mesh.assert_not_called()

    def test_a_utf8_mesh_is_extracted(self):
        mesh, _ = _extract(_load_handler(), "models/W\u00fcrfel.obj", UTF8_OBJ)
        mesh.assert_called_once()

    def test_a_cad_file_is_not_checked(self):
        _, cad = _extract(_load_handler(), "models/part.step", NOISE, handler_type="cad")
        cad.assert_called_once()


# Loads each input through the real mesh_extractor (and so the pinned trimesh) in a child interpreter,
# first unchecked, as the extraction did before #1044, then behind check_input_text. Prints one JSON
# line per input: the error each path raised, or None when it loaded.
_REAL_PROBE = r"""
import importlib.util, json, os, sys, types
from unittest.mock import MagicMock

lambda_dir, work_dir = sys.argv[1], sys.argv[2]
common = types.ModuleType("common")
common.logger = types.ModuleType("common.logger")
common.logger.safeLogger = lambda **kw: MagicMock()
sys.modules["common"] = common
sys.modules["common.logger"] = common.logger


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


extractor = load("mesh_extractor", os.path.join(lambda_dir, "metadata_extractors", "mesh_extractor.py"))
input_text = load("input_text", os.path.join(lambda_dir, "input_text.py"))
charset_normalizer = importlib.util.find_spec("charset_normalizer") is not None
for name in sorted(os.listdir(work_dir)):
    path = os.path.join(work_dir, name)
    result = {"name": name, "charset_normalizer": charset_normalizer}
    try:
        extractor.extract_mesh_metadata(path)
        result["unchecked"] = None
    except Exception as e:
        result["unchecked"] = str(e)
    try:
        input_text.check_input_text(path, os.path.splitext(name)[1], name)
        extractor.extract_mesh_metadata(path)
        result["checked"] = None
    except Exception as e:
        result["checked"] = str(e)
    print(json.dumps(result))
"""


@pytest.mark.unit
class TestRealTrimesh:
    """The inputs above, through the trimesh pinned in requirements.txt with nothing else added."""

    @pytest.fixture(scope="class")
    def results(self, tmp_path_factory):
        pytest.importorskip("trimesh")
        work_dir = tmp_path_factory.mktemp("real-meshes")
        for name, data in {
            "latin1.obj": LATIN1_OBJ,
            "latin1.stl": LATIN1_STL,
            "noise.obj": NOISE,
            "utf8.obj": UTF8_OBJ,
            "utf8.stl": UTF8_STL,
            "binary.stl": _binary_stl(header=b"W\xfcrfel \xff\xfe"),
            "utf8.glb": _glb(_gltf_bytes("utf-8")),
        }.items():
            (work_dir / name).write_bytes(data)
        completed = subprocess.run([sys.executable, "-c", _REAL_PROBE, _LAMBDA_DIR, str(work_dir)],
                                   capture_output=True, text=True, timeout=300)
        assert completed.returncode == 0, completed.stderr
        return {r["name"]: r for r in map(json.loads, completed.stdout.splitlines())}

    @pytest.mark.parametrize("name", ["latin1.obj", "latin1.stl", "noise.obj"])
    def test_text_that_is_not_utf8_fails_with_the_message_not_the_missing_module(self, results, name):
        result = results[name]
        assert result["checked"] and result["checked"].startswith(name + " could not be read as ")
        if not result["charset_normalizer"]:
            # Without the check, the pinned trimesh ends the run on the module the image lacks.
            assert "charset_normalizer" in result["unchecked"]

    @pytest.mark.parametrize("name", ["utf8.obj", "utf8.stl", "binary.stl", "utf8.glb"])
    def test_utf8_and_binary_inputs_still_load(self, results, name):
        assert results[name]["unchecked"] is None
        assert results[name]["checked"] is None
