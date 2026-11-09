#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""preview/pcPotreeViewer: the per-run working folder (#1028) and the launch version (#994) together.

An E57/PLY run is two Batch jobs. The first runs the command constructPipeline returns: the definition,
then ``{"inputVersions": {"PDAL": <version>}}``. The second runs the command the PDAL container hands
back on its task token. These tests drive the real constructPipeline command through the container's
entry-point mapping (``apply_input_versions``) and ``core.run``, with the converters stubbed, and pin:

- the first stage (PDAL) reads the asset at the launch version and writes into the run's own folder;
- the second stage (POTREE) reads the intermediate from that same run folder with no version (it reads
  the object PDAL just wrote), and its command is the one-element command an older image also reads;
- a LAS/LAZ run's single POTREE stage reads the asset at the launch version and has no run folder.
"""

import importlib.util
import json
import os
import sys
import types
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from conftest import load_stage_module  # noqa: E402

objects = load_stage_module("utils.pipeline.objects")
core = load_stage_module("pipelines.core")
pdal_pipeline = load_stage_module("pipelines.pdal.pipeline")
potree_pipeline = load_stage_module("pipelines.potree.pipeline")

_LAMBDA_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "lambda", "constructPipeline.py")

VERSION = "3HL4kqtJlcpXroDTDmJ.rmSpXd3dIbrHY"
JOB = "PipelineJob_20261105_070000_001_abcd1234"
RUN_DIR = f"db1/xid1/scan.e57/preview/PotreeViewerRuns/{JOB}/"
VIEWER_DIR = "db1/xid1/scan.e57/preview/PotreeViewer/"


def _construct_pipeline():
    """Load the Potree constructPipeline lambda under a private name, with its logger stubbed."""
    if "customLogging.logger" not in sys.modules:
        pkg, mod = types.ModuleType("customLogging"), types.ModuleType("customLogging.logger")
        mod.safeLogger = lambda **kw: MagicMock()
        pkg.logger = mod
        sys.modules["customLogging"], sys.modules["customLogging.logger"] = pkg, mod
    # constructPipeline imports its sibling lasHeader, as it does from the Lambda code root. Appended,
    # not prepended, so nothing in the Lambda folder shadows a container module.
    lambda_dir = os.path.dirname(_LAMBDA_FILE)
    if lambda_dir not in sys.path:
        sys.path.append(lambda_dir)
    spec = importlib.util.spec_from_file_location("pc_potree_construct_pipeline", _LAMBDA_FILE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _event(file_key, version_id=VERSION):
    event = {
        "jobName": JOB,
        "inputS3AssetFilePath": f"s3://asset-bkt/{file_key}",
        "outputS3AssetFilesPath": "s3://asset-bkt/xid1/",
        "outputS3AssetPreviewPath": "s3://asset-bkt/xid1/",
        "outputS3AssetMetadataPath": "s3://asset-bkt/xid1/",
        "inputOutputS3AssetAuxiliaryFilesPath": f"s3://aux-bkt/db1/{file_key}/preview/PotreeViewer",
        "externalSfnTaskToken": "tok",
    }
    if version_id is not None:
        event["inputVersionId"] = version_id
    return event


@pytest.fixture
def stage_runs(monkeypatch):
    """Stub both converters and the task-token calls; record the stage each job ran and the output it sent."""
    seen = {"stages": [], "sent": []}

    def fake_run(stage, *args, **kwargs):
        seen["stages"].append(json.loads(json.dumps(stage.inputFile | {"type": stage.type,
                                                                        "temporaryFiles": stage.temporaryFiles,
                                                                        "outputFiles": stage.outputFiles})))
        stage.status = objects.PipelineStatus.COMPLETE
        return stage

    monkeypatch.setattr(pdal_pipeline, "run", fake_run)
    monkeypatch.setattr(potree_pipeline, "run", fake_run)
    monkeypatch.setattr(core.sfn, "send_task_success", lambda output: seen["sent"].append(output))
    monkeypatch.setattr(core.sfn, "send_task_failure", MagicMock())
    monkeypatch.setattr(core.sfn, "send_external_task_heartbeat", MagicMock())
    return seen


def _run_job(command):
    """What the container entry point does with a Batch command (``__main__.main``)."""
    return core.run(objects.apply_input_versions(json.loads(command[0]), command[1:]))


@pytest.mark.unit
class TestE57RunFolderAndLaunchVersion:
    def test_first_stage_gets_the_version_and_the_run_folder(self, stage_runs):
        result = _construct_pipeline().lambda_handler(_event("xid1/scan.e57"), None)
        command = result["definition"]
        assert len(command) == 2
        assert json.loads(command[1]) == {"inputVersions": {"PDAL": VERSION}}

        _run_job(command)
        pdal = stage_runs["stages"][0]
        assert pdal["type"] == "PDAL"
        assert pdal["objectKey"] == "xid1/scan.e57" and pdal["versionId"] == VERSION
        assert pdal["outputFiles"] == {"bucketName": "aux-bkt", "objectDir": RUN_DIR}
        assert pdal["temporaryFiles"] == {"bucketName": "aux-bkt", "objectDir": RUN_DIR}

    def test_second_stage_reads_the_run_folder_with_no_version(self, stage_runs):
        command = _construct_pipeline().lambda_handler(_event("xid1/scan.e57"), None)["definition"]
        first = _run_job(command)

        # The POTREE job's command is the one the PDAL container handed back on its task token.
        handed = stage_runs["sent"][0]
        assert handed.currentStageType == "POTREE"
        assert len(handed.definition) == 1, "the second stage's command carries no version argument"
        assert first.definition == handed.definition

        _run_job(handed.definition)
        potree = stage_runs["stages"][1]
        assert potree["type"] == "POTREE"
        assert potree["bucketName"] == "aux-bkt"
        assert potree["objectKey"] == f"{RUN_DIR}scan.laz"
        assert not potree.get("versionId"), "the intermediate is read at its current version"
        assert potree["temporaryFiles"] == {"bucketName": "aux-bkt", "objectDir": RUN_DIR}
        assert potree["outputFiles"] == {"bucketName": "aux-bkt", "objectDir": VIEWER_DIR}

    def test_without_a_version_both_commands_are_the_one_element_form(self, stage_runs):
        command = _construct_pipeline().lambda_handler(_event("xid1/scan.e57", version_id=None), None)["definition"]
        assert len(command) == 1
        _run_job(command)
        _run_job(stage_runs["sent"][0].definition)
        assert [s.get("versionId", "") for s in stage_runs["stages"]] == ["", ""]
        assert [s["temporaryFiles"]["objectDir"] for s in stage_runs["stages"]] == [RUN_DIR, RUN_DIR]


@pytest.mark.unit
class TestLazRunReadsTheLaunchVersion:
    def test_single_potree_stage_gets_the_version_and_no_run_folder(self, stage_runs):
        command = _construct_pipeline().lambda_handler(_event("xid1/scan.laz"), None)["definition"]
        assert json.loads(command[1]) == {"inputVersions": {"POTREE": VERSION}}
        _run_job(command)
        potree = stage_runs["stages"][0]
        assert potree["objectKey"] == "xid1/scan.laz" and potree["versionId"] == VERSION
        assert potree["temporaryFiles"] == {"bucketName": "", "objectDir": ""}
        assert stage_runs["sent"][0].currentStageType is None
