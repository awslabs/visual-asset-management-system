#  SPDX-License-Identifier: Apache-2.0

"""simulation/isaacLabTraining: a JSON input file's defaults are read at the run's launch version (#994).

The Isaac Lab container reads no input file; the one read of it is ``openPipeline``'s standing defaults
from a selected JSON file. ``vamsExecute`` puts ``inputVersionId`` from the manifest's first input file
in the state-machine input, and ``openPipeline`` reads that version. An event without it reads the
current object, and a version that no longer exists fails the run naming the file and version rather
than taking defaults from whatever the file holds now. Reuses the fixtures of
``test_isaaclab_bucket_regions`` and ``test_manifest_refactor`` (same directory; the suite runs alone)."""

import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

_LAMBDA_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _LAMBDA_DIR not in sys.path:
    sys.path.insert(0, _LAMBDA_DIR)

import test_isaaclab_bucket_regions as regions  # noqa: E402
import test_manifest_refactor as refactor  # noqa: E402

VERSION = "3HL4kqtJlcpXroDTDmJ.rmSpXd3dIbrHY"


@pytest.mark.unit
class TestVamsExecuteForwardsTheInputVersion:
    @pytest.mark.parametrize("version_id", [VERSION, "null"])
    def test_the_state_machine_input_carries_the_manifest_version(self, version_id):
        manifest = refactor.TestVamsExecute()._manifest()
        manifest["inputFiles"][0]["versionId"] = version_id
        assert regions.TestVamsExecuteResolvesBucketRegions()._run(manifest)["inputVersionId"] == version_id

    @pytest.mark.parametrize("version_id", ["", None])
    def test_no_version_sends_an_empty_value(self, version_id):
        manifest = refactor.TestVamsExecute()._manifest()
        manifest["inputFiles"][0]["versionId"] = version_id
        assert regions.TestVamsExecuteResolvesBucketRegions()._run(manifest)["inputVersionId"] == ""


@pytest.mark.unit
class TestOpenPipelineReadsTheJsonInputAtItsVersion:
    def _run(self, client, **overrides):
        mod = refactor.TestOpenPipeline()._load()
        mod._bucket_regions.clear()
        event = regions.TestOpenPipelineMapsBucketsToRegions()._event(
            inputS3AssetFilePath="s3://in-bkt/xidM/config.json", **overrides)
        with patch.object(mod, "s3_client", client), patch.object(mod, "abort_external_workflow", MagicMock()):
            return mod.lambda_handler(event, MagicMock())

    def _client(self, body=b'{"trainingConfig": {"maxIterations": 7}}'):
        client = MagicMock(name="default")
        client.get_object.return_value = {"Body": MagicMock(read=lambda: body)}
        return client

    def test_the_version_is_read(self):
        client = self._client()
        out = self._run(client, inputVersionId=VERSION)
        client.get_object.assert_called_once_with(Bucket="in-bkt", Key="xidM/config.json", VersionId=VERSION)
        assert json.loads(out["definition"])["trainingConfig"]["maxIterations"] == 7

    def test_an_event_without_one_reads_the_current_object(self):
        client = self._client()
        self._run(client)
        client.get_object.assert_called_once_with(Bucket="in-bkt", Key="xidM/config.json")

    @pytest.mark.parametrize("code", ["NoSuchVersion", "NoSuchKey", "404", "405", "MethodNotAllowed"])
    def test_a_deleted_version_fails_the_run_naming_it(self, code):
        client = MagicMock(name="default")
        client.get_object.side_effect = ClientError({"Error": {"Code": code}}, "GetObject")
        with pytest.raises(Exception) as excinfo:
            self._run(client, inputVersionId=VERSION)
        assert "xidM/config.json" in str(excinfo.value) and VERSION in str(excinfo.value)

    def test_another_read_failure_still_falls_back_to_no_defaults(self):
        client = MagicMock(name="default")
        client.get_object.side_effect = ClientError({"Error": {"Code": "403"}}, "GetObject")
        out = self._run(client, inputVersionId=VERSION)
        assert out["status"] == "STARTING"

    def test_a_non_json_input_is_not_read_at_all(self):
        client = self._client()
        mod = refactor.TestOpenPipeline()._load()
        assert mod.load_config_from_s3("s3://in-bkt/xidM/scene.usd", VERSION) == {}
        client.get_object.assert_not_called()
