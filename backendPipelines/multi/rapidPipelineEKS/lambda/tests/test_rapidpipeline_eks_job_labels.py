#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""The Kubernetes Job a RapidPipeline EKS run starts, and its pods, carry the pipeline label
(vams/pipeline) the construct sets in PIPELINE_JOB_LABELS, as the other pipelines tag their AWS Batch
jobs and Amazon ECS tasks. The existing labels, which the handler matches its own job by, stay."""

import importlib
import json
import os
import sys
import types
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
    "CONTAINER_IMAGE_URI": "123456789012.dkr.ecr.us-east-1.amazonaws.com/rapid-pipeline:latest",
    "EKS_CLUSTER_NAME": "test-cluster",
    "KUBERNETES_NAMESPACE": "default",
}.items():
    os.environ.setdefault(_k, _v)

LABELS = {"vams/pipeline": "rapidPipelineEks"}

pytestmark = pytest.mark.unit


def _ctx():
    ctx = MagicMock()
    ctx.aws_request_id = "req-test"
    ctx.get_remaining_time_in_millis.return_value = 300000
    return ctx


def _event():
    return {
        "operation": "CONSTRUCT_PIPELINE",
        "jobName": "PipelineJobEKS_x",
        "inputS3AssetFilePath": "s3://abkt/xidM/part.glb",
        "outputS3AssetFilesPath": "s3://abkt/pipelines/p1/MJOB/output/E1/files/",
        "inputOutputS3AssetAuxiliaryFilesPath": "s3://aux/xidM/eks/p1",
        "inputMetadataS3Location": "s3://abkt/.../metadata.json",
        "inputConfigurationS3Location": "s3://abkt/.../config.json",
        "externalSfnTaskToken": "tok",
        "outputFileType": ".glb",
    }


def _manifest(labels_env):
    with patch.dict(os.environ):
        if labels_env is None:
            os.environ.pop("PIPELINE_JOB_LABELS", None)
        else:
            os.environ["PIPELINE_JOB_LABELS"] = labels_env
        if "consolidated_handler" in sys.modules:
            mod = importlib.reload(sys.modules["consolidated_handler"])
        else:
            mod = importlib.import_module("consolidated_handler")
    s3 = MagicMock()
    s3.get_object.side_effect = Exception("AccessDenied")
    with patch.object(mod, "s3", s3):
        return mod.lambda_handler(_event(), _ctx())["jobManifest"]


def test_the_job_and_its_pods_carry_the_pipeline_label():
    manifest = _manifest(json.dumps(LABELS))
    job_labels = manifest["metadata"]["labels"]
    pod_labels = manifest["spec"]["template"]["metadata"]["labels"]
    assert job_labels["vams/pipeline"] == "rapidPipelineEks"
    assert pod_labels["vams/pipeline"] == "rapidPipelineEks"
    # The labels the handler finds its own job by are unchanged.
    assert job_labels["job-id"] == pod_labels["job-id"]
    assert job_labels["app"] == pod_labels["app"] == "rapid-pipeline"


def test_without_the_labels_variable_the_job_carries_only_its_own_labels():
    manifest = _manifest(None)
    assert "vams/pipeline" not in manifest["metadata"]["labels"]
    assert "vams/pipeline" not in manifest["spec"]["template"]["metadata"]["labels"]
