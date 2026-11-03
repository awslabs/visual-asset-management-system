#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""genAi/metadata3dLabeling: the input's launch version travels from the manifest to the Batch container (#994).

``vamsExecute`` resolves ``inputVersionId`` from the manifest's first input file and passes it to
``openPipeline``, which forwards it in the state-machine input; ``constructPipeline`` puts it on the
Blender render stage's ``inputFile.versionId`` (the metadata generation stage reads the renders from
the auxiliary bucket and carries none). An event without it reads the current object, as before. Reuses the
fixtures of ``test_manifest_refactor`` and ``test_m3dl_bucket_regions`` (same directory; the suite
runs alone)."""

import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

_LAMBDA_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _LAMBDA_DIR not in sys.path:
    sys.path.insert(0, _LAMBDA_DIR)

import test_manifest_refactor as refactor  # noqa: E402
import test_m3dl_bucket_regions as regions  # noqa: E402

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

    def test_the_payload_carries_the_manifest_version(self):
        manifest = refactor.TestVamsExecute()._manifest()
        manifest["inputFiles"][0]["versionId"] = VERSION
        assert self._run(manifest)["inputVersionId"] == VERSION

    @pytest.mark.parametrize("version_id", ["", "null", None])
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


@pytest.mark.unit
class TestConstructPipelinePutsTheVersionOnTheStage:
    def _definition(self, **overrides):
        construct = regions.TestConstructPipelineMapsBucketsToRegions()
        return construct._definition(construct._event(**overrides))

    def test_the_stage_input_carries_the_version(self):
        assert self._definition(inputVersionId=VERSION)["stages"][0]["inputFile"]["versionId"] == VERSION

    def test_an_event_without_one_carries_an_empty_version(self):
        assert self._definition()["stages"][0]["inputFile"]["versionId"] == ""

    def test_the_render_reading_stage_carries_no_version(self):
        stages = self._definition(inputVersionId=VERSION)["stages"]
        assert all("versionId" not in stage["inputFile"] for stage in stages[1:])
