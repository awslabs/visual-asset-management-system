#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Two E57/PLY runs on one file, and a run whose POTREE stage fails (#1028).

An E57/PLY run is two Batch jobs: PDAL converts the input to a ``.laz`` intermediate, then POTREE
builds the octree the web viewer reads from ``{base}/preview/PotreeViewer/``. When the intermediate was
written into that viewer folder, runs on one file shared it: one run's POTREE stage removed the other
run's ``.laz`` as superseded, so the other run failed at its download, and a PDAL stage removed the
previous octree before any replacement existed, so a failed POTREE stage left the asset with no preview.

``constructPipeline`` now gives each run a working folder of its own (both stages' ``temporaryFiles``).
These tests drive the real PDAL and POTREE stage modules against one in-memory bucket, with fake
converters, and check: concurrent runs both finish and leave a working viewer; PDAL succeeding then
POTREE failing keeps the previous preview; the POTREE stage removes only its own working folder; and
a definition built before the change (empty ``temporaryFiles``, intermediate in the viewer folder) still
runs on this image."""

import itertools
import os
import sys
from contextlib import ExitStack
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from conftest import load_stage_module  # noqa: E402

objects = load_stage_module("utils.pipeline.objects")
PipelineStage = objects.PipelineStage
PipelineStatus = objects.PipelineStatus
s3_module = load_stage_module("utils.aws.s3")
pdal_pipeline = load_stage_module("pipelines.pdal.pipeline")
potree_pipeline = load_stage_module("pipelines.potree.pipeline")

AUX = "aux-bkt"
ASSET = "asset-bkt"
BASE = "db1/xid1/scan.e57/preview/"
VIEWER_DIR = BASE + "PotreeViewer/"
OCTREE = ["metadata.json", "hierarchy.bin", "octree.bin"]
PREVIOUS_OCTREE = {VIEWER_DIR + name: "previous octree" for name in OCTREE}


def run_dir(job):
    return f"{BASE}PotreeViewerRuns/{job}/"


def two_stage_definition(job):
    """The stages ``construct_pdal_definition`` builds for an E57 run named ``job``."""
    working = {"bucketName": AUX, "objectDir": run_dir(job)}
    pdal = PipelineStage(
        type="PDAL",
        inputFile={"bucketName": ASSET, "objectKey": "xid1/scan.e57", "fileExtension": ".e57"},
        outputFiles=dict(working),
        outputMetadata={"bucketName": "", "objectDir": ""},
        temporaryFiles=dict(working),
    )
    potree = PipelineStage(
        type="POTREE",
        inputFile={"bucketName": AUX, "objectKey": run_dir(job) + "scan.laz", "fileExtension": ".laz"},
        outputFiles={"bucketName": AUX, "objectDir": VIEWER_DIR},
        outputMetadata={"bucketName": "", "objectDir": ""},
        temporaryFiles=dict(working),
    )
    return pdal, potree


def pre_change_definition():
    """The stages a definition built before #1028 carries: the intermediate in the viewer folder and
    no temporary files. A run in flight across an upgrade reaches this image with it."""
    empty = {"bucketName": "", "objectDir": ""}
    pdal = PipelineStage(
        type="PDAL",
        inputFile={"bucketName": ASSET, "objectKey": "xid1/scan.e57", "fileExtension": ".e57"},
        outputFiles={"bucketName": AUX, "objectDir": VIEWER_DIR},
        outputMetadata=dict(empty),
        temporaryFiles=dict(empty),
    )
    potree = PipelineStage(
        type="POTREE",
        inputFile={"bucketName": AUX, "objectKey": VIEWER_DIR + "scan.laz", "fileExtension": ".laz"},
        outputFiles={"bucketName": AUX, "objectDir": VIEWER_DIR},
        outputMetadata=dict(empty),
        temporaryFiles=dict(empty),
    )
    return pdal, potree


class FakeBucketStore:
    """One in-memory S3 shared by every stage of every run, plus fake PDAL and PotreeConverter
    binaries that write what the real ones would."""

    def __init__(self, tmp_path, existing=None):
        self.objects = {(ASSET, "xid1/scan.e57"): "e57 bytes"}
        for key, body in (existing or {}).items():
            self.objects[(AUX, key)] = body
        self.tmp_path = tmp_path
        self.dirs = itertools.count()
        self.potree_exit = 0
        self.upload_fails_for = ()

    def keys(self, prefix=""):
        return sorted(key for bucket, key in self.objects if bucket == AUX and key.startswith(prefix))

    # s3 helpers -------------------------------------------------------------------------------------
    def download(self, bucket, object_key, file_path):
        if (bucket, object_key) not in self.objects:
            return None
        with open(file_path, "w", encoding="utf-8") as handle:
            handle.write(self.objects[(bucket, object_key)])
        return file_path

    def upload(self, bucket, object_key, file_path):
        if any(object_key.endswith(name) for name in self.upload_fails_for):
            return None
        with open(file_path, encoding="utf-8") as handle:
            self.objects[(bucket, object_key)] = handle.read()
        return object_key

    def list(self, bucket, path):
        return [{"key": key, "relativePath": key[len(path):]}
                for b, key in sorted(self.objects) if b == bucket and key.startswith(path)]

    def delete(self, bucket, object_key):
        self.objects.pop((bucket, object_key), None)
        return object_key

    def delete_all(self, bucket, path):
        raise AssertionError(f"a stage cleared {bucket}/{path} wholesale")

    def create_dir(self, parts):
        path = os.path.join(str(self.tmp_path), f"{next(self.dirs)}-{parts[-1]}")
        os.makedirs(path, exist_ok=True)
        return path

    # converters -------------------------------------------------------------------------------------
    def popen(self, cmd, *args, **kwargs):
        store = self

        class _Popen:
            def wait(self):
                if cmd[0] == "pdal":
                    with open(cmd[cmd.index("-o") + 1], "w", encoding="utf-8") as handle:
                        handle.write("laz bytes")
                    return 0
                if store.potree_exit != 0:
                    return store.potree_exit
                source = cmd[cmd.index("--source") + 1]
                outdir = cmd[cmd.index("--outdir") + 1]
                with open(source, encoding="utf-8") as handle:
                    converted = "octree of " + handle.read()
                for name in OCTREE:
                    with open(os.path.join(outdir, name), "w", encoding="utf-8") as handle:
                        handle.write(converted)
                return 0

            def communicate(self, *args, **kwargs):
                # The PDAL stage captures the converter's output to report it on a failure.
                self.returncode = self.wait()
                return "", None

        return _Popen()

    def run_stage(self, stage):
        """Run one stage the way ``core.run`` does; an exception counts as a failed Batch job."""
        module = pdal_pipeline if stage.type == "PDAL" else potree_pipeline
        with ExitStack() as stack:
            stack.enter_context(patch.object(s3_module, "download", side_effect=self.download))
            stack.enter_context(patch.object(s3_module, "uploadV2", side_effect=self.upload))
            stack.enter_context(patch.object(s3_module, "get_all_files_in_path", side_effect=self.list))
            stack.enter_context(patch.object(s3_module, "delete", side_effect=self.delete))
            stack.enter_context(patch.object(s3_module, "delete_all_path_contents", side_effect=self.delete_all))
            stack.enter_context(patch.object(module.ext, "create_dir", side_effect=self.create_dir))
            stack.enter_context(patch.object(module.subprocess, "Popen", side_effect=self.popen))
            try:
                return module.run(stage, "", "", False).status
            except Exception:  # noqa: BLE001 - an uncaught raise fails the Batch job
                return PipelineStatus.FAILED


@pytest.mark.unit
class TestConcurrentRunsOnOneFile:
    def test_two_interleaved_runs_both_finish(self, tmp_path):
        """A's PDAL, B's PDAL, A's POTREE, B's POTREE: the order that lost B's intermediate."""
        store = FakeBucketStore(tmp_path, existing=PREVIOUS_OCTREE)
        a_pdal, a_potree = two_stage_definition("JobA")
        b_pdal, b_potree = two_stage_definition("JobB")

        assert store.run_stage(a_pdal) == PipelineStatus.COMPLETE
        assert store.run_stage(b_pdal) == PipelineStatus.COMPLETE
        assert store.run_stage(a_potree) == PipelineStatus.COMPLETE
        assert store.run_stage(b_potree) == PipelineStatus.COMPLETE

    def test_the_last_run_to_finish_is_the_preview(self, tmp_path):
        store = FakeBucketStore(tmp_path, existing=PREVIOUS_OCTREE)
        a_pdal, a_potree = two_stage_definition("JobA")
        b_pdal, b_potree = two_stage_definition("JobB")
        for stage in (a_pdal, b_pdal, a_potree, b_potree):
            store.run_stage(stage)

        assert store.keys(VIEWER_DIR) == sorted(VIEWER_DIR + name for name in OCTREE)
        assert all(store.objects[(AUX, VIEWER_DIR + name)] == "octree of laz bytes" for name in OCTREE)

    def test_no_working_folder_is_left_behind(self, tmp_path):
        store = FakeBucketStore(tmp_path)
        a_pdal, a_potree = two_stage_definition("JobA")
        b_pdal, b_potree = two_stage_definition("JobB")
        for stage in (a_pdal, b_pdal, a_potree, b_potree):
            store.run_stage(stage)

        assert store.keys(BASE + "PotreeViewerRuns/") == []

    def test_a_potree_stage_removes_only_its_own_working_folder(self, tmp_path):
        store = FakeBucketStore(tmp_path)
        a_pdal, a_potree = two_stage_definition("JobA")
        b_pdal, _b_potree = two_stage_definition("JobB")
        store.run_stage(a_pdal)
        store.run_stage(b_pdal)
        store.run_stage(a_potree)

        assert store.keys(run_dir("JobA")) == []
        assert store.keys(run_dir("JobB")) == [run_dir("JobB") + "scan.laz"]


@pytest.mark.unit
class TestAFailedPotreeStageKeepsThePreviousPreview:
    def test_the_pdal_stage_leaves_the_viewer_folder_alone(self, tmp_path):
        store = FakeBucketStore(tmp_path, existing=PREVIOUS_OCTREE)
        pdal, _potree = two_stage_definition("JobA")

        assert store.run_stage(pdal) == PipelineStatus.COMPLETE
        assert {key: store.objects[(AUX, key)] for key in store.keys(VIEWER_DIR)} == PREVIOUS_OCTREE

    def test_pdal_ok_then_converter_failure_keeps_the_previous_octree(self, tmp_path):
        store = FakeBucketStore(tmp_path, existing=PREVIOUS_OCTREE)
        pdal, potree = two_stage_definition("JobA")
        store.potree_exit = 3

        assert store.run_stage(pdal) == PipelineStatus.COMPLETE
        assert store.run_stage(potree) == PipelineStatus.FAILED
        assert {key: store.objects[(AUX, key)] for key in store.keys(VIEWER_DIR)} == PREVIOUS_OCTREE

    def test_pdal_ok_then_upload_failure_keeps_the_previous_octree(self, tmp_path):
        store = FakeBucketStore(tmp_path, existing=PREVIOUS_OCTREE)
        pdal, potree = two_stage_definition("JobA")
        store.upload_fails_for = ("hierarchy.bin",)

        assert store.run_stage(pdal) == PipelineStatus.COMPLETE
        assert store.run_stage(potree) == PipelineStatus.FAILED
        assert set(store.keys(VIEWER_DIR)) == set(PREVIOUS_OCTREE)

    def test_a_failed_potree_stage_still_removes_its_working_folder(self, tmp_path):
        store = FakeBucketStore(tmp_path, existing=PREVIOUS_OCTREE)
        pdal, potree = two_stage_definition("JobA")
        store.potree_exit = 3
        store.run_stage(pdal)
        store.run_stage(potree)

        assert store.keys(run_dir("JobA")) == []


@pytest.mark.unit
class TestWorkingFolderGuards:
    def test_a_temporary_folder_that_overlaps_the_viewer_folder_is_not_removed(self, tmp_path):
        """The removal is scoped to the run's working folder; a definition naming the viewer folder (or
        a folder holding it) there must not turn it into a wholesale delete of the preview."""
        store = FakeBucketStore(tmp_path, existing=PREVIOUS_OCTREE)
        for overlapping in (VIEWER_DIR, BASE, VIEWER_DIR + "sub/"):
            potree = PipelineStage(
                type="POTREE",
                inputFile={"bucketName": AUX, "objectKey": "missing.laz", "fileExtension": ".laz"},
                outputFiles={"bucketName": AUX, "objectDir": VIEWER_DIR},
                outputMetadata={"bucketName": "", "objectDir": ""},
                temporaryFiles={"bucketName": AUX, "objectDir": overlapping},
            )
            store.run_stage(potree)
            assert set(store.keys(VIEWER_DIR)) == set(PREVIOUS_OCTREE), overlapping

    def test_a_temporary_folder_without_a_trailing_slash_is_not_removed(self, tmp_path):
        """``.../PotreeViewerRuns/JobA`` as a prefix also matches ``JobAB/``, another run's folder."""
        store = FakeBucketStore(tmp_path)
        store.objects[(AUX, run_dir("JobAB") + "scan.laz")] = "other run"
        potree = PipelineStage(
            type="POTREE",
            inputFile={"bucketName": AUX, "objectKey": "missing.laz", "fileExtension": ".laz"},
            outputFiles={"bucketName": AUX, "objectDir": VIEWER_DIR},
            outputMetadata={"bucketName": "", "objectDir": ""},
            temporaryFiles={"bucketName": AUX, "objectDir": run_dir("JobA").rstrip("/")},
        )
        store.run_stage(potree)
        assert store.keys(run_dir("JobAB")) == [run_dir("JobAB") + "scan.laz"]


@pytest.mark.unit
class TestADefinitionBuiltBeforeTheChange:
    def test_a_pre_change_two_stage_run_still_finishes_and_publishes(self, tmp_path):
        """Upgrade window: the run's definition was built by the previous constructPipeline."""
        store = FakeBucketStore(tmp_path, existing=PREVIOUS_OCTREE)
        pdal, potree = pre_change_definition()

        assert store.run_stage(pdal) == PipelineStatus.COMPLETE
        assert store.run_stage(potree) == PipelineStatus.COMPLETE
        # The octree is published and the intermediate the old PDAL path left in the viewer folder is
        # removed as superseded, as before the change.
        assert store.keys(VIEWER_DIR) == sorted(VIEWER_DIR + name for name in OCTREE)

    def test_a_pre_change_pdal_stage_no_longer_removes_the_previous_octree(self, tmp_path):
        """The PDAL stage removed what its destination held once its upload finished; with a pre-change
        definition that destination is the viewer folder, so a POTREE failure after it left no preview."""
        store = FakeBucketStore(tmp_path, existing=PREVIOUS_OCTREE)
        pdal, potree = pre_change_definition()
        store.potree_exit = 3

        assert store.run_stage(pdal) == PipelineStatus.COMPLETE
        assert store.run_stage(potree) == PipelineStatus.FAILED
        for key, body in PREVIOUS_OCTREE.items():
            assert store.objects.get((AUX, key)) == body, key
