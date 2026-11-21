#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""conversion/cogConvert: the input's launch version travels from the manifest to the Batch container (#994).

``vamsExecute`` resolves ``inputVersionId`` from the manifest's first input file and passes it to
``openPipeline``, which forwards it in the state-machine input; ``constructPipeline`` names it in a
second Batch command argument beside the definition, which stays free of it, and
``executeBatchJob`` submits both. An event without it reads the current object, as before. Reuses the
fixtures of ``test_cog_manifest_refactor`` and ``test_cog_bucket_regions`` (same directory; the suite
runs alone)."""

import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

_LAMBDA_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _LAMBDA_DIR not in sys.path:
    sys.path.insert(0, _LAMBDA_DIR)

import test_cog_manifest_refactor as refactor  # noqa: E402
import test_cog_bucket_regions as regions  # noqa: E402
import test_cog_batch_job_registration as registration  # noqa: E402

VERSION = "3HL4kqtJlcpXroDTDmJ.rmSpXd3dIbrHY"


@pytest.mark.unit
class TestVamsExecuteForwardsTheInputVersion:
    def _run(self, manifest):
        base = refactor.TestVamsExecute()
        mod = base._load()
        invoke = MagicMock(return_value={"StatusCode": 200})
        with patch.object(mod, "s3_client", regions._manifest_s3(manifest)), \
                patch.object(mod.lambda_client, "invoke", invoke):
            resp = mod.lambda_handler({"body": json.dumps(base._body())}, MagicMock())
        assert resp["statusCode"] == 200
        return json.loads(invoke.call_args.kwargs["Payload"].decode("utf-8"))

    @pytest.mark.parametrize("version_id", [VERSION, "null"])
    def test_the_payload_carries_the_manifest_version(self, version_id):
        manifest = refactor.TestVamsExecute()._manifest()
        manifest["inputFiles"][0]["versionId"] = version_id
        assert self._run(manifest)["inputVersionId"] == version_id

    @pytest.mark.parametrize("version_id", ["", None])
    def test_no_version_sends_an_empty_value(self, version_id):
        manifest = refactor.TestVamsExecute()._manifest()
        manifest["inputFiles"][0]["versionId"] = version_id
        assert self._run(manifest)["inputVersionId"] == ""


@pytest.mark.unit
class TestOpenPipelineForwardsTheInputVersion:
    def _run(self, event):
        return regions.TestOpenPipelineForwardsBucketRegions()._run(event)

    def test_the_state_machine_input_carries_the_version(self):
        event = dict(refactor.TestOpenPipeline()._event(), inputVersionId=VERSION)
        assert self._run(event)["inputVersionId"] == VERSION

    def test_an_event_from_an_older_lambda_forwards_an_empty_version(self):
        assert self._run(refactor.TestOpenPipeline()._event())["inputVersionId"] == ""


def _command(**overrides):
    construct = regions.TestConstructPipelineMapsBucketsToRegions()
    return construct._command(construct._event(**overrides))


@pytest.mark.unit
class TestConstructPipelinePutsTheVersionBesideTheDefinition:
    @pytest.mark.parametrize("version_id", [VERSION, "null"])
    def test_the_second_argument_names_the_stage_version(self, version_id):
        command = _command(inputVersionId=version_id)
        assert len(command) == 2
        assert json.loads(command[1]) == {"inputVersions": {"COG_CONVERT": version_id}}

    def test_an_event_without_one_writes_only_the_definition(self):
        assert len(_command()) == 1

    def test_the_batch_submit_sends_every_argument(self):
        # executeBatchJob submits the list constructPipeline returned as the container command.
        command = _command(inputVersionId=VERSION)
        mod = registration._load_execute_batch_job()
        mod.lambda_handler({"jobName": "j", "definition": command, "taskToken": "tok"}, MagicMock())
        assert mod.batch.submit_job.call_args.kwargs["containerOverrides"]["command"] == command
