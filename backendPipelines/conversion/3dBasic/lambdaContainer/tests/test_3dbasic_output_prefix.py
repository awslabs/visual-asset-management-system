#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Loose USD outputs keep resolving when the workflow writes them back under an output path prefix
(in-development defect D3).

The workflow inserts the execution's output path extension (a launch's, or the workflow's
``defaultOutputFileBaseExecutionPathExtension``) immediately before each output file's own name
(``backend/backend/common/workflows/outputPathExtension.py``). A layer and a texture in a sub-folder
end up apart: ``m/E/m.usdc`` and ``m/textures/E/a.png``. The pipeline now writes textures beside the
layer and rewrites every loose layer's references to where the write-back puts the files they name.

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

    def test_without_a_prefix_nothing_is_rewritten(self, tmp_path):
        export = tmp_path / "export"
        export.mkdir()
        usd_io.write_usd(__import__("new_format_helpers").textured_cube(), str(export / "m.usda"), 1.0, "y", ".usda")
        before = (export / "m.usda").read_text()
        for extension in (None, "", "/"):
            assert usd_io.relink_for_output_extension(str(export), extension) == 0
        assert (export / "m.usda").read_text() == before

    def test_a_reference_outside_the_export_is_left_alone(self, tmp_path):
        export = tmp_path / "export"
        (export / "sub").mkdir(parents=True)
        (export / "sub" / "t.png").write_bytes(b"png")
        layer = export / "a.usda"
        layer.write_text('#usda 1.0\ndef "X" {\n    asset a = @./sub/t.png@\n    asset b = @./gone.png@\n'
                         '    asset c = @/etc/hostname@\n}\n')
        usd_io.relink_for_output_extension(str(export), "/E/")
        text = layer.read_text()
        assert "@../sub/E/t.png@" in text and "@./gone.png@" in text and "@/etc/hostname@" in text
