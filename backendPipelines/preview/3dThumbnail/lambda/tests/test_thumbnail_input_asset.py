#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""preview/3dThumbnail: where the input sits in its asset travels from the manifest to the Batch container.

``vamsExecute`` forwards the first input entry's ``relativePath``, ``assetRootS3Key`` and
``assetRootBucket`` as ``inputRelativePath``, ``inputAssetRootS3Key`` and ``inputAssetRootBucket``;
``openPipeline`` carries them in the state-machine input; ``constructPipeline`` names them in the Batch
command's second argument as ``inputAsset``, beside the launch version and never inside the definition,
because the image parses the definition into dataclasses that refuse an unknown key.

The upgrade window, both directions:

- A new Lambda's command reaching an OLD image: the image built before the input-version field
  (``fixtures/pre994``) reads only the first argument; the image at development 8cddbe3f6
  (``fixtures/pre1415``) and the image at development 100d3ce68 (``fixtures/pre382``) read the second
  through ``apply_input_versions``, which requires ``inputVersions`` and ignores any other key. All three
  start the run and derive the subfolder from the key, as they always did.
- An old Lambda's command reaching the NEW image: no ``inputAsset``, so the image derives the subfolder
  and the reference root from the key (the container suite's ``test_thumbnail_manifest_input_asset``).

Reuses the fixtures of ``test_manifest_refactor`` and ``test_thumbnail_input_version`` (same directory;
the suite runs alone)."""

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
import test_thumbnail_input_version as versions  # noqa: E402

_FIXTURES = os.path.normpath(os.path.join(_LAMBDA_DIR, "..", "..", "..", "tests", "fixtures"))
_PRE994_DIR = os.path.join(_FIXTURES, "pre994")
if _PRE994_DIR not in sys.path:
    sys.path.insert(0, _PRE994_DIR)
import legacy_parse as legacy  # noqa: E402

# The image's utils/pipeline/objects.py at development 8cddbe3f6 and at development 100d3ce68
# (`git rev-parse <commit>:<path>`).
OLDER_IMAGES = {
    "8cddbe3f6": (os.path.join(_FIXTURES, "pre1415", "thumbnail_objects.py"),
                  "0f4a1eeb89874f2903e87679297d97becf9af1c5"),
    "100d3ce68": (os.path.join(_FIXTURES, "pre382", "thumbnail_objects.py"),
                  "bec294bccd1dfcbcd3d7c9230d99615359e99213"),
}

ASSET = {"relativePath": "/scans/pump.glb", "assetRootS3Key": "projects/site-a/", "assetRootBucket": "abkt"}
EVENT_FIELDS = {"inputRelativePath": "/scans/pump.glb", "inputAssetRootS3Key": "projects/site-a/",
                "inputAssetRootBucket": "abkt"}
VERSION = versions.VERSION


def _git_blob_id(path):
    with open(path, "rb") as f:
        content = f.read()
    return hashlib.sha1(b"blob %d\x00" % len(content) + content).hexdigest()  # nosec B324 - git object id, not a security use


def _objects_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _parse_like(objects, command):
    params = objects.apply_input_versions(json.loads(command[0]), command[1:])
    definition = objects.PipelineDefinition(**params)
    stage = objects.PipelineStage(**definition.stages.pop(0))
    stage_input = objects.StageInput(**stage.inputFile)
    objects.StageOutput(**stage.outputFiles)
    return stage_input


def _command(**overrides):
    return versions.TestConstructPipelinePutsTheVersionBesideTheDefinition()._command(**overrides)


@pytest.mark.unit
class TestVamsExecuteForwardsTheInputAsset:
    def _run(self, manifest):
        base = refactor.TestVamsExecuteUsesManifest()
        mod = base._load_module()
        invoke = MagicMock(return_value={"StatusCode": 200})
        with patch.object(mod, "s3_client", versions._manifest_s3(manifest)), \
                patch.object(mod.lambda_client, "invoke", invoke):
            resp = mod.lambda_handler({"body": json.dumps(base._manifest_body())}, MagicMock())
        assert resp["statusCode"] == 200
        return json.loads(invoke.call_args.kwargs["Payload"].decode("utf-8"))

    def test_the_payload_carries_the_first_entrys_location(self):
        manifest = refactor.TestVamsExecuteUsesManifest()._manifest()
        manifest["inputFiles"][0].update(relativePath="/scans/pump.glb", assetRootS3Key="projects/site-a/",
                                         assetRootBucket="abkt", key="projects/site-a/scans/pump.glb")
        payload = self._run(manifest)
        assert {k: payload[k] for k in EVENT_FIELDS} == EVENT_FIELDS

    def test_an_entry_without_the_newer_fields_sends_empty_ones(self):
        manifest = refactor.TestVamsExecuteUsesManifest()._manifest()
        payload = self._run(manifest)
        assert (payload["inputRelativePath"], payload["inputAssetRootS3Key"],
                payload["inputAssetRootBucket"]) == ("", "xidM/", "")


@pytest.mark.unit
class TestOpenPipelineForwardsTheInputAsset:
    def _run(self, event):
        base = refactor.TestOpenPipelineRegistration()
        mod = base._load_module()
        start = base._mock_start()
        with patch.object(mod.sfn, "start_execution", start), \
                patch.object(mod.events_client, "put_events", MagicMock()), \
                patch.object(mod.computeTier, "select_compute_tier", MagicMock(return_value="standard")):
            resp = mod.lambda_handler(event, MagicMock())
        assert resp["statusCode"] == 200
        return json.loads(start.call_args.kwargs["input"])

    def test_the_state_machine_input_carries_it(self):
        sfn_input = self._run(dict(refactor.TestOpenPipelineRegistration()._event(), **EVENT_FIELDS))
        assert {k: sfn_input[k] for k in EVENT_FIELDS} == EVENT_FIELDS

    def test_an_event_from_an_older_lambda_forwards_empty_values(self):
        sfn_input = self._run(refactor.TestOpenPipelineRegistration()._event())
        assert [sfn_input[k] for k in EVENT_FIELDS] == ["", "", ""]


@pytest.mark.unit
class TestConstructPipelineNamesTheInputAssetBesideTheDefinition:
    def test_without_a_version_the_document_carries_an_empty_version_map(self):
        command = _command(**EVENT_FIELDS)
        assert json.loads(command[1]) == {"inputVersions": {}, "inputAsset": ASSET}

    def test_with_a_version_both_are_named(self):
        command = _command(inputVersionId=VERSION, **EVENT_FIELDS)
        assert json.loads(command[1]) == {"inputVersions": {"PREVIEW_3D_THUMBNAIL": VERSION},
                                          "inputAsset": ASSET}

    def test_an_event_without_it_writes_the_command_as_before(self):
        assert len(_command()) == 1
        assert len(_command(inputAssetRootBucket="abkt")) == 1

    def test_the_definition_is_the_one_development_writes(self):
        assert _command(inputVersionId=VERSION, **EVENT_FIELDS)[0] == versions.DEVELOPMENT_DEFINITION


@pytest.mark.unit
class TestAnOlderImageStartsTheNewCommand:
    @pytest.mark.parametrize("commit", sorted(OLDER_IMAGES))
    def test_the_snapshots_are_developments_code(self, commit):
        path, blob = OLDER_IMAGES[commit]
        assert _git_blob_id(path) == blob

    @pytest.mark.parametrize("commit", sorted(OLDER_IMAGES))
    @pytest.mark.parametrize("version_id", [VERSION, ""])
    def test_an_image_built_since_the_version_field_parses_it(self, commit, version_id):
        objects = _objects_module(OLDER_IMAGES[commit][0], f"thumbnail_objects_{commit}")
        stage_input = _parse_like(objects, _command(inputVersionId=version_id, **EVENT_FIELDS))
        assert (stage_input.objectKey, stage_input.versionId) == ("xidM/test/pump.glb", version_id)

    def test_the_image_built_before_the_version_field_parses_it(self):
        _, _, stage_input = legacy.parse_like_development("3dThumbnail", _command(**EVENT_FIELDS))
        assert stage_input.objectKey == "xidM/test/pump.glb"

    def test_a_stage_field_is_what_the_older_images_refuse(self):
        # The negative control for keeping the location out of the definition.
        definition = json.loads(_command()[0])
        definition["stages"][0]["inputFile"]["relativePath"] = "/scans/pump.glb"
        objects = _objects_module(OLDER_IMAGES["100d3ce68"][0], "thumbnail_objects_negative")
        with pytest.raises(TypeError, match="relativePath"):
            _parse_like(objects, [json.dumps(definition)])


@pytest.mark.unit
class TestTheNewImageReadsTheLocation:
    def _objects(self):
        path = os.path.normpath(os.path.join(_LAMBDA_DIR, "..", "container", "preview_pipeline", "utils",
                                             "pipeline", "objects.py"))
        return _objects_module(path, "container_objects_input_asset")

    def test_the_new_command_names_the_location(self):
        command = _command(inputVersionId=VERSION, **EVENT_FIELDS)
        assert self._objects().input_asset_location(command[1:]) == ASSET

    @pytest.mark.parametrize("overrides", [{}, {"inputVersionId": VERSION}])
    def test_an_older_lambdas_command_names_none(self, overrides):
        assert self._objects().input_asset_location(_command(**overrides)[1:]) is None
