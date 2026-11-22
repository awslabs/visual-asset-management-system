# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The COG lands beside its input, in the folder its manifest ``relativePath`` names.

``constructPipeline`` names where the input sits in its asset in the Batch command's second argument
(``{"inputVersions": {...}, "inputAsset": {"relativePath", "assetRootS3Key", "assetRootBucket"}}``);
``__main__`` reads it with ``objects.input_asset_location`` and ``core.run`` takes the output subfolder,
and the asset-relative paths of the ``cog_link`` attributes, from the folder of ``relativePath``. The key
is searched for the asset ID only when a launch names no location: the key of an asset created on an
existing bucket folder does not contain the asset ID, and a chained step's input is keyed under the run's
output folder."""

import json
import signal

import pytest

from cog_convert_pipeline import convert, core
from cog_convert_pipeline.utils.pipeline import objects
from conftest import fixture_path, requires_gdal
# world: the fake S3 and task-token fixture of the whole-stage tests (same directory; the suite runs alone).
from test_core_flow import BUCKET, _attrs, _definition, world  # noqa: F401

ASSET = "xA1"
EXISTING_FOLDER_KEY = "projects/site-a/maps/north/{name}"
LOCATION = {"relativePath": "/maps/north/{name}", "assetRootS3Key": "projects/site-a/",
            "assetRootBucket": BUCKET}


def _location(name):
    return {k: v.format(name=name) for k, v in LOCATION.items()}


@pytest.mark.unit
class TestTheSubfolderComesFromTheRelativePath:
    def test_an_asset_on_an_existing_bucket_folder(self):
        key = "projects/site-a/maps/north/scene.tif"
        # The key does not contain the asset ID: the search alone puts the COG at the output root.
        assert convert.relative_subdir(key, ASSET) == ""
        assert convert.relative_subdir(key, ASSET, "/maps/north/scene.tif") == "maps/north"

    def test_a_chained_input(self):
        key = "pipelines/p0/J/output/E1/files/maps/scene.tif"
        assert convert.relative_subdir(key, ASSET) == ""
        assert convert.relative_subdir(key, ASSET, "/maps/scene.tif") == "maps"

    def test_same_named_inputs_in_two_folders_keep_apart(self):
        left = convert.relative_subdir("projects/site-a/left/scene.tif", ASSET, "/left/scene.tif")
        right = convert.relative_subdir("projects/site-a/right/scene.tif", ASSET, "/right/scene.tif")
        assert (left, right) == ("left", "right")

    def test_a_file_at_the_asset_root(self):
        assert convert.relative_subdir(f"{ASSET}/scene.tif", ASSET, "/scene.tif") == ""

    def test_without_the_manifest_the_key_is_searched(self):
        assert convert.relative_subdir(f"{ASSET}/maps/scene.tif", ASSET, "") == "maps"


@pytest.mark.unit
class TestTheCommandNamesTheLocation:
    def test_the_second_argument_names_it(self):
        location = _location("scene.tif")
        args = [json.dumps({"inputVersions": {}, "inputAsset": location})]
        assert objects.input_asset_location(args) == location

    @pytest.mark.parametrize("args", [
        [],
        [json.dumps({"inputVersions": {"COG_CONVERT": "v-1"}})],
        [json.dumps({"inputVersions": {}, "inputAsset": {"relativePath": "", "assetRootS3Key": ""}})],
        ["{not json"],
        [json.dumps({"inputVersions": {}, "inputAsset": "not a document"})],
    ])
    def test_an_older_lambdas_command_or_an_unreadable_one_names_none(self, args):
        assert objects.input_asset_location(args) is None

    def test_a_value_that_is_not_a_string_reads_as_empty(self):
        args = [json.dumps({"inputVersions": {}, "inputAsset": {"relativePath": "/a.tif", "assetRootS3Key": 7}})]
        assert objects.input_asset_location(args) == {
            "relativePath": "/a.tif", "assetRootS3Key": "", "assetRootBucket": ""}

    def test_the_version_document_still_applies(self):
        definition = _definition(EXISTING_FOLDER_KEY.format(name="scene.tif"))
        args = [json.dumps({"inputVersions": {"COG_CONVERT": "v-2"}, "inputAsset": _location("scene.tif")})]
        params = objects.apply_input_versions(definition, args)
        assert params["stages"][0]["inputFile"]["versionId"] == "v-2"


@pytest.mark.unit
class TestMainHandsTheLocationToTheRun:
    def _main(self, monkeypatch, argv):
        from cog_convert_pipeline import __main__ as entry

        seen = []

        def fake_run(params, input_asset=None):
            seen.append(input_asset)
            return objects.PipelineExecutionParams(
                jobName="job1", currentStageType="COG_CONVERT", definition=[], inputMetadata="",
                inputParameters="", status=objects.PipelineStatus.COMPLETE)

        monkeypatch.setattr(core, "run", fake_run)
        monkeypatch.setattr(entry.sys, "argv", argv)
        previous = signal.getsignal(signal.SIGTERM)
        try:
            assert entry.main() == 0
        finally:
            signal.signal(signal.SIGTERM, previous)
        return seen

    def test_the_new_command(self, monkeypatch):
        definition = json.dumps(_definition(EXISTING_FOLDER_KEY.format(name="scene.tif")))
        extra = json.dumps({"inputVersions": {}, "inputAsset": _location("scene.tif")})
        assert self._main(monkeypatch, ["prog", definition, extra]) == [_location("scene.tif")]

    def test_an_older_lambdas_command(self, monkeypatch):
        definition = json.dumps(_definition(f"{ASSET}/scene.tif"))
        assert self._main(monkeypatch, ["prog", definition]) == [None]


@pytest.mark.unit
class TestTheRunWritesBesideTheInput:
    def test_a_skipped_file_names_its_asset_path(self, world):
        # epsg3857.tif is already a COG in EPSG:3857, so the run writes only its skip note, without GDAL.
        fake, tokens = world
        key = EXISTING_FOLDER_KEY.format(name="epsg3857.tif")
        fake.put_source(key, fixture_path("epsg3857.tif"))
        out = core.run(_definition(key), input_asset=_location("epsg3857.tif"))
        assert out.status is objects.PipelineStatus.COMPLETE and tokens.failure == []
        note = json.loads(fake.objects[(BUCKET, "pipelines/p/results/cog-convert-skipped.json")])
        assert note["file"] == "/maps/north/epsg3857.tif"

    def test_without_the_location_the_key_is_searched(self, world):
        fake, _tokens = world
        key = EXISTING_FOLDER_KEY.format(name="epsg3857.tif")
        fake.put_source(key, fixture_path("epsg3857.tif"))
        core.run(_definition(key))
        note = json.loads(fake.objects[(BUCKET, "pipelines/p/results/cog-convert-skipped.json")])
        # The key holds no asset ID segment, so the file is named at the asset root.
        assert note["file"] == "/epsg3857.tif"

    @requires_gdal
    def test_the_cog_and_its_links_sit_in_the_inputs_folder(self, world):
        fake, tokens = world
        key = EXISTING_FOLDER_KEY.format(name="utm33.tif")
        fake.put_source(key, fixture_path("utm33.tif"))
        out = core.run(_definition(key, version="v-1"), input_asset=_location("utm33.tif"))
        assert out.status is objects.PipelineStatus.COMPLETE and tokens.failure == []
        assert fake.uploaded == ["pipelines/p/files/maps/north/utm33.tif.cog.tif"]
        cog_rows = _attrs(fake, "pipelines/p/metadata/maps/north/utm33.tif.cog.tif.attribute.json")
        assert cog_rows["cog_link_cogOf"] == "/maps/north/utm33.tif"
        assert _attrs(fake, "pipelines/p/metadata/maps/north/utm33.tif.attribute.json") == {
            "cog_link_cogFile": "/maps/north/utm33.tif.cog.tif"}
