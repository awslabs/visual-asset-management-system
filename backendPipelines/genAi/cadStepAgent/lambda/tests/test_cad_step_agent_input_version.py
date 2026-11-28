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
# The definition origin/development's constructPipeline writes for the no-version test event
DEVELOPMENT_DEFINITION = (
    '{"jobName": "CadStepAgent_20260922_185500_123_ab12cd34", "stageType": "CAD_STEP_AGENT", "m'
    'ode": "modify", "inputFile": {"bucketName": "in-bkt", "objectKey": "xasset1/sub/dir/part.s'
    'tp", "fileExtension": ".stp"}, "outputFiles": {"bucketName": "out-bkt", "objectDir": "xass'
    'et1/", "relativeSubdir": "sub/dir/", "fileName": "part.stp"}, "outputMetadata": {"bucketNa'
    'me": "out-bkt", "objectDir": "xasset1/metadata/"}, "outputResults": {"bucketName": "out-bk'
    't", "objectDir": "p/cad/J/output/E1/results/"}, "auxiliary": {"bucketName": "aux", "object'
    'Dir": "xasset1/pipeline/"}, "bucketRegions": {}, "assetId": "xasset1", "databaseId": "db1"'
    ', "agent": {"prompt": "Add four M3 holes", "allowInternetResearch": true, "modelProvider":'
    ' "bedrock", "modelId": "", "maxAttempts": 4, "maxRunSeconds": 3600, "scriptTimeoutSeconds"'
    ': 300, "writeAgentMetadata": false}}'
)


@pytest.mark.unit
class TestVamsExecuteForwardsTheInputVersion:
    @pytest.mark.parametrize("version_id", [VERSION, "null"])
    def test_the_payload_carries_the_manifest_version(self, version_id):
        manifest = regions._manifest()
        manifest["inputFiles"][0]["versionId"] = version_id
        assert regions.TestVamsExecuteForwardsBucketRegions()._run(manifest)["inputVersionId"] == version_id

    @pytest.mark.parametrize("version_id", ["", None])
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

    def test_an_event_without_one_writes_no_version_field(self):
        definition = regions.TestConstructPipelineMapsBucketsToRegions()._definition(regions._event())
        assert "versionId" not in definition["inputFile"]

    def test_without_a_version_the_definition_is_the_one_development_writes(self):
        # Captured from origin/development's constructPipeline for the same event. An image built before
        # the version field parses this definition during an upgrade, so a no-version run must not change it.
        definition = regions.TestConstructPipelineMapsBucketsToRegions()._definition(regions._event())
        assert json.dumps(definition) == DEVELOPMENT_DEFINITION

    def test_a_generate_run_still_has_no_input_file(self):
        definition = regions.TestConstructPipelineMapsBucketsToRegions()._definition(
            regions._event(with_input=False, inputVersionId=VERSION), mode="generate")
        assert definition["inputFile"] is None
