#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Every input this pipeline reads converted to every output it writes, with real files and the real
libraries (trimesh, usd-core, pycollada, lxml) through ``conversion_worker.convert_model``. The built
image is covered by test_3dbasic_golden_round_trip.py; this is the same matrix without Docker.

What each cell checks: the triangle count, the bounding box after the unit and axis conventions, the
texture where both formats carry one, and that the output opens again. Point clouds to a target that
stores triangles only are refused.
"""

import io
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import new_format_helpers as helpers  # noqa: E402

trimesh = pytest.importorskip("trimesh")
pytest.importorskip("pxr")
pytest.importorskip("collada")
pytest.importorskip("lxml")
pytest.importorskip("networkx")
pytest.importorskip("PIL")

import usd_io  # noqa: E402

INPUTS = [".stl", ".obj", ".ply", ".gltf", ".glb", ".xyz", ".usd", ".usda", ".usdc", ".usdz", ".dae", ".3mf", ".off"]
OUTPUTS = [".stl", ".obj", ".ply", ".gltf", ".glb", ".usda", ".usdc", ".usdz", ".off", ".dae", ".3mf"]
USD = {".usd", ".usda", ".usdc", ".usdz"}
TRIANGLE_ONLY = {".stl", ".off", ".dae", ".3mf"}
TEXTURED_INPUTS = {".obj", ".gltf", ".glb", ".usd", ".usda", ".usdc", ".usdz"}
TEXTURED_OUTPUTS = {".obj", ".gltf", ".glb", ".usda", ".usdc", ".usdz"}
# What each input file declares (USD fixtures are written as metres and Y up).
Y_UP_DECLARED = {".gltf", ".glb", ".dae"} | USD


@pytest.fixture(scope="module")
def sources(tmp_path_factory):
    """``{extension: (path of the input file, the geometry it came from)}``."""
    root = tmp_path_factory.mktemp("sources")
    cube, ball = helpers.textured_cube(), helpers.sphere()
    made = {}

    def put(name, data, geometry, folder=None):
        directory = root / (folder or name)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / name).write_bytes(data)
        made[os.path.splitext(name)[1] if name != "part.usd" else ".usd"] = (str(directory / name), geometry)

    put("part.stl", ball.export(file_type="stl"), ball)
    obj_text, companions = trimesh.exchange.obj.export_obj(
        cube, include_texture=True, return_texture=True, mtl_name="part.mtl")
    put("part.obj", obj_text.encode(), cube)
    for companion, data in companions.items():
        (root / "part.obj" / companion).write_bytes(data)
    put("part.ply", ball.export(file_type="ply"), ball)
    gltf = trimesh.Scene(cube).export(file_type="gltf")
    put("part.gltf", gltf["model.gltf"], cube)
    for companion, data in gltf.items():
        if companion != "model.gltf":
            (root / "part.gltf" / companion).write_bytes(data)
    put("part.glb", cube.export(file_type="glb"), cube)
    put("part.xyz", "\n".join(" ".join(repr(float(v)) for v in row) for row in ball.vertices).encode(), ball)
    put("part.dae", ball.export(file_type="dae"), ball)
    put("part.3mf", ball.export(file_type="3mf"), ball)
    put("part.off", ball.export(file_type="off").encode(), ball)
    for extension in (".usda", ".usdc", ".usdz"):
        path = helpers.write_usd_fixture(str(root / extension.lstrip(".")), "part" + extension, cube)
        made[extension] = (path, cube)
    usda = made[".usda"][0]
    generic = root / "generic"
    generic.mkdir()
    (generic / "part.usd").write_bytes(open(usda, "rb").read())
    textures = os.path.join(os.path.dirname(usda), "textures")
    os.makedirs(generic / "textures")
    for name in os.listdir(textures):
        (generic / "textures" / name).write_bytes(open(os.path.join(textures, name), "rb").read())
    made[".usd"] = (str(generic / "part.usd"), cube)
    return made


def _extents(loaded):
    return np.array(loaded.bounds[1] - loaded.bounds[0])


def _expected_extents(input_extension, output_extension, extents):
    extents = np.array(extents, dtype=float)
    if input_extension == ".3mf" and output_extension in (".glb", ".gltf"):
        # millimetres to metres, and Z up to Y up
        extents = extents * 0.001
        extents = np.array([extents[0], extents[2], extents[1]])
    elif input_extension == ".3mf":
        pass
    elif output_extension == ".3mf" and input_extension in Y_UP_DECLARED:
        # 3MF is always Z up: a Y-up source is rotated
        extents = np.array([extents[0], extents[2], extents[1]])
    return extents


def _load_output(path, extension):
    if extension in USD:
        return usd_io.read_usd(path, os.path.dirname(path))[0]
    return trimesh.load(path)


def _triangles(loaded):
    parts = list(loaded.geometry.values()) if hasattr(loaded, "geometry") else [loaded]
    return sum(len(getattr(part, "faces", [])) for part in parts if getattr(part, "faces", None) is not None)


def _points(loaded):
    parts = list(loaded.geometry.values()) if hasattr(loaded, "geometry") else [loaded]
    return sum(len(part.vertices) for part in parts)


def _textures(loaded):
    parts = list(loaded.geometry.values()) if hasattr(loaded, "geometry") else [loaded]
    found = []
    for part in parts:
        material = getattr(getattr(part, "visual", None), "material", None)
        image = getattr(material, "baseColorTexture", None) or getattr(material, "image", None)
        if image is not None:
            found.append(image.size)
    return found


CELLS = [(i, o) for i in INPUTS for o in OUTPUTS]


@pytest.mark.unit
@pytest.mark.parametrize("input_extension,output_extension", CELLS,
                         ids=[f"{i.lstrip('.')}-to-{o.lstrip('.')}" for i, o in CELLS])
def test_every_input_converts_to_every_output(tmp_path, sources, input_extension, output_extension):
    source_path, geometry = sources[input_extension]
    if input_extension == ".xyz" and output_extension in TRIANGLE_ONLY:
        with pytest.raises(ValueError, match="contains no triangles"):
            helpers.convert(tmp_path, source_path, output_extension)
        return
    output = helpers.convert(tmp_path, source_path, output_extension)
    loaded = _load_output(output, output_extension)

    if input_extension == ".xyz":
        assert _points(loaded) == len(geometry.vertices)
        return
    assert _triangles(loaded) == len(geometry.faces), (input_extension, output_extension)
    np.testing.assert_allclose(
        _extents(loaded), _expected_extents(input_extension, output_extension, geometry.extents),
        rtol=1e-3, atol=1e-5)
    if input_extension in TEXTURED_INPUTS and output_extension in TEXTURED_OUTPUTS:
        assert _textures(loaded), f"{input_extension} -> {output_extension} lost the texture"
