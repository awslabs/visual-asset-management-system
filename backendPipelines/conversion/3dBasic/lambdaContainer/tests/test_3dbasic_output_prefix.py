#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Loose USD outputs keep resolving when the workflow writes them back under an output path prefix
(in-development defect D3).

The workflow inserts the execution's output path extension (a launch's, or the workflow's
``defaultOutputFileBaseExecutionPathExtension``) immediately before each output file's own name
(``backend/backend/common/workflows/outputPathExtension.py``). A layer and a texture in a sub-folder
end up apart: ``m/E/m.usdc`` and ``m/textures/E/a.png``. The pipeline writes textures beside the layer
and writes every reference with the path the file will have after the write-back
(``usd_io.placed_reference``), as the layer is written, so no layer is opened again (D7).

Each case converts through the real worker child, then places the uploaded files the way the
write-back does and opens the result with USD."""

import ast
import importlib.util
import io
import os
import shutil
import sys
from unittest.mock import MagicMock, patch

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_LAMBDA_DIR = os.path.dirname(_HERE)
sys.path.insert(0, _LAMBDA_DIR)
sys.path.insert(0, _HERE)

pytest.importorskip("pxr")
trimesh = pytest.importorskip("trimesh")

from pxr import Sdf, Usd, UsdGeom  # noqa: E402

import usd_io  # noqa: E402
import test_3dbasic_reference_collectors as refs  # noqa: E402

_BACKEND = os.path.normpath(os.path.join(
    _LAMBDA_DIR, "..", "..", "..", "..", "backend", "backend", "common", "workflows", "outputPathExtension.py"))

PREFIXES = ["/o/x/", "/run-1/", "/E_"]


def _segment(path, name):
    source = open(path, encoding="utf-8").read()
    node = next(n for n in ast.parse(source).body if getattr(n, "name", None) == name)
    return ast.get_source_segment(source, node)


@pytest.mark.unit
class TestThePlacementIsTheWorkflowsOwn:
    @pytest.mark.parametrize("name", ["normalize_output_path_extension", "apply_output_path_extension"])
    def test_the_vendored_function_is_the_backends(self, name):
        assert _segment(os.path.join(_LAMBDA_DIR, "usd_io.py"), name) == _segment(_BACKEND, name)

    def test_the_manifest_extension_reaches_the_job(self):
        spec = importlib.util.spec_from_file_location("conv_prefix", os.path.join(_LAMBDA_DIR, "lambda.py"))
        mod = importlib.util.module_from_spec(spec)
        with patch("boto3.client", MagicMock()), patch("boto3.resource", MagicMock()):
            spec.loader.exec_module(mod)
        manifest = {"inputFiles": [{"bucket": "b", "key": "x/a.glb", "relativePath": "/a.glb"}],
                    "outputs": {"bucket": "o", "files": "f/"},
                    "outputTarget": {"fileBaseExecutionPathExtension": "/o/x/"}}
        with patch.object(mod, "fetch_manifest", return_value=manifest):
            mod.resolve_inputs_from_manifest({"inputManifestS3Location": "s3://b/m.json"})
        assert mod._output_path_extension == "/o/x/"
        with patch.object(mod, "fetch_manifest", return_value={"inputFiles": []}):
            mod.resolve_inputs_from_manifest({})
        assert mod._output_path_extension is None


def _written_back(uploaded, files_prefix, extension, root):
    """Place each uploaded file at its asset-relative path with the extension, as the write-back does."""
    for key, data in uploaded.items():
        relative = usd_io.apply_output_path_extension(key[len(files_prefix):], extension)
        target = os.path.join(root, relative)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "wb") as handle:
            handle.write(data)
    return root


def _convert_with_prefix(objects, relative, output, options, extension):
    spec = importlib.util.spec_from_file_location("conv_prefix_run", os.path.join(_LAMBDA_DIR, "lambda.py"))
    mod = importlib.util.module_from_spec(spec)
    with patch("boto3.client", MagicMock()), patch("boto3.resource", MagicMock()):
        spec.loader.exec_module(mod)
    client = refs.FakeS3(objects)
    uploaded = {}

    def _download(bucket, key, path, **_):
        with open(path, "wb") as handle:
            handle.write(client.objects[key])
        return path

    def _upload(bucket, key, path):
        with open(path, "rb") as handle:
            uploaded[key] = handle.read()
        return key

    key = refs.ROOT + relative
    mod._results_location = None
    mod._output_path_extension = extension
    with patch.object(mod, "download", side_effect=_download), \
            patch.object(mod, "uploadV2", side_effect=_upload), \
            patch.object(mod, "head_input", return_value=(len(client.objects[key]), '"e"')), \
            patch.object(mod, "_s3_client_for_bucket", return_value=client):
        mod.convert_input_output(f"s3://abkt/{key}", "s3://obkt/files/", output, os.path.dirname(relative), "",
                                 options or {})
    return uploaded


def _textured_glb():
    return refs.ROOT + "scene/box.glb", __import__("new_format_helpers").textured_cube().export(file_type="glb")


def _unresolved(stage):
    """Asset paths the composed stage names that resolve to no file."""
    missing = []
    for layer in stage.GetUsedLayers():
        if not layer.realPath:
            continue
        _, references, _ = __import__("pxr.UsdUtils", fromlist=["x"]).ExtractExternalReferences(layer.realPath)
        for reference in references:
            base = str(reference).split("[", 1)[0]
            if base and not os.path.isfile(os.path.join(os.path.dirname(layer.realPath), base)):
                missing.append((os.path.basename(layer.realPath), str(reference)))
    return missing


@pytest.mark.unit
class TestLooseUsdUnderAPrefix:
    @pytest.mark.parametrize("extension", PREFIXES)
    @pytest.mark.parametrize("output", [".usdc", ".usda"])
    def test_a_flattened_layer_keeps_its_texture(self, tmp_path, extension, output):
        key, data = _textured_glb()
        uploaded = _convert_with_prefix({key: data}, "scene/box.glb", output,
                                        {"outputType": output}, extension)
        root = _written_back(uploaded, "files/", extension, str(tmp_path / "asset"))
        layer = os.path.join(root, usd_io.apply_output_path_extension(f"scene/box/box{output}", extension))
        assert os.path.isfile(layer), sorted(uploaded)
        stage = Usd.Stage.Open(layer)
        assert _unresolved(stage) == []
        scene = usd_io.read_usd(layer, root)[0]
        assert any(getattr(g.visual.material, "baseColorTexture", None) is not None for g in scene.geometry.values())

    @pytest.mark.parametrize("extension", PREFIXES)
    def test_convert_linked_layers_keep_resolving(self, tmp_path, extension):
        objects = refs._scene_objects()
        uploaded = _convert_with_prefix(objects, "scene/root.usda", ".usdc",
                                        {"referenceHandling": "convert-linked"}, extension)
        root = _written_back(uploaded, "files/", extension, str(tmp_path / "asset"))
        layer = os.path.join(root, usd_io.apply_output_path_extension("scene/root/root.usdc", extension))
        stage = Usd.Stage.Open(layer)
        assert _unresolved(stage) == []
        # /M from the sublayer, and /Body composed from the referenced layer's default prim (a mesh).
        assert stage.GetPrimAtPath("/M").IsA(UsdGeom.Mesh)
        assert stage.GetPrimAtPath("/Body").IsA(UsdGeom.Mesh)
        wheel = os.path.join(root, usd_io.apply_output_path_extension("scene/root/parts/wheel.usdc", extension))
        texture = os.path.join(root, usd_io.apply_output_path_extension("scene/root/parts/tex/green.png", extension))
        assert os.path.isfile(wheel) and os.path.isfile(texture)

    @pytest.mark.parametrize("extension,expected", [
        (None, "./t.png"), ("/", "./t.png"), ("/E/", "./t.png"), ("/E_", "./E_t.png"),
    ])
    def test_a_file_beside_the_layer(self, extension, expected):
        assert usd_io.placed_reference("m.usdc", "t.png", extension) == expected

    @pytest.mark.parametrize("extension,expected", [
        (None, "./parts/w.usdc"), ("/E/", "../parts/E/w.usdc"), ("/E_", "./parts/E_w.usdc"),
    ])
    def test_a_file_in_a_sub_folder(self, extension, expected):
        assert usd_io.placed_reference("root.usdc", "parts/w.usdc", extension) == expected

    def test_back_up_from_a_sub_folder(self):
        assert usd_io.placed_reference("parts/w.usdc", "tex/g.png", "/E/") == "../../tex/E/g.png"


@pytest.mark.unit
class TestALargeLayerIsNotReopened:
    """In-development defect D7: with an output path prefix the worker reopened every written layer
    after the export to rewrite its references; for a 281 MB STL written as USD the second load did not
    fit in the worker's address-space limit (signal 11 / std::bad_alloc). The references are now
    written with their placed paths as the layer is written, so no written layer is opened again."""

    @pytest.fixture()
    def opens(self, monkeypatch):
        from pxr import Sdf as _Sdf
        original = _Sdf.Layer.FindOrOpen
        calls = []

        def spy(identifier, *args, **kwargs):
            calls.append(str(identifier))
            return original(identifier, *args, **kwargs)
        monkeypatch.setattr(_Sdf.Layer, "FindOrOpen", staticmethod(spy))
        original_open = Usd.Stage.Open

        def stage_spy(*args, **kwargs):
            calls.append(str(args[0]) if args else "")
            return original_open(*args, **kwargs)
        monkeypatch.setattr(Usd.Stage, "Open", staticmethod(stage_spy))
        return calls

    @pytest.mark.parametrize("output", [".usdc", ".usda"])
    @pytest.mark.parametrize("extension", ["/o/x/", "/E_"])
    def test_a_flattened_output_is_never_reopened(self, tmp_path, opens, output, extension):
        import conversion_worker
        import new_format_helpers as helpers
        source = tmp_path / "src" / "big.stl"
        source.parent.mkdir()
        source.write_bytes(trimesh.creation.icosphere(subdivisions=6).export(file_type="stl"))
        work = tmp_path / "run"
        (work / "input").mkdir(parents=True)
        (work / "export").mkdir()
        (work / "input" / "big.stl").write_bytes(source.read_bytes())
        output_file = work / "export" / f"big{output}"
        conversion_worker.convert_model({
            "input_file": str(work / "input" / "big.stl"), "input_dir": str(work / "input"),
            "input_relative_path": "big.stl", "by_reference": {}, "output_file": str(output_file),
            "output_filetype": output, "input_name": "big.stl", "options": {},
            "output_path_extension": extension})
        assert output_file.is_file()
        reopened = [path for path in opens if os.path.realpath(path.split("[")[0]) == os.path.realpath(output_file)]
        assert reopened == [], reopened

    def test_a_textured_output_names_its_texture_by_the_placed_path(self, tmp_path, opens):
        import new_format_helpers as helpers
        export = tmp_path / "export"
        export.mkdir()
        usd_io.write_usd(helpers.textured_cube(), str(export / "m.usda"), 1.0, "y", ".usda",
                         output_path_extension="/E_")
        assert opens == []
        text = (export / "m.usda").read_text()
        assert "@./E_material_0.png@" in text and (export / "material_0.png").is_file()

    def test_a_usdz_package_keeps_its_internal_names(self, tmp_path):
        import new_format_helpers as helpers
        export = tmp_path / "export"
        export.mkdir()
        usd_io.write_usd(helpers.textured_cube(), str(export / "m.usdz"), 1.0, "y", ".usdz",
                         output_path_extension="/E_")
        import zipfile
        with zipfile.ZipFile(export / "m.usdz") as archive:
            layer = next(n for n in archive.namelist() if n.endswith(".usdc"))
            assert "material_0.png" in archive.namelist()
        scene = usd_io.read_usd(str(export / "m.usdz"), str(export))[0]
        assert any(getattr(g.visual.material, "baseColorTexture", None) is not None for g in scene.geometry.values())
