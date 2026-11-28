#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""An E57/PLY run keeps its PDAL intermediate in a working folder of its own (#1028).

The viewer folder ``{base}/preview/PotreeViewer/`` is fixed per input file. When the PDAL stage wrote
its ``.laz`` there, two runs on one file shared that object: one run's POTREE stage removed the other
run's intermediate as superseded, and a PDAL stage removed the previous octree before the POTREE stage
had produced a replacement. Each run now writes the intermediate under
``{base}/preview/PotreeViewerRuns/<jobName>/`` and names that folder as both stages' ``temporaryFiles``,
so the POTREE stage reads it and removes it, and only the POTREE stage writes the viewer folder.

The definition keeps the keys an older container image parses (``temporaryFiles`` already exists on
every stage), so a run that reaches an image built before this change during an upgrade still starts."""

import importlib
import os
import sys
import types
from unittest.mock import MagicMock

import pytest

_LAMBDA_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _LAMBDA_DIR not in sys.path:
    sys.path.insert(0, _LAMBDA_DIR)

if "customLogging" not in sys.modules:
    _cl_pkg = types.ModuleType("customLogging")
    _cl_logger = types.ModuleType("customLogging.logger")
    _cl_logger.safeLogger = lambda **kw: MagicMock()
    _cl_pkg.logger = _cl_logger
    sys.modules["customLogging"] = _cl_pkg
    sys.modules["customLogging.logger"] = _cl_logger

for k, v in {"AWS_DEFAULT_REGION": "us-east-1", "AWS_REGION": "us-east-1"}.items():
    os.environ.setdefault(k, v)

VIEWER_DIR = "db1/xid1/scan.e57/preview/PotreeViewer/"
STAGE_KEYS = {"type", "inputFile", "outputFiles", "outputMetadata", "temporaryFiles"}


def _load():
    if "constructPipeline" in sys.modules:
        return importlib.reload(sys.modules["constructPipeline"])
    return importlib.import_module("constructPipeline")


def _event(file_key="xid1/scan.e57", job_name="PipelineJob_20261104_010203_004_abcd1234"):
    return {
        "jobName": job_name,
        "inputS3AssetFilePath": f"s3://asset-bkt/{file_key}",
        "outputS3AssetFilesPath": "s3://asset-bkt/xid1/",
        "outputS3AssetPreviewPath": "s3://asset-bkt/xid1/",
        "outputS3AssetMetadataPath": "s3://asset-bkt/xid1/",
        "inputOutputS3AssetAuxiliaryFilesPath": f"s3://aux-bkt/db1/{file_key}/preview/PotreeViewer",
        "externalSfnTaskToken": "tok",
    }


@pytest.mark.unit
class TestPdalIntermediateIsPerRun:
    def test_the_pdal_stage_writes_to_a_folder_named_for_the_run(self):
        definition = _load().construct_pdal_definition(_event())
        pdal, potree = definition["stages"]

        run_dir = "db1/xid1/scan.e57/preview/PotreeViewerRuns/PipelineJob_20261104_010203_004_abcd1234/"
        assert pdal["outputFiles"] == {"bucketName": "aux-bkt", "objectDir": run_dir}
        assert potree["inputFile"]["bucketName"] == "aux-bkt"
        assert potree["inputFile"]["objectKey"] == run_dir + "scan.laz"
        assert potree["outputFiles"] == {"bucketName": "aux-bkt", "objectDir": VIEWER_DIR}

    def test_both_stages_name_the_run_folder_as_their_temporary_files(self):
        definition = _load().construct_pdal_definition(_event())
        for stage in definition["stages"]:
            assert stage["temporaryFiles"]["bucketName"] == "aux-bkt"
            assert stage["temporaryFiles"]["objectDir"] == definition["stages"][0]["outputFiles"]["objectDir"]

    def test_two_concurrent_runs_on_one_file_share_no_intermediate(self):
        """The #1028 race: the second run's intermediate must not be in the folder the first run's
        POTREE stage lists and clears, and neither run's PDAL stage may write the viewer folder."""
        mod = _load()
        first = mod.construct_pdal_definition(_event(job_name="PipelineJob_A"))
        second = mod.construct_pdal_definition(_event(job_name="PipelineJob_B"))

        first_run_dir = first["stages"][0]["outputFiles"]["objectDir"]
        second_run_dir = second["stages"][0]["outputFiles"]["objectDir"]
        assert first_run_dir != second_run_dir
        assert not first_run_dir.startswith(second_run_dir) and not second_run_dir.startswith(first_run_dir)
        for definition in (first, second):
            pdal, potree = definition["stages"]
            assert not pdal["outputFiles"]["objectDir"].startswith(VIEWER_DIR)
            assert not potree["inputFile"]["objectKey"].startswith(VIEWER_DIR)
            # Both runs still publish to the one viewer folder the web viewer reads.
            assert potree["outputFiles"]["objectDir"] == VIEWER_DIR

    def test_the_run_folder_is_not_inside_or_a_prefix_match_of_the_viewer_folder(self):
        """The POTREE stage lists ``VIEWER_DIR`` to find superseded objects; a run folder below it, or
        one sharing its key prefix, would be listed and deleted as superseded."""
        run_dir = _load().construct_pdal_definition(_event())["stages"][0]["outputFiles"]["objectDir"]
        assert not run_dir.startswith(VIEWER_DIR)
        assert not run_dir.startswith(VIEWER_DIR.rstrip("/") + "/")

    def test_a_missing_job_name_still_gets_a_folder_of_its_own(self):
        mod = _load()
        first = mod.construct_pdal_definition(_event(job_name=""))
        second = mod.construct_pdal_definition(_event(job_name=""))
        first_dir = first["stages"][0]["outputFiles"]["objectDir"]
        assert first_dir.startswith("db1/xid1/scan.e57/preview/PotreeViewerRuns/")
        assert first_dir.endswith("/") and first_dir != "db1/xid1/scan.e57/preview/PotreeViewerRuns/"
        assert first_dir != second["stages"][0]["outputFiles"]["objectDir"]

    def test_the_stage_keys_are_the_ones_an_older_image_parses(self):
        for stage in _load().construct_pdal_definition(_event())["stages"]:
            assert set(stage) == STAGE_KEYS
            assert set(stage["temporaryFiles"]) == {"bucketName", "objectDir"}


@pytest.mark.unit
class TestSingleStageRunIsUnchanged:
    def test_a_las_laz_run_writes_the_viewer_folder_and_has_no_working_folder(self):
        definition = _load().construct_potree_definition(_event(file_key="xid1/scan.laz"))
        (potree,) = definition["stages"]
        assert potree["inputFile"]["objectKey"] == "xid1/scan.laz"
        assert potree["outputFiles"]["objectDir"] == "db1/xid1/scan.laz/preview/PotreeViewer/"
        assert potree["temporaryFiles"] == {"bucketName": "", "objectDir": ""}
