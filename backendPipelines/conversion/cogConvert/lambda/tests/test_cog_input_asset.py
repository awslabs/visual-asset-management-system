#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""conversion/cogConvert: where the input sits in its asset travels from the manifest.

``vamsExecute`` forwards the first input entry's ``relativePath``, ``assetRootS3Key`` and
``assetRootBucket`` as ``inputRelativePath``, ``inputAssetRootS3Key`` and ``inputAssetRootBucket``;
``openPipeline`` carries them in the state-machine input; ``constructPipeline`` names the location in the
Batch command's second argument as ``inputAsset``, never inside the definition (the image's dataclasses
refuse an unknown key), and ``executeBatchJob`` submits the whole command.

The upgrade window: the image at development e7f37e251 (``fixtures/pre382``) reads the second argument
through ``apply_input_versions``, which requires ``inputVersions`` and ignores any other key, so it starts
the run. An older Lambda's command reaching the new image names no location, and the image derives the
subfolder from the key (the container's ``test_cog_input_asset_location``). Reuses the fixtures of
``test_manifest_refactor``, ``test_cog_bucket_regions`` and ``test_batch_job_registration`` (same
directory; the suite runs alone)."""

import hashlib
import importlib.util
import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

_LAMBDA_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _LAMBDA_DIR not in sys.path:
    sys.path.insert(0, _LAMBDA_DIR)

import test_manifest_refactor as refactor  # noqa: E402
import test_cog_bucket_regions as regions  # noqa: E402
import test_cog_input_version as versions  # noqa: E402
import test_batch_job_registration as registration  # noqa: E402

_FIXTURES = os.path.normpath(os.path.join(_LAMBDA_DIR, "..", "..", "..", "tests", "fixtures"))

# The image's utils/pipeline/objects.py at development e7f37e251 (`git rev-parse <commit>:<path>`).
PRE382_OBJECTS = os.path.join(_FIXTURES, "pre382", "cog_convert_objects.py")
PRE382_BLOB = "526ac5487840b2c8d6ee863855cb21fe13691cc7"

ASSET = {"relativePath": "/maps/north/scan.tif", "assetRootS3Key": "projects/site-a/",
         "assetRootBucket": "in-bkt"}
EVENT_FIELDS = {"inputRelativePath": "/maps/north/scan.tif", "inputAssetRootS3Key": "projects/site-a/",
                "inputAssetRootBucket": "in-bkt"}
VERSION = versions.VERSION


def _git_blob_id(path):
    with open(path, "rb") as f:
        content = f.read()
    return hashlib.sha1(b"blob %d\x00" % len(content) + content).hexdigest()  # nosec B324 - git object id, not a security use


def _command(**overrides):
    construct = regions.TestConstructPipelineMapsBucketsToRegions()
    return construct._command(construct._event(**overrides))


@pytest.mark.unit
class TestVamsExecuteForwardsTheInputAsset:
    def _run(self, manifest):
        base = refactor.TestVamsExecute()
        mod = base._load()
        invoke = MagicMock(return_value={"StatusCode": 200})
        with patch.object(mod, "s3_client", regions._manifest_s3(manifest)), \
                patch.object(mod.lambda_client, "invoke", invoke):
            resp = mod.lambda_handler({"body": json.dumps(base._body())}, MagicMock())
        assert resp["statusCode"] == 200
        return json.loads(invoke.call_args.kwargs["Payload"].decode("utf-8"))

    def test_the_payload_carries_the_first_entrys_location(self):
        manifest = refactor.TestVamsExecute()._manifest()
        manifest["inputFiles"][0].update(ASSET)
        payload = self._run(manifest)
        assert {k: payload[k] for k in EVENT_FIELDS} == EVENT_FIELDS

    def test_an_entry_without_them_sends_empty_values(self):
        manifest = refactor.TestVamsExecute()._manifest()
        for name in ASSET:
            manifest["inputFiles"][0].pop(name, None)
        payload = self._run(manifest)
        assert [payload[k] for k in EVENT_FIELDS] == ["", "", ""]


@pytest.mark.unit
class TestOpenPipelineForwardsTheInputAsset:
    def _run(self, event):
        return regions.TestOpenPipelineForwardsBucketRegions()._run(event)

    def test_the_state_machine_input_carries_it(self):
        sfn_input = self._run(dict(refactor.TestOpenPipeline()._event(), **EVENT_FIELDS))
        assert {k: sfn_input[k] for k in EVENT_FIELDS} == EVENT_FIELDS

    def test_an_event_from_an_older_lambda_forwards_empty_values(self):
        sfn_input = self._run(refactor.TestOpenPipeline()._event())
        assert [sfn_input[k] for k in EVENT_FIELDS] == ["", "", ""]


@pytest.mark.unit
class TestConstructPipelineNamesTheInputAssetBesideTheDefinition:
    def test_without_a_version_the_document_carries_an_empty_version_map(self):
        command = _command(**EVENT_FIELDS)
        assert len(command) == 2
        assert json.loads(command[1]) == {"inputVersions": {}, "inputAsset": ASSET}

    def test_with_a_version_both_are_named(self):
        command = _command(inputVersionId=VERSION, **EVENT_FIELDS)
        assert json.loads(command[1]) == {"inputVersions": {"COG_CONVERT": VERSION}, "inputAsset": ASSET}

    def test_an_event_without_it_writes_the_command_as_before(self):
        assert len(_command()) == 1
        assert json.loads(_command(inputVersionId=VERSION)[1]) == {"inputVersions": {"COG_CONVERT": VERSION}}

    def test_the_definition_does_not_carry_it(self):
        assert _command(inputVersionId=VERSION, **EVENT_FIELDS)[0] == _command(inputVersionId=VERSION)[0]

    def test_the_batch_submit_sends_every_argument(self):
        command = _command(inputVersionId=VERSION, **EVENT_FIELDS)
        mod = registration._load_execute_batch_job()
        mod.lambda_handler({"jobName": "j", "definition": command, "taskToken": "tok"}, MagicMock())
        assert mod.batch.submit_job.call_args.kwargs["containerOverrides"]["command"] == command


@pytest.mark.unit
class TestAnOlderImageStartsTheNewCommand:
    def test_the_e7f37e251_snapshot_is_developments_code(self):
        assert _git_blob_id(PRE382_OBJECTS) == PRE382_BLOB

    @pytest.mark.parametrize("version_id", [VERSION, ""])
    def test_the_image_at_e7f37e251_parses_it(self, version_id):
        spec = importlib.util.spec_from_file_location("cog_objects_e7f37e251", PRE382_OBJECTS)
        objects = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(objects)
        command = _command(inputVersionId=version_id, **EVENT_FIELDS)
        params = objects.apply_input_versions(json.loads(command[0]), command[1:])
        definition = objects.PipelineDefinition(**params)
        stage = objects.PipelineStage(**definition.stages.pop(0))
        stage_input = objects.StageInput(**stage.inputFile)
        assert (stage_input.objectKey, stage_input.versionId) == ("xidC/scan.tif", version_id)


@pytest.mark.unit
class TestTheNewImageReadsTheLocation:
    def _objects(self):
        path = os.path.normpath(os.path.join(_LAMBDA_DIR, "..", "container", "cog_convert_pipeline",
                                             "utils", "pipeline", "objects.py"))
        spec = importlib.util.spec_from_file_location("cog_container_objects_input_asset", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_the_new_command_names_the_location(self):
        command = _command(inputVersionId=VERSION, **EVENT_FIELDS)
        assert self._objects().input_asset_location(command[1:]) == ASSET

    @pytest.mark.parametrize("overrides", [{}, {"inputVersionId": VERSION}])
    def test_an_older_lambdas_command_names_none(self, overrides):
        assert self._objects().input_asset_location(_command(**overrides)[1:]) is None
