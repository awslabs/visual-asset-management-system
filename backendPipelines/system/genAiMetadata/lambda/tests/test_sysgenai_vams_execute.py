#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""vamsExecute captures the workflow task token before anything can fail, resolves the manifest,
threads the file identity to openPipeline, and reports EVERY failure route against the token — the
pre-invoke rejections and the nested invoke's FunctionError channel included. A route that returns or
raises without reporting leaves the workflow task RUNNING for its full 18,000-second taskTimeout."""

import io
import json
import os
from unittest.mock import MagicMock

import pytest

import sysgenai_harness as h

MANIFEST_KEY = "pipelines/workflowExecutionInputs/E1/pipeline1/manifest.json"
MANIFEST_URI = f"s3://abkt/{MANIFEST_KEY}"
MODEL_OPS_VAMS_EXECUTE = os.path.join(h.REPO_ROOT, "backendPipelines", "multi", "modelOps", "lambda",
                                      "vamsExecuteModelOps.py")


def _body(**over):
    body = {
        "TaskToken": h.TASK_TOKEN,
        "workflowDatabaseId": "GLOBAL",
        "workflowId": "system-genai-metadata",
        "workflowExecutionId": "E1",
        "inputManifestS3Location": MANIFEST_URI,
        "inputConfigurationS3Location": "s3://abkt/pipelines/workflowExecutionInputs/E1/pipeline1/config.json",
        "executingUserName": "user@x",
        "executingRequestContext": "user@x",
    }
    body.update(over)
    return body


def _manifest(**over):
    manifest = {
        "schemaVersion": 1,
        "inputFiles": [{"relativePath": "/models/pump.glb", "databaseId": "dbM", "assetId": "xidM",
                        "assetRootS3Key": "xidM/", "auxPreviewPrefix": "dbM/xidM/models/pump.glb/preview",
                        "bucket": "abkt", "bucketId": "bkt-01", "key": "xidM/models/pump.glb", "versionId": "v1"}],
        "inputMetadataS3Location": "s3://abkt/pipelines/workflowExecutionInputs/E1/metadata.json",
        "outputs": {"bucket": "abkt",
                    "files": "pipelines/sgm/sgm/output/E1/files/",
                    "previews": "pipelines/sgm/sgm/output/E1/previews/",
                    "metadata": "pipelines/sgm/sgm/output/E1/metadata/",
                    "results": "pipelines/sgm/sgm/output/E1/results/"},
        "outputTarget": {"locationType": "asset", "assetId": "xidM", "databaseId": "dbM",
                         "fileBaseExecutionPathExtension": "/"},
        "auxBucket": "aux",
        "auxTempPrefix": "pipelines/system-genai-metadata/E1/",
        "auxPreviewPipelineSuffix": "",
        "systemConfig": {"orchestrationBusArn": "arn:aws:events:us-east-1:1:event-bus/vams-orchestration",
                         "orchestrationEventPrefix": "vams.prod.execution.E1.pipeline.P1"},
    }
    manifest.update(over)
    return manifest


def _run(body, manifest, invoke_response=None):
    mod = h.load_handler("vamsExecuteSystemGenAiMetadataPipeline")
    s3 = h.FakeS3()
    if manifest is not None:
        s3.put_json("abkt", MANIFEST_KEY, manifest)
    mod.s3_client = s3
    mod.sfn_client = MagicMock()
    mod.lambda_client = MagicMock()
    mod.lambda_client.invoke = MagicMock(return_value=invoke_response or {"StatusCode": 200})
    response = mod.lambda_handler({"body": json.dumps(body)}, MagicMock())
    return mod, response


def _payload(mod):
    return json.loads(mod.lambda_client.invoke.call_args.kwargs["Payload"].decode("utf-8"))


def _assert_token_failed(mod):
    assert mod.sfn_client.send_task_failure.call_count == 1
    kwargs = mod.sfn_client.send_task_failure.call_args.kwargs
    assert kwargs["taskToken"] == h.TASK_TOKEN
    assert kwargs["error"] == "SystemGenAiMetadataPipelineError"
    assert len(kwargs["cause"]) <= 256


@pytest.mark.unit
class TestHappyPath:
    def test_forwards_the_file_identity_and_every_output_path(self):
        mod, response = _run(_body(), _manifest())
        assert response["statusCode"] == 200
        mod.sfn_client.send_task_failure.assert_not_called()
        invoke_kwargs = mod.lambda_client.invoke.call_args.kwargs
        assert invoke_kwargs["FunctionName"] == "test-open-pipeline"
        assert invoke_kwargs["InvocationType"] == "RequestResponse"
        payload = _payload(mod)
        assert payload == {
            "inputS3AssetFilePath": "s3://abkt/xidM/models/pump.glb",
            "outputS3AssetFilesPath": "s3://abkt/pipelines/sgm/sgm/output/E1/files/",
            "outputS3AssetPreviewPath": "s3://abkt/pipelines/sgm/sgm/output/E1/previews/",
            "outputS3AssetMetadataPath": "s3://abkt/pipelines/sgm/sgm/output/E1/metadata/",
            "outputS3AssetResultsPath": "s3://abkt/pipelines/sgm/sgm/output/E1/results/",
            "inputOutputS3AssetAuxiliaryFilesPath": "s3://aux/pipelines/system-genai-metadata/E1/",
            "inputMetadataS3Location": "s3://abkt/pipelines/workflowExecutionInputs/E1/metadata.json",
            "inputConfigurationS3Location":
                "s3://abkt/pipelines/workflowExecutionInputs/E1/pipeline1/config.json",
            "sfnExternalTaskToken": h.TASK_TOKEN,
            "executingUserName": "user@x",
            "executingRequestContext": "user@x",
            "orchestrationEventPrefix": "vams.prod.execution.E1.pipeline.P1",
            "assetId": "xidM",
            "databaseId": "dbM",
            "bucketId": "bkt-01",
            "relativePath": "/models/pump.glb",
            "versionId": "v1",
            "workflowExecutionId": "E1",
        }

    def test_an_unversioned_file_forwards_an_empty_version(self):
        manifest = _manifest()
        manifest["inputFiles"][0]["versionId"] = ""
        mod, response = _run(_body(), manifest)
        assert response["statusCode"] == 200
        assert _payload(mod)["versionId"] == ""

    def test_a_manifest_without_a_bucket_id_still_starts_the_run(self):
        """A manifest built from an earlier workflow step's outputs carries no bucketId. The analysis
        and the metadata layers do not depend on it, so the run starts with an empty value and the
        embedding step decides what it can publish."""
        manifest = _manifest()
        del manifest["inputFiles"][0]["bucketId"]
        mod, response = _run(_body(), manifest)
        assert response["statusCode"] == 200
        mod.sfn_client.send_task_failure.assert_not_called()
        assert _payload(mod)["bucketId"] == ""


@pytest.mark.unit
class TestPreInvokeRejections:
    def test_missing_body_is_a_400_before_a_token_exists(self):
        mod = h.load_handler("vamsExecuteSystemGenAiMetadataPipeline")
        mod.sfn_client = MagicMock()
        mod.lambda_client = MagicMock()
        response = mod.lambda_handler({}, MagicMock())
        assert response["statusCode"] == 400
        mod.lambda_client.invoke.assert_not_called()
        # No token has been read yet, so there is nothing to report against.
        mod.sfn_client.send_task_failure.assert_not_called()

    def test_missing_task_token_is_a_500_without_a_callback(self):
        body = _body()
        del body["TaskToken"]
        mod, response = _run(body, _manifest())
        assert response["statusCode"] == 500
        mod.lambda_client.invoke.assert_not_called()
        mod.sfn_client.send_task_failure.assert_not_called()

    def test_an_unreadable_manifest_fails_the_token(self):
        mod, response = _run(_body(), None)
        assert response["statusCode"] == 500
        mod.lambda_client.invoke.assert_not_called()
        _assert_token_failed(mod)

    def test_a_multi_file_manifest_fails_the_token(self):
        manifest = _manifest()
        manifest["inputFiles"].append(dict(manifest["inputFiles"][0], key="xidM/models/second.glb",
                                           relativePath="/models/second.glb"))
        mod, response = _run(_body(), manifest)
        assert response["statusCode"] == 500
        mod.lambda_client.invoke.assert_not_called()
        _assert_token_failed(mod)

    def test_a_manifest_with_no_input_file_fails_the_token(self):
        mod, response = _run(_body(), _manifest(inputFiles=[]))
        assert response["statusCode"] == 500
        mod.lambda_client.invoke.assert_not_called()
        _assert_token_failed(mod)

    def test_a_manifest_without_a_results_prefix_fails_the_token(self):
        manifest = _manifest()
        manifest["outputs"]["results"] = ""
        mod, response = _run(_body(), manifest)
        assert response["statusCode"] == 500
        mod.lambda_client.invoke.assert_not_called()
        _assert_token_failed(mod)

    def test_a_folder_input_is_a_400_and_fails_the_token(self):
        manifest = _manifest()
        manifest["inputFiles"][0]["key"] = "xidM/models/"
        manifest["inputFiles"][0]["relativePath"] = "/models/"
        mod, response = _run(_body(), manifest)
        assert response["statusCode"] == 400
        mod.lambda_client.invoke.assert_not_called()
        _assert_token_failed(mod)


@pytest.mark.unit
class TestOpenPipelineFunctionError:
    def test_a_raised_open_pipeline_fails_the_task_token(self):
        mod, response = _run(_body(), _manifest(), {"StatusCode": 200, "FunctionError": "Unhandled"})
        mod.lambda_client.invoke.assert_called_once()
        assert response["statusCode"] == 500
        _assert_token_failed(mod)

    def test_a_handled_function_error_is_also_a_failure(self):
        mod, response = _run(_body(), _manifest(), {"StatusCode": 200, "FunctionError": "Handled"})
        assert response["statusCode"] == 500
        _assert_token_failed(mod)

    def test_a_non_200_status_is_a_failure(self):
        mod, response = _run(_body(), _manifest(), {"StatusCode": 202})
        assert response["statusCode"] == 500
        _assert_token_failed(mod)

    def test_a_clean_invoke_succeeds(self):
        mod, response = _run(_body(), _manifest(), {"StatusCode": 200})
        assert response["statusCode"] == 200
        mod.sfn_client.send_task_failure.assert_not_called()

    def test_execute_pipeline_raises_with_the_reported_error_type(self):
        mod = h.load_handler("vamsExecuteSystemGenAiMetadataPipeline")
        mod.lambda_client = MagicMock()
        mod.lambda_client.invoke = MagicMock(return_value={"StatusCode": 200, "FunctionError": "Unhandled"})
        resolved = mod.manifestHelper.resolve_inputs({}, _manifest())
        with pytest.raises(Exception) as raised:
            mod.execute_pipeline(resolved, h.TASK_TOKEN, "user@x", "user@x", "E1")
        assert "Unhandled" in str(raised.value)

    def test_the_guard_is_copied_verbatim_from_the_canonical_handler(self):
        """backendPipelines/CLAUDE.md asks for the FunctionError guard to be copied rather than
        re-worded so the copies stay comparable; the canonical copy is multi/modelOps."""
        def guard_lines(path):
            with io.open(path, encoding="utf-8") as handle:
                lines = [line.strip() for line in handle]
            start = lines.index("if lambda_response.get('FunctionError'):")
            return lines[start:start + 3]

        this_handler = os.path.join(h.LAMBDA_DIR, "vamsExecuteSystemGenAiMetadataPipeline.py")
        assert guard_lines(this_handler) == guard_lines(MODEL_OPS_VAMS_EXECUTE)
        assert guard_lines(this_handler) == [
            "if lambda_response.get('FunctionError'):",
            "raise Exception(",
            "\"Invoke Open Pipeline Lambda Failed: \" + str(lambda_response.get('FunctionError')))",
        ]
