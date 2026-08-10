# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the v2.5 -> v2.6 migration record builders.

Run from this directory:  python -m pytest test_v2_5_to_v2_6_migration.py -q

The migration module's filename is not a valid python identifier, so it is loaded by path. Several
tests feed an emitted row to the BACKEND reader that consumes it (the re-run key reconstruction, the
trigger file-filter matcher, the template body-storage plan), so an emitted shape is proved against
the code that reads it rather than against a restatement of it.
"""

import importlib.util
import json
import os
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_MODULE_PATH = os.path.join(_HERE, "v2.5_to_v2.6_migration.py")

sys.path.insert(0, os.path.join(_HERE, "..", "..", "..", "..", "backend", "backend"))
from common.workflows import templateBodyStorage as _tbs  # noqa: E402
from common.workflows.executionValidation import (  # noqa: E402
    apply_input_file_filters as _apply_input_file_filters,
)


def _to_asset_relative_key(full_key, asset_root_s3_key):
    """The v2.6 re-run conversion (executionService._to_asset_relative_key), restated here because
    importing executionService pulls in its module-level SSM resource-name resolution."""
    fk = "/" + (full_key or "").lstrip("/")
    root = (asset_root_s3_key or "").strip("/")
    if root:
        body = fk.lstrip("/")
        if body == root or body == root + "/":
            return "/"
        if body.startswith(root + "/"):
            return "/" + body[len(root) + 1:]
    return fk


def _load_module():
    spec = importlib.util.spec_from_file_location("v2_5_to_v2_6_migration", _MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mig = _load_module()


class FakeDynamoClient:
    """Minimal DynamoDB client double: canned scan/get results per table, recorded batch writes."""

    def __init__(self, tables):
        self.tables = tables  # table name -> list of wire-format items
        self.writes = {}      # table name -> list of written items

    def scan(self, **kwargs):
        return {"Items": list(self.tables.get(kwargs["TableName"], []))}

    def get_item(self, TableName, Key):
        for item in self.tables.get(TableName, []):
            if all(item.get(attr) == value for attr, value in Key.items()):
                return {"Item": item}
        return {}

    def batch_write_item(self, RequestItems):
        for table_name, requests in RequestItems.items():
            written = self.writes.setdefault(table_name, [])
            for request in requests:
                written.append(request["PutRequest"]["Item"])
        return {"UnprocessedItems": {}}

    def put_item(self, TableName, Item):
        self.writes.setdefault(TableName, []).append(Item)
        return {}


class FakeS3Client:
    """Minimal S3 client double: records every put_object body by key."""

    def __init__(self):
        self.objects = {}

    def put_object(self, Bucket, Key, Body):
        self.objects[(Bucket, Key)] = Body
        return {}


_EXEC_CFG = {
    "workflow_executions_storage_table_name_v1": "legacy",
    "workflow_executions_storage_table_name_v2": "mainV2",
    "workflow_execution_inputs_storage_table_name": "inputs",
    "pipeline_executions_storage_table_name": "pexec",
    "pipeline_execution_input_files_storage_table_name": "pinFiles",
    "workflow_storage_table_name": "workflowV1",
    "workflow_execution_configuration_storage_table_name": "wfConfig",
    "pipeline_execution_input_configuration_storage_table_name": "pexecConfig",
    "asset_storage_table_name": "assets",
    "s3_asset_buckets_storage_table_name": "buckets",
}


def _asset_row(database_id="db1", asset_id="asset1", location_key="myasset/", bucket_id="b1"):
    return {
        "databaseId": {"S": database_id},
        "assetId": {"S": asset_id},
        "bucketId": {"S": bucket_id},
        "assetLocation": {"M": {"Key": {"S": location_key}}},
    }


def _bucket_row(bucket_id="b1", bucket_name="vams-assets", prefix="", is_default=True):
    return {
        "bucketId": {"S": bucket_id},
        "bucketName": {"S": bucket_name},
        "baseAssetsPrefix": {"S": prefix},
        "isDefault": {"BOOL": is_default},
    }


def _legacy_execution(execution_id, start_date):
    return {
        "executionId": {"S": execution_id},
        "databaseId": {"S": "db1"},
        "assetId": {"S": "asset1"},
        "workflowId": {"S": "wf1"},
        "workflowDatabaseId": {"S": "db1"},
        "workflow_arn": {"S": "arn:aws:states:us-east-1:1:stateMachine:vams-wf1"},
        "execution_arn": {"S": "arn:aws:states:us-east-1:1:execution:vams-wf1:e"},
        "inputAssetFileKey": {"S": "model.glb"},
        "startDate": {"S": start_date},
        "stopDate": {"S": ""},
        "executionStatus": {"S": "RUNNING"},
    }


def _workflow_v1_with_two_pipelines():
    return {
        "workflowId": {"S": "wf1"},
        "databaseId": {"S": "db1"},
        "specifiedPipelines": {"M": {"functions": {"L": [
            {"M": {"name": {"S": "p1"}, "databaseId": {"S": "db1"}}},
            {"M": {"name": {"S": "p2"}, "databaseId": {"S": "db1"}}},
        ]}}},
    }


def _run_executions(legacy_rows, workflow_rows=None, asset_rows=None, bucket_rows=None, cfg=None):
    client = FakeDynamoClient({
        "legacy": legacy_rows,
        "workflowV1": workflow_rows if workflow_rows is not None else [_workflow_v1_with_two_pipelines()],
        "assets": asset_rows if asset_rows is not None else [_asset_row()],
        "buckets": bucket_rows if bucket_rows is not None else [_bucket_row()],
    })
    counts, total = mig.migrate_workflow_executions(
        client, cfg if cfg is not None else _EXEC_CFG, dry_run=False, limit=None)
    return client, counts, total


class TestExecutionStartDateIsSparse:
    """executionStartDate is the SK of four GSIs; DynamoDB rejects an empty indexed key attribute."""

    def test_main_and_inputs_rows_omit_an_empty_start_date(self):
        client, counts, _total = _run_executions([_legacy_execution("e1", "")])

        main_row = client.writes["mainV2"][0]
        inputs_row = client.writes["inputs"][0]
        assert "executionStartDate" not in main_row
        assert "executionStartDate" not in inputs_row
        assert counts["no_start_date"] == 1

    def test_main_and_inputs_rows_carry_a_converted_start_date(self):
        client, counts, _total = _run_executions(
            [_legacy_execution("e1", "01/15/2026, 10:30:00")])

        assert client.writes["mainV2"][0]["executionStartDate"] == {"S": "2026-01-15T10:30:00Z"}
        assert client.writes["inputs"][0]["executionStartDate"] == {"S": "2026-01-15T10:30:00Z"}
        assert counts["no_start_date"] == 0

    def test_no_written_row_carries_an_empty_indexed_key_attribute(self):
        client, _counts, _total = _run_executions([_legacy_execution("e1", "")])

        for table in ("mainV2", "inputs"):
            for row in client.writes[table]:
                assert row.get("executionStartDate") != {"S": ""}


class TestPipelineExecutionChainKeyIsSparse:
    """from_pipeline_execution_id is the PipelineExecChainGSI SK; the first pipeline has no parent."""

    def test_first_pipeline_row_omits_the_chain_key_and_later_rows_set_it(self):
        client, _counts, _total = _run_executions([_legacy_execution("e1", "")])

        rows = client.writes["pexec"]
        assert len(rows) == 2
        assert "from_pipeline_execution_id" not in rows[0]
        assert rows[1]["from_pipeline_execution_id"] == rows[0]["pipelineExecutionId"]

    def test_deleted_pipeline_fallback_row_omits_the_chain_key(self):
        client, _counts, _total = _run_executions([_legacy_execution("e1", "")], workflow_rows=[])

        rows = client.writes["pexec"]
        assert len(rows) == 1
        assert rows[0]["pipelineId"] == {"S": "DELETED"}
        assert "from_pipeline_execution_id" not in rows[0]


class TestExecutionConfigurationSnapshot:
    """The detail view reads the workflow configuration row for an execution's output target, and
    re-run reads the per-pipeline configuration rows for template parameters."""

    def test_workflow_config_row_targets_the_legacy_input_asset(self):
        client, counts, _total = _run_executions([_legacy_execution("e1", "")])

        row = client.writes["wfConfig"][0]
        assert row["workflowExecutionId"] == {"S": "e1"}
        assert row["recordType"] == {"S": "configuration"}
        assert row["outputLocationType"] == {"S": "asset"}
        assert row["outputAssetId"] == {"S": "asset1"}
        assert row["outputDatabaseId"] == {"S": "db1"}
        assert row["outputFileBaseExecutionPathExtension"] == {"S": "/"}
        assert counts["wf_config"] == 1

    def test_workflow_config_row_carries_the_output_asset_index_key(self):
        """WorkflowExecConfigByOutputAssetGSI is SPARSE, so a row omitting its partition attribute is
        absent from the index entirely — the migrated execution would then be missing from its own
        output asset's execution history, with the output-target fields still looking correct."""
        client, _counts, _total = _run_executions([_legacy_execution("e1", "")])

        row = client.writes["wfConfig"][0]
        assert row["outputDatabaseId:outputAssetId"] == {"S": "db1:asset1"}
        # Must agree with the target fields it indexes; a mismatch indexes the run under another asset.
        assert row["outputDatabaseId:outputAssetId"] == {
            "S": f"{row['outputDatabaseId']['S']}:{row['outputAssetId']['S']}"}

    def test_pipeline_config_row_per_pipeline_execution(self):
        client, counts, _total = _run_executions([_legacy_execution("e1", "")])

        pexec_ids = [r["pipelineExecutionId"] for r in client.writes["pexec"]]
        cfg_ids = [r["pipelineExecutionId"] for r in client.writes["pexecConfig"]]
        assert cfg_ids == pexec_ids
        assert counts["pexec_config"] == 2

    def test_consolidated_builtin_pipeline_config_carries_its_template(self):
        client, _counts, _total = _run_executions(
            [_legacy_execution("e1", "")],
            workflow_rows=[{
                "workflowId": {"S": "wf1"},
                "databaseId": {"S": "db1"},
                "specifiedPipelines": {"M": {"functions": {"L": [
                    {"M": {"name": {"S": "conversion-3d-basic-to-obj"},
                           "databaseId": {"S": "GLOBAL"}}},
                ]}}},
            }])

        assert client.writes["pexecConfig"][0]["templateId"] == {"S": "convert-to-obj"}

    def test_snapshot_rows_are_skipped_when_the_tables_are_unconfigured(self):
        cfg = {k: v for k, v in _EXEC_CFG.items() if 'configuration_storage' not in k}
        client = FakeDynamoClient({
            "legacy": [_legacy_execution("e1", "")],
            "workflowV1": [_workflow_v1_with_two_pipelines()],
        })
        counts, _total = mig.migrate_workflow_executions(client, cfg, dry_run=False, limit=None)

        assert "wfConfig" not in client.writes
        assert counts["wf_config"] == 0
        assert counts["errors"] == 0


class TestExecutionPipelineIdRemap:
    def test_consolidated_builtin_pipeline_id_is_rewritten_on_the_execution_row(self):
        client, _counts, _total = _run_executions(
            [_legacy_execution("e1", "")],
            workflow_rows=[{
                "workflowId": {"S": "wf1"},
                "databaseId": {"S": "db1"},
                "specifiedPipelines": {"M": {"functions": {"L": [
                    {"M": {"name": {"S": "conversion-3d-basic-to-obj"},
                           "databaseId": {"S": "GLOBAL"}}},
                ]}}},
            }])

        row = client.writes["pexec"][0]
        assert row["pipelineId"] == {"S": "conversion-3d-basic"}
        assert row["pipelineDatabaseId:pipelineId"] == {"S": "GLOBAL:conversion-3d-basic"}


class TestBatchWriteFallsBackPerItem:
    """A ValidationException fails the whole batch_write_item request, so the remaining valid rows
    in the chunk must still be written one at a time."""

    def test_one_invalid_row_does_not_discard_the_rest_of_its_chunk(self):
        from botocore.exceptions import ClientError

        class RejectingBatchClient(FakeDynamoClient):
            def batch_write_item(self, RequestItems):
                raise ClientError(
                    {"Error": {"Code": "ValidationException", "Message": "bad key"}},
                    "BatchWriteItem")

            def put_item(self, TableName, Item):
                if Item.get("bad") == {"BOOL": True}:
                    raise ClientError(
                        {"Error": {"Code": "ValidationException", "Message": "bad key"}}, "PutItem")
                return super().put_item(TableName=TableName, Item=Item)

        client = RejectingBatchClient({})
        batch = [{"workflowExecutionId": {"S": f"e{i}"}} for i in range(3)]
        batch[1]["bad"] = {"BOOL": True}

        written, errors = mig.flush_batch_write(client, "mainV2", batch, dry_run=False)

        assert written == 2
        assert errors == 1
        assert [r["workflowExecutionId"] for r in client.writes["mainV2"]] == [{"S": "e0"}, {"S": "e2"}]


class TestAuxPreviewKeyMapping:
    def test_location_key_whose_first_segment_is_a_database_id_still_relocates(self):
        index = {"db1/myasset/": "db1"}
        old_key = "db1/myasset/scan.laz/preview/PotreeViewer/metadata.json"

        assert mig._new_aux_preview_key(old_key, index) == f"db1/{old_key}"

    def test_an_already_relocated_key_is_skipped(self):
        index = {"myasset/": "db1"}
        relocated = "db1/myasset/scan.laz/preview/PotreeViewer/metadata.json"

        assert mig._new_aux_preview_key(relocated, index) is None

    def test_longest_matching_location_base_wins(self):
        index = {"base/": "db1", "base/nested/": "db2"}
        old_key = "base/nested/model.glb/preview/thumb.png"

        assert mig._new_aux_preview_key(old_key, index) == f"db2/{old_key}"

    def test_reserved_and_non_preview_keys_are_skipped(self):
        index = {"myasset/": "db1"}

        assert mig._new_aux_preview_key("pipelines/run/out.glb", index) is None
        assert mig._new_aux_preview_key("myasset/model.glb", index) is None


class TestAssetLocationIndexCollision:
    def test_a_location_key_shared_across_databases_keeps_the_first_database(self):
        client = FakeDynamoClient({"assets": [
            {"databaseId": {"S": "db1"},
             "assetLocation": {"M": {"Key": {"S": "myasset/"}}}},
            {"databaseId": {"S": "db2"},
             "assetLocation": {"M": {"Key": {"S": "myasset/"}}}},
        ]})

        index, database_ids = mig._build_asset_location_index(client, "assets")

        assert index == {"myasset/": "db1"}
        assert database_ids == {"db1", "db2"}


_DEF_CFG = {
    "pipeline_storage_table_name_v1": "pipelineV1",
    "pipeline_storage_table_name_v2": "pipelineV2",
    "pipeline_templates_storage_table_name": "templates",
    "workflow_storage_table_name": "workflowV1",
    "workflow_storage_table_name_v2": "workflowV2",
    "workflow_triggers_storage_table_name": "triggers",
    "pipeline_template_body_bucket_name": "vams-assets",
}


def _v1_pipeline(pipeline_id, input_parameters=""):
    row = {
        "databaseId": {"S": "db1"},
        "pipelineId": {"S": pipeline_id},
        "name": {"S": pipeline_id},
        "pipelineType": {"S": "standardFile"},
        "pipelineExecutionType": {"S": "Lambda"},
        "assetType": {"S": ".glb"},
    }
    if input_parameters:
        row["inputParameters"] = {"S": input_parameters}
    return row


def _v1_workflow(pipeline_ids):
    return {
        "databaseId": {"S": "db1"},
        "workflowId": {"S": "wf1"},
        "workflow_arn": {"S": "arn:aws:states:us-east-1:1:stateMachine:vams-wf1"},
        "specifiedPipelines": {"M": {"functions": {"L": [
            {"M": {"name": {"S": pid}, "databaseId": {"S": "db1"}}} for pid in pipeline_ids
        ]}}},
    }


def _run_definitions(pipelines, workflows, existing_v2_workflows=None, cfg=None, s3_client=None):
    client = FakeDynamoClient({
        "pipelineV1": pipelines,
        "workflowV1": workflows,
        "workflowV2": existing_v2_workflows or [],
    })
    counts, totals = mig.migrate_pipeline_workflow_definitions(
        client, cfg if cfg is not None else _DEF_CFG, dry_run=False, limit=None,
        s3_client=s3_client if s3_client is not None else FakeS3Client())
    return client, counts, totals


class TestMigratedTemplateIsApplied:
    def test_migrated_template_is_flagged_as_the_pipeline_default(self):
        client, counts, _totals = _run_definitions(
            [_v1_pipeline("p1", '{"quality": "high"}')], [])

        template = client.writes["templates"][0]
        assert template["templateId"] == {"S": "migrated-default"}
        assert template["isDefault"] == {"BOOL": True}
        assert template["configBody"] == {"S": '{"quality": "high"}'}
        assert counts["templates"] == 1

    def test_workflow_ref_points_at_the_migrated_template(self):
        client, _counts, _totals = _run_definitions(
            [_v1_pipeline("p1", '{"quality": "high"}'), _v1_pipeline("p2")],
            [_v1_workflow(["p1", "p2"])])

        refs = client.writes["workflowV2"][0]["specifiedPipelines"]["L"]
        assert refs[0]["M"]["defaultTemplateId"] == {"S": "migrated-default"}
        # A pipeline with no V1 inputParameters has no migrated template to point at.
        assert refs[1]["M"]["defaultTemplateId"] == {"S": ""}

    def test_consolidated_builtin_reference_keeps_its_per_format_template(self):
        client, _counts, _totals = _run_definitions([], [{
            "databaseId": {"S": "db1"},
            "workflowId": {"S": "wf1"},
            "specifiedPipelines": {"M": {"functions": {"L": [
                {"M": {"name": {"S": "conversion-3d-basic-to-obj"},
                       "databaseId": {"S": "GLOBAL"}}},
            ]}}},
        }])

        ref = client.writes["workflowV2"][0]["specifiedPipelines"]["L"][0]["M"]
        assert ref["pipelineId"] == {"S": "conversion-3d-basic"}
        assert ref["defaultTemplateId"] == {"S": "convert-to-obj"}


class TestMigratedDefinitionDates:
    """dateModified is the by-date GSI sort key, so the V1 creation date is carried over rather than
    collapsing every migrated row onto the migration timestamp."""

    def test_the_v1_date_created_is_carried_over(self):
        row = _v1_pipeline("p1", '{"quality": "high"}')
        row["dateCreated"] = {"S": '"January 15 2026 - 10:30:00"'}
        client, _counts, _totals = _run_definitions([row], [])

        pipeline_row = client.writes["pipelineV2"][0]
        assert pipeline_row["dateCreated"] == {"S": "2026-01-15T10:30:00Z"}
        assert pipeline_row["dateModified"] == {"S": "2026-01-15T10:30:00Z"}
        assert client.writes["templates"][0]["dateCreated"] == {"S": "2026-01-15T10:30:00Z"}

    def test_an_unparseable_v1_date_falls_back_to_the_migration_timestamp(self):
        row = _v1_pipeline("p1")
        row["dateCreated"] = {"S": "not a date"}
        client, _counts, _totals = _run_definitions([row], [])

        # Falls back to the run's ISO-8601 timestamp rather than storing the unparseable value.
        assert client.writes["pipelineV2"][0]["dateCreated"]["S"].endswith("Z")
        assert client.writes["pipelineV2"][0]["dateCreated"]["S"] != "not a date"

    def test_a_missing_v1_date_falls_back_to_the_migration_timestamp(self):
        client, _counts, _totals = _run_definitions([_v1_pipeline("p1")], [])

        assert client.writes["pipelineV2"][0]["dateCreated"]["S"].endswith("Z")


class TestMigratedExecutionStatusVocabulary:
    def test_the_v1_complete_status_becomes_succeeded(self):
        legacy = _legacy_execution("e1", "01/15/2026, 10:30:00")
        legacy["executionStatus"] = {"S": "COMPLETE"}
        client, _counts, _total = _run_executions([legacy])

        assert client.writes["mainV2"][0]["executionStatus"] == {"S": "SUCCEEDED"}
        assert client.writes["pexec"][0]["executionStatus"] == {"S": "SUCCEEDED"}

    def test_migrated_executions_use_the_reserved_system_identity(self):
        client, _counts, _total = _run_executions([_legacy_execution("e1", "")])

        assert client.writes["mainV2"][0]["triggeredByUserId"] == {"S": "SYSTEM_USER"}

    def test_a_non_terminal_row_converges_to_a_terminal_status_and_stop_date(self):
        # _legacy_execution defaults to RUNNING with no stopDate: the SFN history is long gone, so
        # leaving it non-terminal would make listExecutions re-poll an expired ARN forever.
        client, counts, _total = _run_executions(
            [_legacy_execution("e1", "01/15/2026, 10:30:00")])

        main_row = client.writes["mainV2"][0]
        assert main_row["executionStatus"] == {"S": "TIMED_OUT"}
        assert main_row["executionStopDate"] == {"S": "2026-01-15T10:30:00Z"}
        assert counts["unresolved_status"] == 1

    def test_a_terminal_row_with_a_stop_date_is_left_alone(self):
        legacy = _legacy_execution("e1", "01/15/2026, 10:30:00")
        legacy["executionStatus"] = {"S": "FAILED"}
        legacy["stopDate"] = {"S": "01/15/2026, 11:00:00"}
        client, counts, _total = _run_executions([legacy])

        main_row = client.writes["mainV2"][0]
        assert main_row["executionStatus"] == {"S": "FAILED"}
        assert main_row["executionStopDate"] == {"S": "2026-01-15T11:00:00Z"}
        assert counts["unresolved_status"] == 0


class TestPipelineConstraintFields:
    """The V2 pipeline record carries the V1 pipelineType label as its free-text `category`;
    pipelineType is not a V2 record field or an ABAC constraint field."""

    def test_v1_pipeline_type_becomes_the_category_label(self):
        client, _counts, _totals = _run_definitions([_v1_pipeline("p1")], [])

        row = client.writes["pipelineV2"][0]
        assert row["category"] == {"S": "standardFile"}
        assert "pipelineType" not in row


class TestGlobalBuiltInClassification:
    """V1 accepted the GLOBAL keyword on create, so a GLOBAL definition is only a built-in when its
    id is one the CDK vamsSchema importer owns."""

    def test_a_global_built_in_pipeline_is_skipped(self):
        row = _v1_pipeline("conversion-3d-basic-to-obj")
        row["databaseId"] = {"S": "GLOBAL"}
        client, counts, _totals = _run_definitions([row], [])

        assert client.writes.get("pipelineV2") is None
        assert counts["skipped_global"] == 1
        assert counts["pipelines"] == 0

    def test_a_user_created_global_pipeline_is_migrated(self):
        row = _v1_pipeline("my-shared-pipeline")
        row["databaseId"] = {"S": "GLOBAL"}
        client, counts, _totals = _run_definitions([row], [])

        assert client.writes["pipelineV2"][0]["pipelineId"] == {"S": "my-shared-pipeline"}
        assert counts["pipelines"] == 1
        assert counts["skipped_global"] == 0

    def test_a_user_created_global_workflow_is_migrated(self):
        workflow = _v1_workflow(["p1"])
        workflow["databaseId"] = {"S": "GLOBAL"}
        workflow["workflowId"] = {"S": "my-shared-workflow"}
        client, counts, _totals = _run_definitions([], [workflow])

        assert client.writes["workflowV2"][0]["workflowId"] == {"S": "my-shared-workflow"}
        assert counts["workflows"] == 1

    def test_a_global_built_in_workflow_is_skipped(self):
        workflow = _v1_workflow(["p1"])
        workflow["databaseId"] = {"S": "GLOBAL"}
        workflow["workflowId"] = {"S": "conversion-3d-basic-to-obj"}
        client, counts, _totals = _run_definitions([], [workflow])

        assert client.writes.get("workflowV2") is None
        assert counts["skipped_global"] == 1


class TestMigratedDefinitionsDoNotGrantNewCapabilities:
    """The V2 record builders default both flags off, so a migrated definition must not gain an
    inline-config override or a redirectable output target its V1 form never had."""

    def test_migrated_pipeline_does_not_allow_a_custom_template_override(self):
        client, _counts, _totals = _run_definitions([_v1_pipeline("p1")], [])

        system_config = client.writes["pipelineV2"][0]["systemConfig"]["M"]
        assert system_config["allowCustomTemplateOverride"] == {"BOOL": False}

    def test_migrated_workflow_locks_its_output_target(self):
        client, _counts, _totals = _run_definitions([], [_v1_workflow(["p1"])])

        output_target = client.writes["workflowV2"][0]["systemConfig"]["M"]["outputTarget"]["M"]
        assert output_target["locationType"] == {"S": "asset"}
        assert output_target["allowOverride"] == {"BOOL": False}


class TestRequireTemplateBuiltInsAllCarryATemplate:
    """A built-in whose pipeline.json sets requireTemplate=true and whose templates carry no
    isDefault flag resolves no template at execute time, so a migrated workflow reference to it must
    name one explicitly through CONSOLIDATED_PIPELINE_ID_MAP."""

    @staticmethod
    def _built_in_bundles():
        """(pipelineId, requireTemplate, [(templateId, isDefault)]) per shipped vamsSchema bundle."""
        import glob
        import json as json_module

        repo_root = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                 "..", "..", "..", ".."))
        bundles = []
        for pipeline_path in sorted(glob.glob(
                os.path.join(repo_root, "backendPipelines", "**", "vamsSchema", "**", "pipeline.json"),
                recursive=True)):
            bundle_dir = os.path.dirname(pipeline_path)
            with open(pipeline_path, encoding="utf-8") as handle:
                pipeline = json_module.load(handle)
            require_template = (pipeline.get("systemConfig") or {}).get("requireTemplate", False)
            templates = []
            templates_dir = os.path.join(bundle_dir, "templates")
            if os.path.isdir(templates_dir):
                for name in sorted(os.listdir(templates_dir)):
                    if not name.endswith(".json") or name.endswith(".webform.json"):
                        continue
                    with open(os.path.join(templates_dir, name), encoding="utf-8") as handle:
                        template = json_module.load(handle)
                    templates.append((template.get("templateId", ""),
                                      bool(template.get("isDefault", False))))
            bundles.append((bundle_dir, require_template, templates))
        return bundles

    def test_the_eks_built_in_reference_carries_its_template(self):
        client, _counts, _totals = _run_definitions([], [{
            "databaseId": {"S": "db1"},
            "workflowId": {"S": "wf1"},
            "specifiedPipelines": {"M": {"functions": {"L": [
                {"M": {"name": {"S": "rapid-pipeline-eks-to-glb"},
                       "databaseId": {"S": "GLOBAL"}}},
            ]}}},
        }])

        ref = client.writes["workflowV2"][0]["specifiedPipelines"]["L"][0]["M"]
        assert ref["pipelineId"] == {"S": "rapid-pipeline-eks-to-glb"}
        assert ref["defaultTemplateId"] == {"S": "rapid-pipeline-eks-to-glb"}

    def test_the_isaaclab_evaluation_built_in_reference_carries_its_template(self):
        client, _counts, _totals = _run_definitions([], [{
            "databaseId": {"S": "db1"},
            "workflowId": {"S": "wf1"},
            "specifiedPipelines": {"M": {"functions": {"L": [
                {"M": {"name": {"S": "isaaclab-evaluation"}, "databaseId": {"S": "GLOBAL"}}},
            ]}}},
        }])

        ref = client.writes["workflowV2"][0]["specifiedPipelines"]["L"][0]["M"]
        assert ref["pipelineId"] == {"S": "isaaclab-evaluation"}
        assert ref["defaultTemplateId"] == {"S": "isaaclab-evaluation-cartpole"}

    def test_every_require_template_built_in_resolves_a_template(self):
        bundles = self._built_in_bundles()
        assert bundles, "no shipped vamsSchema bundles found"

        mapped_template_ids = {template_id for _new_id, template_id
                               in mig.CONSOLIDATED_PIPELINE_ID_MAP.values()}
        unresolvable = []
        for bundle_dir, require_template, templates in bundles:
            if not require_template:
                continue
            if any(is_default for _template_id, is_default in templates):
                continue
            if any(template_id in mapped_template_ids for template_id, _is_default in templates):
                continue
            unresolvable.append(bundle_dir)

        assert not unresolvable, (
            "requireTemplate built-ins with neither an isDefault template nor a "
            f"CONSOLIDATED_PIPELINE_ID_MAP entry: {unresolvable}")


class TestMigratedWorkflowStateMachine:
    def test_stale_v1_state_machine_arn_is_not_carried_over(self):
        client, _counts, _totals = _run_definitions([], [_v1_workflow(["p1"])])

        assert client.writes["workflowV2"][0]["workflow_arn"] == {"S": ""}
        assert client.writes["workflowV2"][0]["jobNames"] == {"L": []}

    def test_rerun_preserves_a_v2_state_machine_deployed_by_a_re_save(self):
        client, _counts, _totals = _run_definitions([], [_v1_workflow(["p1"])], existing_v2_workflows=[{
            "databaseId": {"S": "db1"},
            "workflowId": {"S": "wf1"},
            "workflow_arn": {"S": "arn:aws:states:us-east-1:1:stateMachine:vams-wf1redeployed"},
            "jobNames": {"L": [{"S": "abcde-p1"}]},
        }])

        row = client.writes["workflowV2"][0]
        assert row["workflow_arn"] == {"S": "arn:aws:states:us-east-1:1:stateMachine:vams-wf1redeployed"}
        assert row["jobNames"] == {"L": [{"S": "abcde-p1"}]}


class TestMigratedEventBridgeEventSignature:
    """A V1 EventBridge pipeline stored resourceId + eventSource + eventDetailType. Dropping the last
    two silently repoints the migrated pipeline at 'vams.pipeline' / the pipelineId, so the customer's
    EventBridge rule no longer matches and a waitForCallback run hangs for its whole taskTimeout."""

    @staticmethod
    def _v1_eventbridge_pipeline(bus_arn, source="customer.vams.render",
                                 detail_type="RenderJobRequested"):
        row = _v1_pipeline("eb-pipeline")
        row["pipelineExecutionType"] = {"S": "EventBridge"}
        row["waitForCallback"] = {"S": "Enabled"}
        row["userProvidedResource"] = {"S": json.dumps({
            "isProvided": True, "resourceId": bus_arn, "resourceType": "EventBridge",
            "eventSource": source, "eventDetailType": detail_type,
        })}
        return row

    def test_source_and_detail_type_are_carried_through(self):
        client, _counts, _totals = _run_definitions(
            [self._v1_eventbridge_pipeline("arn:aws:events:us-east-1:123456789012:event-bus/my-bus")],
            [])

        eb = client.writes["pipelineV2"][0]["executionConfig"]["M"]["eventBridge"]["M"]
        assert eb["busArn"] == {"S": "arn:aws:events:us-east-1:123456789012:event-bus/my-bus"}
        assert eb["source"] == {"S": "customer.vams.render"}
        assert eb["detailType"] == {"S": "RenderJobRequested"}

    def test_the_v1_default_bus_keyword_becomes_an_empty_bus_arn(self):
        """V1 stored the literal 'default' for the account default bus, which is not a bus ARN and
        fails EVENTBRIDGE_BUS_ARN validation. An empty value is what the task builder resolves to
        'default'."""
        client, _counts, _totals = _run_definitions(
            [self._v1_eventbridge_pipeline("default")], [])

        eb = client.writes["pipelineV2"][0]["executionConfig"]["M"]["eventBridge"]["M"]
        assert eb["busArn"] == {"S": ""}
        assert eb["source"] == {"S": "customer.vams.render"}

    def test_an_eventbridge_pipeline_with_no_stored_resource_still_gets_the_block(self):
        row = _v1_pipeline("eb-pipeline")
        row["pipelineExecutionType"] = {"S": "EventBridge"}
        client, _counts, _totals = _run_definitions([row], [])

        eb = client.writes["pipelineV2"][0]["executionConfig"]["M"]["eventBridge"]["M"]
        assert eb == {"busArn": {"S": ""}, "source": {"S": ""}, "detailType": {"S": ""}}


class TestMigratedAssetScopeAllowsWholeAsset:
    """A V1 execute request that omitted fileKey ran against the whole asset, so a migrated pipeline
    and workflow must keep permitting the whole-asset ('/') selection or every launch 400s."""

    def test_migrated_pipeline_allows_a_whole_asset_run(self):
        client, _counts, _totals = _run_definitions([_v1_pipeline("p1")], [])

        scope = client.writes["pipelineV2"][0]["systemConfig"]["M"]["assetScope"]["M"]
        assert scope["wholeAssetAllowed"] == {"BOOL": True}
        assert scope["folderAllowed"] == {"BOOL": False}
        assert scope["crossAssetAllowed"] == {"BOOL": False}
        assert scope["singleAssetOnly"] == {"BOOL": True}

    def test_migrated_workflow_allows_a_whole_asset_run(self):
        client, _counts, _totals = _run_definitions([], [_v1_workflow(["p1"])])

        scope = client.writes["workflowV2"][0]["systemConfig"]["M"]["assetScope"]["M"]
        assert scope["wholeAssetAllowed"] == {"BOOL": True}
        assert scope["folderAllowed"] == {"BOOL": False}


class TestMigratedWorkflowInputAssetRoot:
    """The V2 workflow-input record locates each input file's own asset root; a re-run strips that root
    to recover the asset-relative key. Without it a whole-asset input is re-read as a folder
    selection."""

    def test_input_row_carries_its_asset_bucket_and_root(self):
        client, _counts, _total = _run_executions([_legacy_execution("e1", "")])

        row = client.writes["inputs"][0]
        assert row["s3Bucket"] == {"S": "vams-assets"}
        assert row["assetRootS3Key"] == {"S": "myasset/"}

    def test_a_whole_asset_input_round_trips_back_to_the_root_selection(self):
        """The v2.6 re-run path is _to_asset_relative_key(inputAssetFileKey, assetRootS3Key)."""
        legacy = _legacy_execution("e1", "")
        # V1 stored inputAssetFileKey as the FULL asset-bucket key; with no fileKey that was the
        # asset base prefix.
        legacy["inputAssetFileKey"] = {"S": "myasset/"}
        client, _counts, _total = _run_executions([legacy])

        row = client.writes["inputs"][0]
        full_key = row["inputAssetFileKey"]["S"]
        root = row["assetRootS3Key"]["S"]
        assert _to_asset_relative_key(full_key, root) == "/"

    def test_a_per_file_input_round_trips_back_to_its_relative_key(self):
        legacy = _legacy_execution("e1", "")
        legacy["inputAssetFileKey"] = {"S": "myasset/models/model.glb"}
        client, _counts, _total = _run_executions([legacy])

        row = client.writes["inputs"][0]
        assert _to_asset_relative_key(
            row["inputAssetFileKey"]["S"], row["assetRootS3Key"]["S"]) == "/models/model.glb"

    def test_an_archived_asset_partition_still_resolves_its_location(self):
        client, _counts, _total = _run_executions(
            [_legacy_execution("e1", "")],
            asset_rows=[_asset_row(database_id="db1#deleted")])

        assert client.writes["inputs"][0]["assetRootS3Key"] == {"S": "myasset/"}

    def test_the_fields_are_empty_when_the_asset_tables_are_unconfigured(self):
        cfg = {k: v for k, v in _EXEC_CFG.items()
               if k not in ("asset_storage_table_name", "s3_asset_buckets_storage_table_name")}
        client, counts, _total = _run_executions([_legacy_execution("e1", "")], cfg=cfg)

        row = client.writes["inputs"][0]
        assert row["s3Bucket"] == {"S": ""}
        assert row["assetRootS3Key"] == {"S": ""}
        assert counts["errors"] == 0


class TestMigratedPipelineDatabaseIdFallback:
    """A v2.4.x workflow entry carried no databaseId, so the pipeline cache holds the key with an EMPTY
    value — dict.get(key, default) can never return the default. An empty pipelineDatabaseId resolves
    no pipeline definition and indexes the row under ':pipelineId'."""

    def test_an_entry_without_a_database_id_falls_back_to_the_workflow_database(self):
        client, _counts, _total = _run_executions(
            [_legacy_execution("e1", "")],
            workflow_rows=[{
                "workflowId": {"S": "wf1"},
                "databaseId": {"S": "db1"},
                "specifiedPipelines": {"M": {"functions": {"L": [
                    {"M": {"name": {"S": "my-conv"}}},
                ]}}},
            }])

        row = client.writes["pexec"][0]
        assert row["pipelineDatabaseId"] == {"S": "db1"}
        assert row["pipelineDatabaseId:pipelineId"] == {"S": "db1:my-conv"}

    def test_an_explicit_entry_database_id_still_wins(self):
        client, _counts, _total = _run_executions(
            [_legacy_execution("e1", "")],
            workflow_rows=[{
                "workflowId": {"S": "wf1"},
                "databaseId": {"S": "db1"},
                "specifiedPipelines": {"M": {"functions": {"L": [
                    {"M": {"name": {"S": "my-conv"}, "databaseId": {"S": "db2"}}},
                ]}}},
            }])

        assert client.writes["pexec"][0]["pipelineDatabaseId"] == {"S": "db2"}


class TestMigratedTemplateBodyStorage:
    """Template bodies route through the same hybrid inline/S3 storage the template service uses. A V1
    inputParameters JSON had no length cap, so a body written inline above the threshold exceeds
    DynamoDB's 400 KB item limit and the pipeline migrates WITHOUT its template."""

    def test_a_small_body_stays_inline_and_carries_its_hash(self):
        body = '{"quality": "high"}'
        client, counts, _totals = _run_definitions([_v1_pipeline("p1", body)], [])

        template = client.writes["templates"][0]
        assert template["bodyStorage"] == {"S": "inline"}
        assert template["configBody"] == {"S": body}
        assert template["configBodyS3Key"] == {"S": ""}
        assert template["configBodyHash"] == {"S": _tbs.content_hash(body)}
        assert counts["templates"] == 1

    def test_a_body_over_the_inline_threshold_is_offloaded_to_the_default_bucket(self):
        body = '{"blob": "' + ("x" * (_tbs.INLINE_THRESHOLD_BYTES + 1)) + '"}'
        assert _tbs.should_offload(body, "")
        s3_client = FakeS3Client()
        client, counts, _totals = _run_definitions(
            [_v1_pipeline("p1", body)], [], s3_client=s3_client)

        template = client.writes["templates"][0]
        expected_key = _tbs.config_body_s3_key("db1", "p1", "migrated-default")
        assert template["bodyStorage"] == {"S": "s3"}
        assert template["configBody"] == {"S": ""}
        assert template["configBodyS3Key"] == {"S": expected_key}
        assert template["configBodyHash"] == {"S": _tbs.content_hash(body)}
        assert s3_client.objects[("vams-assets", expected_key)] == body.encode("utf-8")
        assert counts["templates"] == 1

    def test_an_unresolvable_bucket_skips_the_template_rather_than_writing_an_oversized_row(self):
        body = '{"blob": "' + ("x" * (_tbs.INLINE_THRESHOLD_BYTES + 1)) + '"}'
        cfg = dict(_DEF_CFG, pipeline_template_body_bucket_name=None)
        client, counts, _totals = _run_definitions([_v1_pipeline("p1", body)], [], cfg=cfg)

        assert client.writes.get("templates") is None
        assert counts["templates"] == 0
        assert counts["errors"] == 1
        # The pipeline itself still migrates.
        assert counts["pipelines"] == 1

    def test_a_skipped_template_is_not_referenced_by_a_migrated_workflow(self):
        body = '{"blob": "' + ("x" * (_tbs.INLINE_THRESHOLD_BYTES + 1)) + '"}'
        cfg = dict(_DEF_CFG, pipeline_template_body_bucket_name=None)
        client, _counts, _totals = _run_definitions(
            [_v1_pipeline("p1", body)], [_v1_workflow(["p1"])], cfg=cfg)

        ref = client.writes["workflowV2"][0]["specifiedPipelines"]["L"][0]["M"]
        assert ref["defaultTemplateId"] == {"S": ""}


class TestMigratedFileUploadTrigger:
    """V1's autoTriggerOnFileExtensionsUpload is a WorkflowTriggersStorageTable fileUpload row in V2.
    Without one, every auto-execute workflow silently stops firing on upload after the upgrade."""

    @staticmethod
    def _v1_workflow_with_auto_trigger(extensions):
        workflow = _v1_workflow(["p1"])
        workflow["autoTriggerOnFileExtensionsUpload"] = {"S": extensions}
        return workflow

    def test_an_extension_list_becomes_a_filtered_file_upload_trigger(self):
        client, counts, _totals = _run_definitions(
            [], [self._v1_workflow_with_auto_trigger("glb,.laz, E57")])

        row = client.writes["triggers"][0]
        assert row["workflowDatabaseId:workflowId"] == {"S": "db1:wf1"}
        assert row["triggerType"] == {"S": "fileUpload"}
        # TriggersByBaseTypeGSI is queried by exact type, so the bare type must be carried separately.
        assert row["triggerBaseType"] == {"S": "fileUpload"}
        assert row["triggerId"] == {"S": ""}
        assert row["enabled"] == {"BOOL": True}
        filters = row["triggerConfig"]["M"]["inputFileFilters"]["M"]
        assert filters["allow"] == {"L": [{"S": "*.glb"}, {"S": "*.laz"}, {"S": "*.e57"}]}
        assert filters["exclude"] == {"L": []}
        assert counts["triggers"] == 1

    def test_the_allow_all_keyword_becomes_an_unrestricted_trigger(self):
        for keyword in ("all", ".all", "ALL"):
            client, _counts, _totals = _run_definitions(
                [], [self._v1_workflow_with_auto_trigger(keyword)])

            filters = client.writes["triggers"][0]["triggerConfig"]["M"]["inputFileFilters"]["M"]
            assert filters["allow"] == {"L": []}, keyword

    def test_the_emitted_patterns_match_an_uploaded_file(self):
        """The dispatcher applies the trigger's allow list with the same matcher the execute path
        uses, so the migrated patterns must be in the form that matcher reads."""
        client, _counts, _totals = _run_definitions(
            [], [self._v1_workflow_with_auto_trigger("glb")])

        filters = client.writes["triggers"][0]["triggerConfig"]["M"]["inputFileFilters"]["M"]
        allow = [entry["S"] for entry in filters["allow"]["L"]]
        assert _apply_input_file_filters(
            [{"relativeFileKey": "/models/model.glb"}], {"allow": allow, "exclude": []})
        assert not _apply_input_file_filters(
            [{"relativeFileKey": "/models/model.stl"}], {"allow": allow, "exclude": []})

    def test_a_workflow_without_an_auto_trigger_writes_no_trigger_row(self):
        client, counts, _totals = _run_definitions([], [_v1_workflow(["p1"])])

        assert client.writes.get("triggers") is None
        assert counts["triggers"] == 0

    def test_a_disabled_workflow_migrates_a_disabled_trigger(self):
        workflow = self._v1_workflow_with_auto_trigger("glb")
        workflow["enabled"] = {"BOOL": False}
        client, _counts, _totals = _run_definitions([], [workflow])

        assert client.writes["triggers"][0]["enabled"] == {"BOOL": False}

    def test_a_skipped_built_in_workflow_writes_no_trigger_row(self):
        workflow = self._v1_workflow_with_auto_trigger("glb")
        workflow["databaseId"] = {"S": "GLOBAL"}
        workflow["workflowId"] = {"S": "conversion-3d-basic-to-obj"}
        client, counts, _totals = _run_definitions([], [workflow])

        assert client.writes.get("triggers") is None
        assert counts["skipped_global"] == 1


class TestBatchWriteUnprocessedItemsRetry:
    """DynamoDB returns rows unprocessed when the table or a GSI throttles. Retries fired back to back
    hit the same exhausted write bucket, so the rows come back unprocessed and are dropped."""

    class _ThrottlingClient(FakeDynamoClient):
        def __init__(self, throttle_times):
            super().__init__({})
            self.throttle_times = throttle_times
            self.batch_calls = 0

        def batch_write_item(self, RequestItems):
            self.batch_calls += 1
            if self.batch_calls <= self.throttle_times:
                table_name = next(iter(RequestItems))
                return {"UnprocessedItems": {table_name: RequestItems[table_name]}}
            return super().batch_write_item(RequestItems)

    def test_retries_sleep_between_attempts(self, monkeypatch):
        sleeps = []
        monkeypatch.setattr(mig.time, "sleep", sleeps.append)
        client = self._ThrottlingClient(throttle_times=3)

        written, errors = mig.flush_batch_write(
            client, "mainV2", [{"workflowExecutionId": {"S": "e0"}}], dry_run=False)

        assert (written, errors) == (1, 0)
        assert len(sleeps) == 3
        # Exponential with jitter: each window is strictly larger than the previous one's ceiling.
        assert sleeps[0] < sleeps[1] < sleeps[2]

    def test_a_prolonged_throttle_falls_back_to_per_row_writes_rather_than_dropping_rows(
            self, monkeypatch):
        monkeypatch.setattr(mig.time, "sleep", lambda _seconds: None)
        # Throttles past the retry budget, so every retry returns the rows unprocessed.
        client = self._ThrottlingClient(throttle_times=mig._BATCH_WRITE_MAX_RETRIES + 1)
        batch = [{"workflowExecutionId": {"S": f"e{i}"}} for i in range(3)]

        written, errors = mig.flush_batch_write(client, "mainV2", batch, dry_run=False)

        assert (written, errors) == (3, 0)
        assert [r["workflowExecutionId"] for r in client.writes["mainV2"]] == [
            {"S": "e0"}, {"S": "e1"}, {"S": "e2"}]

    def test_the_retry_budget_is_not_three_back_to_back_attempts(self):
        assert mig._BATCH_WRITE_MAX_RETRIES > 3
        assert mig._batch_write_backoff_seconds(1) > 0
        assert (mig._batch_write_backoff_seconds(20)
                <= mig._BATCH_WRITE_BACKOFF_MAX_SECONDS * 2)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
