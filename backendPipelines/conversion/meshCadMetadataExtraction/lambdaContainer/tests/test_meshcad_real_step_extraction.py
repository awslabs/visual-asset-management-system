#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""STEP files run through the real extractor: ``extract_metadata`` with cadquery, on a committed STEP
file and on models generated here (#1039).

``cq.importers.importStep`` returns a cadquery ``Workplane``, not a ``Shape``. The STEP extractor
called ``ShapeType()`` on it, so every ``.step`` / ``.stp`` input failed with "'Workplane' object has
no attribute 'ShapeType'" and wrote no attributes.

- A one-solid part (``fixtures/cylinder-r4-h5.step``) extracts its real geometry, as ``.step`` and as
  ``.stp``.
- A two-body compound records an assembly hierarchy with both bodies.
- A file with two STEP roots counts both, not only the first.
- A file with no shapes fails the run and says so.
- A model with no solid has no volume, and surfaces beside solids add nothing to it (#1190).
  cadquery's ``Volume()`` on a shell or face returns its surface area, so a surface model used to
  report its area as ``volume``.

The fixtures were written by cadquery 2.6.0:

- ``cylinder-r4-h5.step``, a cylinder of radius 4 and height 5:
  ``cq.exporters.export(cq.Workplane("XY").cylinder(5, 4), "cylinder-r4-h5.step")``.
- ``plane-10x20-surface.step``, one 10 x 20 plane face and no solid (a ``SHELL_BASED_SURFACE_MODEL``):
  ``cq.exporters.export(cq.Face.makePlane(10, 20), "plane-10x20-surface.step")``.

The cases run in one child interpreter because the other modules in this directory install a
``MagicMock`` in ``sys.modules['cadquery']`` at collection, which would otherwise be what this module
imports. They are skipped when cadquery is not installed.
"""

import json
import math
import os
import subprocess
import sys

import pytest

_LAMBDA_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
_FIXTURE = os.path.join(_FIXTURES, "cylinder-r4-h5.step")
_SURFACE_FIXTURE = os.path.join(_FIXTURES, "plane-10x20-surface.step")

_SCRIPT = r'''
import importlib.util, json, os, shutil, sys, tempfile, types
from unittest.mock import MagicMock, patch

LAMBDA_DIR, FIXTURE, SURFACE_FIXTURE = sys.argv[1], sys.argv[2], sys.argv[3]
sys.path.insert(0, LAMBDA_DIR)
import cadquery as cq
from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer

try:
    import aws_lambda_powertools  # noqa: F401  (the real container logger when it is installed)
except ImportError:
    common = types.ModuleType("common"); common_logger = types.ModuleType("common.logger")
    common_logger.safeLogger = lambda **kw: MagicMock()
    common.logger = common_logger
    sys.modules["common"] = common; sys.modules["common.logger"] = common_logger

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_REGION", "us-east-1")
spec = importlib.util.spec_from_file_location("meshcad_real_step", os.path.join(LAMBDA_DIR, "lambda.py"))
mod = importlib.util.module_from_spec(spec)
with patch("boto3.client", MagicMock()), patch("boto3.resource", MagicMock()):
    spec.loader.exec_module(mod)

work = tempfile.mkdtemp()
results = {}


def run(name, source, relative_path):
    """extract_metadata on a local file, with the S3 download and upload replaced by local copies."""
    uploaded = []

    def download(bucket, key, path):
        shutil.copyfile(source, path)
        return path

    def upload(bucket, key, path):
        with open(path, encoding="utf-8") as handle:
            uploaded.append({"key": key, "body": json.load(handle)})
        return key

    with patch.object(mod, "download", download), patch.object(mod, "upload", upload):
        try:
            mod.extract_metadata(relative_path, f"s3://abkt/xasset/{relative_path}", "s3://obkt/out/metadata/")
        except Exception as e:
            results[name] = {"error": str(e), "uploaded": uploaded}
            return
    attributes = {}
    for upload_record in uploaded:
        for entry in upload_record["body"]["metadata"]:
            attributes[entry["metadataKey"]] = json.loads(entry["metadataValue"])
    results[name] = {"keys": [u["key"] for u in uploaded], "attributes": attributes}


# The committed one-solid part, under both STEP extensions.
run("part_step", FIXTURE, "parts/cylinder-r4-h5.step")
stp = os.path.join(work, "cylinder.stp")
shutil.copyfile(FIXTURE, stp)
run("part_stp", stp, "parts/cylinder-r4-h5.stp")

# Two bodies in one compound, as an assembly export writes them.
two_bodies = os.path.join(work, "two-bodies.step")
cq.exporters.export(
    cq.Workplane("XY").box(10, 10, 10).union(cq.Workplane("XY").box(10, 10, 10).translate((30, 0, 0))),
    two_bodies)
run("compound", two_bodies, "parts/two-bodies.step")

# Two separate STEP roots in one file.
two_roots = os.path.join(work, "two-roots.step")
writer = STEPControl_Writer()
for offset in (0, 30):
    writer.Transfer(cq.Workplane("XY").box(10, 10, 10).translate((offset, 0, 0)).val().wrapped, STEPControl_AsIs)
writer.Write(two_roots)
results["two_roots_imported"] = [type(v).__name__ for v in cq.importers.importStep(two_roots).vals()]
run("two_roots", two_roots, "parts/two-roots.step")

# A well-formed STEP file with no shapes in it.
empty = os.path.join(work, "empty.step")
with open(empty, "w", encoding="utf-8") as handle:
    handle.write(
        "ISO-10303-21;\nHEADER;\nFILE_DESCRIPTION(('empty'),'2;1');\n"
        "FILE_NAME('empty.step','2026-01-01T00:00:00',(''),(''),'','','');\n"
        "FILE_SCHEMA(('AUTOMOTIVE_DESIGN { 1 0 10303 214 1 1 1 1 }'));\nENDSEC;\nDATA;\n"
        "#1 = APPLICATION_CONTEXT('core data for automotive mechanical design processes');\n"
        "ENDSEC;\nEND-ISO-10303-21;\n")
run("empty", empty, "parts/empty.step")

# Surface models: the committed one-face file, a closed box surface with no solid, and two surface roots.
run("surface", SURFACE_FIXTURE, "parts/plane-10x20-surface.step")
box = cq.Workplane("XY").box(10, 10, 10).val()
closed_surface = os.path.join(work, "closed-surface.step")
writer = STEPControl_Writer()
writer.Transfer(cq.Shell.makeShell(box.Faces()).wrapped, STEPControl_AsIs)
writer.Write(closed_surface)
run("closed_surface", closed_surface, "parts/closed-surface.step")
two_surfaces = os.path.join(work, "two-surfaces.step")
writer = STEPControl_Writer()
for offset in (0, 30):
    writer.Transfer(cq.Face.makePlane(10, 20).translate((offset, 0, 0)).wrapped, STEPControl_AsIs)
writer.Write(two_surfaces)
run("two_surfaces", two_surfaces, "parts/two-surfaces.step")

# A solid and a loose 10 x 20 face in one compound. The face is off the z = 0 plane: there, cadquery
# would count it as no volume and the case would pass without the fix.
solid_and_surface = os.path.join(work, "solid-and-surface.step")
cq.exporters.export(cq.Compound.makeCompound([box, cq.Face.makePlane(10, 20).translate((50, 0, 3))]),
                    solid_and_surface)
run("solid_and_surface", solid_and_surface, "parts/solid-and-surface.step")

print("RESULTS " + json.dumps(results))
'''


def _child_has_dependencies():
    # nosemgrep: dangerous-subprocess-use-audit -- fixed argv (this interpreter, a constant import check), no shell
    probe = subprocess.run([sys.executable, "-c", "import cadquery, OCP, trimesh, botocore"],
                           capture_output=True, text=True)
    return probe.returncode == 0


@pytest.fixture(scope="module")
def results():
    if not _child_has_dependencies():
        pytest.skip("cadquery, trimesh and botocore are needed for the real STEP extraction")
    # nosemgrep: dangerous-subprocess-use-audit -- fixed argv (this interpreter, this module's script, directory and fixtures), no shell
    run = subprocess.run([sys.executable, "-c", _SCRIPT, _LAMBDA_DIR, _FIXTURE, _SURFACE_FIXTURE],
                         capture_output=True, text=True, timeout=300)
    assert run.returncode == 0, run.stderr[-4000:]
    line = [l for l in run.stdout.splitlines() if l.startswith("RESULTS ")][-1]
    return json.loads(line[len("RESULTS "):])


def _ok(results, name):
    result = results[name]
    assert "error" not in result, result
    return result


@pytest.mark.unit
class TestAStepPartIsRead:
    @pytest.mark.parametrize("name,extension", [("part_step", "step"), ("part_stp", "stp")])
    def test_the_part_writes_its_geometry(self, results, name, extension):
        result = _ok(results, name)
        assert result["keys"] == [f"out/metadata/parts/cylinder-r4-h5.{extension}.attribute.json"], result
        geometry = result["attributes"]["geometric_metadata"]
        # Each section records its own failure as extraction_error, so the run can succeed without
        # having read the model. The values are what show the geometry was read.
        assert "extraction_error" not in geometry, geometry
        assert geometry["dimensions"] == pytest.approx({"width": 8, "height": 8, "depth": 5}, abs=1e-3)
        assert geometry["volume"] == pytest.approx(math.pi * 4 ** 2 * 5, rel=1e-6)
        assert geometry["surface_area"] == pytest.approx(2 * math.pi * 4 * (4 + 5), rel=1e-6)
        statistics = result["attributes"]["shape_statistics"]
        assert "extraction_error" not in statistics, statistics
        assert statistics["solids"] == 1 and statistics["faces"] == 3, statistics
        assert statistics["face_types"] == {"PLANE": 2, "CYLINDER": 1}, statistics

    def test_a_single_part_has_no_assembly_hierarchy(self, results):
        assert "assembly_hierarchy" not in _ok(results, "part_step")["attributes"]


@pytest.mark.unit
class TestEveryBodyIsRead:
    def test_a_compound_records_each_body_in_its_assembly_hierarchy(self, results):
        attributes = _ok(results, "compound")["attributes"]
        hierarchy = attributes["assembly_hierarchy"]
        assert [component["type"] for component in hierarchy] == ["Solid", "Solid"], hierarchy
        for component in hierarchy:
            assert component["geometry"]["volume"] == pytest.approx(1000, rel=1e-6), component
        assert attributes["shape_statistics"]["solids"] == 2, attributes

    def test_a_file_with_two_roots_counts_both(self, results):
        # The premise: the file really has two roots, which val() alone would cut to the first.
        assert results["two_roots_imported"] == ["Solid", "Solid"], results
        attributes = _ok(results, "two_roots")["attributes"]
        assert attributes["shape_statistics"]["solids"] == 2, attributes
        assert attributes["geometric_metadata"]["volume"] == pytest.approx(2000, rel=1e-6), attributes
        assert len(attributes["assembly_hierarchy"]) == 2, attributes


@pytest.mark.unit
class TestAnEmptyStepFileFails:
    def test_it_fails_with_a_clear_message_and_writes_nothing(self, results):
        result = results["empty"]
        assert "the STEP file contains no shapes" in result.get("error", ""), result
        assert result["uploaded"] == [], result


@pytest.mark.unit
class TestVolumeIsTheVolumeOfTheSolids:
    @pytest.mark.parametrize("name,area", [("surface", 200), ("closed_surface", 600), ("two_surfaces", 400)])
    def test_a_model_with_no_solid_has_no_volume(self, results, name, area):
        attributes = _ok(results, name)["attributes"]
        # The premise: the model really has no solid.
        assert attributes["shape_statistics"]["solids"] == 0, attributes
        geometry = attributes["geometric_metadata"]
        assert "extraction_error" not in geometry, geometry
        # The key stays, as it does when cadquery cannot compute a volume; only its value is empty.
        assert "volume" in geometry and geometry["volume"] is None, geometry
        assert geometry["surface_area"] == pytest.approx(area, rel=1e-6), geometry

    def test_a_surface_beside_a_solid_adds_nothing_to_the_volume(self, results):
        attributes = _ok(results, "solid_and_surface")["attributes"]
        geometry = attributes["geometric_metadata"]
        assert geometry["volume"] == pytest.approx(1000, rel=1e-6), geometry
        assert geometry["surface_area"] == pytest.approx(600 + 200, rel=1e-6), geometry
        assert [component["type"] for component in attributes["assembly_hierarchy"]] == ["Solid"], attributes
