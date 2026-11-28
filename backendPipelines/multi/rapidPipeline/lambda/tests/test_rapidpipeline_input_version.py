#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""multi/rapidPipeline: the container reads its input at the S3 version the run recorded at launch.

``vamsExecute`` forwards the manifest's ``inputVersionId``, ``openPipeline`` puts it on the state
machine input, and ``constructPipeline`` builds the container command's input fetch from it: ``aws
s3api get-object --version-id`` into the file name ``aws s3 cp <object> .`` would have chosen, or
today's exact ``aws s3 cp`` when there is no version. Reuses the fixtures of
``test_manifest_refactor`` (same directory; the suite runs alone)."""

import json
import os
import shlex
import sys
from unittest.mock import MagicMock, patch

import pytest

_LAMBDA_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _LAMBDA_DIR not in sys.path:
    sys.path.insert(0, _LAMBDA_DIR)

import test_manifest_refactor as refactor  # noqa: E402


def _manifest_s3(manifest):
    s3 = MagicMock()
    s3.get_object.return_value = {"Body": MagicMock(read=lambda: json.dumps(manifest).encode("utf-8"))}
    return s3


@pytest.mark.unit
class TestVamsExecuteForwardsTheVersion:
    def _payload(self, version_id=None):
        base = refactor.TestVamsExecute()
        mod = base._load()
        manifest = base._manifest()
        if version_id is not None:
            manifest["inputFiles"][0]["versionId"] = version_id
        invoke = MagicMock(return_value={"StatusCode": 200, "Payload": MagicMock(read=lambda: b"")})
        with patch.object(mod, "s3_client", _manifest_s3(manifest)), \
                patch.object(mod.lambda_client, "invoke", invoke):
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
class TestTheInputFetch:
    def _command(self, event, config=None):
        base = refactor.TestConstructPipelineReadsConfigFromS3()
        mod = base._load()
        s3 = MagicMock()
        if config is None:
            s3.get_object.side_effect = Exception("missing")
        else:
            s3.get_object.return_value = {"Body": MagicMock(read=lambda: json.dumps(config).encode("utf-8"))}
        with patch.object(mod, "s3", s3):
            return mod.lambda_handler(event, MagicMock())["commands"][2]

    def _event(self, **extra):
        event = refactor.TestConstructPipelineReadsConfigFromS3()._event()
        event["inputS3AssetFilePath"] = "s3://abkt/xidM/parts/model.obj"
        event["outputFileType"] = ".glb"
        event.update(extra)
        return event

    def test_without_a_version_the_command_is_unchanged(self):
        expected = ("aws s3 cp s3://abkt/xidM/parts/model.obj . && /rpdx/rpdx -i model.obj -c -e model.glb "
                    "&& aws s3 cp model.glb s3://abkt/pipelines/p1/MJOB/output/E1/files/")
        assert self._command(self._event()) == expected
        assert self._command(self._event(inputVersionId="")) == expected

    def test_with_a_version_the_input_is_read_at_it(self):
        command = self._command(self._event(inputVersionId="v-launch"))
        fetch = command.split(" && ", 1)[0]
        assert shlex.split(fetch) == ["aws", "s3api", "get-object", "--bucket", "abkt", "--key",
                                      "xidM/parts/model.obj", "--version-id", "v-launch", "model.obj",
                                      ">", "/dev/null"]
        assert "aws s3 cp s3://abkt/xidM/parts/model.obj" not in command
        # rpdx reads the file the fetch wrote.
        assert "/rpdx/rpdx -i model.obj" in command

    def test_with_a_config_only_the_input_fetch_changes(self):
        command = self._command(self._event(inputVersionId="v-launch"), config={"settings": {}})
        assert command.startswith("aws s3 cp s3://aux/auxbkt/rapidPipeline/rp_config_PipelineJob_x.json rp_config.json && "
                                  "aws s3api get-object --bucket abkt --key xidM/parts/model.obj --version-id v-launch ")

    def test_every_value_is_quoted(self):
        mod = refactor.TestConstructPipelineReadsConfigFromS3()._load()
        fetch = mod.input_fetch_command("abkt", "xidM/a;touch$IFS/x;`id`.obj", "a;touch$IFS/x;`id`.obj",
                                        "v;rm -rf /")
        shell_text = "".join(fetch.split("'")[::2])
        assert "touch" not in shell_text and "`id`" not in shell_text and "rm -rf" not in shell_text
        assert shlex.split(fetch)[:10] == ["aws", "s3api", "get-object", "--bucket", "abkt", "--key",
                                           "xidM/a;touch$IFS/x;`id`.obj", "--version-id", "v;rm -rf /",
                                           "a;touch$IFS/x;`id`.obj"]
