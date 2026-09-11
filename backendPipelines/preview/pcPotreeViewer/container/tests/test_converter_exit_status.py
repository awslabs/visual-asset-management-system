#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Both pcPotreeViewer stages clear the destination before uploading, so a conversion that produced
nothing must be reported as a failure BEFORE the delete rather than after it.

Each stage runs its converter as a child process and previously discarded the exit status: the POTREE
stage inferred success from a directory listing and the PDAL stage reported a hardcoded output name,
so a failed converter reached ``s3.delete_all_path_contents`` and then returned a success response --
destroying an existing viewer artifact and reporting SUCCESS.

The converters are replaced by a fake ``subprocess.Popen`` that writes the files a real run would (or
none) and exits with a chosen status, so neither PotreeConverter nor PDAL has to be installed. The S3
helpers are patched; the local directories are real, so ``os.listdir`` and ``os.path.isfile`` report
on files the fake converter actually wrote."""

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from conftest import load_stage_module  # noqa: E402

objects = load_stage_module("utils.pipeline.objects")
PipelineStage = objects.PipelineStage
PipelineStatus = objects.PipelineStatus

OUTPUT_DIR_KEY = "dbM/xidM/scan.laz/preview/PotreeViewer/"


def fake_popen(returncode, writes=()):
    """Stand in for a converter subprocess: write ``writes`` (absolute paths), then exit with
    ``returncode``."""

    class _Popen:
        def __init__(self, cmd, *args, **kwargs):
            self.cmd = cmd
            for path in writes:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w", encoding="utf-8") as handle:
                    handle.write("point cloud bytes")

        def wait(self):
            return returncode

    return _Popen


def make_stage(stage_type, object_key):
    return PipelineStage(
        type=stage_type,
        inputFile={"bucketName": "abkt", "objectKey": object_key,
                   "fileExtension": os.path.splitext(object_key)[1]},
        outputFiles={"bucketName": "aux-bkt", "objectDir": OUTPUT_DIR_KEY},
        outputMetadata={"bucketName": "", "objectDir": ""},
        temporaryFiles={"bucketName": "", "objectDir": ""},
    )


class StageHarness:
    """Patched S3 + local directories for one ``pipeline.run`` call."""

    def __init__(self, module, tmp_path):
        self.module = module
        self.tmp_path = tmp_path
        self.input_dir = str(tmp_path / "input")
        self.output_dir = str(tmp_path / "output")
        os.makedirs(self.input_dir, exist_ok=True)
        os.makedirs(self.output_dir, exist_ok=True)
        self.delete = MagicMock()
        self.upload = MagicMock(side_effect=lambda bucket, key, path: key)
        # The PDAL stage replaces the destination's contents rather than clearing the prefix, so it
        # lists the destination and deletes per object. Stubbed for both stages, or the unpatched
        # helpers reach Amazon S3 for real.
        self.list_existing = MagicMock(return_value=[])
        self.delete_object = MagicMock(side_effect=lambda bucket, key: key)

    def _create_dir(self, parts):
        return self.output_dir if parts[-1] == "output" else self.input_dir

    def _download(self, bucket, object_key, file_path):
        with open(file_path, "w", encoding="utf-8") as handle:
            handle.write("point cloud bytes")
        return file_path

    def run(self, stage, popen):
        module = self.module
        with patch.object(module.ext, "create_dir", side_effect=self._create_dir), \
                patch.object(module.s3, "download", side_effect=self._download), \
                patch.object(module.s3, "delete_all_path_contents", self.delete), \
                patch.object(module.s3, "get_all_files_in_path", self.list_existing), \
                patch.object(module.s3, "delete", self.delete_object), \
                patch.object(module.s3, "uploadV2", self.upload), \
                patch.object(module.subprocess, "Popen", popen):
            return module.run(stage, "", "", False)


@pytest.mark.unit
class TestPotreeStage:
    def _module(self):
        return load_stage_module("pipelines.potree.pipeline")

    def test_converter_failure_leaves_existing_output_and_fails(self, tmp_path):
        """PotreeConverter exits non-zero: the destination must not be cleared and the stage must
        report FAILED. Against the unfixed stage the exit status is discarded, the empty directory
        listing is treated as the output, the destination IS cleared and the stage returns COMPLETE."""
        harness = StageHarness(self._module(), tmp_path)
        result = harness.run(make_stage("POTREE", "xidM/scan.laz"), fake_popen(1))

        assert result.status == PipelineStatus.FAILED
        assert "exited with code 1" in result.errorMessage
        harness.delete.assert_not_called()
        harness.upload.assert_not_called()

    def test_converter_success_with_no_output_fails(self, tmp_path):
        """Exit 0 but nothing written: still a failure, and still no delete. Unfixed, this is the
        exact silent-no-output path -- it clears the destination and reports COMPLETE."""
        harness = StageHarness(self._module(), tmp_path)
        result = harness.run(make_stage("POTREE", "xidM/scan.laz"), fake_popen(0))

        assert result.status == PipelineStatus.FAILED
        assert "no output files" in result.errorMessage
        harness.delete.assert_not_called()
        harness.upload.assert_not_called()

    def test_successful_conversion_uploads_every_file(self, tmp_path):
        """No-regression: a real conversion still uploads every file the converter produced.

        The destination is no longer CLEARED before the upload — it is replaced afterwards, so that a
        failed upload cannot leave the asset with neither the previous octree nor the new one. The
        ordering itself is asserted by ``test_potree_destination_replace_order.py``, which records every
        S3 call in one log; this test keeps its scope to "the conversion still produces and uploads its
        output".
        """
        harness = StageHarness(self._module(), tmp_path)
        writes = [os.path.join(harness.output_dir, "metadata.json"),
                  os.path.join(harness.output_dir, "octree.bin")]
        result = harness.run(make_stage("POTREE", "xidM/scan.laz"), fake_popen(0, writes))

        assert result.status == PipelineStatus.COMPLETE
        harness.delete.assert_not_called()
        assert harness.upload.call_count == 2
        uploaded_keys = sorted(call.args[1] for call in harness.upload.call_args_list)
        assert uploaded_keys == sorted([os.path.join(OUTPUT_DIR_KEY, "metadata.json"),
                                        os.path.join(OUTPUT_DIR_KEY, "octree.bin")])

    def test_failed_upload_fails_the_stage(self, tmp_path):
        """``uploadV2`` returns None on a ClientError, and the stage must report FAILED rather than
        swallowing it into a silent no-output COMPLETE.

        The destination is no longer cleared before the upload, so the previous octree survives this
        failure — that property is asserted in ``test_potree_destination_replace_order.py``. What
        remains this test's subject is that the failure is reported at all."""
        harness = StageHarness(self._module(), tmp_path)
        harness.upload = MagicMock(return_value=None)
        writes = [os.path.join(harness.output_dir, "metadata.json")]
        result = harness.run(make_stage("POTREE", "xidM/scan.laz"), fake_popen(0, writes))

        assert result.status == PipelineStatus.FAILED
        assert "Failed to upload" in result.errorMessage

    def test_uppercase_extension_is_converted(self, tmp_path):
        """VAMS accepts an uppercase extension at every upstream gate (the execute API matches
        ``inputFileFilters`` case-insensitively and openPipeline lowercases before its check), so the
        stage must too. Unfixed, the case-sensitive ``endswith`` rejects it as an unsupported file
        type and the converter never runs."""
        harness = StageHarness(self._module(), tmp_path)
        writes = [os.path.join(harness.output_dir, "metadata.json")]
        result = harness.run(make_stage("POTREE", "xidM/SCAN.LAZ"), fake_popen(0, writes))

        assert result.status == PipelineStatus.COMPLETE
        harness.upload.assert_called_once()


@pytest.mark.unit
class TestPdalStage:
    def _module(self):
        return load_stage_module("pipelines.pdal.pipeline")

    def test_converter_failure_leaves_existing_output_and_fails(self, tmp_path):
        """PDAL exits non-zero: no delete, stage FAILED. Unfixed, the exit status is discarded and
        the hardcoded output list carries the stage past the delete."""
        harness = StageHarness(self._module(), tmp_path)
        result = harness.run(make_stage("PDAL", "xidM/scan.e57"), fake_popen(1))

        assert result.status == PipelineStatus.FAILED
        assert "exited with code 1" in result.errorMessage
        harness.delete.assert_not_called()
        harness.upload.assert_not_called()

    def test_exit_zero_without_a_written_file_fails(self, tmp_path):
        """Exit 0 but no .laz on disk: the hardcoded output name must not be reported. Unfixed, the
        destination is cleared and the upload is attempted against a file that does not exist."""
        harness = StageHarness(self._module(), tmp_path)
        result = harness.run(make_stage("PDAL", "xidM/scan.e57"), fake_popen(0))

        assert result.status == PipelineStatus.FAILED
        assert "Failed to convert to LAS/LAZ format" in result.errorMessage
        harness.delete.assert_not_called()
        harness.upload.assert_not_called()

    def test_successful_conversion_uploads_then_replaces(self, tmp_path):
        """No-regression: a real conversion still uploads the .laz, and reaches the destination through
        a listing rather than a prefix clear. The removal ORDER is pinned separately, in
        test_pdal_destination_replace_order.py."""
        harness = StageHarness(self._module(), tmp_path)
        writes = [os.path.join(harness.output_dir, "scan.laz")]
        result = harness.run(make_stage("PDAL", "xidM/scan.e57"), fake_popen(0, writes))

        assert result.status == PipelineStatus.COMPLETE
        harness.delete.assert_not_called()
        harness.list_existing.assert_called_once_with("aux-bkt", OUTPUT_DIR_KEY)
        harness.upload.assert_called_once_with(
            "aux-bkt", os.path.join(OUTPUT_DIR_KEY, "scan.laz"),
            os.path.join(harness.output_dir, "scan.laz"))

    def test_failed_upload_fails_the_stage(self, tmp_path):
        """The destination has NOT been touched at that point, so the previous viewer data survives an
        upload failure."""
        harness = StageHarness(self._module(), tmp_path)
        harness.upload = MagicMock(return_value=None)
        writes = [os.path.join(harness.output_dir, "scan.laz")]
        result = harness.run(make_stage("PDAL", "xidM/scan.e57"), fake_popen(0, writes))

        assert result.status == PipelineStatus.FAILED
        assert "Failed to upload" in result.errorMessage
        harness.delete.assert_not_called()
        harness.delete_object.assert_not_called()

    def test_uppercase_extension_is_converted(self, tmp_path):
        """Unfixed, the case-sensitive ``endswith`` rejects ``SCAN.E57`` and the converter never
        runs."""
        harness = StageHarness(self._module(), tmp_path)
        writes = [os.path.join(harness.output_dir, "SCAN.laz")]
        result = harness.run(make_stage("PDAL", "xidM/SCAN.E57"), fake_popen(0, writes))

        assert result.status == PipelineStatus.COMPLETE
        harness.upload.assert_called_once()
