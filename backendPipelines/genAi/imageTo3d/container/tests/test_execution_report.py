#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Tests for the run report the EXECUTION carries, as distinct from asset metadata.

The report describes a RUN -- which model produced the mesh, what the print gate measured, what the
projection recovered, how long each stage took. The workflow reads every object under a run's results
prefix and records its content against the execution, so that is where it belongs.

Asset metadata was the wrong home for it and not merely untidy: asset-level metadata is SINGULAR, so an
asset carrying output from several models keeps only the last run's report and silently replaces every
earlier one -- which is the case this pipeline exists for. Per-FILE metadata is a different thing and
stays; that is what identifies the model on each mesh.

Two things are asserted here because both were nearly shipped wrong. The report must reach the execution
as DATA rather than as the metadata writer's own envelope with every nested part escaped inside a string.
And neither report file may be uploaded into the asset: the output-upload skip list is what keeps them
out, and a name known only to the writer would have leaked the new one in as a stray file.
"""

import importlib.util
import json
import os

import pytest

_CONTAINER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(scope="module")
def pipeline():
    """The container entry module, loaded by file because its name is ``__main__.py``."""
    spec = importlib.util.spec_from_file_location(
        "imageto3d_container_main", os.path.join(_CONTAINER_DIR, "__main__.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.unit
class TestTheReportReachesTheExecutionAsData:
    def test_a_nested_part_arrives_as_an_object_not_an_escaped_string(self, pipeline):

        entries = {
            "IMAGE_TO_3D_MODEL": "step1x3d",
            "IMAGE_TO_3D_PRINT_GATE_REPORT": json.dumps({"passed": False, "nonManifoldEdges": 33}),
        }
        report = pipeline.execution_report(entries)

        assert report["print_gate_report"] == {"passed": False, "nonManifoldEdges": 33}
        assert not isinstance(report["print_gate_report"], str)

    def test_the_namespacing_prefix_is_dropped(self, pipeline):
        """IMAGE_TO_3D_ exists to namespace keys sharing an asset's metadata with everything else. A
        report of this run's own shares with nothing."""
        report = pipeline.execution_report({"IMAGE_TO_3D_MODEL": "sam3d"})

        assert report == {"model": "sam3d"}

    def test_a_plain_string_is_left_alone(self, pipeline):

        report = pipeline.execution_report({"IMAGE_TO_3D_TEXTURE_STATUS": "projectedFromPhotograph"})

        assert report["texture_status"] == "projectedFromPhotograph"

    def test_a_string_that_only_looks_like_json_survives(self, pipeline):
        """A note beginning with a brace must not be silently discarded when it does not parse."""
        report = pipeline.execution_report({"IMAGE_TO_3D_GENERATION_NOTES": "{not json after all"})

        assert report["generation_notes"] == "{not json after all"

    def test_a_key_without_the_prefix_keeps_its_name(self, pipeline):

        report = pipeline.execution_report({"somethingElse": "kept"})

        assert report["somethingElse"] == "kept"


@pytest.mark.unit
class TestNeitherReportIsUploadedIntoTheAsset:
    def test_both_report_names_are_in_the_output_upload_skip_list(self, pipeline):
        """Neither report is an asset file. The skip list is the only thing keeping them out of the
        asset, so a report name that the skip list does not know becomes a stray file beside the
        meshes -- which is exactly what adding the second one nearly did."""
        source = open(os.path.join(_CONTAINER_DIR, "__main__.py"), encoding="utf-8").read()
        assert "skip_names=(GATE_REPORT_FILENAME, EXECUTION_REPORT_FILENAME)" in source
        assert pipeline.GATE_REPORT_FILENAME != pipeline.EXECUTION_REPORT_FILENAME

    def test_the_two_reports_are_distinct_files(self, pipeline):
        """They carry the same content in two shapes, so one cannot overwrite the other."""
        assert pipeline.EXECUTION_REPORT_FILENAME.endswith(".json")
        assert pipeline.GATE_REPORT_FILENAME.endswith(".json")
