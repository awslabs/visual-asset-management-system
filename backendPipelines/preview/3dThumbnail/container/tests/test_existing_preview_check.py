#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Tests for the overwriteExistingPreviewFiles guard.

A generated ``.previewFile.*`` is written back beside its input file in the asset bucket, while the
stage output directory is a fresh per-execution pipeline output prefix. The guard therefore lists
the INPUT file's bucket + directory."""

import os
from unittest.mock import MagicMock, patch

import pytest

from preview_pipeline import core
from preview_pipeline.utils.pipeline.objects import (
    PipelineStage,
    PipelineStatus,
    PipelineType,
    StageInput,
    StageOutput,
)


def _stage_input(key="xid/test/pump.e57", bucket="asset-bucket"):
    return StageInput(bucketName=bucket, objectKey=key, fileExtension=".e57")


@pytest.mark.unit
class TestCheckExistingPreview:
    def test_lists_the_input_files_directory_in_the_asset_bucket(self):
        listing = MagicMock(return_value=["xid/test/pump.e57.previewFile.gif"])
        with patch.object(core.s3, "list_objects_with_prefix", listing):
            found = core._check_existing_preview(_stage_input(), "pump.e57", False)
        assert found == "xid/test/pump.e57.previewFile.gif"
        listing.assert_called_once_with("asset-bucket", "xid/test/pump.e57.previewFile.")

    def test_asset_root_input_lists_the_bucket_root_prefix(self):
        listing = MagicMock(return_value=[])
        with patch.object(core.s3, "list_objects_with_prefix", listing):
            core._check_existing_preview(_stage_input(key="pump.e57"), "pump.e57", False)
        listing.assert_called_once_with("asset-bucket", "pump.e57.previewFile.")

    def test_no_existing_preview_returns_none(self):
        with patch.object(core.s3, "list_objects_with_prefix", MagicMock(return_value=[])):
            assert core._check_existing_preview(_stage_input(), "pump.e57", False) is None

    def test_local_test_lists_the_input_files_local_directory(self, tmp_path):
        input_file = tmp_path / "pump.e57"
        input_file.write_bytes(b"")
        (tmp_path / "pump.e57.previewFile.gif").write_bytes(b"")
        found = core._check_existing_preview(
            _stage_input(key=str(input_file), bucket=""), "pump.e57", True)
        assert found == os.path.join(str(tmp_path), "pump.e57.previewFile.gif")

    def test_local_test_without_existing_preview_returns_none(self, tmp_path):
        input_file = tmp_path / "pump.e57"
        input_file.write_bytes(b"")
        assert core._check_existing_preview(
            _stage_input(key=str(input_file), bucket=""), "pump.e57", True) is None


@pytest.mark.unit
class TestSkipOnExistingPreview:
    def _stage(self):
        output = StageOutput(
            bucketName="run-bucket",
            objectDir="pipelines/preview3dThumbnail/JOB/output/E1/files/")
        return PipelineStage(
            type=PipelineType.PREVIEW_3D_THUMBNAIL,
            inputFile={"bucketName": "asset-bucket", "objectKey": "xid/pump.e57",
                       "fileExtension": ".e57"},
            outputFiles=output.__dict__,
            outputMetadata=output.__dict__,
            temporaryFiles=output.__dict__,
        )

    def test_existing_preview_completes_without_regenerating(self):
        with patch.object(core, "_check_existing_preview",
                          MagicMock(return_value="xid/pump.e57.previewFile.gif")), \
                patch.object(core.s3, "download", MagicMock()) as download:
            result = core._run_preview_pipeline(
                self._stage(), {}, {"overwriteExistingPreviewFiles": False}, False, "xid")
        assert result.status == PipelineStatus.COMPLETE
        assert not result.errorMessage
        download.assert_not_called()
