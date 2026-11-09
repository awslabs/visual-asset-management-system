#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""genAi/imageTo3d: every view travels from the manifest to the Batch container with its launch version (#994).

``vamsExecute`` sends each view as ONE object, ``{"bucket", "key", "versionId"}`` in ``inputViews``, built
from one manifest entry, so a view's object and its version cannot fall out of step. ``openPipeline`` names
the list on the state-machine input (this hop enumerates its fields, so a field it does not name is
dropped) and refuses a malformed view; ``constructPipeline`` reads ``inputViews`` as the only source of the
definition's ``inputFiles`` and of the input bucket Regions, and fails the run on a malformed view. For an
event from a development build before the view list, which carries ``inputS3AssetFilePaths``,
``constructPipeline`` reads every path equal to the primary at ``inputVersionId`` and every other path at the
current object. Reuses the fixtures of ``test_imageto3d_pipeline`` (same directory; the suite runs alone)."""

import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_imageto3d_pipeline import (  # noqa: E402
    TestConstructPipeline, TestOpenPipeline, TestVamsExecute, _s3_reading)

VERSION = "3HL4kqtJlcpXroDTDmJ.rmSpXd3dIbrHY"
BACK_VERSION = "Lm9vYmFyLmJhY2sudmVyc2lvbg"
MUG = "xidM/photos/mug.png"
MUG_BACK = "xidM/photos/mug_back.png"
# The definition origin/development's constructPipeline writes for the no-version test event
DEVELOPMENT_DEFINITION = (
    '{"jobName": "ImageTo3dJob_20260101_000000_abc123", "model": "trellis2", "assetId": "xidM",'
    ' "databaseId": "dbM", "inputFile": {"bucketName": "abkt", "objectKey": "xidM/photos/mug.pn'
    'g", "fileExtension": ".png"}, "inputFiles": [{"bucketName": "abkt", "objectKey": "xidM/pho'
    'tos/mug.png", "fileExtension": ".png"}], "outputFiles": {"bucketName": "abkt", "objectDir"'
    ': "pipelines/imageTo3d/JOB/output/E1/files/"}, "outputMetadata": {"bucketName": "abkt", "o'
    'bjectDir": "pipelines/imageTo3d/JOB/output/E1/metadata/"}, "outputResults": {"bucketName":'
    ' "", "objectDir": ""}, "temporaryFiles": {"bucketName": "aux", "objectDir": "pipelines/ima'
    'geTo3d/E1/"}, "inputMetadataS3Location": "s3://abkt/.../metadata.json", "inputConfiguratio'
    'nS3Location": "s3://abkt/.../config.json", "bucketRegions": {}}'
)
# The definition origin/development (edbfcf87c) writes for a two-view event from its own openPipeline:
# inputS3AssetFilePaths, no version anywhere, the input bucket in another Region.
DEVELOPMENT_LEGACY_TWO_VIEW_DEFINITION = (
    '{"jobName": "ImageTo3dJob_20260101_000000_abc123", "model": "trellis2", "assetId": "xidM",'
    ' "databaseId": "dbM", "inputFile": {"bucketName": "abkt", "objectKey": "xidM/photos/mug.pn'
    'g", "fileExtension": ".png"}, "inputFiles": [{"bucketName": "abkt", "objectKey": "xidM/pho'
    'tos/mug.png", "fileExtension": ".png"}, {"bucketName": "abkt", "objectKey": "xidM/photos/m'
    'ug_back.png", "fileExtension": ".png"}], "outputFiles": {"bucketName": "abkt", "objectDir"'
    ': "pipelines/imageTo3d/JOB/output/E1/files/"}, "outputMetadata": {"bucketName": "abkt", "o'
    'bjectDir": "pipelines/imageTo3d/JOB/output/E1/metadata/"}, "outputResults": {"bucketName":'
    ' "", "objectDir": ""}, "temporaryFiles": {"bucketName": "aux", "objectDir": "pipelines/ima'
    'geTo3d/E1/"}, "inputMetadataS3Location": "s3://abkt/.../metadata.json", "inputConfiguratio'
    'nS3Location": "s3://abkt/.../config.json", "bucketRegions": {"abkt": "eu-west-1"}}'
)


def _view(key, version_id, bucket="abkt"):
    return {"bucket": bucket, "key": key, "versionId": version_id}


def _views_manifest(*views):
    """A manifest whose input files are the given (key, versionId) pairs of the one asset, in order."""
    manifest = TestVamsExecute()._manifest()
    first = manifest["inputFiles"][0]
    manifest["inputFiles"] = [dict(first, key=key, relativePath="/" + key.split("/", 1)[1], versionId=version_id)
                              for key, version_id in views]
    return manifest


def _vams_execute_refused(manifest, config):
    """Run vamsExecute expecting a refusal: nothing invoked, the waiting task failed; returns the message."""
    vams_execute = TestVamsExecute()
    mod = vams_execute._load()
    invoke, send_failure = MagicMock(), MagicMock()
    with patch.object(mod, "s3_client", _s3_reading(manifest, config)), \
            patch.object(mod.lambda_client, "invoke", invoke), \
            patch.object(mod.sfn_client, "send_task_failure", send_failure):
        resp = mod.lambda_handler({"body": json.dumps(vams_execute._body())}, MagicMock())
    assert resp["statusCode"] == 500
    invoke.assert_not_called()
    send_failure.assert_called_once()
    return json.loads(resp["body"])["message"]


@pytest.mark.unit
class TestVamsExecuteSendsEachViewAsOneObject:
    def test_each_view_carries_its_object_and_version_together(self):
        _mod, resp, payload = TestVamsExecute()._run(
            manifest=_views_manifest((MUG, VERSION), (MUG_BACK, BACK_VERSION)), config={"MODEL": "hunyuan3d"})
        assert resp["statusCode"] == 200
        assert payload["inputViews"] == [_view(MUG, VERSION), _view(MUG_BACK, BACK_VERSION)]
        assert payload["inputVersionId"] == VERSION
        assert "inputVersionIds" not in payload

    def test_the_same_photo_at_two_versions_is_two_views_with_their_own_versions(self):
        _mod, resp, payload = TestVamsExecute()._run(
            manifest=_views_manifest((MUG, VERSION), (MUG, BACK_VERSION)), config={"MODEL": "hunyuan3d"})
        assert resp["statusCode"] == 200
        assert payload["inputViews"] == [_view(MUG, VERSION), _view(MUG, BACK_VERSION)]

    def test_the_path_list_is_derived_from_the_views(self):
        """Kept for a development build's openPipeline and constructPipeline, which read only this list."""
        _mod, _resp, payload = TestVamsExecute()._run(
            manifest=_views_manifest((MUG, VERSION), (MUG_BACK, BACK_VERSION)), config={"MODEL": "hunyuan3d"})
        assert payload["inputS3AssetFilePaths"] == [f"s3://abkt/{MUG}", f"s3://abkt/{MUG_BACK}"]

    @pytest.mark.parametrize("version_id", ["", "null"])
    def test_an_empty_or_null_version_is_sent_as_it_is(self, version_id):
        _mod, _resp, payload = TestVamsExecute()._run(manifest=_views_manifest((MUG, version_id)))
        assert payload["inputVersionId"] == version_id
        assert payload["inputViews"] == [_view(MUG, version_id)]

    @pytest.mark.parametrize("broken", [{"key": ""}, {"bucket": ""}, {"key": None}])
    def test_a_manifest_entry_naming_no_object_fails_the_run(self, broken):
        """Dropping it would reconstruct from fewer views than were selected, and succeed."""
        manifest = _views_manifest((MUG, VERSION), (MUG_BACK, BACK_VERSION))
        manifest["inputFiles"][1].update(broken)
        message = _vams_execute_refused(manifest, {"MODEL": "hunyuan3d"})
        assert "Input file 2 of 2" in message


@pytest.mark.unit
class TestOpenPipelineForwardsAndGatesTheViews:
    def _event(self, views=None, paths=None):
        event = TestOpenPipeline()._event()
        event["inputVersionId"] = VERSION
        if views is not None:
            event["inputViews"] = views
        if paths is not None:
            event["inputS3AssetFilePaths"] = paths
        return event

    def test_the_state_machine_input_carries_the_view_list(self):
        views = [_view(MUG, VERSION), _view(MUG_BACK, BACK_VERSION)]
        _mod, resp, start, _fail = TestOpenPipeline()._run(self._event(views=views))
        assert resp["statusCode"] == 200
        payload = json.loads(start.call_args.kwargs["input"])
        assert payload["inputViews"] == views
        assert payload["inputVersionId"] == VERSION
        assert "inputVersionIds" not in payload

    @pytest.mark.parametrize("broken, reason", [
        ({"key": ""}, "Input view 2 of 2 names no key"),
        ({"bucket": None}, "Input view 2 of 2 names no bucket"),
        ({"versionId": None}, "Input view 2 of 2 carries no versionId string"),
    ])
    def test_a_malformed_view_is_refused_before_the_state_machine_starts(self, broken, reason):
        views = [_view(MUG, VERSION), dict(_view(MUG_BACK, BACK_VERSION), **broken)]
        _mod, resp, start, send_failure = TestOpenPipeline()._run(self._event(views=views))
        assert resp["statusCode"] == 400
        assert reason in resp["body"]["message"]
        start.assert_not_called()
        send_failure.assert_called_once()

    def test_a_view_of_an_unsupported_type_in_the_view_list_is_refused(self):
        views = [_view(MUG, VERSION), _view("xidM/photos/notes.txt", BACK_VERSION)]
        _mod, resp, start, send_failure = TestOpenPipeline()._run(self._event(views=views))
        assert resp["statusCode"] == 400
        assert "notes.txt" in resp["body"]["message"]
        start.assert_not_called()
        send_failure.assert_called_once()

    def test_an_event_from_a_development_build_before_input_views_forwards_its_path_list(self):
        paths = [f"s3://abkt/{MUG}", f"s3://abkt/{MUG_BACK}"]
        event = self._event(paths=paths)
        del event["inputVersionId"]
        _mod, resp, start, _fail = TestOpenPipeline()._run(event)
        assert resp["statusCode"] == 200
        payload = json.loads(start.call_args.kwargs["input"])
        assert (payload["inputViews"], payload["inputS3AssetFilePaths"], payload["inputVersionId"]) == (
            [], paths, "")


@pytest.mark.unit
class TestConstructPipelineReadsEachViewWithItsVersion:
    def _handler(self):
        return TestConstructPipeline()._load().lambda_handler

    def _definition(self, **overrides):
        construct = TestConstructPipeline()
        out = self._handler()(construct._event(**overrides), MagicMock())
        return json.loads(out["definition"][2])

    def test_each_view_carries_its_own_version(self):
        definition = self._definition(
            inputVersionId=VERSION, inputViews=[_view(MUG, VERSION), _view(MUG_BACK, BACK_VERSION)])
        assert definition["inputFile"]["versionId"] == VERSION
        assert [(view["objectKey"], view["versionId"]) for view in definition["inputFiles"]] == [
            (MUG, VERSION), (MUG_BACK, BACK_VERSION)]

    def test_the_same_photo_at_two_versions_stays_two_views(self):
        definition = self._definition(
            inputVersionId=VERSION, inputViews=[_view(MUG, VERSION), _view(MUG, BACK_VERSION)])
        assert definition["inputFiles"] == [
            {"bucketName": "abkt", "objectKey": MUG, "fileExtension": ".png", "versionId": VERSION},
            {"bucketName": "abkt", "objectKey": MUG, "fileExtension": ".png", "versionId": BACK_VERSION},
        ]

    def test_a_view_without_a_version_writes_no_version_field(self):
        definition = self._definition(
            inputVersionId=VERSION, inputViews=[_view(MUG, VERSION), _view(MUG_BACK, "")])
        assert definition["inputFiles"][0]["versionId"] == VERSION
        assert "versionId" not in definition["inputFiles"][1]

    def test_the_view_list_is_the_only_source_of_the_views_and_their_regions(self):
        """A path list on the same event (a development build's field) is not read beside it."""
        definition = self._definition(
            inputVersionId=VERSION, inputBucketRegion="eu-west-1",
            inputViews=[_view(MUG, VERSION), _view(MUG_BACK, BACK_VERSION, bucket="viewbkt")],
            inputS3AssetFilePaths=[f"s3://abkt/{MUG}", "s3://otherbkt/xidM/photos/stale.png"])
        assert [(view["bucketName"], view["objectKey"]) for view in definition["inputFiles"]] == [
            ("abkt", MUG), ("viewbkt", MUG_BACK)]
        assert definition["bucketRegions"] == {"abkt": "eu-west-1", "viewbkt": "eu-west-1"}

    @pytest.mark.parametrize("views, reason", [
        ([_view(MUG, VERSION), {"bucket": "abkt", "versionId": BACK_VERSION}], "Input view 2 of 2 names no key"),
        ([_view(MUG, VERSION), {"key": MUG_BACK, "versionId": BACK_VERSION}], "Input view 2 of 2 names no bucket"),
        ([_view(MUG, VERSION), {"bucket": "abkt", "key": MUG_BACK}], "Input view 2 of 2 carries no versionId"),
        ([_view(MUG, VERSION), _view(MUG_BACK, 7)], "Input view 2 of 2 carries no versionId"),
        ([_view(MUG, VERSION), f"s3://abkt/{MUG_BACK}"], "Input view 2 of 2 is not an object"),
        ({"bucket": "abkt", "key": MUG, "versionId": VERSION}, "inputViews must be a list"),
    ])
    def test_a_malformed_view_fails_the_run(self, views, reason):
        """Raised, so the state machine's catch routes the message to PipelineEndTask, which fails the
        waiting task with it; the view is never padded, skipped or read at the current object."""
        construct = TestConstructPipeline()
        with pytest.raises(ValueError, match=reason) as raised:
            self._handler()(construct._event(inputVersionId=VERSION, inputViews=views), MagicMock())
        # The name the workflow shows as the failure's Error.
        assert raised.type.__name__ == "MalformedInputView"

    def test_a_single_view_run_carries_the_primary_version(self):
        definition = self._definition(inputVersionId=VERSION, inputViews=[_view(MUG, VERSION)])
        assert [view["versionId"] for view in definition["inputFiles"]] == [VERSION]

    def test_without_a_view_list_the_primary_is_read_at_its_version(self):
        """A run without a manifest: vamsExecute sends an empty view list."""
        definition = self._definition(inputVersionId=VERSION, inputViews=[])
        assert [(view["objectKey"], view["versionId"]) for view in definition["inputFiles"]] == [(MUG, VERSION)]

    def test_without_a_version_the_definition_is_the_one_development_writes(self):
        # Captured from origin/development's constructPipeline for the same event. An image built before
        # the version field parses this definition during an upgrade, so a no-version run must not change it.
        assert json.dumps(self._definition()) == DEVELOPMENT_DEFINITION
        assert json.dumps(self._definition(inputViews=[_view(MUG, "")])) == DEVELOPMENT_DEFINITION

    def test_a_path_list_event_without_versions_reads_every_view_at_the_current_object(self):
        """A development build before the version field sends inputS3AssetFilePaths and no version; the
        definition is byte-identical to the one development writes for it."""
        definition = self._definition(
            inputBucketRegion="eu-west-1", inputS3AssetFilePaths=[f"s3://abkt/{MUG}", f"s3://abkt/{MUG_BACK}"])
        assert json.dumps(definition) == DEVELOPMENT_LEGACY_TWO_VIEW_DEFINITION

    def test_a_mixed_build_run_keeps_the_primary_version_and_reads_other_views_current(self):
        """While the three functions run different builds, a development openPipeline sends the path list
        with its parallel inputVersionIds, which this module does not read: the primary path keeps
        inputVersionId, so inputFile and inputFiles[0] agree, and every other view reads the current object."""
        definition = self._definition(
            inputVersionId=VERSION, inputVersionIds=[VERSION, BACK_VERSION],
            inputS3AssetFilePaths=[f"s3://abkt/{MUG}", f"s3://abkt/{MUG_BACK}"])
        assert definition["inputFile"]["versionId"] == VERSION
        assert definition["inputFiles"][0]["versionId"] == VERSION
        assert "versionId" not in definition["inputFiles"][1]

    def test_a_mixed_build_single_view_run_keeps_its_version(self):
        definition = self._definition(inputVersionId=VERSION, inputS3AssetFilePaths=[f"s3://abkt/{MUG}"])
        assert [view["versionId"] for view in definition["inputFiles"]] == [VERSION]

    def test_a_mixed_build_run_gives_the_primary_version_to_every_copy_of_the_primary_path(self):
        """The primary file selected at two versions is listed twice in the path list. Both copies carry the
        primary's version, so the container reads the file once, at that version, as it does any repeat."""
        definition = self._definition(
            inputVersionId=VERSION, inputVersionIds=[VERSION, BACK_VERSION, ""],
            inputS3AssetFilePaths=[f"s3://abkt/{MUG}", f"s3://abkt/{MUG}", f"s3://abkt/{MUG_BACK}"])
        assert [(view["objectKey"], view.get("versionId")) for view in definition["inputFiles"]] == [
            (MUG, VERSION), (MUG, VERSION), (MUG_BACK, None)]

    def test_a_development_build_path_that_names_no_object_fails_the_run(self):
        construct = TestConstructPipeline()
        with pytest.raises(ValueError, match="Input view 2 of 2 is not an s3://bucket/key location"):
            self._handler()(construct._event(inputS3AssetFilePaths=[f"s3://abkt/{MUG}", "s3://abkt"]), MagicMock())


@pytest.mark.unit
class TestTheWholeChain:
    def test_each_view_reaches_the_definition_with_its_own_version(self):
        """vamsExecute -> openPipeline -> constructPipeline, each hop fed the previous one's output."""
        _mod, _resp, message = TestVamsExecute()._run(
            manifest=_views_manifest((MUG, VERSION), (MUG, BACK_VERSION), (MUG_BACK, "")),
            config={"MODEL": "hunyuan3d"})
        _mod, resp, start, _fail = TestOpenPipeline()._run(message)
        assert resp["statusCode"] == 200
        state_input = json.loads(start.call_args.kwargs["input"])
        out = TestConstructPipeline()._load().lambda_handler(state_input, MagicMock())
        definition = json.loads(out["definition"][2])
        assert [(view["objectKey"], view.get("versionId")) for view in definition["inputFiles"]] == [
            (MUG, VERSION), (MUG, BACK_VERSION), (MUG_BACK, None)]
        assert definition["inputFile"]["versionId"] == VERSION
