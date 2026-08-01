# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the v2.5 -> v2.6 migration record builders.

Run from this directory:  python -m pytest test_v2_5_to_v2_6_migration.py -q

The migration module's filename is not a valid python identifier, so it is loaded by path.
"""

import importlib.util
import os

import pytest

_MODULE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "v2.5_to_v2.6_migration.py")


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


_EXEC_CFG = {
    "workflow_executions_storage_table_name_v1": "legacy",
    "workflow_executions_storage_table_name_v2": "mainV2",
    "workflow_execution_inputs_storage_table_name": "inputs",
    "pipeline_executions_storage_table_name": "pexec",
    "pipeline_execution_input_files_storage_table_name": "pinFiles",
    "workflow_storage_table_name": "workflowV1",
    "workflow_execution_configuration_storage_table_name": "wfConfig",
    "pipeline_execution_input_configuration_storage_table_name": "pexecConfig",
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


def _run_executions(legacy_rows, workflow_rows=None):
    client = FakeDynamoClient({
        "legacy": legacy_rows,
        "workflowV1": workflow_rows if workflow_rows is not None else [_workflow_v1_with_two_pipelines()],
    })
    counts, total = mig.migrate_workflow_executions(client, _EXEC_CFG, dry_run=False, limit=None)
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


def _run_definitions(pipelines, workflows, existing_v2_workflows=None):
    client = FakeDynamoClient({
        "pipelineV1": pipelines,
        "workflowV1": workflows,
        "workflowV2": existing_v2_workflows or [],
    })
    counts, totals = mig.migrate_pipeline_workflow_definitions(
        client, _DEF_CFG, dry_run=False, limit=None)
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


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
