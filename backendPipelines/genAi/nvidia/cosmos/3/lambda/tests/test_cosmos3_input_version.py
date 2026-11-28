#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Cosmos 3: the input file's launch-time S3 version travels from the manifest to the Batch container.

``vamsExecute`` forwards the manifest's ``inputVersionId``, ``openPipeline`` puts it on the state machine
input and ``constructPipeline`` on the container definition, which downloads the input at it. An older
payload without the field reads the current object. Reuses the fixtures of ``test_cosmos3_bucket_regions`` and
``test_manifest_refactor`` (same directory; the suite runs alone)."""

import json
import os
import sys

import pytest

_LAMBDA_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _LAMBDA_DIR not in sys.path:
    sys.path.insert(0, _LAMBDA_DIR)

import test_manifest_refactor as refactor  # noqa: E402
import test_cosmos3_bucket_regions as regions  # noqa: E402


@pytest.mark.unit
class TestVamsExecuteForwardsTheVersion:
    def _payload(self, version_id=None):
        manifest = refactor.TestVamsExecuteCosmos3Pipeline()._manifest()
        if version_id is not None:
            manifest["inputFiles"][0]["versionId"] = version_id
        return regions.TestVamsExecuteResolvesBucketRegions()._run(manifest)

    def test_the_manifest_version(self):
        assert self._payload("v-launch")["inputVersionId"] == "v-launch"

    @pytest.mark.parametrize("version_id", [None, ""])
    def test_no_version_to_send(self, version_id):
        assert self._payload(version_id)["inputVersionId"] == ""

    def test_the_null_version_is_sent(self):
        assert self._payload("null")["inputVersionId"] == "null"


@pytest.mark.unit
class TestOpenPipelinePutsItOnTheStateMachineInput:
    def test_carried(self):
        event = dict(refactor.TestOpenPipeline()._event(), inputVersionId="v-launch")
        assert regions.TestOpenPipelineForwardsBucketRegions()._run(event)["inputVersionId"] == "v-launch"

    def test_an_older_payload_reads_the_current_object(self):
        sfn_input = regions.TestOpenPipelineForwardsBucketRegions()._run(refactor.TestOpenPipeline()._event())
        assert sfn_input["inputVersionId"] == ""


@pytest.mark.unit
class TestConstructPipelinePutsItOnTheDefinition:
    def _definition(self, **overrides):
        construct = regions.TestConstructPipelineMapsBucketsToRegions()
        return construct._definition(construct._event(**overrides))

    def test_carried(self):
        assert self._definition(inputVersionId="v-launch")["inputVersionId"] == "v-launch"

    def test_an_older_payload_reads_the_current_object(self):
        assert self._definition()["inputVersionId"] == ""
        assert self._definition(inputVersionId=None)["inputVersionId"] == ""
