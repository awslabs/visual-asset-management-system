#  SPDX-License-Identifier: Apache-2.0

"""GenAI CAD STEP agent: the input's launch version travels from the manifest to the agent container (#994).

``vamsExecute`` resolves ``inputVersionId`` from the manifest's first input file and passes it to
``openPipeline``, which forwards it in the state-machine input; ``constructPipeline`` puts it on the
definition's ``inputFile.versionId``, and both run tasks hand that definition to the container
unchanged. An event without it reads the current object. Reuses the fixtures of
``test_cad_step_agent_bucket_regions`` and ``test_cad_step_agent_handlers`` (same directory; the suite
runs alone)."""

import json
import os
import sys

import pytest

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _TESTS_DIR not in sys.path:
    sys.path.insert(0, _TESTS_DIR)

import test_cad_step_agent_bucket_regions as regions  # noqa: E402
import test_cad_step_agent_handlers as handlers  # noqa: E402

VERSION = "3HL4kqtJlcpXroDTDmJ.rmSpXd3dIbrHY"


@pytest.mark.unit
class TestVamsExecuteForwardsTheInputVersion:
    def test_the_payload_carries_the_manifest_version(self):
        manifest = regions._manifest()
        manifest["inputFiles"][0]["versionId"] = VERSION
        assert regions.TestVamsExecuteForwardsBucketRegions()._run(manifest)["inputVersionId"] == VERSION

    @pytest.mark.parametrize("version_id", ["", "null", None])
    def test_no_version_sends_an_empty_value(self, version_id):
        manifest = regions._manifest()
        manifest["inputFiles"][0]["versionId"] = version_id
        assert regions.TestVamsExecuteForwardsBucketRegions()._run(manifest)["inputVersionId"] == ""

    def test_a_generate_run_has_no_input_version(self):
        payload = regions.TestVamsExecuteForwardsBucketRegions()._run(regions._manifest(with_input=False))
        assert payload["inputVersionId"] == ""


@pytest.mark.unit
class TestOpenPipelineForwardsTheInputVersion:
    def test_the_state_machine_input_carries_the_version(self):
        event = dict(handlers._open_event("s3://in-bkt/xasset1/part.stp"), inputVersionId=VERSION)
        assert regions.TestOpenPipelineForwardsBucketRegions()._sfn_input(event)["inputVersionId"] == VERSION

    def test_an_event_from_an_older_lambda_forwards_an_empty_version(self):
        sfn_input = regions.TestOpenPipelineForwardsBucketRegions()._sfn_input(
            handlers._open_event("s3://in-bkt/xasset1/part.stp"))
        assert sfn_input["inputVersionId"] == ""


@pytest.mark.unit
class TestConstructPipelinePutsTheVersionOnTheInput:
    def test_the_input_file_carries_the_version(self):
        definition = regions.TestConstructPipelineMapsBucketsToRegions()._definition(
            regions._event(inputVersionId=VERSION))
        assert definition["inputFile"]["versionId"] == VERSION

    def test_an_event_without_one_carries_an_empty_version(self):
        definition = regions.TestConstructPipelineMapsBucketsToRegions()._definition(regions._event())
        assert definition["inputFile"]["versionId"] == ""

    def test_a_generate_run_still_has_no_input_file(self):
        definition = regions.TestConstructPipelineMapsBucketsToRegions()._definition(
            regions._event(with_input=False, inputVersionId=VERSION), mode="generate")
        assert definition["inputFile"] is None
