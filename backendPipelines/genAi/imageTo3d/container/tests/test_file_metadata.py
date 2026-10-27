#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Tests for the metadata each exported mesh carries about itself.

The run report is ASSET-level: one document per execution, describing whichever run wrote it last. An
asset holding meshes from several models therefore has no way to say which model produced which file, and
answering that means opening the execution that made it. These per-file documents close that gap — the
model, the licence it carries, whether the file itself holds colour, and the run's measurements.

The naming rule is the part that has to be exact. The write-back reads a document under the metadata
prefix as belonging to the file its stem names, and it FAILS when that file is absent from the output. So
a document may only be written for a file the export actually produced, and it must not be written into
the tree that uploads to the asset's files prefix, where it would become an asset file rather than
metadata.
"""

import importlib.util
import json
import os

import pytest

import export

_CONTAINER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(scope="module")
def main_module():
    """The container entry module, loaded by file because its name is ``__main__.py``."""
    spec = importlib.util.spec_from_file_location(
        "imageto3d_container_main", os.path.join(_CONTAINER_DIR, "__main__.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Gate:
    """The fields the entry builder reads off a gate report."""

    passed = True
    bodies = 1
    expected_bodies = 1


NOTES = {"model": "stable3dgen", "modelName": "Stable-X Stable3DGen (Hi3DGen)", "modelLicense": "MIT"}
BENCHMARK = {
    "faceCount": 188244,
    "host": {"instanceType": "g5.8xlarge", "gpuName": "NVIDIA A10G"},
    "stageSeconds": {"generate": 44.4, "preprocess": 60.6, "cleanup": 24.6, "projectColour": 0.57},
    "shape": {"fillRatio": 0.2166},
}


@pytest.mark.unit
class TestTheNamingRule:
    def test_a_document_is_named_after_the_file_it_attaches_to(self):
        key = export.file_metadata_key("asset/", "", "mug.glb")

        assert key.endswith("mug.glb.metadata.json")

    def test_the_relative_subdirectory_is_preserved(self):
        """A file inside the asset keeps its relative path, or the write-back looks in the wrong place.

        The subdirectory is passed as ``compute_relative_subdir`` produces it — with a trailing slash
        when non-empty, and empty for a file at the asset root — because the key composition
        concatenates it verbatim rather than joining paths.
        """
        subdir = export.compute_relative_subdir("x0254/shots/front/mug.png", "x0254")
        assert subdir == "shots/front/"

        key = export.file_metadata_key("asset/", subdir, "mug.stl")

        assert key == "asset/shots/front/mug.stl.metadata.json"

    def test_a_file_at_the_asset_root_has_no_subdirectory(self):
        key = export.file_metadata_key("asset/", export.compute_relative_subdir("x0254/mug.png", "x0254"),
                                       "mug.glb")

        assert key == "asset/mug.glb.metadata.json"

    def test_the_asset_document_keeps_its_reserved_name(self):
        """``asset.metadata.json`` is the one name that attaches to the asset rather than to a file."""
        assert export.metadata_report_key("asset/", "").endswith("asset.metadata.json")


@pytest.mark.unit
class TestWhichFormatsCarryColour:
    @pytest.mark.parametrize("filename", ["mug.glb", "mug.gltf", "mug.obj"])
    def test_the_colour_carrying_formats(self, filename):
        assert export.carries_colour_in_file(filename)

    def test_stl_carries_none(self):
        """STL stores triangles and nothing else. That is the format, not a fault."""
        assert not export.carries_colour_in_file("mug.stl")


@pytest.mark.unit
class TestTheEntriesEachFileCarries:
    def _entries(self, main_module, filename, texture_status="projectedFromPhotograph",
                 benchmark_report=BENCHMARK, projection_report=None):
        return main_module.file_metadata_entries(
            filename, "stable3dgen", "ImageTo3dJob_1", "mug.png", NOTES, _Gate(),
            texture_status, 132.5, benchmark_report, projection_report)

    def test_it_names_the_model_that_produced_the_file(self, main_module):
        entries = self._entries(main_module, "mug.glb")

        assert entries["IMAGE_TO_3D_MODEL"] == "stable3dgen"
        assert entries["IMAGE_TO_3D_MODEL_NAME"] == "Stable-X Stable3DGen (Hi3DGen)"
        assert entries["IMAGE_TO_3D_MODEL_LICENSE"] == "MIT"

    def test_it_names_the_photograph_and_the_run(self, main_module):
        """Without these a file cannot be traced back to its input without opening the execution."""
        entries = self._entries(main_module, "mug.glb")

        assert entries["IMAGE_TO_3D_SOURCE_IMAGE_NAME"] == "mug.png"
        assert entries["IMAGE_TO_3D_JOB_NAME"] == "ImageTo3dJob_1"

    def test_a_textured_mesh_still_reports_no_colour_IN_an_stl(self, main_module):
        """The mesh's texture status and what the FILE holds are different questions.

        A run whose mesh carries colour writes a colourless STL, and labelling that file with the mesh's
        status would tell a reader it has colour it does not have.
        """
        glb = self._entries(main_module, "mug.glb")
        stl = self._entries(main_module, "mug.stl")

        assert glb["IMAGE_TO_3D_TEXTURE_STATUS"] == "projectedFromPhotograph"
        assert stl["IMAGE_TO_3D_TEXTURE_STATUS"] == "projectedFromPhotograph"
        assert glb["IMAGE_TO_3D_COLOUR_IN_FILE"] == "true"
        assert stl["IMAGE_TO_3D_COLOUR_IN_FILE"] == "false"

    def test_a_geometry_only_run_reports_no_colour_in_any_format(self, main_module):
        entries = self._entries(main_module, "mug.glb", texture_status="none")

        assert entries["IMAGE_TO_3D_COLOUR_IN_FILE"] == "false"

    def test_the_measurements_are_flat_as_well_as_whole(self, main_module):
        """Flat so they read in the metadata view and can be searched; whole so nothing is lost."""
        entries = self._entries(main_module, "mug.glb")

        assert entries["IMAGE_TO_3D_INSTANCE_TYPE"] == "g5.8xlarge"
        assert entries["IMAGE_TO_3D_GPU"] == "NVIDIA A10G"
        assert entries["IMAGE_TO_3D_SECONDS_GENERATE"] == "44.4"
        assert entries["IMAGE_TO_3D_SECONDS_PROJECTCOLOUR"] == "0.57"
        assert entries["IMAGE_TO_3D_FILL_RATIO"] == "0.2166"
        assert entries["IMAGE_TO_3D_FACE_COUNT"] == "188244"
        assert json.loads(entries["IMAGE_TO_3D_BENCHMARK"])["host"]["gpuName"] == "NVIDIA A10G"

    def test_a_run_without_a_benchmark_carries_no_measurement_fields(self, main_module):
        """The benchmark is opt-in, so its absence must not leave empty keys behind."""
        entries = self._entries(main_module, "mug.glb", benchmark_report=None)

        assert entries["IMAGE_TO_3D_MODEL"] == "stable3dgen"
        assert "IMAGE_TO_3D_INSTANCE_TYPE" not in entries
        assert "IMAGE_TO_3D_BENCHMARK" not in entries

    def test_projected_colour_records_how_much_was_measured(self, main_module):
        entries = self._entries(
            main_module, "mug.glb",
            projection_report={"verticesMeasured": 0.21, "poseIou": 0.865})

        assert entries["IMAGE_TO_3D_COLOUR_SOURCE"] == "photograph"
        assert entries["IMAGE_TO_3D_COLOUR_MEASURED_FRACTION"] == "0.21"

    def test_no_entry_is_left_blank(self, main_module):
        """A blank metadata value is noise in the asset's metadata view."""
        entries = self._entries(main_module, "mug.glb", benchmark_report=None)

        assert all(value != "" for value in entries.values())


@pytest.mark.unit
class TestWritingTheDocuments:
    def test_one_document_per_exported_file_named_after_it(self, main_module, tmp_path):
        from pathlib import Path

        exported = [Path("mug.glb"), Path("mug.obj"), Path("mug.stl")]

        written = main_module.write_file_reports(
            tmp_path, exported, lambda filename: {"IMAGE_TO_3D_MODEL": "stable3dgen"})

        assert sorted(path.name for path in written) == [
            "mug.glb.metadata.json", "mug.obj.metadata.json", "mug.stl.metadata.json"]
        for path in written:
            document = json.loads(path.read_text(encoding="utf-8"))
            assert document["type"] == "metadata"
            assert document["metadata"][0]["metadataKey"] == "IMAGE_TO_3D_MODEL"

    def test_nothing_is_written_when_no_file_was_exported(self, main_module, tmp_path):
        """A gate failure under failClosed writes no mesh, and a document naming an absent file fails
        the write-back outright."""
        written = main_module.write_file_reports(tmp_path, [], lambda filename: {"K": "v"})

        assert written == []

    def test_the_documents_live_outside_the_directory_that_uploads_as_asset_files(self, main_module):
        """A ``*.metadata.json`` among the asset's files is an asset file, not metadata."""
        assert main_module.FILE_METADATA_DIR != main_module.OUTPUT_DIR
        assert main_module.FILE_METADATA_DIR not in main_module.OUTPUT_DIR.parents
