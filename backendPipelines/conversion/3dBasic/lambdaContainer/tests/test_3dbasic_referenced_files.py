#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Referenced files (#507): the input's buffers, materials and textures are fetched from its own asset,
every reference is checked against an allowlist before any Amazon S3 call, and the loader can read the
fetched files and nothing else. A glTF/GLB buffer that cannot be used fails the run; a material library
or a texture image that cannot be used is skipped with a warning naming it.

These run without trimesh: the module under test only parses the reference syntax of each format. The
conversions that load the fetched files for real are in ``test_3dbasic_real_conversions.py``.
"""

import json
import os
import struct
import sys
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

_LAMBDA_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _LAMBDA_DIR not in sys.path:
    sys.path.insert(0, _LAMBDA_DIR)

import referenced_files as rf  # noqa: E402

ROOT = "xasset/"


class FakeS3:
    """An asset bucket held in memory: HeadObject and download_fileobj over a dict of key -> bytes."""

    def __init__(self, objects, head_error_codes=None):
        self.objects = dict(objects)
        self.head_error_codes = dict(head_error_codes or {})
        self.heads = []
        self.downloads = []

    def head_object(self, Bucket, Key):  # noqa: N803 - boto3 kwarg names
        self.heads.append(Key)
        if Key in self.head_error_codes:
            code = self.head_error_codes[Key]
            raise ClientError({"Error": {"Code": code}, "ResponseMetadata": {"HTTPStatusCode": 500}},
                              "HeadObject")
        if Key not in self.objects:
            raise ClientError({"Error": {"Code": "404"}, "ResponseMetadata": {"HTTPStatusCode": 404}},
                              "HeadObject")
        return {"ContentLength": len(self.objects[Key]), "VersionId": "v1"}

    def download_fileobj(self, Bucket, Key, Fileobj, ExtraArgs=None):  # noqa: N803
        self.downloads.append((Key, ExtraArgs))
        Fileobj.write(self.objects[Key])


def _stage(tmp_path, relative, content):
    """Write the already-downloaded input at its root-relative path under the local root."""
    local_root = tmp_path / "input"
    target = local_root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))
    return str(local_root), str(target)


def _fetch(tmp_path, client, input_relative, content, limits=None, logger=None):
    local_root, local_input = _stage(tmp_path, input_relative, content)
    report = rf.fetch_referenced_files(client, "abkt", ROOT, input_relative, local_input, local_root,
                                       limits=limits, logger=logger)
    return local_root, report


def _warnings(logger):
    return [call.args[0] for call in logger.warning.call_args_list]


def _gltf(*uris, images=()):
    return json.dumps({"asset": {"version": "2.0"},
                       "buffers": [{"uri": u, "byteLength": 4} for u in uris],
                       "images": [{"uri": u} for u in images]})


def _glb(document):
    payload = json.dumps(document).encode("utf-8")
    payload += b" " * (-len(payload) % 4)
    return (b"glTF" + struct.pack("<II", 2, 20 + len(payload))
            + struct.pack("<I4s", len(payload), b"JSON") + payload)


@pytest.mark.unit
class TestResolveReference:
    @pytest.mark.parametrize("reference, referrer, expected", [
        ("buffer.bin", "", "buffer.bin"),
        ("tex/wood.png", "parts", "parts/tex/wood.png"),
        ("../shared/wood.png", "parts/a", "parts/shared/wood.png"),
        ("..\\shared\\wood.png", "parts/a", "parts/shared/wood.png"),
        ("./x.bin", "", "x.bin"),
    ])
    def test_a_relative_path_inside_the_root_is_accepted(self, reference, referrer, expected):
        assert rf.resolve_reference(reference, referrer) == expected

    @pytest.mark.parametrize("reference", [
        "/etc/hostname", "\\\\server\\share\\x.png", "../../other-asset/x.bin", "a/../../../x.bin",
        "..\\..\\x.bin", "..",
        "s3://abkt/other/x.bin", "https://example.com/x.png", "file:///etc/hostname",
        "C:\\Users\\me\\model.mtl", "data:application/octet-stream;base64,AAAA",
        "x\x00.bin", "x\n.bin", "`cmd`", "", "   ", "a" * 1025,
    ])
    def test_anything_else_is_rejected(self, reference):
        assert rf.resolve_reference(reference, "parts") is None

    def test_a_gltf_uri_is_checked_after_percent_decoding(self):
        # The decoded name is what is downloaded, so it is the one that has to pass.
        assert rf.resolve_reference("my%20buffer.bin", "", percent_encoded=True) == "my buffer.bin"
        assert rf.resolve_reference("%2E%2E/other/x.bin", "", percent_encoded=True) is None

    def test_a_non_string_is_rejected(self):
        assert rf.resolve_reference(None) is None
        assert rf.resolve_reference(42) is None


@pytest.mark.unit
class TestConfinementRoot:
    def test_the_root_is_the_key_minus_the_asset_relative_path(self):
        assert rf.confinement_root("xasset/parts/housing/model.obj", "parts/housing") == \
            ("xasset/", "parts/housing/model.obj")

    def test_a_file_at_the_asset_root_is_confined_to_its_directory(self):
        assert rf.confinement_root("xasset/model.obj", "") == ("xasset/", "model.obj")

    def test_an_output_folder_input_is_confined_to_that_folder(self):
        # A file an earlier step wrote is keyed by its asset-relative path under the run's output
        # files folder, so the folder is its root.
        key = "pipelines/p/JOB/output/E1/files/parts/model/model.gltf"
        assert rf.confinement_root(key, "parts/model") == \
            ("pipelines/p/JOB/output/E1/files/", "parts/model/model.gltf")

    def test_a_key_that_does_not_end_with_its_relative_path_falls_back_to_its_directory(self):
        assert rf.confinement_root("xasset/elsewhere/model.obj", "parts") == \
            ("xasset/elsewhere/", "model.obj")

    def test_a_bucket_root_file(self):
        assert rf.confinement_root("model.obj", "") == ("", "model.obj")


@pytest.mark.unit
class TestCollectors:
    def test_gltf_buffers_are_required_images_optional_and_data_uris_skipped(self, tmp_path):
        path = tmp_path / "m.gltf"
        path.write_text(_gltf("b0.bin", "data:application/octet-stream;base64,AAAA",
                              images=("tex.png",)))
        assert rf._gltf_references(str(path)) == [("b0.bin", True, True), ("tex.png", True, False)]

    def test_glb_json_chunk_uris_are_collected(self, tmp_path):
        path = tmp_path / "m.glb"
        path.write_bytes(_glb({"asset": {"version": "2.0"}, "buffers": [{"byteLength": 4}],
                               "images": [{"uri": "tex.png"}]}))
        assert rf._glb_references(str(path)) == [("tex.png", True, False)]

    def test_a_file_that_is_not_a_glb_yields_nothing(self, tmp_path):
        path = tmp_path / "m.glb"
        path.write_bytes(b"not a glb at all")
        assert rf._glb_references(str(path)) == []

    # The names trimesh's OBJ loader asks for (D1): one material library, everything after the first
    # 'mtllib' in the text up to the end of that line. test_3dbasic_real_conversions.py checks the same
    # texts against the installed trimesh.
    @pytest.mark.parametrize("text, expected", [
        ("mtllib my box.mtl\nv 0 0 0\n", ["my box.mtl"]),
        ("mtllib\tmy  box.mtl  \n", ["my  box.mtl"]),
        ("mtllib my box.mtl\r\nv 0 0 0\r\n", ["my box.mtl"]),
        ("mtllib a.mtl b.mtl\n", ["a.mtl b.mtl"]),
        ("mtllib a.mtl\nmtllib b.mtl\n", ["a.mtl"]),
        ("# mtllib c.mtl\nmtllib d.mtl\n", ["c.mtl"]),
        ("o x_mtllib y.mtl\nmtllib d.mtl\n", ["y.mtl"]),
        ("mtllib my\\\nbox.mtl\n", ["mybox.mtl"]),
        ("MTLLIB u.mtl\nv 0 0 0\n", []),
        ("mtllib\nv 0 0 0\n", []),
        ("v 0 0 0\n", []),
    ])
    def test_obj_material_library_is_named_as_the_loader_asks_for_it(self, tmp_path, text, expected):
        path = tmp_path / "m.obj"
        path.write_bytes(text.encode("utf-8"))
        assert rf._obj_references(str(path)) == [(name, False, False) for name in expected]

    def test_mtl_textures_are_the_rest_of_each_map_kd_line(self, tmp_path):
        # The loader asks only for map_Kd values, whole: a spaced name stays one name, an option stays
        # part of it, and the other texture statements are not read.
        path = tmp_path / "m.mtl"
        path.write_text("newmtl m\nmap_Kd my wood.png\nmap_Kd -s 1 1 1 wood.png\nbump normal.png\n"
                        "map_Ka ambient.png\nKd 1 1 1\n  map_kd\t lower.png  \nmap_Kd\n")
        assert rf._mtl_references(str(path)) == [
            ("my wood.png", False, False), ("-s 1 1 1 wood.png", False, False), ("lower.png", False, False)]

    def test_a_document_that_is_not_utf8_is_not_read_for_names(self, tmp_path):
        # The loader reads no name from it either (see _obj_references), so nothing is fetched.
        client, logger = FakeS3({ROOT + "box.mtl": b"newmtl a\n"}), MagicMock()
        _, report = _fetch(tmp_path, client, "m.obj", b"# caf\xe9\nmtllib box.mtl\n", logger=logger)
        assert report.fetched == [] and client.heads == []
        assert "Could not read the references in m.obj" in _warnings(logger)[0]

    def test_ply_texturefile_comment(self, tmp_path):
        path = tmp_path / "m.ply"
        path.write_bytes(b"ply\nformat ascii 1.0\ncomment TextureFile fuze uv.jpg\n"
                         b"element vertex 0\nend_header\ncomment TextureFile after.png\n")
        assert rf._ply_references(str(path)) == [("fuze uv.jpg", False, False)]


@pytest.mark.unit
class TestFetch:
    def test_gltf_buffers_land_beside_the_input_at_their_asset_paths(self, tmp_path):
        client = FakeS3({ROOT + "parts/b0.bin": b"\x00" * 4, ROOT + "parts/tex/t.png": b"png"})
        local_root, report = _fetch(tmp_path, client, "parts/m.gltf",
                                    _gltf("b0.bin", images=("tex/t.png",)))
        assert report.fetched == ["parts/b0.bin", "parts/tex/t.png"]
        assert os.path.isfile(os.path.join(local_root, "parts", "b0.bin"))
        assert os.path.isfile(os.path.join(local_root, "parts", "tex", "t.png"))
        # The measured version is the one downloaded.
        assert client.downloads[0] == (ROOT + "parts/b0.bin", {"VersionId": "v1"})

    def test_an_obj_material_library_and_its_textures_are_fetched(self, tmp_path):
        # The MTL's texture name is relative to the MTL, not to the OBJ.
        client = FakeS3({ROOT + "mats/m.mtl": b"newmtl a\nmap_Kd wood.png\n",
                         ROOT + "mats/wood.png": b"png"})
        _, report = _fetch(tmp_path, client, "model.obj", "mtllib mats/m.mtl\nv 0 0 0\n")
        assert report.fetched == ["mats/m.mtl", "mats/wood.png"]
        # The loader asks for 'wood.png' (it resolves MTL textures against the OBJ's directory), so
        # the resolver keeps the spelling the MTL used.
        assert report.by_reference["wood.png"].endswith(os.path.join("mats", "wood.png"))

    def test_a_missing_buffer_fails_and_names_it(self, tmp_path):
        client = FakeS3({})
        with pytest.raises(rf.ReferencedFileError, match=r"references b0\.bin, which is not in the asset"):
            _fetch(tmp_path, client, "m.gltf", _gltf("b0.bin"))

    def test_a_missing_glb_buffer_fails(self, tmp_path):
        client = FakeS3({})
        document = {"asset": {"version": "2.0"}, "buffers": [{"uri": "b0.bin", "byteLength": 4}]}
        with pytest.raises(rf.ReferencedFileError, match="not in the asset"):
            _fetch(tmp_path, client, "m.glb", _glb(document))

    def test_a_forbidden_head_is_reported_as_missing(self, tmp_path):
        client = FakeS3({}, head_error_codes={ROOT + "b0.bin": "403"})
        with pytest.raises(rf.ReferencedFileError, match="not in the asset"):
            _fetch(tmp_path, client, "m.gltf", _gltf("b0.bin"))

    def test_any_other_s3_error_propagates(self, tmp_path):
        client = FakeS3({}, head_error_codes={ROOT + "b0.bin": "SlowDown"})
        with pytest.raises(ClientError):
            _fetch(tmp_path, client, "m.gltf", _gltf("b0.bin"))

    def test_any_other_s3_error_on_an_optional_reference_propagates(self, tmp_path):
        # Only a missing or unreadable object is skipped; a throttled lookup fails the run rather
        # than converting a model without a texture that exists.
        client = FakeS3({}, head_error_codes={ROOT + "tex.png": "SlowDown"})
        with pytest.raises(ClientError):
            _fetch(tmp_path, client, "m.gltf", _gltf(images=("tex.png",)))

    @pytest.mark.parametrize("reference", [
        "../other-asset/b0.bin", "/var/task/lambda.py", "s3://abkt/other/b0.bin", "C:\\b0.bin",
    ])
    def test_a_rejected_buffer_fails_before_any_s3_call(self, tmp_path, reference):
        client = FakeS3({})
        with pytest.raises(rf.ReferencedFileError, match="not a relative path to a file in the same"):
            _fetch(tmp_path, client, "m.gltf", _gltf(reference))
        assert client.heads == [] and client.downloads == []

    def test_a_buffer_in_another_asset_is_never_requested(self, tmp_path):
        # In-band positive control for the rejection: the other asset's object exists, so a fetch that
        # skipped the check would succeed rather than fail on a missing key.
        client = FakeS3({"xother/b0.bin": b"secret"})
        with pytest.raises(rf.ReferencedFileError):
            _fetch(tmp_path, client, "m.gltf", _gltf("../xother/b0.bin"))
        assert client.heads == []

    def test_the_file_count_is_bounded(self, tmp_path):
        client = FakeS3({ROOT + f"b{i}.bin": b"x" for i in range(3)})
        with pytest.raises(rf.ReferencedFileError, match="more than 2 files"):
            _fetch(tmp_path, client, "m.gltf", _gltf("b0.bin", "b1.bin", "b2.bin"),
                   limits=rf.FetchLimits(max_files=2))

    def test_the_byte_total_is_bounded(self, tmp_path):
        client = FakeS3({ROOT + "b0.bin": b"x" * 10, ROOT + "b1.bin": b"x" * 10})
        with pytest.raises(rf.ReferencedFileError, match="exceed 15 bytes"):
            _fetch(tmp_path, client, "m.gltf", _gltf("b0.bin", "b1.bin"),
                   limits=rf.FetchLimits(max_total_bytes=15))
        assert [key for key, _ in client.downloads] == [ROOT + "b0.bin"]

    def test_a_buffer_past_the_depth_limit_fails(self, tmp_path):
        client = FakeS3({ROOT + "b0.bin": b"x"})
        with pytest.raises(rf.ReferencedFileError, match="at most 0"):
            _fetch(tmp_path, client, "m.gltf", _gltf("b0.bin"), limits=rf.FetchLimits(max_depth=0))

    def test_a_repeated_reference_is_fetched_once(self, tmp_path):
        client = FakeS3({ROOT + "b0.bin": b"x"})
        _, report = _fetch(tmp_path, client, "m.gltf", _gltf("b0.bin", "./b0.bin"))
        assert report.fetched == ["b0.bin"] and len(client.downloads) == 1

    def test_a_format_without_references_fetches_nothing(self, tmp_path):
        client = FakeS3({})
        _, report = _fetch(tmp_path, client, "part.stl", b"solid x\nendsolid x\n")
        assert report.fetched == [] and client.heads == []

    def test_an_unreadable_document_is_left_to_the_loader(self, tmp_path):
        client = FakeS3({})
        _, report = _fetch(tmp_path, client, "m.gltf", "{not json")
        assert report.fetched == [] and client.heads == []


@pytest.mark.unit
class TestOptionalReferencesAreSkipped:
    """Material libraries and texture images: one that is missing, rejected or over a limit is skipped
    with a warning naming it, and the rest of the model is still fetched."""

    def test_a_material_library_named_with_a_space_is_fetched_with_its_texture(self, tmp_path):
        # D1: 'mtllib my box.mtl', as trimesh and Blender write it for 'my box.obj', is one name.
        client = FakeS3({ROOT + "my box.mtl": b"newmtl a\nmap_Kd my wood.png\n",
                         ROOT + "my wood.png": b"png"})
        _, report = _fetch(tmp_path, client, "my box.obj", "mtllib my box.mtl\nv 0 0 0\n")
        assert client.heads == [ROOT + "my box.mtl", ROOT + "my wood.png"]
        assert report.fetched == ["my box.mtl", "my wood.png"] and report.skipped_count == 0
        assert set(report.by_reference) == {"my box.mtl", "my wood.png"}

    def test_a_missing_material_library_named_with_a_space_is_one_warning_naming_it(self, tmp_path):
        client, logger = FakeS3({}), MagicMock()
        _, report = _fetch(tmp_path, client, "my box.obj", "mtllib my box.mtl\nv 0 0 0\n", logger=logger)
        assert client.heads == [ROOT + "my box.mtl"] and report.skipped_count == 1
        warnings = _warnings(logger)
        assert len(warnings) == 1
        assert warnings[0].startswith("Skipped my box.mtl, referenced by my box.obj: it is not in the asset")

    def test_a_material_library_is_read_as_one_whatever_its_extension(self, tmp_path):
        # The loader parses whatever the OBJ names as its material library, so its textures are
        # fetched too.
        client = FakeS3({ROOT + "materials.txt": b"newmtl a\nmap_Kd wood.png\n", ROOT + "wood.png": b"png"})
        _, report = _fetch(tmp_path, client, "box.obj", "mtllib materials.txt\n")
        assert report.fetched == ["materials.txt", "wood.png"]

    def test_a_missing_material_library_is_skipped_and_named(self, tmp_path):
        client, logger = FakeS3({}), MagicMock()
        _, report = _fetch(tmp_path, client, "lonely/box.obj", "mtllib box.mtl\nv 0 0 0\n",
                           logger=logger)
        assert report.fetched == [] and report.skipped_count == 1
        assert client.heads == [ROOT + "lonely/box.mtl"]
        warning = _warnings(logger)[0]
        assert "lonely/box.mtl" in warning and "lonely/box.obj" in warning
        assert "not in the asset" in warning and "converted without it" in warning
        assert report.skipped == [warning]
        assert "box.mtl" not in report.by_reference

    def test_a_windows_absolute_material_library_is_skipped_before_any_s3_call(self, tmp_path):
        client, logger = FakeS3({}), MagicMock()
        _, report = _fetch(tmp_path, client, "box.obj", "mtllib C:\\models\\box.mtl\nv 0 0 0\n",
                           logger=logger)
        assert report.skipped_count == 1 and client.heads == [] and client.downloads == []
        warning = _warnings(logger)[0]
        assert "models" in warning and "not a relative path to a file in the same asset" in warning

    def test_a_material_library_in_another_asset_is_never_requested(self, tmp_path):
        # In-band positive control: the other asset's MTL exists, so a fetch that skipped the check
        # would download it rather than skip it.
        client = FakeS3({"xother/m.mtl": b"newmtl a\n"})
        _, report = _fetch(tmp_path, client, "box.obj", "mtllib ../xother/m.mtl\n")
        assert report.skipped_count == 1 and client.heads == []

    def test_a_missing_texture_is_skipped_and_the_material_library_kept(self, tmp_path):
        client, logger = FakeS3({ROOT + "m.mtl": b"newmtl a\nmap_Kd wood.png\n"}), MagicMock()
        _, report = _fetch(tmp_path, client, "box.obj", "mtllib m.mtl\n", logger=logger)
        assert report.fetched == ["m.mtl"] and report.skipped_count == 1
        assert "wood.png" in _warnings(logger)[0] and "m.mtl" in _warnings(logger)[0]

    @pytest.mark.parametrize("texture", ["/etc/hostname", "../../xother/wood.png", "https://example.com/t.png"])
    def test_a_rejected_texture_is_skipped_and_never_requested(self, tmp_path, texture):
        client = FakeS3({ROOT + "m.mtl": f"newmtl a\nmap_Kd {texture}\n".encode(),
                         "xother/wood.png": b"png"})
        _, report = _fetch(tmp_path, client, "box.obj", "mtllib m.mtl\n")
        assert report.fetched == ["m.mtl"] and report.skipped_count == 1
        assert client.heads == [ROOT + "m.mtl"]

    def test_a_missing_gltf_image_is_skipped_and_the_buffer_fetched(self, tmp_path):
        client, logger = FakeS3({ROOT + "b0.bin": b"x" * 4}), MagicMock()
        _, report = _fetch(tmp_path, client, "m.gltf", _gltf("b0.bin", images=("tex/t.png",)),
                           logger=logger)
        assert report.fetched == ["b0.bin"] and report.skipped_count == 1
        assert "tex/t.png" in _warnings(logger)[0]
        assert "tex/t.png" not in report.by_reference

    def test_a_missing_glb_image_is_skipped(self, tmp_path):
        client = FakeS3({})
        document = {"asset": {"version": "2.0"}, "buffers": [{"byteLength": 4}],
                    "images": [{"uri": "tex.png"}]}
        _, report = _fetch(tmp_path, client, "m.glb", _glb(document))
        assert report.fetched == [] and report.skipped_count == 1

    def test_a_missing_ply_texture_is_skipped(self, tmp_path):
        client = FakeS3({})
        _, report = _fetch(tmp_path, client, "m.ply",
                           b"ply\nformat ascii 1.0\ncomment TextureFile uv.jpg\nend_header\n")
        assert report.skipped_count == 1

    def test_a_texture_name_is_escaped_in_the_warning(self, tmp_path):
        logger = MagicMock()
        _fetch(tmp_path, FakeS3({}), "m.gltf", _gltf(images=("evil\nline.png",)), logger=logger)
        assert "\n" not in _warnings(logger)[0] and "\\n" in _warnings(logger)[0]

    def test_missing_optional_references_count_toward_the_file_limit(self, tmp_path):
        # A missing name is looked up with HeadObject, so it counts: a model cannot drive an unbounded
        # number of Amazon S3 requests by naming files that do not exist.
        client = FakeS3({})
        images = tuple(f"t{i}.png" for i in range(5))
        _, report = _fetch(tmp_path, client, "m.gltf", _gltf(images=images),
                           limits=rf.FetchLimits(max_files=2))
        assert len(client.heads) == 2 and report.skipped_count == 5

    def test_a_texture_over_the_byte_total_is_skipped(self, tmp_path):
        client = FakeS3({ROOT + "t0.png": b"x" * 10, ROOT + "t1.png": b"x" * 10})
        _, report = _fetch(tmp_path, client, "m.gltf", _gltf(images=("t0.png", "t1.png")),
                           limits=rf.FetchLimits(max_total_bytes=15))
        assert report.fetched == ["t0.png"] and report.skipped_count == 1
        assert [key for key, _ in client.downloads] == [ROOT + "t0.png"]

    def test_a_texture_past_the_depth_limit_is_skipped(self, tmp_path):
        client = FakeS3({ROOT + "m.mtl": b"map_Kd wood.png\n", ROOT + "wood.png": b"png"})
        _, report = _fetch(tmp_path, client, "model.obj", "mtllib m.mtl\n",
                           limits=rf.FetchLimits(max_depth=1))
        assert report.fetched == ["m.mtl"] and report.skipped_count == 1
        assert ROOT + "wood.png" not in client.heads

    def test_the_warnings_are_bounded(self, tmp_path):
        logger = MagicMock()
        count = rf.MAX_SKIPPED_LOGGED + 10
        images = tuple(f"/abs/t{i}.png" for i in range(count))
        _, report = _fetch(tmp_path, FakeS3({}), "m.gltf", _gltf(images=images), logger=logger)
        assert report.skipped_count == count and len(report.skipped) == rf.MAX_SKIPPED_LOGGED
        warnings = _warnings(logger)
        assert len(warnings) == rf.MAX_SKIPPED_LOGGED + 1
        assert "10 further referenced files" in warnings[-1]

    def test_a_buffer_naming_a_file_already_skipped_still_fails(self, tmp_path, monkeypatch):
        # The collectors list buffers first, so this order does not come from a real document; the
        # buffer rule holds whatever the order.
        monkeypatch.setitem(rf._COLLECTORS, ".gltf", lambda path: [
            rf.Reference("x.bin", True, False), rf.Reference("x.bin", True, True)])
        with pytest.raises(rf.ReferencedFileError, match="not in the asset"):
            _fetch(tmp_path, FakeS3({}), "m.gltf", "{}")

    def test_a_skipped_reference_is_never_served(self, tmp_path):
        client = FakeS3({})
        local_root, report = _fetch(tmp_path, client, "box.obj", "mtllib box.mtl\nmtllib /etc/x.mtl\n")
        # A file at the skipped name's local path, which the fetch did not write, is still refused.
        (tmp_path / "input" / "box.mtl").write_bytes(b"newmtl planted\n")
        resolver = rf.ConfinedResolver(local_root, "box.obj", report.by_reference)
        for name in ("box.mtl", "/etc/x.mtl"):
            with pytest.raises(FileNotFoundError):
                resolver.get(name)


@pytest.mark.unit
class TestConfinedResolver:
    def _resolver(self, tmp_path):
        client = FakeS3({ROOT + "parts/my buffer.bin": b"BUF", ROOT + "parts/tex/t.png": b"PNG"})
        local_root, report = _fetch(tmp_path, client, "parts/m.gltf",
                                    _gltf("my%20buffer.bin", images=("tex/t.png",)))
        return local_root, rf.ConfinedResolver(local_root, "parts/m.gltf", report.by_reference)

    def test_it_serves_a_fetched_file_by_the_spelling_the_document_uses(self, tmp_path):
        _, resolver = self._resolver(tmp_path)
        assert resolver["my%20buffer.bin"] == b"BUF"
        assert resolver.get("tex/t.png") == b"PNG"
        assert "tex/t.png" in resolver

    def test_it_refuses_a_path_outside_the_fetched_set(self, tmp_path):
        local_root, resolver = self._resolver(tmp_path)
        outside = tmp_path / "outside.bin"
        outside.write_bytes(b"NOT YOURS")
        # A local file that is not part of the fetch, even inside the local root, is refused too.
        (tmp_path / "input" / "parts" / "stray.bin").write_bytes(b"stray")
        for name in (str(outside), "../outside.bin", "../../outside.bin", "stray.bin",
                     "file://" + str(outside), "missing.bin"):
            with pytest.raises(FileNotFoundError):
                resolver.get(name)
            assert name not in resolver

    def test_it_exposes_the_attributes_the_loader_reads(self, tmp_path):
        local_root, resolver = self._resolver(tmp_path)
        assert resolver.parent == os.path.join(os.path.realpath(local_root), "parts")
        assert resolver.file_name == "m.gltf"
