#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""A USD stage's point and curve prims are named, not drawn.

The loader tessellates meshes and the built-in solids. Points, curve and patch prims were skipped
without a word: a stage of only those failed with a message that did not say why, and a stage of a mesh
beside them previewed the mesh and reported plain success. The preview stays the same; the job log, the
incomplete-preview result and the no-geometry error now say what was left out.

The stage tests need the real pxr/pyvista and run inside the pipeline image.
"""

import json
import logging
from unittest.mock import MagicMock

import numpy as np
import pytest

import test_thumbnail_incomplete_preview as incomplete_tests
from conftest import requires_real
from preview_pipeline import core, dependencies
from preview_pipeline.format_handlers import usd_handler
from preview_pipeline.utils.pipeline.objects import PipelineStatus

_MESH = """
    def Mesh "Tri"
    {
        int[] faceVertexCounts = [3]
        int[] faceVertexIndices = [0, 1, 2]
        point3f[] points = [(0, 0, 0), (1, 0, 0), (0, 1, 0)]
    }"""
_POINTS = """
    def Points "Dust"
    {
        point3f[] points = [(0, 0, 0), (1, 1, 1), (2, 2, 2)]
        float[] widths = [0.1]
    }"""
_CURVES = """
    def BasisCurves "Hair"
    {
        uniform token type = "linear"
        int[] curveVertexCounts = [2]
        point3f[] points = [(0, 0, 0), (0, 1, 0)]
        float[] widths = [0.01]
    }"""


def _stage(tmp_path, body, name="scene.usda"):
    path = tmp_path / name
    path.write_text('#usda 1.0\n(\n    defaultPrim = "World"\n    upAxis = "Y"\n)\n'
                    f'def Xform "World"\n{{{body}\n}}\n')
    return str(path)


def _load(path):
    report = {}
    data = usd_handler.load(path, report=report)
    return data, report


@pytest.mark.unit
@requires_real("pxr", "pyvista", "vtk", "PIL")
class TestSkippedPrimsAreNamed:
    def test_a_mesh_beside_points_and_curves_previews_the_same_geometry(self, tmp_path):
        alone, alone_report = _load(_stage(tmp_path, _MESH, "alone.usda"))
        mixed, report = _load(_stage(tmp_path, _MESH + _POINTS + _CURVES, "mixed.usda"))
        np.testing.assert_array_equal(np.asarray(mixed.points), np.asarray(alone.points))
        np.testing.assert_array_equal(np.asarray(mixed.faces), np.asarray(alone.faces))
        assert alone_report == {}
        assert report["skippedPrims"] == {
            "Points": {"count": 1, "paths": ["/World/Dust"]},
            "BasisCurves": {"count": 1, "paths": ["/World/Hair"]},
        }

    def test_the_log_names_each_type_with_its_count_and_first_paths(self, tmp_path, caplog):
        with caplog.at_level(logging.WARNING):
            _load(_stage(tmp_path, _MESH + _POINTS + _CURVES))
        messages = [record.getMessage() for record in caplog.records]
        assert any("1 Points prim is not drawn" in m and "/World/Dust" in m for m in messages)
        assert any("1 BasisCurves prim is not drawn" in m and "/World/Hair" in m for m in messages)

    def test_every_type_reads_singular_for_one_and_plural_for_more(self, tmp_path, caplog):
        for name in usd_handler._UNDRAWN_TYPES:
            for count in (1, 2):
                caplog.clear()
                prims = "".join(f'\n    def {name} "X{i}"\n    {{\n    }}' for i in range(count))
                with caplog.at_level(logging.WARNING):
                    _load(_stage(tmp_path, _MESH + prims, f"{name}{count}.usda"))
                lines = [r.getMessage() for r in caplog.records if "not drawn by the 3D Preview" in r.getMessage()]
                expected = (f"1 {name} prim is not drawn" if count == 1 else f"2 {name} prims are not drawn")
                assert len(lines) == 1 and lines[0].startswith(expected), (name, count, lines)
                described = usd_handler.describe_skipped({name: {"count": count, "paths": []}})
                assert described == (f"1 {name} prim" if count == 1 else f"2 {name} prims")
                sentence = core._skipped_prims_sentence({name: {"count": count, "paths": []}})
                assert sentence == (f"1 {name} prim is not drawn by the 3D Preview Thumbnail." if count == 1
                                    else f"2 {name} prims are not drawn by the 3D Preview Thumbnail.")

    def test_the_paths_are_bounded(self, tmp_path):
        many = "".join(_POINTS.replace('"Dust"', f'"Dust{i}"') for i in range(12))
        _, report = _load(_stage(tmp_path, _MESH + many))
        entry = report["skippedPrims"]["Points"]
        assert entry["count"] == 12 and len(entry["paths"]) == usd_handler.MAX_SKIPPED_PATHS

    def test_invisible_and_guide_prims_are_not_counted(self, tmp_path):
        hidden = _POINTS.replace('def Points "Dust"\n    {', 'def Points "Dust"\n    {\n        token visibility = "invisible"')
        guide = _CURVES.replace('def BasisCurves "Hair"\n    {', 'def BasisCurves "Hair"\n    {\n        uniform token purpose = "guide"')
        _, report = _load(_stage(tmp_path, _MESH + hidden + guide))
        assert report == {}

    def test_the_other_undrawn_types_are_named_too(self, tmp_path):
        body = (_MESH + '\n    def NurbsCurves "N"\n    {\n        int[] curveVertexCounts = [4]\n'
                '    }'
                + '\n    def HermiteCurves "H"\n    {\n        int[] curveVertexCounts = [2]\n    }'
                + '\n    def NurbsPatch "P"\n    {\n    }')
        _, report = _load(_stage(tmp_path, body))
        assert sorted(report["skippedPrims"]) == ["HermiteCurves", "NurbsCurves", "NurbsPatch"]

    def test_a_plane_and_a_tet_mesh_are_named_and_the_geometry_is_unchanged(self, tmp_path):
        extra = ('\n    def Plane "Floor"\n    {\n        double width = 20\n        double length = 20\n    }'
                 '\n    def TetMesh "Block"\n    {\n        point3f[] points = [(0, 0, 0), (9, 0, 0), (0, 9, 0), (0, 0, 9)]\n'
                 '        int4[] tetVertexIndices = [(0, 1, 2, 3)]\n    }')
        alone, _ = _load(_stage(tmp_path, _MESH, "alone.usda"))
        mixed, report = _load(_stage(tmp_path, _MESH + extra, "mixed.usda"))
        np.testing.assert_array_equal(np.asarray(mixed.points), np.asarray(alone.points))
        np.testing.assert_array_equal(np.asarray(mixed.faces), np.asarray(alone.faces))
        assert report["skippedPrims"] == {"Plane": {"count": 1, "paths": ["/World/Floor"]},
                                          "TetMesh": {"count": 1, "paths": ["/World/Block"]}}

    def test_a_point_instancer_prototype_of_points_is_named_once(self, tmp_path):
        body = (_MESH + '\n    def PointInstancer "Inst"\n    {\n        rel prototypes = [</World/Inst/Proto>]\n'
                '        int[] protoIndices = [0, 0, 0]\n        point3f[] positions = [(0, 0, 0), (5, 0, 0), (9, 0, 0)]\n'
                + _POINTS.replace('def Points "Dust"', 'def Points "Proto"').replace("\n    ", "\n        ")
                + '\n    }')
        _, report = _load(_stage(tmp_path, body))
        assert report["skippedPrims"]["Points"]["count"] == 1

    def test_a_left_out_rig_roots_points_are_not_counted(self, tmp_path):
        rig = ('\ndef Xform "Rig"\n{\n    def Sphere "environment"\n    {\n        double radius = 5000\n    }\n'
               '    def Points "RigDust"\n    {\n        point3f[] points = [(0, 0, 0)]\n    }\n'
               '    def DistantLight "Sun"\n    {\n    }\n    def Camera "Cam"\n    {\n    }\n}\n')
        path = _stage(tmp_path, _MESH)
        with open(path, "a") as handle:
            handle.write(rig)
        _, report = _load(path)
        assert report.get("leftOut") == ["Rig"] and "skippedPrims" not in report


@pytest.mark.unit
@requires_real("pxr", "pyvista", "vtk", "PIL")
class TestOnlyPointsOrCurves:
    def test_the_error_names_the_types_and_counts(self, tmp_path):
        with pytest.raises(ValueError) as error:
            usd_handler.load(_stage(tmp_path, _POINTS + _POINTS.replace("Dust", "Dust2") + _CURVES))
        assert str(error.value) == ("No valid mesh geometry found in USD stage: the stage holds only "
                                    "2 Points prims and 1 BasisCurves prim, which the 3D Preview Thumbnail "
                                    "does not draw")

    def test_three_types_are_joined_with_commas_and_a_last_and(self, tmp_path):
        plane = '\n    def Plane "Floor"\n    {\n    }'
        with pytest.raises(ValueError) as error:
            usd_handler.load(_stage(tmp_path, _POINTS + _CURVES + plane))
        assert "holds only 1 Points prim, 1 BasisCurves prim and 1 Plane prim, which" in str(error.value)

    def test_the_error_and_the_completion_line_use_one_phrasing(self):
        skipped = {"Points": {"count": 3, "paths": []}, "BasisCurves": {"count": 1, "paths": []},
                   "Plane": {"count": 2, "paths": []}}
        listed = "3 Points prims, 1 BasisCurves prim and 2 Plane prims"
        assert usd_handler.describe_skipped(skipped) == listed
        assert core._skipped_prims_sentence(skipped) == f"{listed} are not drawn by the 3D Preview Thumbnail."

    def test_every_other_empty_stage_keeps_the_original_text(self, tmp_path):
        with pytest.raises(ValueError) as error:
            usd_handler.load(_stage(tmp_path, '\n    def Xform "Empty"\n    {\n    }'))
        assert str(error.value) == "No valid mesh geometry found in USD stage"


@pytest.mark.unit
@requires_real("pxr", "pyvista", "vtk", "PIL")
def test_a_stage_with_none_of_those_prims_reports_nothing_new(tmp_path):
    _, report = _load(_stage(tmp_path, _MESH))
    assert report == {}


# --------------------------------------------------------------------------------------------------
# The run's result
# --------------------------------------------------------------------------------------------------

RESULTS = incomplete_tests.RESULTS
SKIPPED = {"Points": {"count": 3, "paths": ["/World/A", "/World/B", "/World/C"]},
           "BasisCurves": {"count": 1, "paths": ["/World/Hair"]}}


def _run_usd(tmp_path, caplog, monkeypatch, usd_report, fetch, put_json):
    """The pipeline run on a .usd input whose load reports ``usd_report``."""
    stage = incomplete_tests._stage()
    stage.inputFile["fileExtension"] = ".usd"

    def load(file_path, ext, asset_root=None, budgets=None, usd_report=None):
        usd_report.update(report)
        return MagicMock()

    report = usd_report
    monkeypatch.setattr(incomplete_tests, "_stage", lambda: stage)
    return incomplete_tests._run(tmp_path, caplog, fetch, RESULTS, put_json, load=load)


@pytest.mark.unit
class TestTheRunReportsSkippedPrims:
    def test_a_run_that_previews_fine_is_reported_incomplete_but_still_succeeds(self, tmp_path, caplog, monkeypatch):
        put_json = MagicMock(side_effect=lambda bucket, key, body: key)
        result, _ = _run_usd(tmp_path, caplog, monkeypatch, {"skippedPrims": SKIPPED},
                             incomplete_tests._fetch_skipping(), put_json)
        assert result.status == PipelineStatus.COMPLETE
        body = put_json.call_args.args[2]
        document = json.loads(body)
        assert document["message"] == ("Preview generated, but incomplete: 3 Points prims and 1 BasisCurves "
                                       "prim are not drawn by the 3D Preview Thumbnail. See the job log for the list.")
        assert document["counts"]["skippedPrimTypes"] == {"Points": 3, "BasisCurves": 1}
        assert document["counts"]["skipped"] == 0 and document["skippedFiles"] == []
        assert document["skippedPrims"][0] == {"type": "Points", "count": 3,
                                               "paths": ["/World/A", "/World/B", "/World/C"]}

    def test_skipped_prims_are_added_to_a_summary_of_missing_files(self, tmp_path, caplog, monkeypatch):
        put_json = MagicMock(side_effect=lambda bucket, key, body: key)
        fetch = incomplete_tests._fetch_skipping(("scene/parts/wheel.usda", "missing from the asset"))
        _run_usd(tmp_path, caplog, monkeypatch, {"skippedPrims": SKIPPED}, fetch, put_json)
        document = json.loads(put_json.call_args.args[2])
        assert document["counts"]["geometryLayers"] == 1
        assert document["message"].startswith("Preview generated, but incomplete: 1 referenced file ")
        assert document["message"].endswith("prim are not drawn by the 3D Preview Thumbnail.")
        assert document["skippedFiles"][0]["path"] == "scene/parts/wheel.usda"

    def test_a_summary_without_skipped_prims_is_unchanged(self, tmp_path, caplog, monkeypatch):
        with_none = MagicMock(side_effect=lambda bucket, key, body: key)
        reference = MagicMock(side_effect=lambda bucket, key, body: key)
        fetch = incomplete_tests._fetch_skipping(("scene/parts/wheel.usda", "missing from the asset"))
        _run_usd(tmp_path, caplog, monkeypatch, {}, fetch, with_none)
        incomplete_tests._run(tmp_path, caplog, fetch, RESULTS, reference)
        assert with_none.call_args.args[2] == reference.call_args.args[2]
        document = json.loads(with_none.call_args.args[2])
        assert "skippedPrimTypes" not in document["counts"] and "skippedPrims" not in document

    def test_the_size_cap_holds_for_the_final_document(self, tmp_path, caplog, monkeypatch):
        put_json = MagicMock(side_effect=lambda bucket, key, body: key)
        many = tuple((f"scene/parts/{'x' * 200}{i}.usda", "missing from the asset") for i in range(120))
        worst = {name: {"count": 9, "paths": ["/World/" + "p" * 240] * usd_handler.MAX_SKIPPED_PATHS}
                 for name in usd_handler._UNDRAWN_TYPES}
        monkeypatch.setattr(dependencies, "MAX_RESULT_DOCUMENT_BYTES", 12 * 1024)
        _run_usd(tmp_path, caplog, monkeypatch, {"skippedPrims": worst},
                 incomplete_tests._fetch_skipping(*many), put_json)
        body = put_json.call_args.args[2]
        document = json.loads(body)
        assert len(body) <= 12 * 1024
        assert document["truncated"] is True and len(document["skippedPrims"]) == len(worst)

    def test_a_document_without_skipped_prims_is_not_resized(self, tmp_path, caplog, monkeypatch):
        with_none = MagicMock(side_effect=lambda bucket, key, body: key)
        reference = MagicMock(side_effect=lambda bucket, key, body: key)
        many = tuple((f"scene/parts/{'x' * 200}{i}.usda", "missing from the asset") for i in range(120))
        monkeypatch.setattr(dependencies, "MAX_RESULT_DOCUMENT_BYTES", 12 * 1024)
        _run_usd(tmp_path, caplog, monkeypatch, {}, incomplete_tests._fetch_skipping(*many), with_none)
        incomplete_tests._run(tmp_path, caplog, incomplete_tests._fetch_skipping(*many), RESULTS, reference)
        assert with_none.call_args.args[2] == reference.call_args.args[2]

    def test_a_complete_stage_still_writes_nothing(self, tmp_path, caplog, monkeypatch):
        put_json = MagicMock()
        result, messages = _run_usd(tmp_path, caplog, monkeypatch, {}, incomplete_tests._fetch_skipping(),
                                    put_json)
        put_json.assert_not_called()
        assert core.SUCCESS_MESSAGE in messages
