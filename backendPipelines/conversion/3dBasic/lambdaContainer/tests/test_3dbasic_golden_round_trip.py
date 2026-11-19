#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Golden round trip: every input format this container accepts, converted to every output format it
writes, by the BUILT IMAGE running under the Lambda runtime interface emulator.

The unit tests exercise the handler with the loader stubbed or in this interpreter. This suite drives
the same image that ships: ``tests/golden/run_golden.sh`` builds it, starts an S3 stand-in (moto) and the
image with the emulator, and runs this module against them. Without ``GOLDEN_RIE_URL`` and
``GOLDEN_S3_ENDPOINT`` the module is skipped, so a plain ``python -m pytest`` of this directory is
unaffected.

Fixtures are generated here from code (a textured cube, an icosphere, a point cloud), so nothing is
downloaded and nothing third-party is committed. Each cell converts one fixture to one format and checks
what the result must keep: triangle count, bounding box, point count, and the texture where both the
input and the output format carry one; a point cloud to STL must be refused with the "no triangles"
message. Adding a format means adding its fixture to ``FIXTURES`` and its extension to ``OUTPUTS``.

``GOLDEN_DUMP_DIR`` (optional) writes every output file to that directory, so two images can be
compared byte for byte."""

import io
import json
import os
import re
import urllib.request

import numpy as np
import pytest

RIE_URL = os.environ.get("GOLDEN_RIE_URL")
S3_ENDPOINT = os.environ.get("GOLDEN_S3_ENDPOINT")
DUMP_DIR = os.environ.get("GOLDEN_DUMP_DIR")

needs_image = pytest.mark.skipif(
    not (RIE_URL and S3_ENDPOINT),
    reason="needs the built image (GOLDEN_RIE_URL) and an S3 endpoint (GOLDEN_S3_ENDPOINT)")

trimesh = pytest.importorskip("trimesh")
boto3 = pytest.importorskip("boto3")
Image = pytest.importorskip("PIL.Image")

ASSET_BUCKET = "golden-assets"
OUTPUT_BUCKET = "golden-outputs"
FILES_PREFIX = "pipelines/golden/JOB/output/E1/files/"

# The formats this container accepts, and the ones it writes (its SUPPORTED_*_FORMATS).
INPUTS = [".stl", ".obj", ".ply", ".gltf", ".glb", ".xyz", ".usd", ".usda", ".usdc", ".usdz", ".dae", ".3mf", ".off",
          ".fbx", ".step", ".stp"]
# GOLDEN_STEP=1 when the image under test was built with ENABLE_STEP_INPUT=true; without it every STEP
# cell must be refused before the download, naming the configuration key.
STEP_IMAGE = os.environ.get("GOLDEN_STEP") == "1"
STEP_FORMATS = {".step", ".stp"}
# The committed 40 x 40 x 10 mm Open CASCADE plate of the CAD STEP agent's evaluation fixtures.
_STEP_PLATE = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "..", "genAi", "cadStepAgent", "evaluation",
    "fixtures", "plate-40x40x10.step"))
OUTPUTS = [".stl", ".obj", ".ply", ".gltf", ".glb", ".usda", ".usdc", ".usdz", ".off", ".dae", ".3mf"]
# Formats that can carry a texture image in this pipeline's output.
TEXTURED_OUTPUTS = {".obj", ".gltf", ".glb", ".usda", ".usdc", ".usdz"}
# The targets that store triangles only: a point cloud to one of them is refused.
TRIANGLE_ONLY_OUTPUTS = {".stl", ".off", ".dae", ".3mf"}
# Formats this suite reads with the container's own USD code rather than trimesh.
USD_FORMATS = {".usd", ".usda", ".usdc", ".usdz"}


def _textured_cube():
    rng = np.random.default_rng(11)
    box = trimesh.creation.box(extents=(2.0, 3.0, 4.0))
    image = Image.fromarray((rng.random((16, 16, 3)) * 255).astype(np.uint8))
    box.visual = trimesh.visual.TextureVisuals(uv=rng.random((len(box.vertices), 2)), image=image)
    return box


def _sphere():
    return trimesh.creation.icosphere(subdivisions=2, radius=1.5)


def _usd_bytes(geometry, extension):
    """A geometry written as USD by the container's own writer (needs usd-core on the test host)."""
    import sys
    import tempfile
    lambda_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if lambda_dir not in sys.path:
        sys.path.insert(0, lambda_dir)
    import usd_io
    work = tempfile.mkdtemp()
    export_dir = os.path.join(work, "export")
    os.makedirs(export_dir)
    path = os.path.join(export_dir, "part" + extension)
    usd_io.write_usd(trimesh.Scene(geometry), path, 1.0, "y", extension)
    files = {}
    for root, _, names in os.walk(export_dir):
        for name in names:
            full = os.path.join(root, name)
            with open(full, "rb") as handle:
                files[os.path.relpath(full, export_dir).replace(os.sep, "/")] = handle.read()
    return files


def _fbx_box():
    import sys
    tests_dir = os.path.dirname(os.path.abspath(__file__))
    if tests_dir not in sys.path:
        sys.path.insert(0, tests_dir)
    import fbx_fixture
    return fbx_fixture.box_fbx(texture="tex/wood.png")


def _png_bytes():
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), (30, 120, 200)).save(buffer, format="PNG")
    return buffer.getvalue()


def _fixture_files():
    """``{input extension: ({name: bytes}, source geometry)}``; the first name is the input file."""
    cube, sphere = _textured_cube(), _sphere()
    obj_text, obj_companions = trimesh.exchange.obj.export_obj(
        cube, include_texture=True, return_texture=True, mtl_name="part.mtl")
    gltf = trimesh.Scene(cube).export(file_type="gltf")
    return {
        ".stl": ({"part.stl": sphere.export(file_type="stl")}, sphere, False),
        ".obj": ({"part.obj": obj_text.encode("utf-8"), **obj_companions}, cube, True),
        ".ply": ({"part.ply": sphere.export(file_type="ply")}, sphere, False),
        ".gltf": ({"part.gltf": gltf["model.gltf"], **{k: v for k, v in gltf.items() if k != "model.gltf"}},
                  cube, True),
        ".glb": ({"part.glb": cube.export(file_type="glb")}, cube, True),
        ".xyz": ({"part.xyz": "\n".join(" ".join(repr(float(v)) for v in row)
                                       for row in sphere.vertices).encode("utf-8")}, sphere, False),
        # A loose USD file's textures are not fetched by the handler yet (it has no USD reference
        # collector), so the cells for them carry no texture expectation; a USDZ holds its own.
        ".usd": ((lambda files: {"part.usd": files.pop("part.usda"), **files})(_usd_bytes(cube, ".usda")), cube, False),
        ".usda": (_usd_bytes(cube, ".usda"), cube, False),
        ".usdc": (_usd_bytes(cube, ".usdc"), cube, False),
        ".usdz": (_usd_bytes(cube, ".usdz"), cube, True),
        ".dae": ({"part.dae": sphere.export(file_type="dae")}, sphere, False),
        ".3mf": ({"part.3mf": sphere.export(file_type="3mf")}, sphere, False),
        ".off": ({"part.off": sphere.export(file_type="off").encode("utf-8")}, sphere, False),
        # A 2 x 3 x 4 box in ASCII FBX with a texture beside it; FBX's unit is the centimetre, so a glTF
        # output (metres) is 0.02 x 0.03 x 0.04.
        ".fbx": ({"part.fbx": _fbx_box(), "tex/wood.png": _png_bytes()},
                 trimesh.creation.box(extents=(2.0, 3.0, 4.0)), True),
        ".step": ({"part.step": open(_STEP_PLATE, "rb").read()}, trimesh.creation.box(extents=(0.04, 0.04, 0.01)), False),
        ".stp": ({"part.stp": open(_STEP_PLATE, "rb").read()}, trimesh.creation.box(extents=(0.04, 0.04, 0.01)), False),
    }


FIXTURES = _fixture_files() if (RIE_URL and S3_ENDPOINT) else {}


@pytest.fixture(scope="module")
def s3():
    client = boto3.client("s3", endpoint_url=S3_ENDPOINT, region_name="us-east-1",
                          aws_access_key_id="test", aws_secret_access_key="test")
    for bucket in (ASSET_BUCKET, OUTPUT_BUCKET):
        client.create_bucket(Bucket=bucket)
    return client


def _invoke(body):
    request = urllib.request.Request(
        RIE_URL, data=json.dumps({"body": body}).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=300) as response:
        return json.loads(response.read().decode("utf-8") or "null")


def _convert(s3, cell, input_extension, output_extension):
    """Stage the fixture in its own asset folder, run the image on it, return ``(response, written)``
    where ``written`` maps each output key under the run's files prefix to its bytes."""
    names, _, _ = FIXTURES[input_extension]
    folder = f"x{cell}"
    for name, data in names.items():
        s3.put_object(Bucket=ASSET_BUCKET, Key=f"{folder}/{name}", Body=data)
    input_name = next(iter(names))
    prefix = f"{FILES_PREFIX}{cell}/"
    manifest = {"inputFiles": [{"relativePath": f"/{input_name}", "bucket": ASSET_BUCKET,
                                "key": f"{folder}/{input_name}"}],
                "outputs": {"bucket": OUTPUT_BUCKET, "files": prefix}}
    s3.put_object(Bucket=ASSET_BUCKET, Key=f"manifests/{cell}.json", Body=json.dumps(manifest).encode())
    s3.put_object(Bucket=ASSET_BUCKET, Key=f"configs/{cell}.json",
                  Body=json.dumps({"outputType": output_extension}).encode())
    response = _invoke({"inputManifestS3Location": f"s3://{ASSET_BUCKET}/manifests/{cell}.json",
                        "inputConfigurationS3Location": f"s3://{ASSET_BUCKET}/configs/{cell}.json",
                        "workflowExecutionId": f"golden-{cell}"})
    written = {}
    listing = s3.list_objects_v2(Bucket=OUTPUT_BUCKET, Prefix=prefix)
    for item in listing.get("Contents", []):
        written[item["Key"][len(prefix):]] = s3.get_object(
            Bucket=OUTPUT_BUCKET, Key=item["Key"])["Body"].read()
    if DUMP_DIR:
        for relative, data in written.items():
            path = os.path.join(DUMP_DIR, cell, relative)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as handle:
                handle.write(data)
    return response, written


# What each input declares: USD fixtures are written as metres and Y up, DAE is metres and Y up by
# specification, glTF and GLB are metres and Y up, and a 3MF fixture is millimetres and Z up.
_Y_UP_DECLARED = {".gltf", ".glb", ".dae"} | USD_FORMATS


def _expected_extents(input_extension, output_extension, extents):
    """The source extents after the unit and axis conventions of the pair (see units_axes.py): a 3MF
    source converted to glTF is millimetres to metres and Z up to Y up, a Y-up source written as 3MF
    is rotated to Z up (3MF is always Z up)."""
    extents = np.array(extents, dtype=float)
    if input_extension in STEP_FORMATS:
        # metres, Z up: glTF is rotated to Y up; 3MF keeps Z up and declares metres.
        if output_extension in (".glb", ".gltf"):
            return np.array([extents[0], extents[2], extents[1]])
        return extents
    if input_extension == ".fbx" and output_extension in (".glb", ".gltf"):
        return extents * 0.01
    if input_extension == ".fbx" and output_extension == ".3mf":
        return np.array([extents[0], extents[2], extents[1]])
    if input_extension == ".3mf" and output_extension in (".glb", ".gltf"):
        return np.array([extents[0], extents[2], extents[1]]) * 0.001
    if output_extension == ".3mf" and input_extension in _Y_UP_DECLARED:
        return np.array([extents[0], extents[2], extents[1]])
    return extents


def _parts(loaded):
    geometry = getattr(loaded, "geometry", None)
    return list(geometry.values()) if isinstance(geometry, dict) else [loaded]


def _summary(loaded):
    parts = _parts(loaded)
    vertices = sum(len(getattr(p, "vertices", [])) for p in parts)
    triangles = sum(len(getattr(p, "faces", [])) for p in parts if getattr(p, "faces", None) is not None)
    bounds = np.array(loaded.bounds)
    textures = []
    for part in parts:
        material = getattr(getattr(part, "visual", None), "material", None)
        image = getattr(material, "baseColorTexture", None) or getattr(material, "image", None)
        if image is not None:
            textures.append(tuple(image.size))
    return {"vertices": vertices, "triangles": triangles, "extents": bounds[1] - bounds[0],
            "textures": textures}


def _load_output(written, output_extension):
    models = [name for name in written if name.lower().endswith(output_extension)]
    assert len(models) == 1, f"expected one {output_extension} model, wrote {sorted(written)}"
    name = models[0]
    folder = name.rsplit("/", 1)[0] + "/" if "/" in name else ""
    if output_extension in USD_FORMATS:
        import sys
        import tempfile
        lambda_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if lambda_dir not in sys.path:
            sys.path.insert(0, lambda_dir)
        import usd_io
        work = tempfile.mkdtemp()
        for written_name, data in written.items():
            target = os.path.join(work, written_name)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "wb") as handle:
                handle.write(data)
        return usd_io.read_usd(os.path.join(work, name), os.path.join(work, folder.rstrip("/")))[0]
    companions = {n[len(folder):]: d for n, d in written.items() if n.startswith(folder) and n != name}
    return trimesh.load(io.BytesIO(written[name]), file_type=output_extension.lstrip("."), resolver=companions)


CELLS = [(i, o) for i in INPUTS for o in OUTPUTS]


@needs_image
@pytest.mark.golden
@pytest.mark.parametrize("input_extension,output_extension", CELLS,
                         ids=[f"{i.lstrip('.')}-to-{o.lstrip('.')}" for i, o in CELLS])
def test_every_input_converts_to_every_output(s3, input_extension, output_extension):
    cell = f"{input_extension.lstrip('.')}_{output_extension.lstrip('.')}"
    response, written = _convert(s3, cell, input_extension, output_extension)

    if input_extension in STEP_FORMATS and not STEP_IMAGE:
        assert isinstance(response, dict) and "enableStepInput" in response.get("errorMessage", ""), response
        assert not written
        return

    if input_extension == ".xyz" and output_extension in TRIANGLE_ONLY_OUTPUTS:
        assert isinstance(response, dict) and "contains no triangles" in response.get("errorMessage", ""), response
        assert not written
        return

    assert not (isinstance(response, dict) and "errorMessage" in response), response
    assert response == {"statusCode": 200, "body": "Success"}
    expected = _summary(FIXTURES[input_extension][1])
    got = _summary(_load_output(written, output_extension))

    if input_extension == ".xyz":
        # A point cloud keeps its points.
        assert got["vertices"] == expected["vertices"]
    elif input_extension in STEP_FORMATS:
        # Tessellated: a plate with rounded nothing still has at least the box's 12 triangles.
        assert got["triangles"] >= 12
    else:
        assert got["triangles"] == expected["triangles"]
    # STL stores 32-bit floats and glTF accessors are 32-bit too.
    np.testing.assert_allclose(
        got["extents"], _expected_extents(input_extension, output_extension, expected["extents"]),
        rtol=1e-4, atol=1e-6)

    textured_source = FIXTURES[input_extension][2]
    if textured_source and output_extension in TEXTURED_OUTPUTS:
        assert got["textures"], f"{input_extension} -> {output_extension} lost the texture"


def test_the_cell_list_covers_every_accepted_input_and_every_written_output():
    # The suite is the contract for the formats the handler accepts: a format added to the handler
    # without a fixture here is a test that fails, not a format nobody round-trips.
    lambda_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(lambda_dir, "lambda.py"), encoding="utf-8") as handle:
        source = handle.read()
    inputs = set(re.findall(r"'(\.\w+)'", re.search(r"SUPPORTED_INPUT_FORMATS = \{([^}]*)\}", source).group(1)))
    outputs = set(re.findall(r"'(\.\w+)'", re.search(r"SUPPORTED_OUTPUT_FORMATS = \{([^}]*)\}", source).group(1)))
    assert inputs == set(INPUTS)
    assert outputs == set(OUTPUTS)
