#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""The USD, COLLADA and FBX reference collectors, the fetch that uses them, and what a run that leaves
referenced files out reports (REFERENCE_HANDLING, MISSING_REFERENCES, conversion-incomplete.json).

- ``reference_collectors.py`` is vendored from the 3D thumbnail container's ``dependencies.py``; every
  definition in it must be the canonical one, character for character.
- The collectors run in the worker child (``conversion_worker.scan_references``); the fetch in the
  parent only downloads. Here the scan runs in this process, which is the same code.
- The handler cases run the real worker child against an in-memory bucket.
"""

import ast
import importlib.util
import io
import json
import os
import sys
import zipfile
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

_HERE = os.path.dirname(os.path.abspath(__file__))
_LAMBDA_DIR = os.path.dirname(_HERE)
sys.path.insert(0, _LAMBDA_DIR)
sys.path.insert(0, _HERE)

pytest.importorskip("pxr")
trimesh = pytest.importorskip("trimesh")
pytest.importorskip("collada")

import conversion_worker  # noqa: E402
import incomplete_result  # noqa: E402
import reference_collectors  # noqa: E402
import referenced_files as rf  # noqa: E402

_CANONICAL = os.path.normpath(os.path.join(
    _LAMBDA_DIR, "..", "..", "..", "preview", "3dThumbnail", "container", "preview_pipeline", "dependencies.py"))
ROOT = "xasset/"

_MESH = """
def Mesh "M" {
    point3f[] points = [(0,0,0),(1,0,0),(0,1,0)]
    int[] faceVertexCounts = [3]
    int[] faceVertexIndices = [0,1,2]
}
"""


def _definitions(path):
    source = open(path, encoding="utf-8").read()
    found = {}
    for node in ast.parse(source).body:
        name = getattr(node, "name", None)
        if name is None and isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
        if name:
            found[name] = ast.get_source_segment(source, node)
    return found


@pytest.mark.unit
class TestTheVendoredCollectorsHaveNotDrifted:
    _OWN = {"logger", "MAX_REFERENCE_LENGTH"}

    def test_every_definition_is_the_canonical_one(self):
        vendored = _definitions(os.path.join(_LAMBDA_DIR, "reference_collectors.py"))
        canonical = _definitions(_CANONICAL)
        compared = [name for name in vendored if name not in self._OWN]
        assert len(compared) >= 25, compared
        for name in compared:
            assert vendored[name] == canonical.get(name), f"{name} differs from dependencies.py; copy the fix"

    def test_the_constant_kept_locally_matches(self):
        assert reference_collectors.MAX_REFERENCE_LENGTH == rf.MAX_REFERENCE_LENGTH
        canonical = open(_CANONICAL, encoding="utf-8").read()
        assert "MAX_REFERENCE_LENGTH = 1024" in canonical

    def test_the_kinds_match_the_ones_the_fetch_and_the_document_use(self):
        for name in ("KIND_GEOMETRY_LAYER", "KIND_DATA_FILE", "KIND_TEXTURE_OR_OTHER", "KIND_METADATA",
                     "KIND_UNSAMPLED_TEXTURE"):
            assert getattr(reference_collectors, name) == getattr(rf, name), name
        for name in ("KIND_GEOMETRY_LAYER", "KIND_DATA_FILE", "KIND_TEXTURE_OR_OTHER"):
            assert getattr(reference_collectors, name) == getattr(incomplete_result, name), name


class FakeS3:
    def __init__(self, objects):
        self.objects = dict(objects)
        self.put = {}

    def head_object(self, Bucket, Key, **_):  # noqa: N803
        if Key not in self.objects:
            raise ClientError({"Error": {"Code": "404"}, "ResponseMetadata": {"HTTPStatusCode": 404}}, "HeadObject")
        return {"ContentLength": len(self.objects[Key]), "VersionId": "v1", "ETag": '"e"'}

    def download_fileobj(self, Bucket, Key, Fileobj, ExtraArgs=None):  # noqa: N803
        Fileobj.write(self.objects[Key])

    def put_object(self, Bucket, Key, Body, **_):  # noqa: N803
        self.put[Key] = Body


def _scan_here(batch):
    return conversion_worker.scan_references({"files": [list(item) for item in batch]})


def _png():
    from PIL import Image
    buffer = io.BytesIO()
    Image.new("RGB", (4, 4), (10, 200, 10)).save(buffer, format="PNG")
    return buffer.getvalue()


def _scene_objects():
    return {
        ROOT + "scene/root.usda": ('#usda 1.0\n(\n    subLayers = [@./parts/wheel.usda@, @./gone.usda@, @/etc/x.usda@]\n)\n'
                                   'def "Body" (references = @../shared/body.usda@) {}\n').encode(),
        ROOT + "scene/parts/wheel.usda": ("#usda 1.0\n" + _MESH + 'def Scope "S" {\n    asset t = @./tex/green.png@\n'
                                          '    asset u = @./tex/missing.png@\n}\n').encode(),
        ROOT + "scene/parts/tex/green.png": _png(),
        ROOT + "shared/body.usda": ('#usda 1.0\n(\n    defaultPrim = "B"\n)\n' + _MESH.replace('"M"', '"B"')).encode(),
    }


def _fetch(tmp_path, client, relative, limits=None):
    local_root = tmp_path / "input"
    target = local_root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(client.objects[ROOT + relative])
    return str(local_root), rf.fetch_referenced_files(
        client, "abkt", ROOT, relative, str(target), str(local_root),
        limits=limits or rf.FetchLimits(max_depth=rf.SCENE_MAX_DEPTH), scan=_scan_here)


@pytest.mark.unit
class TestTheFetchFollowsUsdReferences:
    def test_sublayers_references_and_textures_inside_the_asset_are_fetched(self, tmp_path):
        _, report = _fetch(tmp_path, FakeS3(_scene_objects()), "scene/root.usda")
        assert sorted(report.fetched) == ["scene/parts/tex/green.png", "scene/parts/wheel.usda", "shared/body.usda"]

    def test_what_cannot_be_used_is_skipped_with_its_kind_and_never_fails(self, tmp_path):
        _, report = _fetch(tmp_path, FakeS3(_scene_objects()), "scene/root.usda")
        by_name = {entry["reference"]: entry for entry in report.skipped_entries}
        assert by_name["scene/gone.usda"]["kind"] == rf.KIND_GEOMETRY_LAYER
        assert by_name["/etc/x.usda"]["rejected"] is True
        assert by_name["/etc/x.usda"]["kind"] == rf.KIND_GEOMETRY_LAYER
        assert by_name["scene/parts/tex/missing.png"]["kind"] == rf.KIND_TEXTURE_OR_OTHER
        assert by_name["scene/parts/tex/missing.png"]["referrer"] == "scene/parts/wheel.usda"

    def test_selected_only_fetches_nothing_and_reports_every_reference(self, tmp_path):
        _, report = _fetch(tmp_path, FakeS3(_scene_objects()), "scene/root.usda", rf.FetchLimits(
            max_depth=0, not_followed_reason="not followed: REFERENCE_HANDLING is selected-only"))
        assert report.fetched == []
        assert {e["reason"] for e in report.skipped_entries} == {"not followed: REFERENCE_HANDLING is selected-only"}
        assert len(report.skipped_entries) == 4

    def test_the_scan_runs_once_per_batch_of_queued_files(self, tmp_path):
        calls = []

        def counting(batch):
            calls.append(len(batch))
            return _scan_here(batch)

        local_root = tmp_path / "input"
        target = local_root / "scene/root.usda"
        target.parent.mkdir(parents=True)
        client = FakeS3(_scene_objects())
        target.write_bytes(client.objects[ROOT + "scene/root.usda"])
        rf.fetch_referenced_files(client, "abkt", ROOT, "scene/root.usda", str(target), str(local_root),
                                  limits=rf.FetchLimits(max_depth=8), scan=counting)
        assert calls == [1, 2], calls

    def test_without_a_scan_a_usd_input_fetches_nothing(self, tmp_path):
        local_root = tmp_path / "input"
        target = local_root / "scene/root.usda"
        target.parent.mkdir(parents=True)
        client = FakeS3(_scene_objects())
        target.write_bytes(client.objects[ROOT + "scene/root.usda"])
        report = rf.fetch_referenced_files(client, "abkt", ROOT, "scene/root.usda", str(target), str(local_root))
        assert report.fetched == []

    def test_a_scan_error_is_logged_and_fetches_nothing(self, tmp_path):
        local_root = tmp_path / "input"
        target = local_root / "a.usda"
        target.parent.mkdir(parents=True)
        target.write_text("not usd")
        report = rf.fetch_referenced_files(FakeS3({}), "abkt", ROOT, "a.usda", str(target), str(local_root),
                                           scan=_scan_here)
        assert report.fetched == [] and report.skipped_entries == []


@pytest.mark.unit
class TestTheFetchFollowsColladaAndFbxReferences:
    def _dae(self):
        dae = trimesh.creation.box().export(file_type="dae").decode()
        library = ('<library_images><image id="i1"><init_from>tex/wood.png</init_from></image>'
                   '<image id="i2"><init_from>tex/none.png</init_from></image></library_images>'
                   '<library_nodes><node id="n"><instance_node url="other.dae#x"/></node></library_nodes>')
        return dae.replace("<library_effects>", library + "<library_effects>", 1).encode()

    def test_a_dae_image_is_fetched_and_an_external_instance_is_reported_not_fetched(self, tmp_path):
        client = FakeS3({ROOT + "m.dae": self._dae(), ROOT + "tex/wood.png": _png(), ROOT + "other.dae": b"x"})
        _, report = _fetch(tmp_path, client, "m.dae")
        assert report.fetched == ["tex/wood.png"]
        reasons = {e["reference"]: (e["kind"], e["reason"]) for e in report.skipped_entries}
        assert reasons["other.dae"] == (rf.KIND_GEOMETRY_LAYER, rf._NOT_COMPOSED)
        assert reasons["tex/none.png"][0] == rf.KIND_TEXTURE_OR_OTHER

    def test_a_dae_with_a_doctype_fetches_nothing(self, tmp_path):
        client = FakeS3({ROOT + "m.dae": b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><COLLADA/>'})
        _, report = _fetch(tmp_path, client, "m.dae")
        assert report.fetched == []

    def test_an_fbx_texture_is_fetched_by_its_relative_or_bare_name(self, tmp_path):
        fbx = (b"Kaydara FBX Binary  \x00" + b"\x00" * 10 + b"C:\\Users\\me\\proj\\tex\\wood.png\x00"
               + b"RelativeFilename\x00tex\\wood.png\x00")
        client = FakeS3({ROOT + "m.fbx": fbx, ROOT + "tex/wood.png": _png()})
        _, report = _fetch(tmp_path, client, "m.fbx")
        assert report.fetched == ["tex/wood.png"]
        # The absolute spelling of a texture that was fetched by another spelling is not missing.
        assert report.skipped_entries == []


@pytest.mark.unit
class TestTheIncompleteResult:
    def _entries(self, count=3):
        kinds = [rf.KIND_TEXTURE_OR_OTHER, rf.KIND_GEOMETRY_LAYER, rf.KIND_DATA_FILE]
        return [{"reference": f"dir/f{i}.usda", "referrer": "root.usda", "reason": "it is not in the asset",
                 "kind": kinds[i % 3], "rejected": False, "forSize": False} for i in range(count)]

    def test_the_document_has_the_preview_shape(self):
        document = json.loads(incomplete_result.document("root.usda", "scene/root.usda", self._entries()))
        assert document["schema"] == "vams.conversion3dBasic.incomplete/v1"
        assert document["status"] == "INCOMPLETE"
        assert set(document["counts"]) == {"skipped", "geometryLayers", "dataFiles", "texturesOrOther", "notFollowed"}
        assert document["counts"]["skipped"] == 3
        assert [row["kind"] for row in document["skippedFiles"]] == [rf.KIND_GEOMETRY_LAYER, rf.KIND_DATA_FILE,
                                                                     rf.KIND_TEXTURE_OR_OTHER]
        assert document["truncated"] is False and "incomplete" in document["message"]

    def test_the_document_is_capped(self):
        many = self._entries(500)
        for entry in many:
            entry["reason"] = "r" * 1000
        body = incomplete_result.document("root.usda", "p" * 1000, many)
        document = json.loads(body)
        assert len(body) <= incomplete_result.MAX_RESULT_DOCUMENT_BYTES
        assert len(document["skippedFiles"]) <= incomplete_result.MAX_RESULT_ENTRIES and document["truncated"]
        assert len(document["inputFile"]) <= incomplete_result.MAX_RESULT_TEXT_CHARS

    def test_a_reference_outside_the_asset_is_named_only_by_a_safe_base_name(self):
        entries = [{"reference": "s3://other-bucket/secret/layer.usda", "referrer": "r.usda", "reason": "x",
                    "kind": rf.KIND_GEOMETRY_LAYER, "rejected": True, "forSize": False},
                   {"reference": "http://host:8080/a/b?c", "referrer": "r.usda", "reason": "x",
                    "kind": rf.KIND_GEOMETRY_LAYER, "rejected": True, "forSize": False}]
        body = incomplete_result.document("r.usda", "r.usda", entries).decode()
        assert "other-bucket" not in body and "host" not in body
        assert [row["path"] for row in json.loads(body)["skippedFiles"]] == ["layer.usda", ""]

    def test_the_refusal_names_the_first_twenty(self):
        message = incomplete_result.refusal("root.usda", self._entries(25))
        assert "MISSING_REFERENCES is fail" in message and "and 5 more" in message


def _load_handler():
    spec = importlib.util.spec_from_file_location("conversion_3dbasic_refs", os.path.join(_LAMBDA_DIR, "lambda.py"))
    module = importlib.util.module_from_spec(spec)
    with patch("boto3.client", MagicMock()), patch("boto3.resource", MagicMock()):
        spec.loader.exec_module(module)
    return module


def _run_handler(objects, input_relative, output_type, options=None):
    """Run convert_input_output with the real worker child against an in-memory bucket; return
    ``(message, uploaded {key: bytes}, client)``."""
    mod = _load_handler()
    client = FakeS3(objects)
    uploaded = {}

    def _download(bucket, key, path, **_):
        with open(path, "wb") as handle:
            handle.write(client.objects[key])
        return path

    def _upload(bucket, key, path):
        with open(path, "rb") as handle:
            uploaded[key] = handle.read()
        return key

    key = ROOT + input_relative
    mod._results_location = ("obkt", "results/E1/")
    with patch.object(mod, "download", side_effect=_download), \
            patch.object(mod, "uploadV2", side_effect=_upload), \
            patch.object(mod, "head_input", return_value=(len(client.objects[key]), '"e"')), \
            patch.object(mod, "_s3_client_for_bucket", return_value=client):
        subdir = os.path.dirname(input_relative)
        message = mod.convert_input_output(f"s3://abkt/{key}", "s3://obkt/files/", output_type, subdir, "",
                                           options or {})
    return message, uploaded, client


@pytest.mark.unit
class TestTheHandlerReportsAndRefuses:
    def test_a_usd_scene_converts_with_its_fetched_layers_and_reports_the_rest(self):
        message, uploaded, client = _run_handler(_scene_objects(), "scene/root.usda", ".glb")
        assert "incomplete" in message and "2 geometry layers" in message, message
        glb = trimesh.load(io.BytesIO(uploaded["files/scene/root.glb"]), file_type="glb")
        assert len(glb.geometry) == 2, "the sublayer's and the reference's geometry are both there"
        document = json.loads(client.put["results/E1/conversion-incomplete.json"])
        assert document["counts"]["geometryLayers"] == 2 and document["counts"]["texturesOrOther"] == 1

    def test_missing_references_fail_refuses_before_converting(self):
        with pytest.raises(ValueError, match="MISSING_REFERENCES is fail"):
            _run_handler(_scene_objects(), "scene/root.usda", ".glb", {"missingReferences": "fail"})

    def test_a_complete_run_writes_no_document_and_reports_success(self):
        objects = _scene_objects()
        objects[ROOT + "scene/root.usda"] = ('#usda 1.0\n(\n    subLayers = [@./parts/wheel.usda@]\n)\n').encode()
        objects[ROOT + "scene/parts/wheel.usda"] = ("#usda 1.0\n" + _MESH).encode()
        message, uploaded, client = _run_handler(objects, "scene/root.usda", ".usdc")
        assert message is None and client.put == {}
        assert "files/scene/root.usdc" in uploaded

    def test_convert_linked_uploads_every_converted_layer_in_its_folder(self):
        objects = _scene_objects()
        message, uploaded, _ = _run_handler(objects, "scene/root.usda", ".usdc",
                                            {"referenceHandling": "convert-linked"})
        names = sorted(k[len("files/scene/root/"):] for k in uploaded)
        assert names == ["_linked/shared/body.usdc", "parts/tex/green.png", "parts/wheel.usdc", "root.usdc"], names

    def test_package_puts_the_closure_into_one_usdz(self):
        message, uploaded, _ = _run_handler(_scene_objects(), "scene/root.usda", ".usdz")
        assert list(uploaded) == ["files/scene/root.usdz"]
        with zipfile.ZipFile(io.BytesIO(uploaded["files/scene/root.usdz"])) as archive:
            names = archive.namelist()
        assert any(n.endswith("wheel.usda") for n in names) and any(n.endswith("green.png") for n in names)

    @pytest.mark.parametrize("options,output,match", [
        ({"referenceHandling": "package"}, ".glb", "package writes a .usdz"),
        ({"referenceHandling": "convert-linked"}, ".glb", "convert-linked writes USD layers"),
        ({"referenceHandling": "convert-linked", "targetUnits": "mm"}, ".usdc", "keeps each layer's own unit"),
        ({"referenceHandling": "package", "targetUpAxis": "z"}, ".usdz", "keeps the USD layers as authored"),
    ])
    def test_an_illegal_combination_is_refused_before_download(self, options, output, match):
        with pytest.raises(ValueError, match=match):
            _run_handler(_scene_objects(), "scene/root.usda", output, options)

    def test_selected_only_converts_only_the_selected_layer(self):
        objects = _scene_objects()
        objects[ROOT + "scene/root.usda"] = ('#usda 1.0\n(\n    subLayers = [@./parts/wheel.usda@]\n)\n'
                                             + _MESH.replace('"M"', '"Own"')).encode()
        message, uploaded, client = _run_handler(objects, "scene/root.usda", ".glb",
                                                 {"referenceHandling": "selected-only"})
        glb = trimesh.load(io.BytesIO(uploaded["files/scene/root.glb"]), file_type="glb")
        assert len(glb.geometry) == 1 and "incomplete" in message
        rows = json.loads(client.put["results/E1/conversion-incomplete.json"])["skippedFiles"]
        assert rows[0]["reason"] == "not followed: REFERENCE_HANDLING is selected-only"
