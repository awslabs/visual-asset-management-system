# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""One WorkflowExecutionInputs row per (file, version).

A launch may select one file at two versions. Each version is its own input row (the resolved version is
appended to the sort key), so the execution details list both, a re-run launches both, and a terminal
handler rebuilds both versions' lock keys from the rows. A row written before the version was part of the
key carries the bare 'databaseId:assetId:/key' sort key; every reader keeps reading it, because readers
use the row's attributes and never parse the sort key.

The inputs table is moto's DynamoDB with the key schema storageBuilder-nestedStack.ts deploys, so a
collapse on the real key is what the tests observe."""

import contextlib
import json
import os

import boto3
import pytest
from moto import mock_aws
from unittest.mock import MagicMock, patch

for _name, _value in [
    ("WORKFLOW_EXECUTION_INPUTS_STORAGE_TABLE_NAME", "t-wf-inputs"),
    ("WORKFLOW_EXECUTION_STORAGE_TABLE_V2_NAME", "t-exec-v2"),
    ("PIPELINE_EXECUTIONS_STORAGE_TABLE_NAME", "t-pexec"),
    ("WORKFLOW_EXECUTION_CONFIGURATION_STORAGE_TABLE_NAME", "t-wf-cfg"),
    ("PIPELINE_EXECUTION_INPUT_FILES_STORAGE_TABLE_NAME", "t-pin-files"),
    ("PIPELINE_EXECUTION_INPUT_METADATA_STORAGE_TABLE_NAME", "t-pin-md"),
    ("PIPELINE_EXECUTION_INPUT_CONFIGURATION_STORAGE_TABLE_NAME", "t-pin-cfg"),
    ("PIPELINE_EXECUTION_OUTPUT_FILES_STORAGE_TABLE_NAME", "t-of"),
    ("PIPELINE_EXECUTION_OUTPUT_METADATA_STORAGE_TABLE_NAME", "t-om"),
    ("PIPELINE_EXECUTION_OUTPUT_RESULTS_STORAGE_TABLE_NAME", "t-or"),
    ("PIPELINE_EXECUTION_LOGS_STORAGE_TABLE_NAME", "t-logs"),
    ("EXECUTE_WORKFLOW_V2_LAMBDA_FUNCTION_NAME", "t-execv2"),
    ("S3_ASSETAUXILIARY_STORAGE_BUCKET", "t-aux"),
]:
    os.environ.setdefault(_name, _value)

# Shares the launch harness of the perInputFileVersion lock tests (it also registers the stubs the
# handler package needs at import).
from backend.tests.handlers.workflows.test_execute_per_input_file_version_lock import (  # noqa: E402
    _ASSET, _PIPELINE, _LockTable, _allow_enforcer, _event, _workflow, WF_DB, WF_ID, ewv2)
from backend.tests.workflowDeploymentStub import with_current_deployment  # noqa: E402
from backend.backend.handlers.workflows import executionService as es  # noqa: E402
from backend.backend.handlers.workflows.sfn import interimPipelineTracking as ipt  # noqa: E402

MOD = "backend.backend.handlers.workflows.executeWorkflow"
er = ewv2.er
el = ewv2.el
eo = ipt.eo
SORT_KEY = "databaseId:assetId:inputAssetFileKey"
REGION = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")

TWO_VERSIONS = {"inputFiles": [
    {"databaseId": "db1", "assetId": "a1", "relativeFileKey": "/f.glb", "versionId": "v1"},
    {"databaseId": "db1", "assetId": "a1", "relativeFileKey": "/f.glb", "versionId": "v2"},
]}


def _inputs_table(name):
    ddb = boto3.resource("dynamodb", region_name=REGION)
    table = ddb.create_table(
        TableName=name,
        KeySchema=[{"AttributeName": "workflowExecutionId", "KeyType": "HASH"},
                   {"AttributeName": SORT_KEY, "KeyType": "RANGE"}],
        AttributeDefinitions=[{"AttributeName": "workflowExecutionId", "AttributeType": "S"},
                              {"AttributeName": SORT_KEY, "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )
    table.wait_until_exists()
    return table


def _rows(table, execution_id):
    from boto3.dynamodb.conditions import Key
    return table.query(KeyConditionExpression=Key("workflowExecutionId").eq(execution_id))["Items"]


def _launch(inputs_table, restriction="none", body=TWO_VERSIONS, lock_table=None, user="user1",
            s3_checks=None):
    """Drive the execute handler through a launch whose S3 check resolves each requested version to
    itself (and an unversioned request to 'v-latest'). Every table but the inputs and lock tables is a
    stub. `s3_checks`, when given, collects the (key, versionId) of every S3 existence check."""
    lock_table = lock_table if lock_table is not None else _LockTable()

    def _exists(bucket, key, version_id):
        if s3_checks is not None:
            s3_checks.append((key, version_id))
        return True, "" if key.endswith("/") else (version_id or "v-latest")

    def _table(name):
        if name == ewv2.workflow_execution_inputs_table:
            return inputs_table
        if name == ewv2.workflow_execution_locks_table:
            return lock_table
        return MagicMock()

    with contextlib.ExitStack() as stack:
        for context in (
            patch(f"{MOD}._get_workflow",
                  return_value=with_current_deployment(_workflow(restriction), [_PIPELINE])),
            patch(f"{MOD}._get_pipeline", return_value=dict(_PIPELINE)),
            patch(f"{MOD}._get_asset", return_value=dict(_ASSET)),
            patch(f"{MOD}._default_run_bucket",
                  return_value={"bucketName": "run-bucket", "baseAssetsPrefix": ""}),
            patch(f"{MOD}._asset_bucket_details",
                  return_value={"bucketName": "asset-bucket", "baseAssetsPrefix": ""}),
            patch(f"{MOD}._input_exists_in_s3", side_effect=_exists),
            patch(f"{MOD}.CasbinEnforcer", return_value=_allow_enforcer()),
            patch(f"{MOD}.request_to_claims", return_value={"tokens": [user]}),
            patch(f"{MOD}.s3c"),
            patch(f"{MOD}.logger"),
            patch.object(ewv2.dynamodb, "Table", side_effect=_table),
        ):
            stack.enter_context(context)
        sfn = stack.enter_context(patch(f"{MOD}.sfn_client"))
        sfn.start_execution.return_value = {"executionArn": "arn:exec"}
        response = ewv2.lambda_handler(_event(body), MagicMock())
    assert response["statusCode"] == 200, response
    return json.loads(response["body"])["message"]["executionId"], lock_table


def _legacy_row(execution_id, key="/a1/f.glb", version_id="v-old"):
    """A row as written before the version was part of the sort key."""
    return {
        "workflowExecutionId": execution_id,
        SORT_KEY: f"db1:a1:{key}",
        "databaseId:assetId": "db1:a1",
        "databaseId": "db1", "assetId": "a1", "inputAssetFileKey": key,
        "s3Bucket": "asset-bucket", "assetRootS3Key": "a1/", "versionId": version_id,
        "executionStartDate": "2026-10-01T00:00:00Z", "workflowId": WF_ID, "workflowDatabaseId": WF_DB,
    }


def _rerun(table, execution_id):
    with patch.object(es.dynamodb, "Table", return_value=table), \
         patch.object(es, "get_pipeline_execution_rows", return_value=[]):
        return es._reconstruct_execute_request(execution_id, {}, {})


def _as_set(files):
    return sorted(json.dumps(f, sort_keys=True) for f in files)


@pytest.mark.integration
@mock_aws
class TestRerunReusesOnlyAPinnedVersion:
    """A launch records whether the caller named the version (versionPinned). A re-run reads a named
    version again and takes the current version of a file selected without one; a row written before
    the attribute existed re-runs on the current version."""

    def test_a_pinned_version_reruns_on_the_same_version(self):
        table = _inputs_table(ewv2.workflow_execution_inputs_table)
        body = {"inputFiles": [
            {"databaseId": "db1", "assetId": "a1", "relativeFileKey": "/f.glb", "versionId": "v1"}]}
        execution_id, _locks = _launch(table, body=body)
        assert [r["versionPinned"] for r in _rows(table, execution_id)] == [True]
        assert _rerun(table, execution_id)["inputFiles"] == [
            {"databaseId": "db1", "assetId": "a1", "relativeFileKey": "/f.glb", "versionId": "v1"}]

    def test_an_unpinned_file_reruns_on_the_current_version(self):
        table = _inputs_table(ewv2.workflow_execution_inputs_table)
        body = {"inputFiles": [{"databaseId": "db1", "assetId": "a1", "relativeFileKey": "/f.glb"}]}
        execution_id, _locks = _launch(table, body=body)
        rows = _rows(table, execution_id)
        assert [(r["versionId"], r["versionPinned"]) for r in rows] == [("v-latest", False)]
        assert _rerun(table, execution_id)["inputFiles"] == [
            {"databaseId": "db1", "assetId": "a1", "relativeFileKey": "/f.glb"}]

    def test_a_folder_selection_is_never_pinned(self):
        table = _inputs_table(ewv2.workflow_execution_inputs_table)
        body = {"inputFiles": [{"databaseId": "db1", "assetId": "a1", "relativeFileKey": "/sub/"}]}
        execution_id, _locks = _launch(table, body=body)
        assert [r["versionPinned"] for r in _rows(table, execution_id)] == [False]

    def test_a_legacy_row_without_the_attribute_reruns_on_the_current_version(self):
        table = _inputs_table(es.workflow_execution_inputs_table)
        table.put_item(Item=_legacy_row("e-legacy", version_id="v-old"))
        assert "versionPinned" not in _rows(table, "e-legacy")[0]
        assert _rerun(table, "e-legacy")["inputFiles"] == [
            {"databaseId": "db1", "assetId": "a1", "relativeFileKey": "/f.glb"}]

    def test_the_same_version_named_and_unnamed_is_one_pinned_row_in_either_order(self):
        for files in (
            [{"relativeFileKey": "/f.glb"}, {"relativeFileKey": "/f.glb", "versionId": "v-latest"}],
            [{"relativeFileKey": "/f.glb", "versionId": "v-latest"}, {"relativeFileKey": "/f.glb"}],
        ):
            table = _inputs_table(ewv2.workflow_execution_inputs_table)
            body = {"inputFiles": [dict(f, databaseId="db1", assetId="a1") for f in files]}
            execution_id, _locks = _launch(table, body=body)
            assert [(r["versionId"], r["versionPinned"]) for r in _rows(table, execution_id)] == [
                ("v-latest", True)]
            table.delete()

    def test_a_pinned_and_an_unpinned_version_of_one_file_rerun_each_their_way(self):
        table = _inputs_table(ewv2.workflow_execution_inputs_table)
        body = {"inputFiles": [
            {"databaseId": "db1", "assetId": "a1", "relativeFileKey": "/f.glb", "versionId": "v1"},
            {"databaseId": "db1", "assetId": "a1", "relativeFileKey": "/f.glb"},
        ]}
        execution_id, _locks = _launch(table, body=body)
        assert _as_set(_rerun(table, execution_id)["inputFiles"]) == _as_set([
            {"databaseId": "db1", "assetId": "a1", "relativeFileKey": "/f.glb", "versionId": "v1"},
            {"databaseId": "db1", "assetId": "a1", "relativeFileKey": "/f.glb"},
        ])

    def test_a_per_input_file_version_launch_reruns_on_both_pinned_versions(self):
        table = _inputs_table(ewv2.workflow_execution_inputs_table)
        execution_id, locks = _launch(table, restriction="perInputFileVersion")
        rows = _rows(table, execution_id)
        assert sorted((r["versionId"], r["versionPinned"]) for r in rows) == [("v1", True), ("v2", True)]
        acquired = [p["Item"]["lockKey"] for p in locks.puts]
        assert acquired == [el.build_lock_key(WF_DB, WF_ID, "db1", "a1", "/a1/f.glb", v)
                            for v in ("v1", "v2")]
        assert sorted(el.lock_keys_for_execution(_workflow("perInputFileVersion"), rows)) == sorted(acquired)
        assert _as_set(_rerun(table, execution_id)["inputFiles"]) == _as_set([
            {"databaseId": "db1", "assetId": "a1", "relativeFileKey": "/f.glb", "versionId": v}
            for v in ("v1", "v2")])

    def test_a_per_input_file_version_launch_without_a_version_reruns_on_the_current_version(self):
        table = _inputs_table(ewv2.workflow_execution_inputs_table)
        body = {"inputFiles": [{"databaseId": "db1", "assetId": "a1", "relativeFileKey": "/f.glb"}]}
        execution_id, _locks = _launch(table, restriction="perInputFileVersion", body=body)
        assert _rerun(table, execution_id)["inputFiles"] == [
            {"databaseId": "db1", "assetId": "a1", "relativeFileKey": "/f.glb"}]


@pytest.mark.integration
@mock_aws
class TestOnlyAChosenVersionIsPinned:
    """A version counts as pinned only when someone chose it: a version named in an API or CLI launch.
    A fileUpload trigger launch carries the uploaded version so the run reads it, but nobody chose it,
    so a re-run takes the file's current version."""

    def _trigger_body(self, version_id="v7"):
        return {
            "inputFiles": [{"databaseId": "db1", "assetId": "a1", "relativeFileKey": "/f.glb",
                            "versionId": version_id}],
            "outputAssetId": "a1", "outputDatabaseId": "db1", "triggerType": "fileUpload",
        }

    def test_a_trigger_launch_is_not_pinned_but_reads_its_launch_version(self):
        table = _inputs_table(ewv2.workflow_execution_inputs_table)
        checks = []
        execution_id, _locks = _launch(
            table, body=self._trigger_body(), user="SYSTEM_USER", s3_checks=checks)
        assert [(r["versionId"], r["versionPinned"]) for r in _rows(table, execution_id)] == [
            ("v7", False)]
        assert checks == [("a1/f.glb", "v7")]
        assert _rerun(table, execution_id)["inputFiles"] == [
            {"databaseId": "db1", "assetId": "a1", "relativeFileKey": "/f.glb"}]

    def test_a_trigger_launch_under_a_version_lock_reads_and_locks_its_launch_version(self):
        table = _inputs_table(ewv2.workflow_execution_inputs_table)
        execution_id, locks = _launch(
            table, restriction="perInputFileVersion", body=self._trigger_body(), user="SYSTEM_USER")
        assert [p["Item"]["lockKey"] for p in locks.puts] == [
            el.build_lock_key(WF_DB, WF_ID, "db1", "a1", "/a1/f.glb", "v7")]
        assert [r["versionPinned"] for r in _rows(table, execution_id)] == [False]

    def test_an_api_launch_naming_a_version_is_pinned(self):
        table = _inputs_table(ewv2.workflow_execution_inputs_table)
        body = {"inputFiles": [
            {"databaseId": "db1", "assetId": "a1", "relativeFileKey": "/f.glb", "versionId": "v7"}]}
        execution_id, _locks = _launch(table, body=body, user="user1")
        assert [r["versionPinned"] for r in _rows(table, execution_id)] == [True]
        assert _rerun(table, execution_id)["inputFiles"] == [
            {"databaseId": "db1", "assetId": "a1", "relativeFileKey": "/f.glb", "versionId": "v7"}]

    def test_a_user_launch_labelled_file_upload_with_a_version_stays_pinned(self):
        table = _inputs_table(ewv2.workflow_execution_inputs_table)
        execution_id, _locks = _launch(table, body=self._trigger_body(), user="user1")
        assert [r["versionPinned"] for r in _rows(table, execution_id)] == [True]

    def test_a_rerun_of_a_trigger_launch_is_itself_unpinned(self):
        table = _inputs_table(ewv2.workflow_execution_inputs_table)
        execution_id, _locks = _launch(table, body=self._trigger_body(), user="SYSTEM_USER")
        rerun_body = _rerun(table, execution_id)
        table.delete()
        table = _inputs_table(ewv2.workflow_execution_inputs_table)
        rerun_id, _locks = _launch(table, body={"inputFiles": rerun_body["inputFiles"]}, user="user1")
        assert [(r["versionId"], r["versionPinned"]) for r in _rows(table, rerun_id)] == [
            ("v-latest", False)]


@pytest.mark.unit
class TestSortKeyShape:
    def test_a_resolved_version_is_part_of_the_sort_key(self):
        rows = [er.build_workflow_execution_input_record(
            workflow_execution_id="e", database_id="db1", asset_id="a1", input_asset_file_key="a1/f.glb",
            execution_start_date="", workflow_id=WF_ID, workflow_database_id=WF_DB, version_id=v)
            for v in ("v1", "v2")]
        assert [r[SORT_KEY] for r in rows] == ["db1:a1:/a1/f.glb#v1", "db1:a1:/a1/f.glb#v2"]

    def test_a_versionless_input_keeps_the_legacy_key(self):
        row = er.build_workflow_execution_input_record(
            workflow_execution_id="e", database_id="db1", asset_id="a1", input_asset_file_key="a1/",
            execution_start_date="", workflow_id=WF_ID, workflow_database_id=WF_DB, version_id="")
        assert row[SORT_KEY] == "db1:a1:/a1/"


@pytest.mark.integration
@mock_aws
class TestLaunchRecordsEveryVersion:
    def test_two_versions_of_one_file_are_two_rows(self):
        table = _inputs_table(ewv2.workflow_execution_inputs_table)
        execution_id, _locks = _launch(table)
        rows = _rows(table, execution_id)
        assert sorted((r["inputAssetFileKey"], r["versionId"]) for r in rows) == [
            ("/a1/f.glb", "v1"), ("/a1/f.glb", "v2")]

    def test_latest_and_the_same_version_named_explicitly_are_one_row(self):
        table = _inputs_table(ewv2.workflow_execution_inputs_table)
        body = {"inputFiles": [
            {"databaseId": "db1", "assetId": "a1", "relativeFileKey": "/f.glb"},
            {"databaseId": "db1", "assetId": "a1", "relativeFileKey": "/f.glb", "versionId": "v-latest"},
        ]}
        execution_id, _locks = _launch(table, body=body)
        assert [r["versionId"] for r in _rows(table, execution_id)] == ["v-latest"]

    def test_a_per_input_file_version_launch_records_and_rebuilds_both_lock_keys(self):
        table = _inputs_table(ewv2.workflow_execution_inputs_table)
        execution_id, locks = _launch(table, restriction="perInputFileVersion")
        acquired = [p["Item"]["lockKey"] for p in locks.puts]
        assert acquired == [el.build_lock_key(WF_DB, WF_ID, "db1", "a1", "/a1/f.glb", v)
                            for v in ("v1", "v2")]
        rebuilt = el.lock_keys_for_execution(_workflow("perInputFileVersion"), _rows(table, execution_id))
        assert sorted(rebuilt) == sorted(acquired)


@pytest.mark.integration
@mock_aws
class TestReaders:
    """Every reader over one execution holding a legacy versionless-key row and two new versioned rows of
    another file, and over a legacy-only execution."""

    def _seed(self):
        table = _inputs_table(es.workflow_execution_inputs_table)
        table.put_item(Item=_legacy_row("e-mixed", key="/a1/old.glb"))
        for version in ("v1", "v2"):
            table.put_item(Item=er.build_workflow_execution_input_record(
                workflow_execution_id="e-mixed", database_id="db1", asset_id="a1",
                input_asset_file_key="a1/f.glb", execution_start_date="2026-11-03T00:00:00Z",
                workflow_id=WF_ID, workflow_database_id=WF_DB, s3_bucket="asset-bucket",
                asset_root_s3_key="a1/", version_id=version))
        table.put_item(Item=_legacy_row("e-legacy"))
        return table

    def test_paged_input_files_return_both_shapes_across_pages(self):
        table = self._seed()
        seen, token = [], None
        with patch.object(es.dynamodb, "Table", return_value=table):
            for _ in range(5):
                page = es.page_detail_files("e-mixed", "input", 1, token)
                seen.extend((i["inputAssetFileKey"], i["versionId"]) for i in page["Items"])
                token = page.get("NextToken")
                if not token:
                    break
        assert sorted(seen) == [("/a1/f.glb", "v1"), ("/a1/f.glb", "v2"), ("/a1/old.glb", "v-old")]
        assert set(page["Items"][0]) == {"databaseId", "assetId", "inputAssetFileKey", "versionId"}

    def test_the_asset_probe_finds_both_shapes(self):
        table = self._seed()
        assert es._execution_reads_asset(table, "e-legacy", "db1:a1") is True
        assert es._execution_reads_asset(table, "e-mixed", "db1:a1") is True
        assert es._execution_reads_asset(table, "e-mixed", "db1:a2") is False

    def test_a_rerun_takes_the_current_version_of_a_legacy_row(self):
        table = self._seed()
        rerun = _rerun(table, "e-legacy")
        assert rerun["inputFiles"] == [{"databaseId": "db1", "assetId": "a1", "relativeFileKey": "/f.glb"}]

    def test_the_later_step_manifest_reads_both_shapes(self):
        table = self._seed()
        with patch.object(ipt.dynamodb, "Table", return_value=table):
            entries = ipt._get_original_input_entries("e-mixed")
        assert sorted((e["relativePath"], e["versionId"]) for e in entries) == [
            ("/f.glb", "v1"), ("/f.glb", "v2"), ("/old.glb", "v-old")]

    def test_lock_keys_rebuild_from_a_legacy_row(self):
        table = self._seed()
        assert el.lock_keys_for_execution(_workflow("perInputFileVersion"), _rows(table, "e-legacy")) == [
            el.build_lock_key(WF_DB, WF_ID, "db1", "a1", "/a1/f.glb", "v-old")]

    def test_permanent_delete_removes_both_shapes(self):
        table = self._seed()
        with patch.object(es.dynamodb, "Table", return_value=table):
            es._delete_all_rows(es.workflow_execution_inputs_table,
                                es.Key("workflowExecutionId").eq("e-mixed"),
                                ["workflowExecutionId", SORT_KEY])
        assert _rows(table, "e-mixed") == []


@pytest.mark.unit
class TestShadowedVersionsAreListedOnce:
    def test_an_output_shadowing_a_two_version_path_is_one_entry(self):
        originals = [{"relativePath": "/f.glb", "databaseId": "db1", "assetId": "a1", "bucket": "b",
                      "key": "a1/f.glb", "versionId": v} for v in ("v1", "v2")]
        outputs = [{"relativePath": "/f.glb", "key": "out/f.glb", "versionId": "o1"}]
        resolved = eo.resolve_manifest_input_files(None, originals, "run", "out/", current_output_files=outputs)
        assert [(e["relativePath"], e["versionId"]) for e in resolved] == [("/f.glb", "o1")]

    def test_unshadowed_versions_stay_two_entries(self):
        originals = [{"relativePath": "/f.glb", "databaseId": "db1", "assetId": "a1", "bucket": "b",
                      "key": "a1/f.glb", "versionId": v} for v in ("v1", "v2")]
        resolved = eo.resolve_manifest_input_files(None, originals, "run", "out/", current_output_files=[])
        assert [e["versionId"] for e in resolved] == ["v1", "v2"]

    def test_two_assets_sharing_a_shadowed_path_stay_two_entries(self):
        originals = [{"relativePath": "/f.glb", "databaseId": db, "assetId": asset, "bucket": "b",
                      "key": f"{asset}/f.glb", "versionId": "v1",
                      "auxPreviewPrefix": f"aux/{asset}/"} for db, asset in (("db1", "a1"), ("db2", "a2"))]
        outputs = [{"relativePath": "/f.glb", "key": "out/f.glb", "versionId": "o1"}]
        resolved = eo.resolve_manifest_input_files(None, originals, "run", "out/", current_output_files=outputs)
        assert [(e["databaseId"], e["assetId"], e["versionId"], e["auxPreviewPrefix"]) for e in resolved] == [
            ("db1", "a1", "o1", "aux/a1/"), ("db2", "a2", "o1", "aux/a2/")]
