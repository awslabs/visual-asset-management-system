#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""The set of input formats this pipeline advertises is the set it actually accepts.

Three surfaces have to agree, and each one is authored separately: the `vamsSchema` declaration (the
pipeline filter, the workflow filter, and the file-upload trigger filter), the human-readable
descriptions an operator reads when choosing the pipeline, and the `ALLOWED_INPUT_FILEEXTENSIONS`
gate the openPipeline lambda enforces at run time. GLTF belongs to none of them: the Blender script
has no `.gltf` branch, so a `.gltf` file would render an object-less scene.
"""

import os
import io
import re
import sys
import json
import types
import importlib
from unittest.mock import MagicMock, patch

import pytest

_LAMBDA_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PIPELINE_ROOT = os.path.dirname(_LAMBDA_DIR)
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(_PIPELINE_ROOT)))
if _LAMBDA_DIR not in sys.path:
    sys.path.insert(0, _LAMBDA_DIR)

# Stub customLogging so the lambdas import without aws_lambda_powertools.
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
    "STATE_MACHINE_ARN": "arn:aws:states:us-east-1:1:stateMachine:GenAiMetadata3dLabeling",
    "ALLOWED_INPUT_FILEEXTENSIONS": ".glb,.fbx,.obj,.stl,.ply,.usd,.dae,.abc",
    "ORCHESTRATION_BUS_NAME": "vams-orchestration",
    "STATE_MACHINE_LOG_GROUP_NAME": "/aws/vendedlogs/GenAiMetadata3dLabeling",
    "STATE_MACHINE_LOG_GROUP_ARN":
        "arn:aws:logs:us-east-1:1:log-group:/aws/vendedlogs/GenAiMetadata3dLabeling:*",
}.items():
    os.environ.setdefault(_k, _v)

PIPELINE_SCHEMA = os.path.join(_PIPELINE_ROOT, "vamsSchema", "pipeline.json")
WORKFLOW_SCHEMA = os.path.join(_PIPELINE_ROOT, "vamsSchema", "workflow.json")
TEMPLATES_DIR = os.path.join(_PIPELINE_ROOT, "vamsSchema", "templates")

# The run-time gate is supplied by the CDK construct, outside this tree. Pinning it here is what
# catches the declaration and the deployed gate drifting apart.
CDK_CONSTRUCT = os.path.join(
    _REPO_ROOT, "infra", "lib", "nestedStacks", "pipelines", "genAi", "metadata3dLabeling",
    "constructs", "metadata3dLabeling-construct.ts")

EXPECTED_EXTENSIONS = sorted([".abc", ".dae", ".fbx", ".glb", ".obj", ".ply", ".stl", ".usd"])


def _load_json(path):
    return json.load(io.open(path, encoding="utf-8"))


def _extensions(allow_patterns):
    return sorted(pattern.replace("*", "").lower() for pattern in allow_patterns)


@pytest.mark.unit
class TestDeclaredFormats:
    def test_pipeline_filter_declares_the_expected_eight(self):
        schema = _load_json(PIPELINE_SCHEMA)
        allow = schema["systemConfig"]["inputFileFilters"]["allow"]
        assert _extensions(allow) == EXPECTED_EXTENSIONS

    def test_workflow_and_trigger_filters_match_the_pipeline(self):
        workflow = _load_json(WORKFLOW_SCHEMA)
        assert _extensions(workflow["systemConfig"]["inputFileFilters"]["allow"]) == EXPECTED_EXTENSIONS
        for trigger in workflow["triggers"]:
            assert _extensions(trigger["inputFileFilters"]["allow"]) == EXPECTED_EXTENSIONS

    def test_no_declaration_advertises_gltf(self):
        """GLTF is not supported: no filter may list it and no description may claim it."""
        surfaces = {PIPELINE_SCHEMA: _load_json(PIPELINE_SCHEMA),
                    WORKFLOW_SCHEMA: _load_json(WORKFLOW_SCHEMA)}
        for name in sorted(os.listdir(TEMPLATES_DIR)):
            if name.endswith(".json"):
                path = os.path.join(TEMPLATES_DIR, name)
                surfaces[path] = _load_json(path)

        for path, body in surfaces.items():
            text = json.dumps(body).lower()
            assert "gltf" not in text, f"{os.path.basename(path)} still advertises GLTF"

    def test_cdk_runtime_gate_matches_the_declaration(self):
        """The deployed ALLOWED_INPUT_FILEEXTENSIONS must be exactly the declared set.

        Cross-tree pin: if the constant is renamed or moved in the CDK construct, update this test
        together with it rather than deleting the assertion.
        """
        assert os.path.isfile(CDK_CONSTRUCT), f"CDK construct not found at {CDK_CONSTRUCT}"
        source = io.open(CDK_CONSTRUCT, encoding="utf-8").read()
        match = re.search(r'allowedInputFileExtensions\s*=\s*"([^"]*)"', source)
        assert match, "allowedInputFileExtensions literal not found in the CDK construct"
        assert sorted(match.group(1).lower().split(",")) == EXPECTED_EXTENSIONS


@pytest.mark.unit
class TestOpenPipelineExtensionGate:
    """openPipeline is the run-time gate: it aborts before starting the state machine."""

    def _load(self):
        if "openPipeline" in sys.modules:
            return importlib.reload(sys.modules["openPipeline"])
        return importlib.import_module("openPipeline")

    def _event(self, key):
        return {
            "inputS3AssetFilePath": f"s3://abkt/xidM/test/{key}",
            "outputS3AssetFilesPath": "s3://abkt/pipelines/p1/MJOB/output/E1/files/",
            "outputS3AssetPreviewPath": "s3://abkt/pipelines/p1/MJOB/output/E1/previews/",
            "outputS3AssetMetadataPath": "s3://abkt/pipelines/p1/MJOB/output/E1/metadata/",
            "inputOutputS3AssetAuxiliaryFilesPath": "s3://aux/xidM/genAi/metadata3dLabeling/",
            "inputMetadataS3Location": "s3://abkt/.../metadata.json",
            "inputConfigurationS3Location": "s3://abkt/.../config.json",
            "sfnExternalTaskToken": "tok-123",
        }

    def _run(self, filename):
        """Run openPipeline against the DEPLOYED extension list, independent of env ordering."""
        mod = self._load()
        import datetime
        start = MagicMock(return_value={
            "executionArn": "arn:aws:states:us-east-1:1:execution:GenAiMetadata3dLabeling:PipelineJob_x",
            "startDate": datetime.datetime(2026, 1, 1, 0, 0, 0),
        })
        sfn_client = MagicMock()
        with patch.object(mod, "ALLOWED_INPUT_FILEEXTENSIONS",
                          ".glb,.fbx,.obj,.stl,.ply,.usd,.dae,.abc"), \
                patch.object(mod.sfn, "start_execution", start), \
                patch.object(mod.sfn, "send_task_failure", sfn_client.send_task_failure), \
                patch.object(mod.events_client, "put_events", MagicMock()):
            response = mod.lambda_handler(self._event(filename), MagicMock())
        return response, start

    @pytest.mark.parametrize("filename", ["pump.gltf", "pump.GLTF"])
    def test_gltf_is_rejected_before_the_state_machine_starts(self, filename):
        response, start = self._run(filename)
        assert response["statusCode"] == 400
        assert "cannot process file type" in response["body"]["message"]
        start.assert_not_called()

    @pytest.mark.parametrize("filename", ["pump.glb", "pump.usd", "pump.dae", "pump.abc",
                                          "pump.obj", "pump.stl", "pump.ply", "pump.fbx"])
    def test_every_declared_extension_is_accepted(self, filename):
        """Positive control for the rejection above: the eight declared formats still start a run."""
        response, start = self._run(filename)
        assert response["statusCode"] == 200
        assert start.call_count == 1
