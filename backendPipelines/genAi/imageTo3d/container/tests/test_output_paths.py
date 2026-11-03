#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Tests for the imageTo3d container's output-key composition and metadata envelope reading.

The write-back step maps whatever relative path the container writes below the output-files prefix
onto the output asset, so an input file nested inside an asset must produce equally nested outputs,
and anything the container adds of its own becomes a stray folder inside every asset. ``assetId`` is
threaded through the pipeline definition and is what anchors that arithmetic; a missing one must
fail rather than quietly write to the asset root.
"""

import importlib.util
import json
import os

import pytest

import export

# The prefix shape the workflow hands the container: the execution id is already a path segment.
_EXECUTION_ID = "3f2c9a10"
_OUTPUT_DIR = f"pipelines/genai-image-to-3d/genai-image-to-3d/output/{_EXECUTION_ID}/files/"
_METADATA_DIR = f"pipelines/genai-image-to-3d/genai-image-to-3d/output/{_EXECUTION_ID}/metadata/"
_ASSET_ID = "xd130a6d6c4b14a0f9e6bd0e8f8e5b1aa"


@pytest.fixture(scope="module")
def main_module():
    """The container entry module, loaded by file (its name is ``__main__.py``)."""
    container_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    spec = importlib.util.spec_from_file_location(
        "imageto3d_container_main", os.path.join(container_dir, "__main__.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.unit
class TestRelativeSubdir:
    def test_an_input_at_the_asset_root_has_no_relative_subdir(self):
        assert export.compute_relative_subdir(f"{_ASSET_ID}/photo.png", _ASSET_ID) == ""

    def test_an_input_two_subdirectories_deep_keeps_both_levels(self):
        assert export.compute_relative_subdir(
            f"{_ASSET_ID}/scans/session-a/photo.png", _ASSET_ID) == "scans/session-a/"

    def test_the_subdir_is_anchored_on_the_threaded_asset_id_not_the_first_segment(self):
        """The asset key may sit below other prefixes; the id is what locates the asset root."""
        assert export.compute_relative_subdir(
            f"uploads/{_ASSET_ID}/scans/photo.png", _ASSET_ID) == "scans/"

    def test_a_missing_asset_id_raises_rather_than_defaulting_to_the_asset_root(self):
        """Writing to the asset root on a blank id would look like a successful run."""
        with pytest.raises(export.ExportError, match="assetId is required"):
            export.compute_relative_subdir(f"{_ASSET_ID}/scans/photo.png", "")

    def test_an_asset_id_absent_from_the_key_raises(self):
        with pytest.raises(export.ExportError, match="does not appear in the input key"):
            export.compute_relative_subdir("someotherasset/scans/photo.png", _ASSET_ID)

    def test_a_chained_step_input_resolves_from_the_pipeline_output_convention_not_the_asset_id(self):
        """A workflow's later step reads the PRIOR step's output, whose key carries no assetId at all
        -- ``.../output/<execId>/files/<name>`` -- and its path within the asset is what follows
        ``files/``. The first two-step run died here: the reconstruction's photograph, handed on to
        the refine step, sits directly under ``files/`` and must resolve to the asset root, not raise."""
        assert export.compute_relative_subdir(
            f"{_OUTPUT_DIR}user_supplied.png", _ASSET_ID) == ""

    def test_a_nested_chained_step_input_keeps_its_subdirectory_below_files(self):
        assert export.compute_relative_subdir(
            f"{_OUTPUT_DIR}scans/session-a/user_supplied.png", _ASSET_ID) == "scans/session-a/"


@pytest.mark.unit
class TestComposeOutputKey:
    def test_an_asset_root_input_writes_directly_under_the_output_prefix(self):
        key = export.compose_output_key(_OUTPUT_DIR, "", "photo.glb")
        assert key == f"{_OUTPUT_DIR}photo.glb"

    def test_a_nested_input_writes_at_the_same_depth_under_the_output_prefix(self):
        subdir = export.compute_relative_subdir(f"{_ASSET_ID}/scans/session-a/photo.png", _ASSET_ID)
        key = export.compose_output_key(_OUTPUT_DIR, subdir, "photo.glb")
        assert key == f"{_OUTPUT_DIR}scans/session-a/photo.glb"

    def test_no_per_job_folder_is_introduced_below_the_prefix(self):
        """The execution id already appears once in the prefix; a container-side job folder would
        repeat it and show up as a stray level inside the asset."""
        key = export.compose_output_key(_OUTPUT_DIR, "", "photo.glb")
        assert f"{_EXECUTION_ID}/{_EXECUTION_ID}" not in key
        assert key.count(_EXECUTION_ID) == 1
        assert key[len(_OUTPUT_DIR):] == "photo.glb"

    def test_a_missing_trailing_slash_on_the_output_dir_makes_no_difference(self):
        assert (export.compose_output_key(_OUTPUT_DIR.rstrip("/"), "scans/", "photo.glb")
                == export.compose_output_key(_OUTPUT_DIR, "scans/", "photo.glb"))

    def test_windows_separators_in_the_relative_path_are_normalised(self):
        key = export.compose_output_key(_OUTPUT_DIR, "", "textures\\material_0.png")
        assert key == f"{_OUTPUT_DIR}textures/material_0.png"


@pytest.mark.unit
class TestUploadOutputDir:
    @staticmethod
    def _tree(root):
        """An OBJ output set with its MTL and a texture in a subfolder, plus the gate report."""
        (root / "photo.glb").write_text("glb", encoding="utf-8")
        (root / "photo.obj").write_text("obj", encoding="utf-8")
        (root / "photo.mtl").write_text("mtl", encoding="utf-8")
        (root / "photo.stl").write_text("stl", encoding="utf-8")
        (root / "textures").mkdir()
        (root / "textures" / "material_0.png").write_text("png", encoding="utf-8")
        (root / "imageTo3d_report.json").write_text("{}", encoding="utf-8")

    def test_the_obj_mtl_texture_structure_survives_the_upload(self, tmp_path):
        self._tree(tmp_path)
        uploaded = []
        keys = export.upload_output_dir(
            lambda bucket, key, path: uploaded.append((bucket, key)),
            str(tmp_path), "asset-bucket", _OUTPUT_DIR, "scans/",
            skip_names=("imageTo3d_report.json",))

        assert set(keys) == {
            f"{_OUTPUT_DIR}scans/photo.glb",
            f"{_OUTPUT_DIR}scans/photo.obj",
            f"{_OUTPUT_DIR}scans/photo.mtl",
            f"{_OUTPUT_DIR}scans/photo.stl",
            f"{_OUTPUT_DIR}scans/textures/material_0.png",
        }
        assert all(bucket == "asset-bucket" for bucket, _key in uploaded)

    def test_the_gate_report_is_filtered_out_of_the_files_walk(self, tmp_path):
        """The report belongs under the metadata prefix; a copy among the asset's files would show
        up as a downloadable artefact."""
        self._tree(tmp_path)
        keys = export.upload_output_dir(
            lambda bucket, key, path: None, str(tmp_path), "asset-bucket", _OUTPUT_DIR, "",
            skip_names=("imageTo3d_report.json",))
        assert not any(key.endswith("imageTo3d_report.json") for key in keys)

    def test_an_empty_output_directory_raises(self, tmp_path):
        with pytest.raises(export.ExportError, match="No output files were produced"):
            export.upload_output_dir(
                lambda bucket, key, path: None, str(tmp_path), "asset-bucket", _OUTPUT_DIR, "")

    def test_a_missing_output_bucket_raises(self, tmp_path):
        self._tree(tmp_path)
        with pytest.raises(export.ExportError, match="An output bucket is required"):
            export.upload_output_dir(
                lambda bucket, key, path: None, str(tmp_path), "", _OUTPUT_DIR, "")


@pytest.mark.unit
class TestMetadataReportKey:
    """The report is asset-level, and the name is what decides that.

    The write-back reads any ``*.metadata.json`` under the metadata prefix, strips the suffix, and
    treats the stem as the asset-relative path of the file the metadata attaches to -- then fails the
    whole execution with "File not found in S3" when that file is not among the outputs. Only the
    reserved name ``asset.metadata.json`` is read as asset-level instead. This run's outputs are
    meshes, so a report named after the input photograph names a file that never exists in the output
    and takes the execution down with it, after the mesh has already been generated and uploaded.
    """

    def test_the_report_key_uses_the_reserved_asset_level_name(self):
        key = export.metadata_report_key(_METADATA_DIR, "scans/")
        assert key == f"{_METADATA_DIR}scans/asset.metadata.json"

    def test_the_report_key_is_never_derived_from_the_input_file_name(self):
        """The regression: a stem naming the source image resolves to a file that is not produced."""
        key = export.metadata_report_key(_METADATA_DIR, "scans/session-a/")
        stem = key[len(_METADATA_DIR):-len(".metadata.json")]
        assert stem.rsplit("/", 1)[-1] == "asset"
        for extension in (".png", ".jpg", ".jpeg"):
            assert extension not in key, f"the report key still names a source image: {key}"

    def test_the_report_key_still_preserves_the_relative_subdirectory(self):
        """Asset-level or not, the report has to land beside the outputs it describes."""
        assert export.metadata_report_key(_METADATA_DIR, "scans/session-a/").startswith(
            f"{_METADATA_DIR}scans/session-a/")


@pytest.mark.unit
class TestExportFormats:
    def test_the_default_set_is_used_for_a_blank_value(self):
        """An optional string tag submits "" from a headless trigger; "" must not mean nothing."""
        for raw in ("", None, "  ", ","):
            assert export.parse_export_formats(raw) == ["glb", "obj", "stl"]

    def test_the_order_is_stable_regardless_of_how_the_list_is_written(self):
        assert export.parse_export_formats("stl, glb") == ["glb", "stl"]
        assert export.parse_export_formats("STL,GLB,stl") == ["glb", "stl"]

    def test_an_unknown_format_raises(self):
        with pytest.raises(export.ExportError, match="Unknown export format"):
            export.parse_export_formats("glb,fbx")


@pytest.mark.unit
class TestDecimate:
    """``TARGET_FACE_COUNT`` is a FACE BUDGET, and trimesh's first positional parameter is
    ``percent`` -- a reduction ratio in [0, 1]. Passing the budget positionally raises
    ``ValueError: target_reduction must be between 0 and 1`` for every budget above 1, which is
    every real budget, so the default 200000 would abort the run after generation had finished."""

    class _Mesh:
        def __init__(self, face_count):
            self.faces = [(0, 1, 2)] * face_count
            self.call = None

        def simplify_quadric_decimation(self, percent=None, face_count=None, aggression=None):
            self.call = {"percent": percent, "face_count": face_count}
            if percent is not None and not 0 <= percent <= 1:
                raise ValueError("`target_reduction` must be between 0 and 1")
            return TestDecimate._Mesh(face_count or 0)

    def test_the_budget_is_passed_as_face_count_not_as_a_reduction_ratio(self):
        mesh = self._Mesh(1_000_000)
        result = export.decimate(mesh, 200000)
        assert mesh.call == {"percent": None, "face_count": 200000}
        assert len(result.faces) == 200000

    def test_a_zero_budget_is_a_no_op(self):
        mesh = self._Mesh(1_000_000)
        assert export.decimate(mesh, 0) is mesh
        assert mesh.call is None

    def test_a_mesh_already_within_budget_is_not_decimated(self):
        mesh = self._Mesh(1000)
        assert export.decimate(mesh, 200000) is mesh
        assert mesh.call is None

    def test_an_unreadable_budget_is_ignored_rather_than_raising(self):
        mesh = self._Mesh(1_000_000)
        assert export.decimate(mesh, "not-a-number") is mesh
        assert mesh.call is None


@pytest.mark.unit
class TestMetadataDocument:
    def test_the_document_carries_the_shape_the_write_back_consumes(self):
        document = export.build_metadata_document({"IMAGE_TO_3D_PRINT_READY": "true"})
        assert document["type"] == "metadata"
        assert document["updateType"] == "update"
        assert document["metadata"] == [{
            "metadataKey": "IMAGE_TO_3D_PRINT_READY",
            "metadataValue": "true",
            "metadataValueType": "string",
        }]

    def test_structured_values_are_rendered_as_json_text(self):
        document = export.build_metadata_document({"REPORT": {"passed": False}})
        assert json.loads(document["metadata"][0]["metadataValue"]) == {"passed": False}
