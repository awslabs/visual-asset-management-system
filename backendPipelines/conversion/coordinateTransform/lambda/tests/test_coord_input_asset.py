#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""conversion/coordinateTransform: where the input sits in its asset travels from the manifest.

``vamsExecute`` forwards the first input entry's ``relativePath``, ``assetRootS3Key`` and
``assetRootBucket`` as ``inputRelativePath``, ``inputAssetRootS3Key`` and ``inputAssetRootBucket``;
``openPipeline`` carries them in the state-machine input; ``constructPipeline`` reads the input's file
metadata at that relative path and names the location in the Batch command's second argument as
``inputAsset``, never inside the definition (the image's dataclasses refuse an unknown key).

The upgrade window: the image built before the input-version field reads only the first argument
(``fixtures/pre994``); the image at development 100d3ce68 (``fixtures/pre382``) reads the second through
``apply_input_versions``, which requires ``inputVersions`` and ignores any other key. Both start the run.
An older Lambda's command reaching the new image names no location, and the image derives the subfolder
from the key (the container's ``test_output_contract``)."""

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
import test_coord_bucket_regions as regions  # noqa: E402
import test_coord_input_version as versions  # noqa: E402

_FIXTURES = os.path.normpath(os.path.join(_LAMBDA_DIR, "..", "..", "..", "tests", "fixtures"))
_PRE994_DIR = os.path.join(_FIXTURES, "pre994")
if _PRE994_DIR not in sys.path:
    sys.path.insert(0, _PRE994_DIR)
import legacy_parse as legacy  # noqa: E402

# The image's utils/pipeline/objects.py at development 100d3ce68 (`git rev-parse <commit>:<path>`).
PRE382_OBJECTS = os.path.join(_FIXTURES, "pre382", "coord_transform_objects.py")
PRE382_BLOB = "d00504c4a6e807d5dde8e139476993e7ec188e40"

ASSET = {"relativePath": "/scans/scan.e57", "assetRootS3Key": "projects/site-a/", "assetRootBucket": "in-bkt"}
EVENT_FIELDS = {"inputRelativePath": "/scans/scan.e57", "inputAssetRootS3Key": "projects/site-a/",
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
        manifest["inputFiles"][0].update(relativePath="/scans/scan.e57", assetRootS3Key="projects/site-a/",
                                         assetRootBucket="in-bkt")
        payload = self._run(manifest)
        assert {k: payload[k] for k in EVENT_FIELDS} == EVENT_FIELDS

    def test_an_entry_without_them_sends_empty_values(self):
        manifest = refactor.TestVamsExecute()._manifest()
        for name in ("relativePath", "assetRootS3Key", "assetRootBucket"):
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
        assert json.loads(command[1]) == {"inputVersions": {"COORD_TRANSFORM": VERSION}, "inputAsset": ASSET}

    def test_an_event_without_it_writes_the_command_as_before(self):
        assert len(_command()) == 1

    def test_the_definition_is_the_one_development_writes(self):
        assert _command(inputVersionId=VERSION, **EVENT_FIELDS)[0] == versions.DEVELOPMENT_DEFINITION


@pytest.mark.unit
class TestTheFileMetadataIsReadAtTheManifestPath:
    def _file_key(self, **overrides):
        construct = regions.TestConstructPipelineMapsBucketsToRegions()
        mod = construct._construct()
        seen = []
        real = mod.manifestHelper.to_legacy_vams_view

        def capture(body, database_id, asset_id, file_key):
            seen.append(file_key)
            return real(body, database_id, asset_id, file_key)

        s3 = refactor.TestConstructPipelineReadsFromS3()._s3_returning({
            "pipelines/workflowExecutionInputs/E1/pipeline1/config.json":
                {"sourceCrs": "EPSG:4326", "targetCrs": "EPSG:27700", "outputFormats": ["laz"]},
            "pipelines/workflowExecutionInputs/E1/metadata.json": {
                "schemaVersion": 2,
                "assets": [{"databaseId": "dbC", "assetId": "xidC",
                            "files": [{"fileKey": "/scans/scan.e57", "metadata": {"site": "north"}}]}]},
        })
        with patch.object(mod.manifestHelper, "to_legacy_vams_view", side_effect=capture), \
                patch.object(mod, "s3", s3):
            mod.lambda_handler(construct._event(**overrides), MagicMock())
        return seen

    def test_an_asset_on_an_existing_bucket_folder(self):
        # The key does not contain the asset ID, so the slice read the asset-level record.
        key = {"inputS3AssetFilePath": "s3://in-bkt/projects/site-a/scans/scan.e57"}
        assert self._file_key(**key) == ["/"]
        assert self._file_key(**key, **EVENT_FIELDS) == ["/scans/scan.e57"]

    def test_a_chained_input(self):
        fields = dict(EVENT_FIELDS, inputRelativePath="/scans/scan.e57")
        assert self._file_key(
            inputS3AssetFilePath="s3://out-bkt/pipelines/p0/J/output/E1/files/scans/scan.e57",
            **fields) == ["/scans/scan.e57"]

    def test_without_the_manifest_the_key_is_sliced_at_the_asset_id(self):
        assert self._file_key() == ["/scan.e57"]


@pytest.mark.unit
class TestAnOlderImageStartsTheNewCommand:
    def test_the_100d3ce68_snapshot_is_developments_code(self):
        assert _git_blob_id(PRE382_OBJECTS) == PRE382_BLOB

    @pytest.mark.parametrize("version_id", [VERSION, ""])
    def test_the_image_at_100d3ce68_parses_it(self, version_id):
        spec = importlib.util.spec_from_file_location("coord_objects_100d3ce68", PRE382_OBJECTS)
        objects = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(objects)
        command = _command(inputVersionId=version_id, **EVENT_FIELDS)
        params = objects.apply_input_versions(json.loads(command[0]), command[1:])
        definition = objects.PipelineDefinition(**params)
        stage = objects.PipelineStage(**definition.stages.pop(0))
        stage_input = objects.StageInput(**stage.inputFile)
        assert (stage_input.objectKey, stage_input.versionId) == ("xidC/scan.e57", version_id)

    def test_the_image_built_before_the_version_field_parses_it(self):
        _, stage, stage_input = legacy.parse_like_development("coordinateTransform", _command(**EVENT_FIELDS))
        assert stage_input.objectKey == "xidC/scan.e57"


@pytest.mark.unit
class TestTheNewImageReadsTheLocation:
    def _objects(self):
        path = os.path.normpath(os.path.join(_LAMBDA_DIR, "..", "container", "coord_transform_pipeline",
                                             "utils", "pipeline", "objects.py"))
        spec = importlib.util.spec_from_file_location("coord_container_objects_input_asset", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_the_new_command_names_the_location(self):
        command = _command(inputVersionId=VERSION, **EVENT_FIELDS)
        assert self._objects().input_asset_location(command[1:]) == ASSET

    @pytest.mark.parametrize("overrides", [{}, {"inputVersionId": VERSION}])
    def test_an_older_lambdas_command_names_none(self, overrides):
        assert self._objects().input_asset_location(_command(**overrides)[1:]) is None
