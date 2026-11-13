#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""preview/3dThumbnail: the execution's results folder travels from the manifest to the Batch container.

The container writes a result document there when a preview was rendered without some of its referenced
files, and the workflow's output step records it on the execution (Output Results). ``vamsExecute``
forwards ``outputS3AssetResultsPath`` (manifest ``outputs.results``), ``openPipeline`` carries it in the
state-machine input, and ``constructPipeline`` names it in the Batch command's second argument, beside
the launch version and never inside the definition.

The upgrade window, both directions:

- A new Lambda's command reaching an OLD image. The image built before the input-version field reads only
  the first argument (``fixtures/pre994``). The image built since reads the second through
  ``apply_input_versions``, which refuses a document without ``inputVersions`` (``fixtures/pre1415``, the
  image's ``objects.py`` at development 8cddbe3f6). The new command keeps the definition byte-identical
  and always puts ``inputVersions`` (empty without a version) in that document, so both images start
  the run; they just write no result document.
- An old Lambda's command reaching the NEW image: no results location, so the image writes nothing and
  renders as before (the container suite's ``test_thumbnail_incomplete_preview``).

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

# The image's utils/pipeline/objects.py at development 8cddbe3f6 (`git ls-tree 8cddbe3f6 <path>`).
PRE1415_OBJECTS = os.path.join(_FIXTURES, "pre1415", "thumbnail_objects.py")
PRE1415_BLOB = "0f4a1eeb89874f2903e87679297d97becf9af1c5"

RESULTS_PREFIX = "pipelines/p1/MJOB/output/E1/results/"
RESULTS_URI = "s3://out-bkt/" + RESULTS_PREFIX
RESULTS = {"bucketName": "out-bkt", "objectDir": RESULTS_PREFIX}
VERSION = versions.VERSION


def _git_blob_id(path):
    with open(path, "rb") as f:
        content = f.read()
    return hashlib.sha1(b"blob %d\x00" % len(content) + content).hexdigest()  # nosec B324 - git object id, not a security use


def _pre1415_objects():
    spec = importlib.util.spec_from_file_location("pre1415_thumbnail_objects", PRE1415_OBJECTS)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _parse_like_8cddbe3f6(command):
    """Parse a Batch command the way the image built from development 8cddbe3f6 does:
    ``run(apply_input_versions(json.loads(sys.argv[1]), sys.argv[2:]))``, then ``PipelineDefinition(**)``,
    ``PipelineStage(**stages.pop(0))``, ``StageInput(**inputFile)``, ``StageOutput(**outputFiles)``."""
    objects = _pre1415_objects()
    params = objects.apply_input_versions(json.loads(command[0]), command[1:])
    definition = objects.PipelineDefinition(**params)
    stage = objects.PipelineStage(**definition.stages.pop(0))
    stage_input = objects.StageInput(**stage.inputFile)
    objects.StageOutput(**stage.outputFiles)
    return definition, stage, stage_input


def _command(**overrides):
    return versions.TestConstructPipelinePutsTheVersionBesideTheDefinition()._command(**overrides)


@pytest.mark.unit
class TestVamsExecuteForwardsTheResultsPath:
    def _run(self, manifest):
        base = refactor.TestVamsExecuteUsesManifest()
        mod = base._load_module()
        invoke = MagicMock(return_value={"StatusCode": 200})
        with patch.object(mod, "s3_client", versions._manifest_s3(manifest)), \
                patch.object(mod.lambda_client, "invoke", invoke):
            resp = mod.lambda_handler({"body": json.dumps(base._manifest_body())}, MagicMock())
        assert resp["statusCode"] == 200
        return json.loads(invoke.call_args.kwargs["Payload"].decode("utf-8"))

    def test_the_payload_carries_the_manifest_results_prefix(self):
        manifest = refactor.TestVamsExecuteUsesManifest()._manifest()
        manifest["outputs"]["results"] = RESULTS_PREFIX
        assert self._run(manifest)["outputS3AssetResultsPath"] == "s3://abkt/" + RESULTS_PREFIX

    def test_a_manifest_without_one_sends_an_empty_value(self):
        manifest = refactor.TestVamsExecuteUsesManifest()._manifest()
        assert self._run(manifest)["outputS3AssetResultsPath"] == ""


@pytest.mark.unit
class TestOpenPipelineForwardsTheResultsPath:
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
        event = dict(refactor.TestOpenPipelineRegistration()._event(), outputS3AssetResultsPath=RESULTS_URI)
        assert self._run(event)["outputS3AssetResultsPath"] == RESULTS_URI

    def test_an_event_from_an_older_lambda_forwards_an_empty_value(self):
        assert self._run(refactor.TestOpenPipelineRegistration()._event())["outputS3AssetResultsPath"] == ""


@pytest.mark.unit
class TestConstructPipelineNamesTheResultsFolderBesideTheDefinition:
    def test_without_a_version_the_document_carries_an_empty_version_map(self):
        command = _command(outputS3AssetResultsPath=RESULTS_URI)
        assert len(command) == 2
        assert json.loads(command[1]) == {"inputVersions": {}, "outputResults": RESULTS}

    def test_with_a_version_both_are_named(self):
        command = _command(outputS3AssetResultsPath=RESULTS_URI, inputVersionId=VERSION)
        assert json.loads(command[1]) == {"inputVersions": {"PREVIEW_3D_THUMBNAIL": VERSION},
                                          "outputResults": RESULTS}

    def test_a_folder_without_its_trailing_slash_gets_one(self):
        command = _command(outputS3AssetResultsPath=RESULTS_URI.rstrip("/"))
        assert json.loads(command[1])["outputResults"] == RESULTS

    @pytest.mark.parametrize("uri", ["", "s3://out-bkt", "s3://out-bkt/", "not-a-uri"])
    def test_no_usable_folder_writes_the_command_as_before(self, uri):
        assert len(_command(outputS3AssetResultsPath=uri)) == 1

    @pytest.mark.parametrize("overrides", [{"outputS3AssetResultsPath": RESULTS_URI},
                                           {"outputS3AssetResultsPath": RESULTS_URI,
                                            "inputVersionId": VERSION}])
    def test_the_definition_is_the_one_development_writes(self, overrides):
        assert _command(**overrides)[0] == versions.DEVELOPMENT_DEFINITION

    def test_the_results_bucket_is_signed_for_the_output_region(self):
        mod = refactor.TestContainerContractAcceptsConstructOutput()._construct_module()
        event = {"inputS3AssetFilePath": "s3://in-bkt/xid/a.glb", "outputS3AssetResultsPath": RESULTS_URI,
                 "outputBucketRegion": "eu-west-1"}
        assert mod.bucket_regions(event) == {"out-bkt": "eu-west-1"}


@pytest.mark.unit
class TestAnOlderImageStartsTheNewCommand:
    def test_the_8cddbe3f6_snapshot_is_developments_code(self):
        assert _git_blob_id(PRE1415_OBJECTS) == PRE1415_BLOB

    @pytest.mark.parametrize("version_id", [VERSION, ""])
    def test_the_image_built_since_the_version_field_parses_it(self, version_id):
        command = _command(outputS3AssetResultsPath=RESULTS_URI, inputVersionId=version_id)
        _, stage, stage_input = _parse_like_8cddbe3f6(command)
        assert stage.type == "PREVIEW_3D_THUMBNAIL"
        assert stage_input.objectKey == "xidM/test/pump.glb"
        assert stage_input.versionId == version_id

    @pytest.mark.parametrize("version_id", [VERSION, ""])
    def test_the_image_built_before_the_version_field_parses_it(self, version_id):
        command = _command(outputS3AssetResultsPath=RESULTS_URI, inputVersionId=version_id)
        _, stage, stage_input = legacy.parse_like_development("3dThumbnail", command)
        assert stage_input.objectKey == "xidM/test/pump.glb"

    def test_a_document_without_the_version_map_is_what_the_8cddbe3f6_image_refuses(self):
        # The negative control for always writing inputVersions.
        command = _command(outputS3AssetResultsPath=RESULTS_URI)
        with pytest.raises(ValueError):
            _parse_like_8cddbe3f6([command[0], json.dumps({"outputResults": RESULTS})])

    def test_a_stage_field_is_what_both_images_refuse(self):
        # The negative control for keeping the location out of the definition.
        definition = json.loads(_command()[0])
        definition["stages"][0]["outputResults"] = RESULTS
        with pytest.raises(TypeError, match="outputResults"):
            _parse_like_8cddbe3f6([json.dumps(definition)])
        with pytest.raises(TypeError, match="outputResults"):
            legacy.parse_like_development("3dThumbnail", [json.dumps(definition)])


@pytest.mark.unit
class TestTheNewImageReadsTheLocation:
    """The container's own reading of the command (its objects module, loaded from source)."""

    def _objects(self):
        path = os.path.normpath(os.path.join(_LAMBDA_DIR, "..", "container", "preview_pipeline", "utils",
                                             "pipeline", "objects.py"))
        spec = importlib.util.spec_from_file_location("container_objects_results", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_the_new_command_names_the_location(self):
        command = _command(outputS3AssetResultsPath=RESULTS_URI, inputVersionId=VERSION)
        objects = self._objects()
        assert objects.results_location(command[1:]) == RESULTS
        params = objects.apply_input_versions(json.loads(command[0]), command[1:])
        assert params["stages"][0]["inputFile"]["versionId"] == VERSION

    @pytest.mark.parametrize("command", [
        lambda: _command(),
        lambda: _command(inputVersionId=VERSION),
    ])
    def test_an_older_lambdas_command_names_none(self, command):
        assert self._objects().results_location(command()[1:]) is None
