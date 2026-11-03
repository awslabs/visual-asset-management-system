#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Conversions run for real: ``convert_input_output`` with trimesh and Pillow, against an in-memory
asset bucket, on models generated here (#506, #507, #508).

- A textured OBJ (OBJ + MTL + PNG in one asset folder) converts to GLB with its texture (#506, #507).
- A glTF whose buffers are separate files converts, including the pipeline's own ``.gltf`` output fed
  back in from the run's output folder, and a separate glTF image is fetched (#507).
- A textured GLB keeps its texture through a GLB re-export (#506).
- A point cloud to STL fails with the STL message; to PLY it converts (#508).
- A buffer outside the asset, an absolute path, and a missing buffer each fail before the loader
  runs; a missing or rejected material library and a missing image are skipped with a warning and the
  geometry converts; the loader cannot read a file the fetch did not stage.
- An input whose text is not UTF-8 -- binary data named as a text format, or an OBJ, ASCII STL, XYZ,
  glTF or GLB JSON chunk saved in another encoding -- fails saying the file could not be read, and
  fetches and writes nothing; the same text in UTF-8, and a binary STL whose header is not UTF-8, still
  convert (#910). These cases also hold in an interpreter without charset_normalizer, as in the image.

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
mod._s3_client_for_bucket = lambda name: bucket


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
                         "warnings": mod.logger.warnings, "written": sorted(set(bucket.objects) - before)}
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

# Inputs whose text is not UTF-8 (#910): binary data named as a text format, and text saved in another
# encoding ('Würfel' in Windows-1252), beside the same names in UTF-8. Each latin1 OBJ names a material
# library that is in the asset, so the result shows whether anything was fetched for it.
import struct
TRIANGLE = "v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n"
NAMED = "mtllib w.mtl\n# W\u00fcrfel\no W\u00fcrfel\n" + TRIANGLE
noise = bytes(rng.integers(0, 256, 4096, dtype=np.uint8))
put("xasset/enc/w.mtl", "newmtl material_0\nKd 1 0 0\n")
put("xasset/enc/noise.obj", noise)
run("obj_binary_noise", "xasset/enc/noise.obj", ".glb", "enc")
put("xasset/enc/latin1.obj", NAMED.encode("cp1252"))
run("obj_latin1", "xasset/enc/latin1.obj", ".glb", "enc")
put("xasset/enc/utf16.obj", NAMED.encode("utf-16"))
run("obj_utf16", "xasset/enc/utf16.obj", ".glb", "enc")
put("xasset/enc/utf8.obj", NAMED.encode("utf-8"))
run("obj_utf8_non_ascii", "xasset/enc/utf8.obj", ".glb", "enc")
# A material library that is not UTF-8, named by a UTF-8 OBJ.
put("xasset/encmtl/w.obj", ("mtllib w.mtl\nusemtl m\n" + TRIANGLE).encode("utf-8"))
put("xasset/encmtl/w.mtl", "newmtl m\n# W\u00fcrfel\nKd 1 0 0\nmap_Kd t.png\n".encode("cp1252"))
run("obj_with_latin1_mtl", "xasset/encmtl/w.obj", ".glb", "encmtl")
put("xasset/enc/noise.stl", noise)
run("stl_binary_noise", "xasset/enc/noise.stl", ".glb", "enc")
ascii_stl = trimesh.creation.box().export(file_type="stl_ascii")
ascii_stl = ascii_stl.decode("utf-8") if isinstance(ascii_stl, bytes) else ascii_stl
put("xasset/enc/latin1_ascii.stl", ascii_stl.replace("solid", "solid W\u00fcrfel", 1).encode("cp1252"))
run("stl_ascii_latin1", "xasset/enc/latin1_ascii.stl", ".glb", "enc")
put("xasset/enc/utf8_ascii.stl", ascii_stl.replace("solid", "solid W\u00fcrfel", 1).encode("utf-8"))
run("stl_ascii_utf8", "xasset/enc/utf8_ascii.stl", ".glb", "enc")
box_stl = trimesh.creation.box().export(file_type="stl")
put("xasset/enc/latin1_header.stl", "W\u00fcrfel".encode("cp1252").ljust(80, b" ") + box_stl[80:])
run("stl_binary_latin1_header", "xasset/enc/latin1_header.stl", ".glb", "enc")
put("xasset/enc/latin1.xyz", ("# W\u00fcrfel\n" + "\n".join("{} {} {}".format(*row) for row in rng.random((10, 3))))
    .encode("cp1252"))
run("xyz_latin1", "xasset/enc/latin1.xyz", ".ply", "enc")
gltf_doc = json.loads(exported["model.gltf"])
gltf_doc.setdefault("asset", {})["generator"] = "W\u00fcrfel"
for name, data in exported.items():
    if name != "model.gltf":
        put(f"xasset/encgltf/{name}", data)
put("xasset/encgltf/model.gltf", json.dumps(gltf_doc, ensure_ascii=False).encode("cp1252"))
run("gltf_latin1", "xasset/encgltf/model.gltf", ".glb", "encgltf")
glb = trimesh.creation.icosphere().export(file_type="glb")
json_length = struct.unpack("<I", glb[12:16])[0]
glb_doc = json.loads(glb[20:20 + json_length])
glb_doc.setdefault("asset", {})["generator"] = "W\u00fcrfel"
glb_json = json.dumps(glb_doc, ensure_ascii=False).encode("cp1252")
glb_json += b" " * (-len(glb_json) % 4)
glb_rest = glb[20 + json_length:]
put("xasset/enc/latin1.glb", struct.pack("<4sII", b"glTF", 2, 20 + len(glb_json) + len(glb_rest))
    + struct.pack("<I4s", len(glb_json), b"JSON") + glb_json + glb_rest)
run("glb_latin1_json", "xasset/enc/latin1.glb", ".obj", "enc")

# A buffer in ANOTHER asset of the same bucket, which exists, named with a relative escape.
document = json.loads(exported["model.gltf"])
put("xother/secret.bin", exported[document["buffers"][0]["uri"]])
escape = json.loads(json.dumps(document))
escape["buffers"][0]["uri"] = "../../xother/secret.bin"
put("xasset/esc/model.gltf", json.dumps(escape))
run("gltf_buffer_in_another_asset", "xasset/esc/model.gltf", ".glb", "esc")

# A buffer named by an absolute path is refused. A file is placed at that path, so the result shows the
# refusal rather than a missing file.
placed_buffer = os.path.join(outside, "placed.bin")
with open(placed_buffer, "wb") as handle:
    handle.write(exported[document["buffers"][0]["uri"]])
absolute = json.loads(json.dumps(document))
absolute["buffers"][0]["uri"] = placed_buffer
put("xasset/abs/model.gltf", json.dumps(absolute))
run("gltf_buffer_absolute_path", "xasset/abs/model.gltf", ".glb", "abs")

# An OBJ whose material library is outside the asset: the first 'mtllib' in its text, which is the
# one the loader reads, is in a comment and names an absolute path. It is skipped and the model
# converts without it. A material library naming an 8x8 texture is placed at that path, so the result
# shows the refusal rather than a missing file.
Image.fromarray((rng.random((8, 8, 3)) * 255).astype(np.uint8)).save(os.path.join(outside, "t.png"))
with open(os.path.join(outside, "placed.mtl"), "w") as handle:
    handle.write(f"newmtl material_0\nmap_Kd {os.path.join(outside, 't.png')}\n")
put("xasset/cmt/box.obj", f"# mtllib {os.path.join(outside, 'placed.mtl')}\n" + text.replace("mtllib box.mtl", ""))
run("obj_material_library_outside_the_asset", "xasset/cmt/box.obj", ".glb", "cmt")

# An OBJ whose material library name has a space (D1), as trimesh and Blender write it for
# 'my box.obj', with and without that library in the asset.
spaced, spaced_files = trimesh.exchange.obj.export_obj(textured_box(), include_texture=True,
                                                       return_texture=True, mtl_name="my box.mtl")
put("xasset/space/my box.obj", spaced)
for name, data in spaced_files.items():
    put(f"xasset/space/{name}", data)
run("obj_spaced_mtllib_to_glb", "xasset/space/my box.obj", ".glb", "space")
put("xasset/spacegone/my box.obj", spaced)
run("obj_spaced_mtllib_missing", "xasset/spacegone/my box.obj", ".glb", "spacegone")

# The names the OBJ and MTL collectors read, beside the names this trimesh asks its resolver for.
import referenced_files as rf


class Asked:
    def __init__(self, served):
        self.served, self.asked, self.parent, self.file_name = served, [], "", "names.obj"

    def get(self, name):
        self.asked.append(name)
        if name in self.served:
            return self.served[name]
        raise FileNotFoundError(name)

    __getitem__ = get


FACES = "v 0 0 0\nv 1 0 0\nv 0 1 0\nvt 0 0\nvt 1 0\nvt 0 1\nf 1/1 2/2 3/3\n"
OBJ_TEXTS = [
    "mtllib my box.mtl\n" + FACES, "mtllib\tmy  box.mtl  \n" + FACES,
    ("mtllib my box.mtl\n" + FACES).replace("\n", "\r\n"), "mtllib a.mtl b.mtl\n" + FACES,
    "mtllib a.mtl\nmtllib b.mtl\n" + FACES, "# mtllib c.mtl\nmtllib d.mtl\n" + FACES,
    "o x_mtllib y.mtl\nmtllib d.mtl\n" + FACES, "mtllib my\\\nbox.mtl\n" + FACES,
    "mtllib my\\\r\nbox.mtl\r\n" + FACES, FACES + "mtllib x.mtl\\\n", "MTLLIB u.mtl\n" + FACES,
    "mtllib\n" + FACES, FACES,
]
MTL_TEXT = ("newmtl a\nmap_Kd my wood.png\nmap_Kd -s 1 1 1 wood.png\nbump normal.png\nmap_Ka ambient.png\n"
            "map_Bump b.png\nnorm n.png\nKd 1 1 1\n  map_kd\t lower.png  \nmap_Kd\nnewmtl b\r\nmap_Kd b c.png\r\n")
names_path = os.path.join(outside, "names.obj")
reference_names = {"obj": [], "mtl": None}
for text in OBJ_TEXTS:
    with open(names_path, "wb") as handle:
        handle.write(text.encode("utf-8"))
    asked = Asked({})
    trimesh.load(names_path, resolver=asked)
    reference_names["obj"].append({"text": text, "trimesh": asked.asked,
                                   "collector": [r.raw for r in rf._obj_references(names_path)]})
with open(names_path, "w") as handle:
    handle.write("mtllib m.mtl\n" + FACES)
mtl_path = os.path.join(outside, "m.mtl")
with open(mtl_path, "wb") as handle:
    handle.write(MTL_TEXT.encode("utf-8"))
asked = Asked({"m.mtl": MTL_TEXT.encode("utf-8")})
trimesh.load(names_path, resolver=asked)
reference_names["mtl"] = {"trimesh": asked.asked[1:], "collector": [r.raw for r in rf._mtl_references(mtl_path)]}
results["reference_names"] = reference_names

print("RESULTS " + json.dumps(results))
'''


def _child_has_dependencies():
    # nosemgrep: dangerous-subprocess-use-audit -- fixed argv (this interpreter, a constant import check), no shell
    probe = subprocess.run([sys.executable, "-c", "import trimesh, PIL, numpy, botocore"],
                           capture_output=True, text=True)
    return probe.returncode == 0


@pytest.fixture(scope="module")
def results():
    if not _child_has_dependencies():
        pytest.skip("trimesh, Pillow, numpy and botocore are needed for the real conversions")
    # nosemgrep: dangerous-subprocess-use-audit -- fixed argv (this interpreter, this module's script and directory), no shell
    run = subprocess.run([sys.executable, "-c", _SCRIPT, _LAMBDA_DIR],
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
class TestReferenceNamesMatchTheLoader:
    """The collectors read the names trimesh asks for (D1), so the fetch requests, and its warnings
    name, only files the loader reads."""

    def test_a_material_library_named_with_a_space_keeps_its_texture(self, results):
        result = _ok(results, "obj_spaced_mtllib_to_glb")
        assert result["model"]["textures"] == [[16, 16]], result
        assert "xasset/space/my box.mtl" in result["requested"], result
        assert not [w for w in result["warnings"] if w.startswith("Skipped")], result

    def test_a_missing_material_library_named_with_a_space_is_one_warning_naming_it(self, results):
        result = _ok(results, "obj_spaced_mtllib_missing")
        assert result["model"]["vertices"] > 0, result
        skipped = [w for w in result["warnings"] if w.startswith("Skipped")]
        assert len(skipped) == 1, result
        assert skipped[0].startswith("Skipped spacegone/my box.mtl, referenced by spacegone/my box.obj"), result

    def test_obj_material_library_names_are_the_ones_trimesh_asks_for(self, results):
        for case in results["reference_names"]["obj"]:
            # An empty name names no file, so the collector leaves it out.
            assert case["collector"] == [name for name in case["trimesh"] if name], case

    def test_mtl_texture_names_are_the_ones_trimesh_asks_for(self, results):
        case = results["reference_names"]["mtl"]
        assert case["trimesh"] and case["collector"] == case["trimesh"], case


@pytest.mark.unit
class TestReferencesAreConfined:
    def test_a_buffer_in_another_asset_is_never_requested(self, results):
        result = results["gltf_buffer_in_another_asset"]
        assert result.get("error") == "ReferencedFileError", result
        assert "xother/secret.bin" not in result["requested"], result

    def test_an_absolute_path_is_rejected_before_loading(self, results):
        result = results["gltf_buffer_absolute_path"]
        assert result.get("error") == "ReferencedFileError", result

    def test_the_loader_cannot_read_a_file_the_fetch_did_not_stage(self, results):
        # A material library outside the asset is skipped: the geometry converts, without the 8x8
        # texture the library at that path names.
        # (A 2x2 texture is trimesh's default for an OBJ with UVs and no material.)
        result = _ok(results, "obj_material_library_outside_the_asset")
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


@pytest.mark.unit
class TestInputsThatAreNotUtf8Text:
    """An input whose text is not UTF-8 fails with a message saying it could not be read, instead of
    trimesh's "No module named 'charset_normalizer'", and nothing is fetched or written (#910)."""

    @pytest.mark.parametrize("name, file_name, read_as", [
        ("obj_binary_noise", "noise.obj", "OBJ text"),
        ("obj_latin1", "latin1.obj", "OBJ text"),
        ("obj_utf16", "utf16.obj", "OBJ text"),
        ("stl_binary_noise", "noise.stl", "STL: it is not a binary STL"),
        ("stl_ascii_latin1", "latin1_ascii.stl", "STL: it is not a binary STL"),
        ("xyz_latin1", "latin1.xyz", "XYZ text"),
        ("gltf_latin1", "model.gltf", "glTF JSON"),
        ("glb_latin1_json", "latin1.glb", "GLB: in its JSON chunk"),
    ])
    def test_it_fails_saying_the_file_could_not_be_read(self, results, name, file_name, read_as):
        result = results[name]
        assert result.get("error") == "InputTextError", result
        assert result["message"].startswith(f"{file_name} could not be read as {read_as}"), result
        assert "is not UTF-8" in result["message"] and "charset" not in result["message"], result
        assert result["written"] == [], result

    def test_nothing_the_obj_references_is_fetched(self, results):
        # The material library it names is in the asset; only the input itself was downloaded.
        assert results["obj_latin1"]["requested"] == ["xasset/enc/latin1.obj"], results["obj_latin1"]

    @pytest.mark.parametrize("name", ["obj_utf8_non_ascii", "stl_ascii_utf8", "stl_binary_latin1_header"])
    def test_utf8_text_and_a_binary_stl_header_still_convert(self, results, name):
        # The positive controls: the same names in UTF-8, and a binary STL, whose header trimesh reads
        # only as metadata.
        assert _ok(results, name)["model"]["vertices"] > 0

    def test_a_material_library_that_is_not_utf8_is_skipped(self, results):
        result = _ok(results, "obj_with_latin1_mtl")
        assert result["model"]["vertices"] > 0, result
        assert "xasset/encmtl/t.png" not in result["requested"], result
        assert any("encmtl/w.mtl" in w for w in result["warnings"]), result
