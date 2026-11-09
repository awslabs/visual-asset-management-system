# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""#1125 D9: the SHIPPED zipArchive bundle, registered by the REAL importer, accepts every selection
shape a zip download or a Zip Archive run launches, through the REAL execute handler.

The live smoke found every folder, whole-asset, multi-file and asset-version zip download refused at
launch (create answered 500, the job FAILED) and Zip Archive runs on a folder or the whole asset
refused the same way: backendPipelines/utility/zipArchive/vamsSchema/workflow.json declared no
systemConfig, so the importer stored the workflow defaults (one input file, no folders, no whole
asset) and executeWorkflow's cross-entity validation refused the launch. Every earlier test wrote the
workflow's systemConfig by hand, so none saw it.

Here nothing about the workflow or pipeline is written by hand (zipBundle.registered_records), the
launch is not mocked (the zip service's execute invoke runs executeWorkflow.lambda_handler, which
runs executionValidation.validate_execution), and each shape is the one the zip service actually
sends (_launch: the selected paths, or '/' for an asset-version pin or more than 1000 entries)."""

import json
import sys
import types

import pytest

from backend.backend.common.workflows import executionValidation as ev
from backend.backend.common.workflows import workflowRecords as wr
from backend.tests.handlers.zipDownloads import test_zip_global_cross_calls_end_to_end as e2e
from backend.tests.handlers.zipDownloads import zipBundle as zb
from backend.tests.handlers.zipDownloads import zipHarness as h

pytestmark = pytest.mark.unit

PIPELINE, WORKFLOW = zb.registered_records()
env = e2e.env  # the fixture: a zip service whose execute invoke runs the real execute handler

SHAPES = {
    "single-file": {"selection": [{"type": "file", "relativePath": "/a.glb"}]},
    "multiple-files": {"selection": [{"type": "file", "relativePath": "/a.glb"},
                                     {"type": "file", "relativePath": "/textures/t1.png"}]},
    "folder": {"selection": [{"type": "folder", "relativePath": "/textures/"}]},
    "folders-and-files": {"selection": [{"type": "folder", "relativePath": "/textures/"},
                                        {"type": "file", "relativePath": "/a.glb"}]},
    "whole-asset": {"selection": [{"type": "asset", "relativePath": "/"}]},
    "per-file-version": {"selection": [{"type": "file", "relativePath": "/a.glb", "versionId": "va1"}]},
    "asset-version": {"selection": [{"type": "asset", "relativePath": "/"}], "assetVersionId": "3"},
    "asset-version-folder": {"selection": [{"type": "folder", "relativePath": "/textures/"}],
                             "assetVersionId": "3"},
}


@pytest.fixture
def versions(monkeypatch):
    """The asset version snapshot an asset-version pin resolves against."""
    fake = types.ModuleType("handlers.assets.assetVersions")
    fake.validate_asset_version_exists = lambda db, a, v: True
    fake.get_asset_file_versions = lambda db, a, v: {"files": [
        {"relativeKey": "/a.glb", "versionId": "va1", "size": 100},
        {"relativeKey": "/textures/t1.png", "versionId": "vt1", "size": 20}]}
    fake.resolve_asset_version_id_from_alias = lambda db, a, alias: "3"
    monkeypatch.setitem(sys.modules, "handlers.assets.assetVersions", fake)


class TestTheRegisteredRecords:
    def test_the_workflow_declares_its_own_system_config(self):
        declared = zb.load_bundle()["workflow"].get("systemConfig")
        assert declared, "workflow.json must declare systemConfig; the defaults refuse every archive"

    def test_the_stored_workflow_accepts_many_files_folders_and_the_whole_asset(self):
        config = WORKFLOW["systemConfig"]
        assert config["inputFileArity"] == "multi"
        assert config["assetScope"]["folderAllowed"] is True
        assert config["assetScope"]["wholeAssetAllowed"] is True
        assert config["assetScope"]["singleAssetOnly"] is True
        assert config["assetScope"]["crossAssetAllowed"] is False
        # Any file type: the archive's own checks refuse blocked types.
        assert config["inputFileFilters"] == {"allow": [], "exclude": []}

    def test_concurrency_is_unrestricted_on_purpose(self):
        # perAsset would make a user's zip downloads of one asset (and other users') wait for each
        # other; the pipeline only reads its inputs and each job writes its own key.
        assert WORKFLOW["systemConfig"]["concurrencyRestriction"] == "none"

    def test_a_zip_archive_run_writes_into_its_input_asset(self):
        assert WORKFLOW["systemConfig"]["outputTarget"] == {"locationType": "asset",
                                                            "allowOverride": False}

    def test_the_update_on_redeploy_stores_the_same_system_config(self):
        # An upgrade (and the existing demo rows) goes through the update body.
        requests = zb.import_requests()
        assert requests["workflow"]["updateBody"]["systemConfig"] == \
            requests["workflow"]["createBody"]["systemConfig"]
        assert requests["workflow"]["createBody"]["systemConfig"] == WORKFLOW["systemConfig"]

    def test_the_defaults_are_what_refused_every_archive(self):
        """The regression this guards: without the declaration the importer stores the defaults,
        and those refuse each shape below."""
        defaults = wr.build_workflow_system_config()
        inputs = [{"databaseId": "db1", "assetId": "a1", "relativeFileKey": "/textures/"},
                  {"databaseId": "db1", "assetId": "a1", "relativeFileKey": "/"}]
        assert ev.workflow_selection_errors(defaults, inputs)

    def test_the_workflow_saves_cleanly_with_its_pipeline(self):
        errors, warnings = ev.validate_workflow_save(WORKFLOW["systemConfig"], [PIPELINE])
        assert errors == [] and warnings == []


@pytest.mark.parametrize("paths", [
    ["/a.glb"], ["/a.glb", "/b.glb"], ["/textures/"], ["/textures/", "/a.glb"], ["/"],
    ["/f%d.glb" % i for i in range(1000)],
], ids=["one-file", "two-files", "folder", "folder-and-file", "whole-asset", "1000-files"])
def test_the_cross_entity_validation_accepts_each_launch_shape(paths):
    inputs = [{"databaseId": "db1", "assetId": "a1", "relativeFileKey": p} for p in paths]
    errors, _ = ev.validate_execution(WORKFLOW["systemConfig"], [dict(PIPELINE, pipelineDatabaseId="GLOBAL")],
                                      inputs)
    assert errors == []


@pytest.mark.parametrize("shape", list(SHAPES))
def test_each_zip_download_shape_launches_through_the_execute_handler(env, versions, shape):
    response = e2e._create(env, SHAPES[shape])
    assert env.lam.responses, "the zip service never reached the execute handler"
    launch = env.lam.responses[0]
    assert launch["statusCode"] == 200, launch["body"]
    assert response["statusCode"] == 200, response["body"]
    row = next(iter(env.zip.items.values()))
    assert row["status"] == "QUEUED" and row["workflowExecutionId"]


def test_the_largest_selection_launches_through_the_execute_handler(env):
    """The API takes up to 1000 selection entries; each is one input of the launch."""
    from backend.backend.models.assetsV3 import ZIP_MAX_SELECTION_ENTRIES as MAX_SELECTION_ENTRIES
    for i in range(MAX_SELECTION_ENTRIES):
        env.s3.add(f"xasset1/many/f{i}.bin", size=1, version=f"v{i}")
    selection = [{"type": "file", "relativePath": f"/many/f{i}.bin"} for i in range(MAX_SELECTION_ENTRIES)]
    response = e2e._create(env, {"selection": selection})
    assert len(json.loads(env.lam.events[0]["body"])["inputFiles"]) == MAX_SELECTION_ENTRIES
    assert env.lam.responses[0]["statusCode"] == 200, env.lam.responses[0]["body"]
    assert response["statusCode"] == 200


@pytest.mark.parametrize("paths", [["/textures/"], ["/"], ["/a.glb", "/b.glb"], ["/textures/", "/a.glb"]],
                         ids=["folder", "whole-asset", "two-files", "folder-and-file"])
def test_a_zip_archive_run_launches_on_the_shape(paths):
    """Mode 2: an ordinary (user) run of the registered workflow, through the real execute handler."""
    from unittest.mock import MagicMock, patch
    tew = e2e.tew
    event = tew._event(body={"inputFiles": [{"databaseId": "db1", "assetId": "a1", "relativeFileKey": p}
                                            for p in paths]},
                       path_params={"workflowDatabaseId": "GLOBAL", "workflowId": zb.WORKFLOW_ID})
    event["requestContext"]["http"]["path"] = f"/workflows/GLOBAL/{zb.WORKFLOW_ID}/execute"
    p = tew.TestExecuteOrchestration()._patches(workflow=WORKFLOW, pipeline=PIPELINE)
    with p["get_workflow"], p["get_pipeline"], p["get_asset"], p["default_bucket"], \
            p["asset_bucket"], p["exists"], p["enforcer"], p["claims"], \
            patch(f"{e2e.EW}.s3c"), patch(f"{e2e.EW}.sfn_client") as sfn, \
            patch(f"{e2e.EW}.dynamodb") as dynamo:
        sfn.start_execution.return_value = {"executionArn": "arn:exec"}
        dynamo.Table.return_value = MagicMock()
        response = e2e.ewv2.lambda_handler(event, MagicMock())
    assert response["statusCode"] == 200, response["body"]
    sfn.start_execution.assert_called_once()
