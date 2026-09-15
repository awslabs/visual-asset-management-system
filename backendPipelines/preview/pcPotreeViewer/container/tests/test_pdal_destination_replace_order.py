#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""The PDAL stage's destination is the Potree viewer directory, so what it removes there is the
previous run's working viewer data.

Clearing that directory before uploading leaves a window in which the previous octree is gone and the
replacement does not exist yet: an S3 upload that fails after the clear leaves the asset with neither.
The clear therefore has to be a REPLACE -- upload first, then remove only what the new output did not
overwrite.

The same objects also feed the following POTREE stage, which downloads the ``.laz`` this stage writes,
so the ordering is observable rather than cosmetic: this stage's destination is byte-identical to the
POTREE stage's own output directory (``construct_pdal_definition`` gives both
``{base}/preview/PotreeViewer/``).

The converter is a fake ``subprocess.Popen`` that writes the files a real run would, so PDAL does not
have to be installed. The S3 helpers are patched and every call is recorded in one ordered log, which
is what makes "after the upload" assertable rather than merely "both happened"."""

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

# What a previous run left in the destination: the Potree 2.0 octree plus the intermediate .laz this
# stage itself produced last time.
PREVIOUS_RUN_OBJECTS = [
    OUTPUT_DIR_KEY + "metadata.json",
    OUTPUT_DIR_KEY + "hierarchy.bin",
    OUTPUT_DIR_KEY + "octree.bin",
    OUTPUT_DIR_KEY + "scan.laz",
]


def fake_popen(returncode, writes=()):
    """Stand in for the PDAL converter: write ``writes`` (absolute paths), then exit with
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


def make_stage(object_key):
    return PipelineStage(
        type="PDAL",
        inputFile={"bucketName": "abkt", "objectKey": object_key,
                   "fileExtension": os.path.splitext(object_key)[1]},
        outputFiles={"bucketName": "aux-bkt", "objectDir": OUTPUT_DIR_KEY},
        outputMetadata={"bucketName": "", "objectDir": ""},
        temporaryFiles={"bucketName": "", "objectDir": ""},
    )


class ReplaceHarness:
    """A patched destination that already holds a previous run's objects, with every S3 call recorded
    in one ordered log."""

    def __init__(self, tmp_path, existing=PREVIOUS_RUN_OBJECTS, upload_fails=False):
        self.module = load_stage_module("pipelines.pdal.pipeline")
        self.input_dir = str(tmp_path / "input")
        self.output_dir = str(tmp_path / "output")
        os.makedirs(self.input_dir, exist_ok=True)
        os.makedirs(self.output_dir, exist_ok=True)
        self.existing = list(existing)
        self.upload_fails = upload_fails
        self.calls = []

    def _create_dir(self, parts):
        return self.output_dir if parts[-1] == "output" else self.input_dir

    def _download(self, bucket, object_key, file_path):
        with open(file_path, "w", encoding="utf-8") as handle:
            handle.write("point cloud bytes")
        return file_path

    def _list(self, bucket, path):
        self.calls.append(("list", path))
        return [{"key": key, "relativePath": key.removeprefix(path)}
                for key in self.existing if key.startswith(path)]

    def _upload(self, bucket, object_key, file_path):
        self.calls.append(("upload", object_key))
        if self.upload_fails:
            return None
        return object_key

    def _delete(self, bucket, object_key):
        self.calls.append(("delete", object_key))
        return object_key

    def _delete_all(self, bucket, path):
        self.calls.append(("delete_all", path))

    def run(self, stage, popen):
        module = self.module
        with patch.object(module.ext, "create_dir", side_effect=self._create_dir), \
                patch.object(module.s3, "download", side_effect=self._download), \
                patch.object(module.s3, "get_all_files_in_path", side_effect=self._list), \
                patch.object(module.s3, "delete", side_effect=self._delete), \
                patch.object(module.s3, "delete_all_path_contents", side_effect=self._delete_all), \
                patch.object(module.s3, "uploadV2", side_effect=self._upload), \
                patch.object(module.subprocess, "Popen", popen):
            return module.run(stage, "", "", False)

    def kind(self, name):
        return [key for call, key in self.calls if call == name]

    def removals(self):
        """Every object this run removed, whether one at a time or by clearing the prefix."""
        return [key for call, key in self.calls if call in ("delete", "delete_all")]


@pytest.mark.unit
class TestTheDestinationIsReplacedNotClearedFirst:
    def test_a_failed_upload_leaves_the_previous_run_intact(self, tmp_path):
        """The residual data-loss window. `uploadV2` returns None on a ClientError; against the
        clear-then-upload order the previous octree has already been removed by then, so the asset is
        left with neither the old viewer data nor the new."""
        harness = ReplaceHarness(tmp_path, upload_fails=True)
        writes = [os.path.join(harness.output_dir, "scan.laz")]
        result = harness.run(make_stage("xidM/scan.e57"), fake_popen(0, writes))

        assert result.status == PipelineStatus.FAILED
        assert "Failed to upload" in result.errorMessage
        assert harness.removals() == [], harness.calls

    def test_the_previous_objects_are_removed_only_after_the_upload_succeeded(self, tmp_path):
        harness = ReplaceHarness(tmp_path)
        writes = [os.path.join(harness.output_dir, "scan.laz")]
        result = harness.run(make_stage("xidM/scan.e57"), fake_popen(0, writes))

        assert result.status == PipelineStatus.COMPLETE
        kinds = [call for call, _ in harness.calls]
        assert "upload" in kinds, harness.calls
        assert kinds.index("upload") < min(
            i for i, kind in enumerate(kinds) if kind in ("delete", "delete_all")), harness.calls

    def test_the_superseded_objects_are_the_ones_removed(self, tmp_path):
        """A positive control for the assertion above: the previous run's objects really are gone at
        the end, so "removed after the upload" is not "never removed"."""
        harness = ReplaceHarness(tmp_path)
        writes = [os.path.join(harness.output_dir, "scan.laz")]
        harness.run(make_stage("xidM/scan.e57"), fake_popen(0, writes))

        assert sorted(harness.removals()) == sorted(
            key for key in PREVIOUS_RUN_OBJECTS if not key.endswith("scan.laz"))

    def test_the_object_this_run_uploaded_is_never_removed(self, tmp_path):
        """The trap in moving the removal after the upload: the destination listing includes the key
        the new output overwrote, so removing everything listed would delete the run's own result."""
        harness = ReplaceHarness(tmp_path)
        writes = [os.path.join(harness.output_dir, "scan.laz")]
        harness.run(make_stage("xidM/scan.e57"), fake_popen(0, writes))

        uploaded = harness.kind("upload")
        assert uploaded == [os.path.join(OUTPUT_DIR_KEY, "scan.laz")], uploaded
        for key in uploaded:
            assert key not in harness.removals(), (key, harness.removals())

    def test_the_destination_listing_actually_returned_objects(self, tmp_path):
        """The corpus control. Every assertion above is about a destination that already holds
        objects, and an empty listing would satisfy the removal assertions vacuously."""
        harness = ReplaceHarness(tmp_path)
        writes = [os.path.join(harness.output_dir, "scan.laz")]
        harness.run(make_stage("xidM/scan.e57"), fake_popen(0, writes))

        assert harness.kind("list") == [OUTPUT_DIR_KEY], harness.calls
        assert harness._list("aux-bkt", OUTPUT_DIR_KEY), "the harness serves an empty destination"

    def test_an_empty_destination_removes_nothing(self, tmp_path):
        """A first run has nothing to supersede, so it must not attempt a removal at all."""
        harness = ReplaceHarness(tmp_path, existing=[])
        writes = [os.path.join(harness.output_dir, "scan.laz")]
        result = harness.run(make_stage("xidM/scan.e57"), fake_popen(0, writes))

        assert result.status == PipelineStatus.COMPLETE
        assert harness.removals() == [], harness.calls

    def test_a_listing_failure_does_not_fail_the_stage_or_remove_anything(self, tmp_path):
        """The listing is a new S3 call on the success path. `delete_all_path_contents` swallowed its
        own errors, and nothing above `pipeline.run` catches an exception -- `pipelines/core.py` calls
        it bare -- so a raise here would kill the container before its SendTaskFailure and hang the
        parent workflow's callback until taskTimeout."""
        harness = ReplaceHarness(tmp_path)
        harness._list = MagicMock(side_effect=RuntimeError("ListObjectsV2 denied"))
        writes = [os.path.join(harness.output_dir, "scan.laz")]
        result = harness.run(make_stage("xidM/scan.e57"), fake_popen(0, writes))

        assert result.status == PipelineStatus.COMPLETE
        assert harness.removals() == [], harness.calls
