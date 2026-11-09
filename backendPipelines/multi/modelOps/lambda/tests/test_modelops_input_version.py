#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""multi/modelOps: the ModelOps handler reads its input at the S3 version the run recorded at launch.

The ModelOps image reads Amazon S3 itself, by the bucket and key in the config's state block, and
cannot take a version. ``vamsExecute`` forwards the manifest's ``inputVersionId`` and ``openPipeline``
puts it on the state machine input. ``constructPipeline`` points the state block at the original
object when that version is still current (or there is no version), and otherwise copies that
version to ``pipelines/modelops.staged-inputs/<pipelineId>/<executionId>/<original key>`` and points
the state block at the copy, which ``pipelineEnd`` deletes when the run ends. The output placement
keeps reading the original key. Reuses the fixtures of ``test_manifest_refactor``
(same directory; the suite runs alone)."""

import json
import os
import shlex
import sys
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

_LAMBDA_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _LAMBDA_DIR not in sys.path:
    sys.path.insert(0, _LAMBDA_DIR)

import test_manifest_refactor as refactor  # noqa: E402


def _client_error(code, operation="HeadObject"):
    return ClientError({"Error": {"Code": code, "Message": code}}, operation)


@pytest.mark.unit
class TestVamsExecuteForwardsTheVersion:
    def _payload(self, version_id=None):
        base = refactor.TestVamsExecute()
        mod = base._load()
        manifest = base._manifest()
        if version_id is not None:
            manifest["inputFiles"][0]["versionId"] = version_id
        s3 = MagicMock()
        s3.get_object.return_value = {"Body": MagicMock(read=lambda: json.dumps(manifest).encode("utf-8"))}
        invoke = MagicMock(return_value={"StatusCode": 200})
        with patch.object(mod, "s3_client", s3), patch.object(mod.lambda_client, "invoke", invoke):
            resp = mod.lambda_handler({"body": json.dumps(base._body())}, MagicMock())
        assert resp["statusCode"] == 200, resp
        return json.loads(invoke.call_args.kwargs["Payload"].decode("utf-8"))

    def test_the_manifest_version(self):
        assert self._payload("v-launch")["inputVersionId"] == "v-launch"

    @pytest.mark.parametrize("version_id", [None, ""])
    def test_no_version_to_send(self, version_id):
        assert self._payload(version_id)["inputVersionId"] == ""

    def test_the_null_version_is_sent(self):
        assert self._payload("null")["inputVersionId"] == "null"


@pytest.mark.unit
class TestOpenPipelinePutsItOnTheStateMachineInput:
    def _sfn_input(self, event):
        base = refactor.TestOpenPipeline()
        mod = base._load()
        start = base._mock_start()
        with patch.object(mod.sfn, "start_execution", start), \
                patch.object(mod.events_client, "put_events", MagicMock()):
            mod.lambda_handler(event, MagicMock())
        return json.loads(start.call_args.kwargs["input"])

    def test_carried(self):
        event = refactor.TestOpenPipeline()._event()
        event["inputVersionId"] = "v-launch"
        assert self._sfn_input(event)["inputVersionId"] == "v-launch"

    def test_an_older_payload_reads_the_current_object(self):
        assert self._sfn_input(refactor.TestOpenPipeline()._event())["inputVersionId"] == ""


@pytest.mark.unit
class TestTheHandlerReadsTheLaunchVersion:
    def _s3(self, current_version="v-current", head_error=None, copy_error=None):
        s3 = MagicMock()
        s3.get_object.return_value = {"Body": MagicMock(read=lambda: json.dumps({"state": {}}).encode("utf-8"))}

        def head_object(**kwargs):
            if head_error is not None:
                raise head_error
            if "VersionId" in kwargs:
                return {"VersionId": kwargs["VersionId"]}
            return {"VersionId": current_version} if current_version else {}

        s3.head_object.side_effect = head_object
        if copy_error is not None:
            s3.copy.side_effect = copy_error
        return s3

    def _event(self, **extra):
        event = refactor.TestConstructPipelineReadsConfigFromS3()._event()
        event["inputOutputS3AssetAuxiliaryFilesPath"] = "s3://aux/pipelines/modelOps/E1/"
        event["assetId"] = "xidM"
        event.update(extra)
        return event

    def _handler_output(self, event, s3, sfn=None):
        mod = refactor.TestConstructPipelineReadsConfigFromS3()._load()
        with patch.object(mod, "s3", s3), patch.object(mod, "sfn", sfn or MagicMock()):
            return mod.lambda_handler(event, MagicMock())

    def _run(self, event, s3, sfn=None):
        out = self._handler_output(event, s3, sfn)
        config = json.loads(shlex.split(out["commands"][2].split("|", 1)[0])[-1])
        return config["state"], config["output"]

    def test_the_output_names_the_staged_copy_for_pipeline_end(self):
        out = self._handler_output(self._event(inputVersionId="v-launch"), self._s3(current_version="v-newer"))
        assert out["stagedInputS3Uri"] == "s3://aux/pipelines/modelops.staged-inputs/modelOps/E1/xidM/sub/model.glb"

    @pytest.mark.parametrize("version_id, current", [("", "v-current"), ("v-launch", "v-launch"), ("v-launch", "")])
    def test_a_run_that_reads_the_original_names_no_copy(self, version_id, current):
        out = self._handler_output(self._event(inputVersionId=version_id), self._s3(current_version=current))
        assert out["stagedInputS3Uri"] == ""

    def test_no_version_makes_no_extra_call(self):
        s3 = self._s3()
        state, _ = self._run(self._event(), s3)
        assert (state["bucket"], state["prefix"], state["name"]) == ("abkt", "xidM/sub", "model")
        s3.head_object.assert_not_called()
        s3.copy.assert_not_called()

    def test_the_current_version_reads_the_original_object(self):
        s3 = self._s3(current_version="v-launch")
        state, _ = self._run(self._event(inputVersionId="v-launch"), s3)
        assert (state["bucket"], state["prefix"]) == ("abkt", "xidM/sub")
        s3.copy.assert_not_called()

    def test_an_unversioned_bucket_reads_the_original_object(self):
        s3 = self._s3(current_version="")
        state, _ = self._run(self._event(inputVersionId="v-launch"), s3)
        assert (state["bucket"], state["prefix"]) == ("abkt", "xidM/sub")
        s3.copy.assert_not_called()

    def test_an_older_version_is_staged_and_read_from_the_copy(self):
        s3 = self._s3(current_version="v-newer")
        state, output = self._run(self._event(inputVersionId="v-launch"), s3)
        s3.copy.assert_called_once_with({"Bucket": "abkt", "Key": "xidM/sub/model.glb", "VersionId": "v-launch"},
                                        "aux", "pipelines/modelops.staged-inputs/modelOps/E1/xidM/sub/model.glb")
        assert (state["bucket"], state["prefix"], state["name"], state["extension"]) == (
            "aux", "pipelines/modelops.staged-inputs/modelOps/E1/xidM/sub", "model", "glb")
        # The output is still placed from the original key: beside its source in the asset.
        assert output["bucket"] == "abkt"
        assert output["prefix"] == "pipelines/p1/MJOB/output/E1/files/sub/optimized"

    @pytest.mark.parametrize("code", ["NoSuchVersion", "NoSuchKey", "404", "405"])
    def test_a_deleted_version_fails_the_run_naming_it(self, code):
        mod = refactor.TestConstructPipelineReadsConfigFromS3()._load()
        sfn = MagicMock()
        s3 = self._s3(head_error=_client_error(code))
        with patch.object(mod, "s3", s3), patch.object(mod, "sfn", sfn), \
                pytest.raises(mod.InputVersionUnavailableError) as raised:
            mod.lambda_handler(self._event(inputVersionId="v-gone"), MagicMock())
        assert "xidM/sub/model.glb" in str(raised.value) and "v-gone" in str(raised.value)
        assert sfn.send_task_failure.call_args.kwargs["taskToken"] == "tok"
        s3.copy.assert_not_called()

    def test_a_version_deleted_during_the_copy_fails_the_run(self):
        mod = refactor.TestConstructPipelineReadsConfigFromS3()._load()
        s3 = self._s3(current_version="v-newer", copy_error=_client_error("NoSuchVersion", "CopyObject"))
        with patch.object(mod, "s3", s3), patch.object(mod, "sfn", MagicMock()), \
                pytest.raises(mod.InputVersionUnavailableError):
            mod.lambda_handler(self._event(inputVersionId="v-gone"), MagicMock())

    @pytest.mark.parametrize("code", ["AccessDenied", "400", "InvalidArgument", "AuthorizationHeaderMalformed"])
    def test_another_error_is_not_reported_as_a_deleted_version(self, code):
        mod = refactor.TestConstructPipelineReadsConfigFromS3()._load()
        s3 = self._s3(head_error=_client_error(code))
        with patch.object(mod, "s3", s3), patch.object(mod, "sfn", MagicMock()), \
                pytest.raises(ClientError):
            mod.lambda_handler(self._event(inputVersionId="v-launch"), MagicMock())

    def test_staged_key_layout(self):
        mod = refactor.TestConstructPipelineReadsConfigFromS3()._load()
        assert mod.staged_input_key("pipelines/m/E1/", "xidM/a/b.glb") == (
            "pipelines/modelops.staged-inputs/m/E1/xidM/a/b.glb")
        assert mod.staged_input_key("", "xidM/b.glb") == "pipelines/modelops.staged-inputs/xidM/b.glb"
        # Every copy lands under the prefix pipelineEnd deletes from and the lifecycle rule expires.
        assert mod.staged_input_key("other/E2", "/xidM/b.glb").startswith(mod.STAGED_INPUT_PREFIX)
