#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""multi/rapidPipelineEKS: the Kubernetes job reads its input at the S3 version the run recorded at launch.

``vamsExecute`` forwards the manifest's ``inputVersionId`` (never a caller's body value),
``openPipeline`` puts it on the state machine input, the state machine maps it into the
CONSTRUCT_PIPELINE payload, and ``consolidated_handler`` builds the job command's input fetch from it:
``aws s3api get-object --version-id`` into the file name ``aws s3 cp <object> .`` would have chosen,
or today's exact ``aws s3 cp`` when there is no version. Reuses the fixtures of
``test_manifest_refactor`` and ``test_command_quoting`` (same directory; the suite runs alone)."""

import json
import os
import shlex
import sys
from unittest.mock import MagicMock, patch

import pytest

_LAMBDA_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _LAMBDA_DIR not in sys.path:
    sys.path.insert(0, _LAMBDA_DIR)

import test_command_quoting as quoting  # noqa: E402
import test_manifest_refactor as refactor  # noqa: E402


@pytest.mark.unit
class TestVamsExecuteForwardsTheVersion:
    def _payload(self, version_id=None, body_version=None, manifest=True):
        base = refactor.TestVamsExecute()
        mod = base._load()
        body = base._body()
        s3 = MagicMock()
        if manifest:
            m = base._manifest()
            if version_id is not None:
                m["inputFiles"][0]["versionId"] = version_id
            s3.get_object.return_value = {"Body": MagicMock(read=lambda: json.dumps(m).encode("utf-8"))}
        else:
            body.pop("inputManifestS3Location")
            s3.get_object.side_effect = Exception("no manifest")
        if body_version is not None:
            body["inputVersionId"] = body_version
        invoke = MagicMock(return_value={"StatusCode": 200, "Payload": MagicMock(read=lambda: b"")})
        with patch.object(mod, "s3_client", s3), patch.object(mod.lambda_client, "invoke", invoke):
            resp = mod.lambda_handler({"body": json.dumps(body)}, quoting._ctx())
        assert resp["statusCode"] == 200, resp
        return json.loads(invoke.call_args.kwargs["Payload"].decode("utf-8"))

    def test_the_manifest_version(self):
        assert self._payload("v-launch")["inputVersionId"] == "v-launch"

    @pytest.mark.parametrize("version_id", [None, ""])
    def test_no_version_to_send(self, version_id):
        assert self._payload(version_id)["inputVersionId"] == ""

    def test_the_null_version_is_sent(self):
        assert self._payload("null")["inputVersionId"] == "null"

    def test_the_version_comes_from_the_manifest_only(self):
        assert self._payload("v-launch", body_version="v-other")["inputVersionId"] == "v-launch"
        assert self._payload(manifest=False, body_version="v-other")["inputVersionId"] == ""


@pytest.mark.unit
class TestOpenPipelinePutsItOnTheStateMachineInput:
    def _sfn_input(self, event):
        base = refactor.TestOpenPipeline()
        mod = base._load()
        start = base._mock_start()
        with patch.object(mod.sfn, "start_execution", start), \
                patch.object(mod.events_client, "put_events", MagicMock()):
            mod.lambda_handler(event, quoting._ctx())
        return json.loads(start.call_args.kwargs["input"])

    def test_carried(self):
        event = refactor.TestOpenPipeline()._event()
        event["inputVersionId"] = "v-launch"
        assert self._sfn_input(event)["inputVersionId"] == "v-launch"

    def test_an_older_payload_reads_the_current_object(self):
        assert self._sfn_input(refactor.TestOpenPipeline()._event())["inputVersionId"] == ""


@pytest.mark.unit
class TestTheInputFetch:
    _run = quoting.TestCommandQuoting._run

    def test_without_a_version_the_command_is_unchanged(self):
        subdir = quoting._load().SAME_FORMAT_OUTPUT_SUBDIR
        expected = ("aws s3 cp s3://abkt/xidM/test/pump.glb . && /rpdx/rpdx -i pump.glb -c -e pump.glb "
                    f"&& aws s3 cp pump.glb s3://abkt/pipelines/p1/MJOB/output/E1/files/{subdir}/pump.glb")
        assert self._run(quoting._event(key="xidM/test/pump.glb")) == expected
        event = quoting._event(key="xidM/test/pump.glb")
        event["inputVersionId"] = ""
        assert self._run(event) == expected

    @pytest.mark.parametrize("output_file_type", [".glb", ".all"])
    def test_with_a_version_the_input_is_read_at_it(self, output_file_type):
        event = quoting._event(output_file_type=output_file_type, key="xidM/test/pump.glb")
        event["inputVersionId"] = "v-launch"
        command = self._run(event)
        fetch = command.split(" && ", 1)[0]
        assert shlex.split(fetch) == ["aws", "s3api", "get-object", "--bucket", "abkt", "--key",
                                      "xidM/test/pump.glb", "--version-id", "v-launch", "pump.glb",
                                      ">", "/dev/null"]
        assert "aws s3 cp s3://abkt/xidM/test/pump.glb" not in command
        assert "/rpdx/rpdx -i pump.glb" in command

    def test_a_versioned_fetch_quotes_metacharacters(self):
        event = quoting._event()
        event["inputVersionId"] = "v;rm -rf /"
        command = self._run(event)
        shell_text = "".join(quoting._unquoted_segments(command))
        assert "touch" not in shell_text and "`id`" not in shell_text and "rm -rf" not in shell_text


@pytest.mark.unit
def test_the_state_machine_maps_the_version_into_construct_pipeline():
    root = _LAMBDA_DIR
    while not os.path.isdir(os.path.join(root, "infra", "lib")):
        root = os.path.dirname(root)
    construct = os.path.join(root, "infra", "lib", "nestedStacks", "pipelines", "multi",
                             "rapidPipelineEKS", "constructs", "rapidPipelineEKS-construct.ts")
    with open(construct, "r", encoding="utf-8") as f:
        source = f.read()
    block = source[source.index('operation: "CONSTRUCT_PIPELINE"'):]
    block = block[:block.index("}),")]
    assert 'inputVersionId: sfn.JsonPath.stringAt("$.inputVersionId")' in block
