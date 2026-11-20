#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""STEP input is opt-in (app.pipelines.useConversion3dBasic.enableStepInput).

- Without it the handler refuses a STEP run before anything is read from Amazon S3, naming the key.
- With it, cascadio (Open CASCADE) tessellates the file to metres, Z up, and the unit and axis plan
  applies as for any declared source. The conversion cases skip where cascadio is not installed; the
  golden suite runs them in the image built with ENABLE_STEP_INPUT=true.
- The assembly placement count is vendored from the 3D thumbnail's cad_handler and kept identical.
"""

import ast
import importlib.util
import os
import sys
import types
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_LAMBDA_DIR = os.path.dirname(_HERE)
sys.path.insert(0, _LAMBDA_DIR)
sys.path.insert(0, _HERE)

import model_formats  # noqa: E402
import step_limits  # noqa: E402

_CANONICAL = os.path.normpath(os.path.join(
    _LAMBDA_DIR, "..", "..", "..", "preview", "3dThumbnail", "container", "preview_pipeline", "format_handlers",
    "cad_handler.py"))
PLATE = os.path.normpath(os.path.join(
    _LAMBDA_DIR, "..", "..", "..", "genAi", "cadStepAgent", "evaluation", "fixtures", "plate-40x40x10.step"))


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
class TestTheVendoredCountHasNotDrifted:
    @pytest.mark.parametrize("name", ["_STRING", "_NAUO", "assembly_occurrences", "_occurrences_below"])
    def test_each_definition_is_the_canonical_one(self, name):
        assert _definitions(os.path.join(_LAMBDA_DIR, "step_limits.py"))[name] == _definitions(_CANONICAL)[name]


def _assembly(levels, fan_out):
    """A STEP text whose assembly places each level's definition ``fan_out`` times in the level above."""
    lines, entity = [], 1
    for level in range(levels):
        for _ in range(fan_out):
            lines.append(f"#{entity}=NEXT_ASSEMBLY_USAGE_OCCURRENCE('a','b',$,#{1000 + level},#{1001 + level},$);")
            entity += 1
    return "ISO-10303-21;\nDATA;\n" + "\n".join(lines) + "\nENDSEC;\n"


@pytest.mark.unit
class TestTheAssemblyBound:
    def test_placements_multiply_through_the_levels(self, tmp_path):
        path = tmp_path / "a.step"
        path.write_text(_assembly(3, 2))
        assert step_limits.assembly_occurrences(str(path), 1000) == 2 + 4 + 8

    def test_a_file_past_the_bound_is_refused_before_open_cascade_reads_it(self, tmp_path, monkeypatch):
        import scene_limits
        path = tmp_path / "input" / "bomb.step"
        path.parent.mkdir()
        path.write_text(_assembly(30, 2))
        monkeypatch.setattr(scene_limits, "MAX_SCENE_NODES", 1000)
        job = {"input_file": str(path), "input_dir": str(path.parent), "input_relative_path": "bomb.step",
               "by_reference": {}, "input_name": "bomb.step"}
        with patch.dict(sys.modules, {"cascadio": MagicMock()}):
            with pytest.raises(ValueError, match="places more than 1,000 components"):
                model_formats._load_step(job, MagicMock())


def _load_handler():
    spec = importlib.util.spec_from_file_location("conversion_3dbasic_step", os.path.join(_LAMBDA_DIR, "lambda.py"))
    module = importlib.util.module_from_spec(spec)
    with patch("boto3.client", MagicMock()), patch("boto3.resource", MagicMock()):
        spec.loader.exec_module(module)
    return module


@pytest.mark.unit
class TestTheOptIn:
    @pytest.mark.parametrize("extension", [".step", ".stp", ".STEP"])
    def test_a_step_run_is_refused_before_any_s3_call_naming_the_key(self, extension, monkeypatch):
        monkeypatch.delenv("ENABLE_STEP_INPUT", raising=False)
        mod = _load_handler()
        head = MagicMock()
        download = MagicMock()
        with patch.object(mod, "head_input", head), patch.object(mod, "download", download):
            with pytest.raises(ValueError, match=r"app\.pipelines\.useConversion3dBasic\.enableStepInput"):
                mod.convert_input_output(f"s3://abkt/x/part{extension}", "s3://obkt/files/", ".glb", "", "")
        head.assert_not_called()
        download.assert_not_called()

    def test_with_the_flag_off_in_the_image_a_step_run_names_the_key_too(self, monkeypatch):
        monkeypatch.setenv("ENABLE_STEP_INPUT", "false")
        assert not model_formats.step_input_enabled()
        monkeypatch.setenv("ENABLE_STEP_INPUT", "true")
        assert model_formats.step_input_enabled()

    def test_an_image_without_cascadio_names_the_key_in_the_worker(self, tmp_path):
        job = {"input_file": PLATE, "input_dir": str(tmp_path), "input_relative_path": "p.step",
               "by_reference": {}, "input_name": "p.step"}
        with patch.dict(sys.modules, {"cascadio": None}):
            with pytest.raises(ValueError, match="enableStepInput"):
                model_formats._load_step(job, MagicMock())


@pytest.mark.unit
def test_no_test_leaves_a_cascadio_stand_in_behind():
    # A stand-in left in sys.modules made the conversion cases below run against a MagicMock in an
    # environment without cascadio (CI), where they must skip.
    module = sys.modules.get("cascadio")
    assert module is None or isinstance(module, types.ModuleType), module


@pytest.mark.unit
class TestStepConversions:
    @pytest.fixture(autouse=True)
    def _needs_cascadio(self):
        # The real package, not a stand-in another test put in sys.modules: only an environment built
        # with requirements-step.txt (the STEP image, or a local venv) converts STEP here. The CI
        # pipeline suite installs requirements.txt only, so these skip there; the golden suite's STEP
        # image cells (run_golden.sh with GOLDEN_STEP=1) are the coverage of the built image.
        module = pytest.importorskip("cascadio", reason="cascadio (requirements-step.txt) is not installed")
        if not isinstance(module, types.ModuleType) or not getattr(module, "__file__", None):
            pytest.skip("cascadio in sys.modules is a test stand-in, not the installed package")
        pytest.importorskip("trimesh")

    @pytest.mark.parametrize("output,expected", [
        (".glb", (0.04, 0.01, 0.04)),           # metres, rotated to Y up
        (".stl", (0.04, 0.04, 0.01)),           # unitless: written as read (metres, Z up)
        (".usda", (0.04, 0.04, 0.01)),          # declares metres and Z up
    ])
    def test_the_plate_converts_in_metres(self, tmp_path, output, expected):
        import new_format_helpers as helpers
        import trimesh
        out = helpers.convert(tmp_path, PLATE, output, name="plate.step")
        if output == ".usda":
            import usd_io
            from pxr import Usd, UsdGeom
            stage = Usd.Stage.Open(out)
            assert UsdGeom.GetStageMetersPerUnit(stage) == 1.0 and str(UsdGeom.GetStageUpAxis(stage)) == "Z"
            loaded = usd_io.read_usd(out, os.path.dirname(out))[0]
        else:
            loaded = trimesh.load(out)
        np.testing.assert_allclose(loaded.bounds[1] - loaded.bounds[0], expected, atol=1e-6)

    def test_millimetres_out_of_a_step_file(self, tmp_path):
        import new_format_helpers as helpers
        import trimesh
        out = helpers.convert(tmp_path, PLATE, ".obj", {"targetUnits": "mm"}, name="plate.step")
        loaded = trimesh.load(out)
        np.testing.assert_allclose(loaded.bounds[1] - loaded.bounds[0], (40, 40, 10), atol=1e-3)
