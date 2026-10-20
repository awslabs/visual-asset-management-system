#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Conversions run for real: ``convert_input_output`` with trimesh and Pillow, against an in-memory
asset bucket, on models generated here (#506, #507, #508).

- A textured OBJ (OBJ + MTL + PNG in one asset folder) converts to GLB with its texture (#506, #507).
- A glTF whose buffers are separate files converts, including the pipeline's own ``.gltf`` output fed
  back in from the run's output folder, and a separate glTF image is fetched (#507).
- A textured GLB keeps its texture through a GLB re-export (#506).
- A point cloud to STL fails with the STL message; to PLY it converts (#508).
- A buffer outside the asset, an absolute local path, and a missing buffer each fail before the loader
  runs; a missing or rejected material library and a missing image are skipped with a warning and the
  geometry converts; the loader cannot read a file the fetch did not stage.

The cases run in one child interpreter because the other modules in this directory install a
``MagicMock`` in ``sys.modules['trimesh']`` at collection, which would otherwise be what this module
imports. They are skipped when trimesh or Pillow is not installed; the in-image run in the PR
evidence executes the same script against the container's own dependency set.
"""

import json
import os
import subprocess
import sys

import pytest

_LAMBDA_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_SCRIPT = r'''
import importlib.util, io, json, os, sys, tempfile, types
from unittest.mock import MagicMock, patch

LAMBDA_DIR = sys.argv[1]
sys.path.insert(0, LAMBDA_DIR)
import numpy as np
import trimesh
from PIL import Image

try:
    import aws_lambda_powertools  # noqa: F401  (the real container logger when it is installed)
except ImportError:
    common = types.ModuleType("common"); common_logger = types.ModuleType("common.logger")
    common_logger.safeLogger = lambda **kw: MagicMock()
    common.logger = common_logger
    sys.modules["common"] = common; sys.modules["common.logger"] = common_logger

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_REGION", "us-east-1")
spec = importlib.util.spec_from_file_location("conversion_3dbasic_real", os.path.join(LAMBDA_DIR, "lambda.py"))
mod = importlib.util.module_from_spec(spec)
with patch("boto3.client", MagicMock()), patch("boto3.resource", MagicMock()):
    spec.loader.exec_module(mod)
from botocore.exceptions import ClientError


class Bucket:
    def __init__(self):
        self.objects, self.requested = {}, []

    def head_object(self, Bucket, Key):
        self.requested.append(Key)
        if Key not in self.objects:
            raise ClientError({"Error": {"Code": "404"}, "ResponseMetadata": {"HTTPStatusCode": 404}}, "HeadObject")
        return {"ContentLength": len(self.objects[Key])}

    def download_fileobj(self, Bucket, Key, Fileobj, ExtraArgs=None, **kwargs):
        self.requested.append(Key)
        Fileobj.write(self.objects[Key])

    def upload_file(self, Filename, Bucket, Key, **kwargs):
        with open(Filename, "rb") as handle:
            self.objects[Key] = handle.read()


bucket = Bucket()
mod.s3_client = bucket
# This handler uploads through the boto3 resource's client.
mod.s3 = types.SimpleNamespace(meta=types.SimpleNamespace(client=bucket))


class Recorder:
    """The handler's logger, keeping the warnings so a case can report what it skipped."""

    def __init__(self):
        self.warnings = []

    def warning(self, message, *args, **kwargs):
        self.warnings.append(str(message))

    def __getattr__(self, name):
        return lambda *args, **kwargs: None


mod.logger = Recorder()
FILES = "pipelines/conv/JOB/output/E1/files/"
rng = np.random.default_rng(7)


def put(key, data):
    bucket.objects[key] = data if isinstance(data, bytes) else data.encode("utf-8")


def textured_box():
    box = trimesh.creation.box()
    image = Image.fromarray((rng.random((16, 16, 3)) * 255).astype(np.uint8))
    box.visual = trimesh.visual.TextureVisuals(uv=rng.random((len(box.vertices), 2)), image=image)
    return box


def summary(key):
    data = bucket.objects[key]
    folder = key.rsplit("/", 1)[0] + "/"
    # A .gltf output names its buffers relative to itself; they were uploaded beside it.
    companions = {k[len(folder):]: v for k, v in bucket.objects.items() if k.startswith(folder)}
    loaded = trimesh.load(io.BytesIO(data), file_type=key.rsplit(".", 1)[-1], resolver=companions)
    parts = list(loaded.geometry.values()) if hasattr(loaded, "geometry") else [loaded]
    # Texture sizes rather than a flag: trimesh gives an OBJ with UVs and no material a 2x2 default
    # texture, so "has a texture" cannot tell a fetched texture from none.
    textures = []
    for part in parts:
        material = getattr(getattr(part, "visual", None), "material", None)
        image = getattr(material, "baseColorTexture", None) or getattr(material, "image", None)
        if image is not None:
            textures.append(list(image.size))
    return {"kinds": sorted(type(p).__name__ for p in parts),
            "vertices": int(sum(len(p.vertices) for p in parts)), "textures": sorted(textures)}


def run(name, input_key, output_type, subdir, check=None):
    before = set(bucket.objects)
    bucket.requested = []
    mod.logger.warnings = []
    try:
        mod.convert_input_output(f"s3://abkt/{input_key}", f"s3://abkt/{FILES}", output_type, subdir)
    except Exception as e:
        results[name] = {"error": type(e).__name__, "message": str(e), "requested": bucket.requested,
                         "warnings": mod.logger.warnings}
        return
    written = sorted(set(bucket.objects) - before)
    model = [k for k in written if k.endswith(output_type)]
    results[name] = {"written": written, "model": summary(model[0]) if model else None,
                     "requested": bucket.requested, "warnings": mod.logger.warnings}


results = {}
outside = tempfile.mkdtemp()

# A textured OBJ with its MTL and PNG beside it in the asset.
text, files = trimesh.exchange.obj.export_obj(textured_box(), include_texture=True, return_texture=True,
                                              mtl_name="box.mtl")
put("xasset/parts/box.obj", text)
for name, data in files.items():
    put(f"xasset/parts/{name}", data)
run("obj_textured_to_glb", "xasset/parts/box.obj", ".glb", "parts")

# The same OBJ without its MTL in the asset.
put("xasset/lonely/box.obj", text)
run("obj_missing_mtl", "xasset/lonely/box.obj", ".glb", "lonely")

# The same OBJ naming its MTL by a Windows absolute path, as an exporter on Windows can write it.
put("xasset/win/box.obj", text.replace("mtllib box.mtl", "mtllib C:\\models\\box.mtl"))
put("xasset/win/box.mtl", files["box.mtl"])
run("obj_windows_absolute_mtllib", "xasset/win/box.obj", ".glb", "win")

# A glTF with separate buffer files.
exported = trimesh.Scene(trimesh.creation.icosphere()).export(file_type="gltf")
for name, data in exported.items():
    put(f"xasset/gltf/{name}", data)
run("gltf_external_buffers_to_glb", "xasset/gltf/model.gltf", ".glb", "gltf")

# The same glTF without its buffer file.
put("xasset/nobuf/model.gltf", exported["model.gltf"])
run("gltf_missing_buffer", "xasset/nobuf/model.gltf", ".glb", "nobuf")

# A textured glTF whose image is a separate PNG, with and without that PNG in the asset.
textured = textured_box().export(file_type="gltf")
textured_doc = json.loads(textured["model.gltf"])
textured_doc["images"] = [{"uri": "tex/base.png", "mimeType": "image/png"}]
png = io.BytesIO()
Image.fromarray((rng.random((16, 16, 3)) * 255).astype(np.uint8)).save(png, format="PNG")
for folder, with_image in (("gltfimg", True), ("noimg", False)):
    put(f"xasset/{folder}/model.gltf", json.dumps(textured_doc))
    for name, data in textured.items():
        if name != "model.gltf":
            put(f"xasset/{folder}/{name}", data)
    if with_image:
        put(f"xasset/{folder}/tex/base.png", png.getvalue())
run("gltf_external_image_to_glb", "xasset/gltfimg/model.gltf", ".glb", "gltfimg")
run("gltf_missing_image_to_glb", "xasset/noimg/model.gltf", ".glb", "noimg")

# The pipeline's own .gltf output, converted again from the run's output folder.
put("xasset/round/ball.glb", trimesh.creation.icosphere().export(file_type="glb"))
run("glb_to_gltf", "xasset/round/ball.glb", ".gltf", "round")
gltf_key = FILES + "round/ball/ball.gltf"
FILES_FIRST = FILES
FILES = "pipelines/conv/JOB/output/E2/files/"
run("pipeline_gltf_output_to_glb", gltf_key, ".glb", "round/ball")
FILES = FILES_FIRST

# A textured GLB re-exported as GLB.
put("xasset/tex/textured.glb", textured_box().export(file_type="glb"))
run("textured_glb_to_glb", "xasset/tex/textured.glb", ".glb", "tex")

# Point clouds.
put("xasset/pc/cloud.xyz", "\n".join(" ".join(str(v) for v in row) for row in rng.random((100, 3))))
run("xyz_to_stl", "xasset/pc/cloud.xyz", ".stl", "pc")
run("xyz_to_ply", "xasset/pc/cloud.xyz", ".ply", "pc")
put("xasset/pc/points.ply", trimesh.PointCloud(rng.random((50, 3))).export(file_type="ply"))
run("point_ply_to_stl", "xasset/pc/points.ply", ".stl", "pc")

# A buffer in ANOTHER asset of the same bucket, which exists, named with a relative escape.
document = json.loads(exported["model.gltf"])
put("xother/secret.bin", exported[document["buffers"][0]["uri"]])
escape = json.loads(json.dumps(document))
escape["buffers"][0]["uri"] = "../../xother/secret.bin"
put("xasset/esc/model.gltf", json.dumps(escape))
run("gltf_buffer_in_another_asset", "xasset/esc/model.gltf", ".glb", "esc")

# A buffer named by an absolute local path that exists on this machine.
local_file = os.path.join(outside, "local.bin")
with open(local_file, "wb") as handle:
    handle.write(exported[document["buffers"][0]["uri"]])
absolute = json.loads(json.dumps(document))
absolute["buffers"][0]["uri"] = local_file
put("xasset/abs/model.gltf", json.dumps(absolute))
run("gltf_buffer_absolute_local_path", "xasset/abs/model.gltf", ".glb", "abs")

# An OBJ whose first 'mtllib' text is in a comment naming a local file. The reference scan reads
# statements only; trimesh's loader takes the first 'mtllib' anywhere in the text, so this is what
# the resolver alone has to refuse. The local MTL points at a local texture.
Image.fromarray((rng.random((8, 8, 3)) * 255).astype(np.uint8)).save(os.path.join(outside, "t.png"))
with open(os.path.join(outside, "local.mtl"), "w") as handle:
    handle.write(f"newmtl material_0\nmap_Kd {os.path.join(outside, 't.png')}\n")
put("xasset/cmt/box.obj", f"# mtllib {os.path.join(outside, 'local.mtl')}\n" + text.replace("mtllib box.mtl", ""))
run("obj_comment_mtllib_local_file", "xasset/cmt/box.obj", ".glb", "cmt")

print("RESULTS " + json.dumps(results))
'''


def _child_has_dependencies():
    probe = subprocess.run([sys.executable, "-c", "import trimesh, PIL, numpy, botocore"],  # nosemgrep: dangerous-subprocess-use-audit
                           capture_output=True, text=True)
    return probe.returncode == 0


@pytest.fixture(scope="module")
def results():
    if not _child_has_dependencies():
        pytest.skip("trimesh, Pillow, numpy and botocore are needed for the real conversions")
    run = subprocess.run([sys.executable, "-c", _SCRIPT, _LAMBDA_DIR],  # nosemgrep: dangerous-subprocess-use-audit
                         capture_output=True, text=True, timeout=300)
    assert run.returncode == 0, run.stderr[-4000:]
    line = [l for l in run.stdout.splitlines() if l.startswith("RESULTS ")][-1]
    return json.loads(line[len("RESULTS "):])


def _ok(results, name):
    result = results[name]
    assert "error" not in result, result
    return result


@pytest.mark.unit
class TestTexturesSurvive:
    def test_a_textured_obj_converts_to_glb_with_its_texture(self, results):
        result = _ok(results, "obj_textured_to_glb")
        assert result["model"]["textures"] == [[16, 16]], result
        # The MTL and PNG were fetched from the OBJ's own folder.
        assert {"xasset/parts/box.mtl", "xasset/parts/material_0.png"} <= set(result["requested"])

    def test_a_textured_glb_keeps_its_texture(self, results):
        assert _ok(results, "textured_glb_to_glb")["model"]["textures"] == [[16, 16]]


@pytest.mark.unit
class TestReferencedFilesAreFetched:
    def test_a_gltf_with_external_buffers_converts(self, results):
        result = _ok(results, "gltf_external_buffers_to_glb")
        assert result["model"]["vertices"] > 0, result

    def test_the_pipelines_own_gltf_output_converts_again(self, results):
        first = _ok(results, "glb_to_gltf")
        assert any(k.endswith("round/ball/ball.gltf") for k in first["written"]), first
        assert any(k.endswith(".bin") for k in first["written"]), first
        second = _ok(results, "pipeline_gltf_output_to_glb")
        assert second["model"]["vertices"] == first["model"]["vertices"], (first, second)

    def test_a_missing_material_library_converts_geometry_only_and_is_named(self, results):
        result = _ok(results, "obj_missing_mtl")
        assert result["model"]["vertices"] > 0, result
        # Not the 16x16 texture the MTL would have brought (2x2 is trimesh's untextured default).
        assert [16, 16] not in result["model"]["textures"], result
        assert any("lonely/box.mtl" in w and "not in the asset" in w for w in result["warnings"]), result

    def test_a_windows_absolute_material_library_converts_geometry_only(self, results):
        # The MTL exists beside the OBJ, so the reference is what is refused, not a missing file.
        result = _ok(results, "obj_windows_absolute_mtllib")
        assert result["model"]["vertices"] > 0, result
        assert "xasset/win/box.mtl" not in result["requested"], result
        assert any("not a relative path" in w for w in result["warnings"]), result

    def test_a_gltf_without_its_buffer_fails_and_names_it(self, results):
        result = results["gltf_missing_buffer"]
        assert result.get("error") == "ReferencedFileError", result
        assert "not in the asset" in result["message"] and ".bin" in result["message"], result

    def test_a_gltf_external_image_is_fetched_and_kept(self, results):
        result = _ok(results, "gltf_external_image_to_glb")
        assert result["model"]["textures"] == [[16, 16]], result
        assert "xasset/gltfimg/tex/base.png" in result["requested"], result

    def test_a_gltf_missing_its_image_converts_without_it(self, results):
        result = _ok(results, "gltf_missing_image_to_glb")
        assert result["model"]["vertices"] > 0, result
        assert [16, 16] not in result["model"]["textures"], result
        assert any("noimg/tex/base.png" in w for w in result["warnings"]), result


@pytest.mark.unit
class TestReferencesAreConfined:
    def test_a_buffer_in_another_asset_is_never_requested(self, results):
        result = results["gltf_buffer_in_another_asset"]
        assert result.get("error") == "ReferencedFileError", result
        assert "xother/secret.bin" not in result["requested"], result

    def test_an_absolute_local_path_is_rejected_before_loading(self, results):
        result = results["gltf_buffer_absolute_local_path"]
        assert result.get("error") == "ReferencedFileError", result

    def test_the_loader_cannot_read_a_file_the_fetch_did_not_stage(self, results):
        # The geometry converts; the 8x8 texture trimesh tried to read from the local disk does not.
        # (A 2x2 texture is trimesh's default for an OBJ with UVs and no material.)
        result = _ok(results, "obj_comment_mtllib_local_file")
        assert result["model"]["vertices"] > 0
        assert [8, 8] not in result["model"]["textures"], result


@pytest.mark.unit
class TestPointClouds:
    @pytest.mark.parametrize("name", ["xyz_to_stl", "point_ply_to_stl"])
    def test_a_point_cloud_to_stl_fails_clearly(self, results, name):
        result = results[name]
        assert result.get("error") == "ValueError", result
        assert "stores triangle meshes only" in result["message"], result
        assert not any(k.endswith(".stl") for k in result.get("written", [])), result

    def test_a_point_cloud_to_ply_keeps_the_points(self, results):
        result = _ok(results, "xyz_to_ply")
        assert result["model"]["kinds"] == ["PointCloud"] and result["model"]["vertices"] == 100
