# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Every step of a workflow leaves a nested asset's files out of a folder input (#1207 D9).

Step 1's manifest entry for a folder input (or the whole asset) carries `excludeLocations`, the stored
locations of other assets nested in the input asset's location under it. Steps 2+ get their manifests from the
interim step, which rebuilds the original inputs from the workflow-input rows and overlays the earlier steps'
outputs (`executionOutputs.resolve_manifest_input_files`). Both now keep the field: the interim step recomputes
it from the row's asset bucket, asset root and input key by the rule step 1 used
(`fileAttribution.folder_input_exclude_locations`), and the shadowing step carries it on both branches,
because the locations are keys in the asset's own bucket under its root, which an earlier step's output never
changes. So a Zip Archive step after another step archives the input asset's own files only, and a run over
the nested asset's own folder still archives that asset's files (leaving out an asset nested in it in turn).
"""

import importlib.util
import io
import os
import sys
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("ASSET_STORAGE_TABLE_NAME", "t-assets")

from tests.handlers.workflows.test_interimPipelineTracking import ipt  # noqa: E402
from tests.common.fileAttributionFakes import AttributionAssetTable, asset_row  # noqa: E402

al = sys.modules['common.assetLocations']
fa = sys.modules['common.fileAttribution']

BUCKET = "abkt"
RUN = "runbkt"
OUTER = asset_row("db-a", "outer", "reg-1", "p/outer/", BUCKET)
INNER = asset_row("db-b", "inner", "reg-1", "p/outer/inner/", BUCKET)
DEEP = asset_row("db-c", "deep", "reg-1", "p/outer/inner/deep/", BUCKET)
ROWS = [OUTER, INNER, DEEP]
OBJECTS = ["p/outer/a.glb", "p/outer/sub/b.glb", "p/outer/inner/i1.glb", "p/outer/inner/x/i2.glb",
           "p/outer/inner/deep/d1.glb"]
CONTAINER = Path(__file__).resolve().parents[4] / "backendPipelines" / "utility" / "zipArchive" / "container"


@pytest.fixture(autouse=True)
def _ready(monkeypatch):
    al.clear_nested_locations_cache()
    monkeypatch.setattr(al, "index_ready", lambda name: True)
    yield
    al.clear_nested_locations_cache()


def _row(asset, relative_folder="/"):
    root = asset["assetLocation"]["Key"]
    return {"inputAssetFileKey": "/" + root + relative_folder.lstrip("/"), "databaseId": asset["databaseId"],
            "assetId": asset["assetId"], "s3Bucket": BUCKET, "assetRootS3Key": root, "versionId": ""}


def _tables(input_rows, asset_table):
    inputs = MagicMock(query=MagicMock(return_value={"Items": list(input_rows)}))
    asset_name = ipt.get_table_name(ipt.ResourceKeys.ASSET_STORAGE_TABLE)
    return lambda name: asset_table if name == asset_name else inputs


@pytest.mark.unit
class TestTheRule:
    def test_a_folder_input_names_the_nested_locations_under_it(self):
        table = AttributionAssetTable(ROWS)
        assert fa.folder_input_exclude_locations(table, BUCKET, "p/outer/", "p/outer/") == [
            "p/outer/inner/", "p/outer/inner/deep/"]
        assert fa.folder_input_exclude_locations(table, BUCKET, "p/outer/inner/", "/p/outer/inner/") == [
            "p/outer/inner/deep/"]
        assert fa.folder_input_exclude_locations(table, BUCKET, "p/outer/", "p/outer/sub/") == []

    def test_a_file_input_names_none_and_reads_nothing(self):
        table = AttributionAssetTable(ROWS)
        assert fa.folder_input_exclude_locations(table, BUCKET, "p/outer/", "p/outer/a.glb") == []
        assert table.reads == []

    def test_before_the_index_is_ready_none(self, monkeypatch):
        monkeypatch.setattr(al, "index_ready", lambda name: False)
        assert fa.folder_input_exclude_locations(AttributionAssetTable(ROWS), BUCKET, "p/outer/", "p/outer/") == []


@pytest.mark.unit
class TestTheInterimStepRebuildsThem:
    def test_a_folder_row_carries_them_and_a_file_row_reads_nothing(self):
        table = AttributionAssetTable(ROWS)
        rows = [_row(OUTER), {**_row(OUTER), "inputAssetFileKey": "/p/outer/a.glb"}]
        with patch.object(ipt.dynamodb, "Table", side_effect=_tables(rows, table)):
            folder, single = ipt._get_original_input_entries("EXEC1")
        assert folder["excludeLocations"] == ["p/outer/inner/", "p/outer/inner/deep/"]
        assert "excludeLocations" not in single

    def test_an_earlier_steps_output_shadowing_an_entry_keeps_them(self):
        """The shadowed entry reads the run bucket; the locations stay keys in the asset's bucket under its
        root, which it keeps (assetRootS3Key, assetRootBucket). A new output file carries none."""
        original = ipt.er.build_manifest_entry("/a.glb", BUCKET, "p/outer/a.glb", "v1", database_id="db-a",
                                               asset_id="outer", asset_root_s3_key="p/outer/",
                                               exclude_locations=["p/outer/inner/"])
        folder = ipt.er.build_manifest_entry("/", BUCKET, "p/outer/", "", database_id="db-a", asset_id="outer",
                                             asset_root_s3_key="p/outer/", exclude_locations=["p/outer/inner/"])
        outputs = [{"key": "out/files/a.glb", "relativePath": "/a.glb", "versionId": "o1"},
                   {"key": "out/files/new.glb", "relativePath": "/new.glb", "versionId": "o2"}]
        shadowed, kept, new = ipt.eo.resolve_manifest_input_files(None, [original, folder], RUN, "out/files/",
                                                                  current_output_files=outputs)
        assert (shadowed["bucket"], shadowed["assetRootBucket"]) == (RUN, BUCKET)
        assert shadowed["excludeLocations"] == kept["excludeLocations"] == ["p/outer/inner/"]
        assert "excludeLocations" not in new


@pytest.fixture(scope="module")
def zip_archive():
    """The Zip Archive container's runner and its in-memory S3 double, loaded from the pipeline's source."""
    sys.path.insert(0, str(CONTAINER))
    spec = importlib.util.spec_from_file_location("zip_archive_container_test_runner",
                                                  CONTAINER / "tests" / "test_runner.py")
    helpers = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helpers)
    yield helpers.runner, helpers.FakeS3, helpers.OneClient
    sys.path.remove(str(CONTAINER))


def _second_step_archive(zip_archive, asset, relative_folder="/"):
    """Run step 2 of a workflow (step 1 wrote no file at an input's path) as the Zip Archive pipeline and
    return the archive's member names."""
    runner, FakeS3, OneClient = zip_archive
    captured = {}
    body = {"workflowExecutionId": "EXEC1", "workflowExecutionS3InputOutputBucket": RUN,
            "outputFilesPrefix": "pipelines/p1/EXEC1/files/",
            "nextPipelineManifestS3Key": "in/EXEC1/pipeline2/manifest.json"}
    with patch.object(ipt.dynamodb, "Table", side_effect=_tables([_row(asset, relative_folder)],
                                                                 AttributionAssetTable(ROWS))), \
            patch.object(ipt.s3c, "put_object", MagicMock(side_effect=lambda **kw: captured.update(
                {kw["Key"]: kw["Body"]}))), \
            patch.object(ipt.eo, "list_current_output_files", return_value=[]):
        ipt.prepare_next_pipeline(body)
    s3 = FakeS3()
    for key in OBJECTS:
        s3.add(key, key.encode(), bucket=BUCKET)
    s3.add(body["nextPipelineManifestS3Key"], captured[body["nextPipelineManifestS3Key"]], bucket=RUN)
    definition = {"mode": "archiveRun", "archiveRun": {
        "runManifest": {"bucket": RUN, "key": body["nextPipelineManifestS3Key"]},
        "assetId": asset["assetId"], "databaseId": asset["databaseId"], "outputBucket": RUN,
        "outputDir": "pipelines/p2/EXEC1/files/", "config": {"format": "zip"}}}
    result = runner.Job(definition, OneClient(s3), None, logger=lambda *a: None).run()
    with zipfile.ZipFile(io.BytesIO(s3.completed[(RUN, result["archiveKey"])])) as z:
        return sorted(z.namelist())


@pytest.mark.unit
class TestAZipArchiveSecondStep:
    def test_a_folder_holding_a_nested_asset_archives_the_assets_own_files_only(self, zip_archive):
        assert _second_step_archive(zip_archive, OUTER) == ["a.glb", "sub/b.glb"]

    def test_the_nested_assets_own_folder_still_archives_its_files(self, zip_archive):
        # The inner asset's own files are archived; the asset nested in it in turn is left out
        assert _second_step_archive(zip_archive, INNER) == ["i1.glb", "x/i2.glb"]

    def test_control_a_folder_with_nothing_nested_under_it(self, zip_archive):
        assert _second_step_archive(zip_archive, OUTER, "/sub/") == ["sub/b.glb"]
