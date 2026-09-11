#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""The POTREE stage replaces its destination rather than clearing it before the upload.

The sibling of ``test_pdal_destination_replace_order.py``, and it exists because the defect was the
same one in the same file pair. S4-PIPELINES-037 named only the PDAL stage; the POTREE stage cleared the
identical prefix ahead of its own upload loop, so a conversion that succeeded and then failed its upload
left the asset with neither the previous octree nor the new one.

That window is worse here than in the PDAL stage. This stage's output IS the viewer data — the octree
the frontend reads directly out of the auxiliary bucket — so losing it makes the asset's point-cloud
preview stop rendering until a run succeeds, with nothing to fall back to.

The converter is a fake ``subprocess.Popen`` that writes the files a real run would, so PotreeConverter
need not be installed. Every S3 call is recorded in ONE ordered log, which is what makes "after the
upload" assertable rather than merely "both happened".
"""

import os
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from conftest import load_stage_module  # noqa: E402

objects = load_stage_module("utils.pipeline.objects")
PipelineStage = objects.PipelineStage
PipelineStatus = objects.PipelineStatus

OUTPUT_DIR_KEY = "dbM/xidM/scan.laz/preview/PotreeViewer/"

# What a previous, working run left in the destination: a complete Potree 2.0 octree.
PREVIOUS_RUN_OBJECTS = [
    OUTPUT_DIR_KEY + "metadata.json",
    OUTPUT_DIR_KEY + "hierarchy.bin",
    OUTPUT_DIR_KEY + "octree.bin",
]

# What this run produces. `metadata.json` and `hierarchy.bin` are rewritten; `octree.bin` is too, but
# `pointcloud.js` is new and `octree.bin` is deliberately kept in both lists so the "keys this run wrote
# are not deleted" property has something to bite on.
THIS_RUN_FILES = ["metadata.json", "hierarchy.bin", "octree.bin", "pointcloud.js"]


def fake_popen(returncode, writes=()):
    """Stand in for PotreeConverter: write ``writes`` (absolute paths), then exit with ``returncode``."""

    class _Popen:
        def __init__(self, cmd, *args, **kwargs):
            self.cmd = cmd
            for path in writes:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w", encoding="utf-8") as handle:
                    handle.write("octree bytes")

        def wait(self):
            return returncode

    return _Popen


def make_stage(object_key="dbM/xidM/scan.laz"):
    return PipelineStage(
        type="POTREE",
        inputFile={"bucketName": "abkt", "objectKey": object_key,
                   "fileExtension": os.path.splitext(object_key)[1]},
        outputFiles={"bucketName": "aux-bkt", "objectDir": OUTPUT_DIR_KEY},
        outputMetadata={"bucketName": "", "objectDir": ""},
        temporaryFiles={"bucketName": "", "objectDir": ""},
    )


class ReplaceHarness:
    """A patched destination already holding a previous run's objects, with one ordered call log."""

    def __init__(self, tmp_path, existing=PREVIOUS_RUN_OBJECTS, upload_fails=False,
                 list_raises=False, produces=THIS_RUN_FILES):
        self.module = load_stage_module("pipelines.potree.pipeline")
        self.input_dir = str(tmp_path / "input")
        self.output_dir = str(tmp_path / "output")
        os.makedirs(self.input_dir, exist_ok=True)
        os.makedirs(self.output_dir, exist_ok=True)
        self.existing = list(existing)
        self.upload_fails = upload_fails
        self.list_raises = list_raises
        self.produces = list(produces)
        self.calls = []

    def _create_dir(self, parts):
        return self.output_dir if parts[-1] == "output" else self.input_dir

    def _download(self, bucket, object_key, file_path):
        with open(file_path, "w", encoding="utf-8") as handle:
            handle.write("point cloud bytes")
        return file_path

    def _list(self, bucket, path):
        self.calls.append(("list", path))
        if self.list_raises:
            raise RuntimeError("ListObjectsV2 denied")
        return [{"key": key, "relativePath": key.removeprefix(path)}
                for key in self.existing if key.startswith(path)]

    def _upload(self, bucket, object_key, file_path):
        self.calls.append(("upload", object_key))
        return None if self.upload_fails else object_key

    def _delete(self, bucket, object_key):
        self.calls.append(("delete", object_key))
        return object_key

    def _delete_all(self, bucket, path):
        self.calls.append(("delete_all", path))

    def run(self):
        module = self.module
        writes = [os.path.join(self.output_dir, name) for name in self.produces]
        with patch.object(module.ext, "create_dir", side_effect=self._create_dir), \
                patch.object(module.s3, "download", side_effect=self._download), \
                patch.object(module.s3, "get_all_files_in_path", side_effect=self._list), \
                patch.object(module.s3, "delete", side_effect=self._delete), \
                patch.object(module.s3, "delete_all_path_contents", side_effect=self._delete_all), \
                patch.object(module.s3, "uploadV2", side_effect=self._upload), \
                patch.object(module.subprocess, "Popen", fake_popen(0, writes)):
            return module.run(make_stage(), "", "", False)

    def kind(self, name):
        return [key for call, key in self.calls if call == name]

    def removals(self):
        """Every object this run removed, one at a time OR by clearing the whole prefix."""
        return [key for call, key in self.calls if call in ("delete", "delete_all")]

    def order(self):
        return [call for call, _key in self.calls]


@pytest.mark.unit
class TestTheDestinationIsReplacedNotClearedFirst:
    def test_a_failed_upload_leaves_the_previous_octree_intact(self, tmp_path):
        """The data-loss window. `uploadV2` returns None on a ClientError; under the clear-then-upload
        order the previous octree is already gone by then, so the asset's viewer data is lost with no
        replacement — and this stage's output is what the frontend reads."""
        harness = ReplaceHarness(tmp_path, upload_fails=True)

        harness.run()

        assert harness.removals() == [], (
            "a failed upload removed the previous run's octree, leaving the asset with no viewer "
            f"data at all: {harness.removals()}")

    def test_the_previous_objects_are_removed_only_after_the_upload_succeeded(self, tmp_path):
        # A stale object is required, not decoration: every object in PREVIOUS_RUN_OBJECTS is rewritten
        # by this run, so without one there is nothing to supersede and the ordering assertion below is
        # vacuous. Its own guard caught exactly that.
        stale = OUTPUT_DIR_KEY + "stale-from-an-older-converter.bin"
        harness = ReplaceHarness(tmp_path, existing=PREVIOUS_RUN_OBJECTS + [stale])

        harness.run()

        order = harness.order()
        assert "upload" in order, "nothing was uploaded, so the ordering assertion is vacuous"
        assert "delete" in order, "nothing was removed, so the ordering assertion is vacuous"
        # `list` has no `rindex`; the last upload's position is computed explicitly.
        last_upload = max(i for i, call in enumerate(order) if call == "upload")
        first_removal = min(i for i, call in enumerate(order) if call == "delete")
        assert first_removal > last_upload, (
            f"a removal happened before the last upload finished: {harness.calls}")

    def test_the_superseded_object_is_the_one_removed(self, tmp_path):
        """Only what this run did NOT rewrite. `octree.bin` and the two rewritten files are in both
        sets, so an implementation that deletes everything it listed would delete its own result."""
        stale = OUTPUT_DIR_KEY + "stale-from-an-older-converter.bin"
        harness = ReplaceHarness(tmp_path, existing=PREVIOUS_RUN_OBJECTS + [stale])

        harness.run()

        assert harness.kind("delete") == [stale], (
            f"expected only the superseded object to be removed, got {harness.kind('delete')}")

    def test_the_object_this_run_uploaded_is_never_removed(self, tmp_path):
        """The trap the per-key deletion introduces, stated as its own assertion."""
        harness = ReplaceHarness(tmp_path)

        harness.run()

        uploaded = set(harness.kind("upload"))
        assert uploaded, "nothing was uploaded, so this assertion is vacuous"
        assert not (uploaded & set(harness.kind("delete"))), (
            "this run deleted a key it had just written: "
            f"{sorted(uploaded & set(harness.kind('delete')))}")

    def test_the_destination_listing_actually_returned_objects(self, tmp_path):
        """The corpus control. Every assertion above is satisfied by a destination that was empty."""
        harness = ReplaceHarness(tmp_path)

        harness.run()

        assert harness.kind("list") == [OUTPUT_DIR_KEY], (
            f"the destination was not listed: {harness.calls}")
        assert len(PREVIOUS_RUN_OBJECTS) >= 3, "the fixture must hold objects to supersede"

    def test_an_empty_destination_removes_nothing(self, tmp_path):
        """A first run has nothing to supersede and must not attempt a removal."""
        harness = ReplaceHarness(tmp_path, existing=[])

        harness.run()

        assert harness.removals() == []
        assert harness.kind("upload"), "the first run must still upload its output"

    def test_a_listing_failure_does_not_fail_the_stage_or_remove_anything(self, tmp_path):
        """A raise here would end the container before it reports the workflow's task token, since
        nothing above ``run`` catches — the parent would then wait out its full task timeout."""
        harness = ReplaceHarness(tmp_path, list_raises=True)

        result = harness.run()

        assert result.status == PipelineStatus.COMPLETE, (
            "a destination listing failure failed the whole stage")
        assert harness.removals() == []
        assert harness.kind("upload"), "the stage must still upload its output"

    def test_the_prefix_is_never_cleared_wholesale(self, tmp_path):
        """The mechanism, named. `delete_all_path_contents` on the destination IS the defect, and this
        fails if it returns however the per-key deletions behave."""
        harness = ReplaceHarness(tmp_path)

        harness.run()

        assert harness.kind("delete_all") == [], (
            f"the destination prefix was cleared wholesale: {harness.kind('delete_all')}")
