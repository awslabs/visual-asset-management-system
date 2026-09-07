#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""This pipeline runs single-node only, and no layer may advertise a node count.

The Batch job definition is a `batch.EcsJobDefinition` holding one `EcsEc2ContainerDefinition`, which
synthesizes an `AWS::Batch::JobDefinition` of type `container`. AWS Batch rejects `nodeOverrides` on a
container-type definition, so a submission built from a node count could never run — and the operator
input that produced it (`computeConfig.numNodes`) reached the submission through four hops. Each hop
is asserted here, because a value re-introduced at any one of them is invisible at the others: a
`numNodes` the state machine still forwards looks harmless right up to the submission that AWS
rejects.
"""

import os
import sys
import json
import types
import importlib
from unittest.mock import MagicMock, patch

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

for _k, _v in {
    "AWS_DEFAULT_REGION": "us-east-1",
    "AWS_REGION": "us-east-1",
    "BATCH_JOB_QUEUE": "isaaclab-queue",
    "BATCH_JOB_DEFINITION": "isaaclab-jobdef",
    "ORCHESTRATION_BUS_NAME": "vams-orchestration",
    "STATE_MACHINE_ARN": "arn:aws:states:us-east-1:1:stateMachine:IsaacLab",
    "STATE_MACHINE_LOG_GROUP_NAME": "",
    "STATE_MACHINE_LOG_GROUP_ARN": "",
}.items():
    os.environ.setdefault(_k, _v)


def _repo_root():
    """Walk up to the repo root rather than counting `..` segments — pipeline directories sit at
    differing depths, and a miscounted relative path fails as a missing file (which reads like a
    broken test) instead of as the assertion these tests are making."""
    path = _LAMBDA_DIR
    while path != os.path.dirname(path):
        if os.path.isdir(os.path.join(path, "infra")) and os.path.isdir(
                os.path.join(path, "backendPipelines")):
            return path
        path = os.path.dirname(path)
    raise RuntimeError("repo root not found from " + _LAMBDA_DIR)


_INFRA_PIPELINE = os.path.join(
    _repo_root(), "infra", "lib", "nestedStacks", "pipelines", "simulation", "isaacLabTraining")
_CONSTRUCT = os.path.join(_INFRA_PIPELINE, "constructs", "isaacLabTraining-construct.ts")


def _load_execute_batch_job():
    with patch("boto3.client") as mock_client:
        clients = {}

        def _factory(name, *a, **kw):
            clients.setdefault(name, MagicMock())
            return clients[name]

        mock_client.side_effect = _factory
        module = importlib.reload(importlib.import_module("executeBatchJob"))
    module.batch.submit_job.return_value = {"jobId": "job-xyz"}
    return module


def _load_open_pipeline():
    if "openPipeline" in sys.modules:
        return importlib.reload(sys.modules["openPipeline"])
    return importlib.import_module("openPipeline")


@pytest.mark.unit
class TestTheSubmissionIsAlwaysSingleNode:
    def _submit(self, **extra):
        mod = _load_execute_batch_job()
        event = {"jobName": "isaac-1", "definition": json.dumps({"trainingConfig": {}}),
                 "taskToken": "tok", "outputS3AssetFilesPath": "s3://b/a/",
                 "inputS3AssetFilePath": "s3://b/a/in.usd"}
        event.update(extra)
        mod.lambda_handler(event, MagicMock())
        return mod.batch.submit_job.call_args.kwargs

    def test_the_submission_carries_container_overrides(self):
        submitted = self._submit()
        assert "containerOverrides" in submitted
        assert "nodeOverrides" not in submitted

    @pytest.mark.parametrize("num_nodes", [2, 8])
    def test_a_node_count_in_the_payload_changes_nothing(self, num_nodes):
        # A stale state machine or a stale template configBody can still deliver the field. Batch
        # rejects nodeOverrides against this container-type job definition, so the field must be
        # inert rather than authoritative.
        submitted = self._submit(numNodes=num_nodes)
        assert "containerOverrides" in submitted
        assert "nodeOverrides" not in submitted
        assert "numNodes" not in json.dumps(submitted)


@pytest.mark.unit
class TestNoLambdaLayerAdvertisesANodeCount:
    def _s3_with_config_file(self, body):
        s3 = MagicMock()
        s3.get_object.return_value = {
            "Body": MagicMock(read=MagicMock(return_value=json.dumps(body).encode()))}
        paginator = MagicMock()
        paginator.paginate.return_value = [{"Contents": []}]
        s3.get_paginator.return_value = paginator
        return s3

    def _open_pipeline_output(self, event_config, file_config):
        mod = _load_open_pipeline()
        event = {
            "jobName": "isaaclab-training-abcd1234",
            "bucketAsset": "asset-bucket",
            "inputAssetLocationKey": "xid130a6/",
            # A .json path is required for the input file to be read as standing defaults at all.
            "inputS3AssetFilePath": "s3://asset-bucket/xid130a6/defaults.json",
            "outputS3AssetFilesPath": "s3://asset-bucket/pipelines/p1/JOB/output/E1/files/",
            "externalSfnTaskToken": "tok-123",
        }
        event.update(event_config)
        with patch.object(mod, "s3_client", self._s3_with_config_file(file_config)), \
                patch.object(mod, "sfn_client", MagicMock()):
            return mod.lambda_handler(event, MagicMock())

    def test_the_state_machine_payload_carries_no_node_count(self):
        # PrepareExecutionState's `parameters` REPLACE the state, so a field this payload does not
        # return cannot be forwarded to the submission.
        out = self._open_pipeline_output(
            {"trainingConfig": {"mode": "train"}, "computeConfig": {"numNodes": 4}},
            {"computeConfig": {"numNodes": 8}})
        assert "numNodes" not in out

    @pytest.mark.parametrize("mode", ["train", "evaluate"])
    def test_the_job_definition_carries_no_compute_config(self, mode):
        # The container reads computeConfig.numNodes into PipelineConfig; an empty section left in
        # the definition would keep that read alive and keep the operator field looking honored.
        training_config = {"mode": mode}
        if mode == "evaluate":
            training_config["checkpointPath"] = "checkpoints/model_300.pt"
        out = self._open_pipeline_output(
            {"trainingConfig": training_config, "computeConfig": {"numNodes": 4}},
            {"computeConfig": {"numNodes": 8}})
        definition = json.loads(out["definition"])
        assert "computeConfig" not in definition
        assert "numNodes" not in out["definition"]

    def test_a_node_count_in_the_input_file_is_not_resurrected(self):
        # The input file is an asset file an operator selected, and it supplies standing defaults for
        # blank fields — so it is the one hop that can reintroduce a removed field unasked.
        out = self._open_pipeline_output(
            {"trainingConfig": {"mode": "train"}}, {"computeConfig": {"numNodes": 8}})
        assert "numNodes" not in json.dumps(out)


@pytest.mark.unit
class TestTheStateMachineInputCarriesNoNodeCount:
    def _load(self):
        if "vamsExecuteIsaacLabPipeline" in sys.modules:
            return importlib.reload(sys.modules["vamsExecuteIsaacLabPipeline"])
        return importlib.import_module("vamsExecuteIsaacLabPipeline")

    def test_the_execute_lambda_drops_a_compute_config_from_the_input_configuration(self):
        # The template's configBody is what an operator edits, so this is where a reinstated
        # `computeConfig` would enter the pipeline.
        mod = self._load()
        manifest = {
            "inputFiles": [{"bucket": "abkt", "key": "xidM/scene.usd", "assetId": "xidM",
                            "databaseId": "dbM"}],
            "outputs": {"files": "s3://abkt/pipelines/p1/MJOB/output/E1/files/"},
            "auxTempPrefix": "s3://aux/xidM/scene.usd/isaac/",
            "inputMetadataS3Location": "s3://abkt/pipelines/workflowExecutionInputs/E1/metadata.json",
            "systemConfig": {"orchestrationBusArn": "arn:bus",
                             "orchestrationEventPrefix": "vams.prod.execution.E1.pipeline.P1"},
        }
        config = {"trainingConfig": {"mode": "train"}, "computeConfig": {"numNodes": 4}}
        s3 = MagicMock()

        def _get_object(Bucket, Key, **kw):
            payload = manifest if Key.endswith("manifest.json") else config
            return {"Body": MagicMock(read=lambda: json.dumps(payload).encode("utf-8"))}

        s3.get_object.side_effect = _get_object
        start = MagicMock(return_value={"executionArn": "arn:ex"})
        body = {
            "TaskToken": "tok-123",
            "inputManifestS3Location":
                "s3://abkt/pipelines/workflowExecutionInputs/E1/pipeline1/manifest.json",
            "inputConfigurationS3Location":
                "s3://abkt/pipelines/workflowExecutionInputs/E1/pipeline1/config.json",
            "outputS3AssetFilesPath": "s3://abkt/legacy/files/",
            "outputS3AssetPreviewPath": "",
            "outputS3AssetMetadataPath": "",
            "inputOutputS3AssetAuxiliaryFilesPath": "s3://aux/legacy/isaac",
        }
        with patch.object(mod, "s3_client", s3), \
                patch.object(mod.sfn_client, "start_execution", start), \
                patch.object(mod.events_client, "put_events", MagicMock()):
            resp = mod.lambda_handler({"body": json.dumps(body)}, MagicMock())
        assert resp["statusCode"] == 200
        sfn_input = json.loads(start.call_args.kwargs["input"])
        assert sfn_input["trainingConfig"] == {"mode": "train"}
        assert "computeConfig" not in sfn_input
        assert "numNodes" not in json.dumps(sfn_input)


@pytest.mark.unit
class TestTheStateMachineForwardsNoNodeCount:
    """The state machine is the one hop outside this pipeline's lambda directory.

    `PrepareExecutionState` reads `$.openResult.Payload.numNodes` and the batch task reads
    `$.numNodes`. A JSONPath naming a field the payload no longer returns is a `States.Runtime`
    failure of the whole execution, not a skipped field — the same class of break that the
    `orchestrationEventPrefix` omission caused. This asserts the construct edit landed with the
    lambda change rather than after it.
    """

    def _construct(self):
        return open(_CONSTRUCT, encoding="utf-8").read()

    def test_the_prepare_state_forwards_no_node_count(self):
        prepare = self._construct().split(
            'new sfn.Pass(this, "PrepareExecutionState"')[1].split("});")[0]
        assert "numNodes" not in prepare

    def test_the_batch_task_payload_carries_no_node_count(self):
        batch_task = self._construct().split(
            'new tasks.LambdaInvoke(this, "ExecuteBatchJobState"')[1].split("});")[0]
        assert "numNodes" not in batch_task

    def test_the_slices_still_select_the_states_they_name(self):
        # Positive control: both assertions above are negative, and a slice that selected nothing
        # would satisfy them without reading the state machine at all.
        source = self._construct()
        prepare = source.split('new sfn.Pass(this, "PrepareExecutionState"')[1].split("});")[0]
        batch_task = source.split(
            'new tasks.LambdaInvoke(this, "ExecuteBatchJobState"')[1].split("});")[0]
        assert '"definition.$"' in prepare
        assert '"definition.$"' in batch_task
