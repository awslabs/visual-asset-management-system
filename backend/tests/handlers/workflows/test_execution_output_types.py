# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Feature #858: execution outputTypes + the paged /details/files read + the full /details/results read.

Covers, in one file, the whole backend spine the feature adds:
  - the pure derive_output_types helper and its File->Metadata->Result ordering;
  - the configuration-record outputTypes kwarg and the update_execution_output_types UPDATE writer;
  - the two list builders projecting outputTypes (global = free; per-asset = via the config-row memo),
    and the load-bearing legacy invariant: a config row with NO outputTypes attribute reads as [];
  - the paged files handler (input single-partition + output step-walk), its auth, and token binding;
  - the full results handler: happy path, empty/legacy empty shape, the 5 MiB payload-safety cut, and
    a row stored already-truncated propagating the flag.

The handler tests load executionService the same way test_details_metadata_paging.py does (tables
resolved at import via env overrides)."""

import json
import os

import pytest
from unittest.mock import MagicMock, patch

os.environ.setdefault("ASSET_STORAGE_TABLE_NAME", "t-assets")
os.environ.setdefault("WORKFLOW_EXECUTION_STORAGE_TABLE_V2_NAME", "t-exec-v2")
os.environ.setdefault("WORKFLOW_EXECUTION_INPUTS_STORAGE_TABLE_NAME", "t-wf-inputs")
os.environ.setdefault("PIPELINE_EXECUTIONS_STORAGE_TABLE_NAME", "t-pexec")
os.environ.setdefault("WORKFLOW_EXECUTION_CONFIGURATION_STORAGE_TABLE_NAME", "t-wf-cfg")
os.environ.setdefault("PIPELINE_EXECUTION_INPUT_FILES_STORAGE_TABLE_NAME", "t-pin-files")
os.environ.setdefault("PIPELINE_EXECUTION_INPUT_METADATA_STORAGE_TABLE_NAME", "t-pin-md")
os.environ.setdefault("PIPELINE_EXECUTION_INPUT_CONFIGURATION_STORAGE_TABLE_NAME", "t-pin-cfg")
os.environ.setdefault("PIPELINE_EXECUTION_OUTPUT_FILES_STORAGE_TABLE_NAME", "t-of")
os.environ.setdefault("PIPELINE_EXECUTION_OUTPUT_METADATA_STORAGE_TABLE_NAME", "t-om")
os.environ.setdefault("PIPELINE_EXECUTION_OUTPUT_RESULTS_STORAGE_TABLE_NAME", "t-or")
os.environ.setdefault("PIPELINE_EXECUTION_LOGS_STORAGE_TABLE_NAME", "t-logs")
os.environ.setdefault("WORKFLOW_STORAGE_TABLE_NAME", "t-workflows")
os.environ.setdefault("PIPELINE_STORAGE_TABLE_NAME", "t-pipelines")
os.environ.setdefault("EXECUTE_WORKFLOW_V2_LAMBDA_FUNCTION_NAME", "t-execv2")

from backend.backend.common.workflows import executionRecords as er  # noqa: E402
from backend.backend.handlers.workflows import executionService as le  # noqa: E402

MOD = "backend.backend.handlers.workflows.executionService"
EXEC_ID = "e1000000000000000000000000000001"

INPUT_FILE_SORT_KEY = "databaseId:assetId:inputAssetFileKey"
OUTPUT_FILE_SORT_KEY = "fileType:relativeFilePath"


# --------------------------------------------------------------------------- #
# 1. The pure derive helper + record kwarg + UPDATE writer (executionRecords)
# --------------------------------------------------------------------------- #

@pytest.mark.unit
class TestDeriveOutputTypes:
    def test_empty_when_nothing_was_produced(self):
        assert er.derive_output_types(False, False, False) == []

    def test_every_single_type(self):
        assert er.derive_output_types(True, False, False) == ["File"]
        assert er.derive_output_types(False, True, False) == ["Metadata"]
        assert er.derive_output_types(False, False, True) == ["Result"]

    def test_order_is_always_file_metadata_result(self):
        # Whatever order the booleans arrive in, the subset is emitted File -> Metadata -> Result.
        assert er.derive_output_types(True, True, True) == ["File", "Metadata", "Result"]
        assert er.derive_output_types(False, True, True) == ["Metadata", "Result"]
        assert er.derive_output_types(True, False, True) == ["File", "Result"]
        assert er.derive_output_types(True, True, False) == ["File", "Metadata"]

    def test_truthy_non_bool_inputs_are_coerced(self):
        # The write points pass bool(list), but guard against a truthy non-bool slipping through.
        assert er.derive_output_types(["f"], {}, "x") == ["File", "Result"]

    def test_the_constant_tuple_is_the_canonical_order(self):
        assert er.EXECUTION_OUTPUT_TYPES == ("File", "Metadata", "Result")
        assert er.EXECUTION_RESULT_RESPONSE_MAX_BYTES == 5 * 1024 * 1024


@pytest.mark.unit
class TestConfigurationRecordOutputTypes:
    def test_launch_record_defaults_to_empty_list(self):
        record = er.build_workflow_configuration_record(EXEC_ID, "", [])
        assert record["outputTypes"] == []

    def test_launch_record_honors_an_explicit_value(self):
        record = er.build_workflow_configuration_record(
            EXEC_ID, "", [], output_types=["File", "Result"])
        assert record["outputTypes"] == ["File", "Result"]

    def test_none_output_types_is_stored_as_empty_list(self):
        record = er.build_workflow_configuration_record(EXEC_ID, "", [], output_types=None)
        assert record["outputTypes"] == []


@pytest.mark.unit
class TestUpdateExecutionOutputTypes:
    def test_updates_the_configuration_row_by_its_key(self):
        dynamo = MagicMock()
        table = MagicMock()
        dynamo.Table.return_value = table
        er.update_execution_output_types(dynamo, "cfg-table", EXEC_ID, ["File", "Metadata"])
        dynamo.Table.assert_called_once_with("cfg-table")
        _args, kwargs = table.update_item.call_args
        assert kwargs["Key"] == {"workflowExecutionId": EXEC_ID, "recordType": "configuration"}
        # The update writes the outputTypes attribute; assert the attribute it names and the value it
        # binds rather than the exact expression text, so a safer equivalent expression still passes.
        assert "outputTypes" in kwargs["UpdateExpression"]
        assert ["File", "Metadata"] in kwargs["ExpressionAttributeValues"].values()

    def test_none_writes_an_empty_list(self):
        dynamo = MagicMock()
        table = MagicMock()
        dynamo.Table.return_value = table
        er.update_execution_output_types(dynamo, "cfg-table", EXEC_ID, None)
        _args, kwargs = table.update_item.call_args
        assert kwargs["ExpressionAttributeValues"] == {":t": []}


# --------------------------------------------------------------------------- #
# 2. List-row projection (global free read + per-asset memo + legacy -> [])
# --------------------------------------------------------------------------- #

@pytest.mark.unit
class TestGlobalListRowOutputTypes:
    def test_global_row_projects_output_types_from_the_threaded_config_row(self):
        main = {"workflowExecutionId": EXEC_ID, "workflowId": "wf", "workflowDatabaseId": "db"}
        row = le._global_list_row(main, {"outputTypes": ["File", "Result"]})
        assert row["outputTypes"] == ["File", "Result"]

    def test_a_legacy_config_row_with_no_attribute_reads_as_empty(self):
        main = {"workflowExecutionId": EXEC_ID}
        assert le._global_list_row(main, {})["outputTypes"] == []
        assert le._global_list_row(main, None)["outputTypes"] == []

    def test_a_malformed_output_types_value_is_coerced_to_empty(self):
        # Defends the response against a non-list stored value (never serializes a non-list).
        main = {"workflowExecutionId": EXEC_ID}
        assert le._global_list_row(main, {"outputTypes": "File"})["outputTypes"] == []
        assert le._global_list_row(main, {"outputTypes": ["File", 7, "Result"]})[
            "outputTypes"] == ["File", "Result"]


@pytest.mark.unit
class TestRowOutputTypesHelper:
    def test_absent_or_non_dict_config_row_is_empty(self):
        assert le._row_output_types(None) == []
        assert le._row_output_types("nope") == []
        assert le._row_output_types({}) == []

    def test_list_of_strings_passes_through(self):
        assert le._row_output_types({"outputTypes": ["File", "Metadata"]}) == ["File", "Metadata"]

    def test_non_string_members_are_dropped(self):
        assert le._row_output_types({"outputTypes": ["File", None, 3, "Result"]}) == [
            "File", "Result"]


@pytest.mark.unit
class TestPerAssetListOutputTypes:
    """build_execution_items threads outputTypes via the per-request config-row memo (NOT free on the
    per-asset path). A memo entry absent, or a legacy config row, reads as []."""

    def _item(self, config_rows):
        main = {"workflowExecutionId": EXEC_ID, "workflowId": "wf", "workflowDatabaseId": "db",
                "executionStatus": "SUCCEEDED", "executionStartDate": "2026-01-01T00:00:00Z"}
        inputs = [{"workflowExecutionId": EXEC_ID, "databaseId": "db", "assetId": "a1",
                   "inputAssetFileKey": "/f.glb", "workflowId": "wf", "workflowDatabaseId": "db"}]
        items = le.build_execution_items(
            input_items=inputs,
            fetch_main_row=lambda eid: main,
            describe_execution=lambda arn: None,
            persist_main_row=lambda item: None,
            workflow_id_filter="",
            workflow_database_id="",
            config_rows=config_rows,
        )
        assert len(items) == 1
        return items[0]

    def test_output_types_come_from_the_memo(self):
        item = self._item({EXEC_ID: {"outputTypes": ["File", "Metadata"]}})
        assert item["outputTypes"] == ["File", "Metadata"]

    def test_a_missing_memo_entry_reads_as_empty(self):
        assert self._item({})["outputTypes"] == []

    def test_a_legacy_config_row_reads_as_empty(self):
        assert self._item({EXEC_ID: {"recordType": "configuration"}})["outputTypes"] == []

    def test_a_none_memo_reads_as_empty(self):
        assert self._item(None)["outputTypes"] == []


# --------------------------------------------------------------------------- #
# 3. Paged files handler
# --------------------------------------------------------------------------- #

def _allow_all():
    e = MagicMock()
    e.enforce.return_value = True
    e.enforceAPI.return_value = True
    return e


def _output_file_row(pexec, rel="/out.glb", file_type="file"):
    return {
        "pipelineExecutionId": pexec,
        OUTPUT_FILE_SORT_KEY: f"{file_type}:{rel}",
        "fileType": file_type, "relativeFilePath": rel,
        "s3Bucket": "secret-bucket", "s3Key": f"x/{rel}", "fileSize": 10,
        "contentType": "model/gltf-binary", "s3VersionId": "v1",
    }


def _input_file_row(exec_id, database_id="db", asset_id="a1", key="/in.glb"):
    return {
        "workflowExecutionId": exec_id,
        INPUT_FILE_SORT_KEY: f"{database_id}:{asset_id}:{key}",
        "databaseId": database_id, "assetId": asset_id, "inputAssetFileKey": key,
        "s3Bucket": "secret-bucket", "assetRootS3Key": "x/", "versionId": "v9",
    }


class FakeFileTable:
    """A DynamoDB table stub honoring KeyConditionExpression / Limit / ExclusiveStartKey per partition
    for the file tables (serves rows in stored order so paging resumption can be asserted)."""

    def __init__(self, rows_by_partition, partition_attr, sort_key):
        self.rows_by_partition = rows_by_partition
        self.partition_attr = partition_attr
        self.sort_key = sort_key

    def query(self, **kwargs):
        cond = kwargs["KeyConditionExpression"]
        partition = [v for v in getattr(cond, "_values", ()) if isinstance(v, str)][-1]
        rows = list(self.rows_by_partition.get(partition, []))
        start = 0
        exclusive = kwargs.get("ExclusiveStartKey")
        if exclusive:
            for index, row in enumerate(rows):
                if row.get(self.sort_key) == exclusive.get(self.sort_key):
                    start = index + 1
                    break
        limit = kwargs.get("Limit", len(rows))
        page = rows[start:start + limit]
        resp = {"Items": page}
        if start + limit < len(rows):
            resp["LastEvaluatedKey"] = {self.partition_attr: partition,
                                        self.sort_key: page[-1][self.sort_key]}
        return resp


def _steps(*pipeline_ids):
    return [{"pipelineExecutionId": f"pe-{pid}", "pipelineId": pid, "pipelineDatabaseId": "db"}
            for pid in pipeline_ids]


def _walk_files(table, prows, collection, page_size, pipeline_id=""):
    with patch(f"{MOD}.dynamodb") as ddb, \
         patch(f"{MOD}.get_pipeline_execution_rows", return_value=prows):
        ddb.Table.return_value = table
        collected, token, pages = [], "", 0
        while True:
            result = le.page_detail_files(EXEC_ID, collection, page_size, token, pipeline_id)
            collected.extend(result["Items"])
            pages += 1
            token = result.get("NextToken")
            if not token:
                break
            assert pages < 500, "paging did not terminate"
    return collected, pages


@pytest.mark.unit
class TestOutputFilesPaging:
    def test_output_files_walk_every_step_once(self):
        rows = {f"pe-{pid}": [_output_file_row(f"pe-{pid}", rel=f"/{pid}{i}.glb") for i in range(c)]
                for pid, c in [("pA", 4), ("pB", 3)]}
        table = FakeFileTable(rows, "pipelineExecutionId", OUTPUT_FILE_SORT_KEY)
        collected, _p = _walk_files(table, _steps("pA", "pB"),
                                    le.DETAIL_FILE_COLLECTION_OUTPUT, 100)
        assert len(collected) == 7
        # Output rows carry the producing pipelineId and omit internal S3 locators.
        assert all("pipelineId" in r for r in collected)
        assert all("s3Key" not in r and "s3Bucket" not in r for r in collected)

    @pytest.mark.parametrize("page_size", [1, 2, 3, 7, 100])
    def test_output_paging_returns_each_row_once(self, page_size):
        rows = {"pe-pA": [_output_file_row("pe-pA", rel=f"/o{i}.glb") for i in range(6)]}
        table = FakeFileTable(rows, "pipelineExecutionId", OUTPUT_FILE_SORT_KEY)
        collected, _p = _walk_files(table, _steps("pA"), le.DETAIL_FILE_COLLECTION_OUTPUT, page_size)
        paths = [r["relativeFilePath"] for r in collected]
        assert paths == [f"/o{i}.glb" for i in range(6)]
        assert len(paths) == len(set(paths))

    def test_output_filter_narrows_to_one_step(self):
        rows = {"pe-pA": [_output_file_row("pe-pA", rel="/a.glb")],
                "pe-pB": [_output_file_row("pe-pB", rel="/b.glb")]}
        table = FakeFileTable(rows, "pipelineExecutionId", OUTPUT_FILE_SORT_KEY)
        collected, _p = _walk_files(table, _steps("pA", "pB"),
                                    le.DETAIL_FILE_COLLECTION_OUTPUT, 100, "pB")
        assert [r["pipelineId"] for r in collected] == ["pB"]


@pytest.mark.unit
class TestInputFilesPaging:
    def test_input_files_are_a_single_partition_query(self):
        rows = {EXEC_ID: [_input_file_row(EXEC_ID, key=f"/in{i}.glb") for i in range(5)]}
        table = FakeFileTable(rows, "workflowExecutionId", INPUT_FILE_SORT_KEY)
        collected, _p = _walk_files(table, _steps("pA", "pB"),
                                    le.DETAIL_FILE_COLLECTION_INPUT, 100)
        assert [r["inputAssetFileKey"] for r in collected] == [f"/in{i}.glb" for i in range(5)]
        # Input rows carry no pipelineId (workflow-level) and no S3 internals.
        assert all("pipelineId" not in r for r in collected)
        assert all("s3Bucket" not in r and "assetRootS3Key" not in r for r in collected)

    @pytest.mark.parametrize("page_size", [1, 2, 3, 100])
    def test_input_paging_returns_each_row_once(self, page_size):
        rows = {EXEC_ID: [_input_file_row(EXEC_ID, key=f"/in{i}.glb") for i in range(6)]}
        table = FakeFileTable(rows, "workflowExecutionId", INPUT_FILE_SORT_KEY)
        collected, _p = _walk_files(table, _steps("pA"), le.DETAIL_FILE_COLLECTION_INPUT, page_size)
        keys = [r["inputAssetFileKey"] for r in collected]
        assert keys == [f"/in{i}.glb" for i in range(6)]
        assert len(keys) == len(set(keys))

    def test_empty_input_files_yield_an_empty_final_page(self):
        table = FakeFileTable({EXEC_ID: []}, "workflowExecutionId", INPUT_FILE_SORT_KEY)
        with patch(f"{MOD}.dynamodb") as ddb, \
             patch(f"{MOD}.get_pipeline_execution_rows", return_value=_steps("pA")):
            ddb.Table.return_value = table
            result = le.page_detail_files(EXEC_ID, le.DETAIL_FILE_COLLECTION_INPUT, 10, "")
        assert result["Items"] == []
        assert "NextToken" not in result
        assert result["collection"] == "input"


@pytest.mark.unit
class TestFilesTokenBinding:
    def test_an_input_token_is_refused_for_output(self):
        rows = {EXEC_ID: [_input_file_row(EXEC_ID, key=f"/in{i}.glb") for i in range(4)]}
        table = FakeFileTable(rows, "workflowExecutionId", INPUT_FILE_SORT_KEY)
        with patch(f"{MOD}.dynamodb") as ddb, \
             patch(f"{MOD}.get_pipeline_execution_rows", return_value=_steps("pA")):
            ddb.Table.return_value = table
            first = le.page_detail_files(EXEC_ID, le.DETAIL_FILE_COLLECTION_INPUT, 2, "")
        token = first.get("NextToken")
        assert token
        # Replaying it against the output collection is refused rather than silently serving rows.
        steps = [("pe-pA", "pA")]
        assert le._decode_detail_files_token(token, steps,
                                             le.DETAIL_FILE_COLLECTION_OUTPUT, "") is None

    def test_a_malformed_token_raises(self):
        table = FakeFileTable({EXEC_ID: [_input_file_row(EXEC_ID)]},
                              "workflowExecutionId", INPUT_FILE_SORT_KEY)
        with patch(f"{MOD}.dynamodb") as ddb, \
             patch(f"{MOD}.get_pipeline_execution_rows", return_value=_steps("pA")):
            ddb.Table.return_value = table
            with pytest.raises(le.VAMSGeneralErrorResponse):
                le.page_detail_files(EXEC_ID, le.DETAIL_FILE_COLLECTION_INPUT, 10, "not-base64!!")


@pytest.mark.unit
class TestFilesHandlerAuthAnd404:
    EVENT = {
        "requestContext": {"http": {"method": "GET",
                                    "path": f"/workflows/executions/{EXEC_ID}/details/files"},
                           "authorizer": {}},
        "pathParameters": {"executionId": EXEC_ID},
        "queryStringParameters": {"collection": "output"},
    }
    MAIN = {"workflowExecutionId": EXEC_ID, "workflowId": "wf", "workflowDatabaseId": "db"}

    def _run(self, enforcer, claims, main=None):
        with patch(f"{MOD}.request_to_claims", return_value=claims), \
             patch(f"{MOD}.CasbinEnforcer", return_value=enforcer), \
             patch(f"{MOD}.get_execution_main_row", return_value=self.MAIN if main is None else main), \
             patch(f"{MOD}.get_workflow_execution_configuration_row", return_value={}), \
             patch(f"{MOD}.get_execution_input_assets", return_value=[]), \
             patch(f"{MOD}.get_pipeline_execution_rows", return_value=_steps("pA")), \
             patch(f"{MOD}.dynamodb") as ddb:
            ddb.Table.return_value = FakeFileTable(
                {"pe-pA": []}, "pipelineExecutionId", OUTPUT_FILE_SORT_KEY)
            return le.lambda_handler(dict(self.EVENT), MagicMock())

    def test_authorized_caller_gets_a_page(self):
        assert self._run(_allow_all(), {"tokens": ["u1"]})["statusCode"] == 200

    def test_unknown_execution_returns_404(self):
        with patch(f"{MOD}.request_to_claims", return_value={"tokens": ["u1"]}), \
             patch(f"{MOD}.CasbinEnforcer", return_value=_allow_all()), \
             patch(f"{MOD}.get_execution_main_row", return_value=None):
            resp = le.lambda_handler(dict(self.EVENT), MagicMock())
        assert resp["statusCode"] == 404

    def test_denied_asset_denies_the_page(self):
        enf = MagicMock()
        enf.enforceAPI.return_value = True
        enf.enforce.side_effect = lambda obj, action, *a, **k: obj.get("object__type") != "asset"
        with patch(f"{MOD}.request_to_claims", return_value={"tokens": ["u1"]}), \
             patch(f"{MOD}.CasbinEnforcer", return_value=enf), \
             patch(f"{MOD}.get_execution_main_row", return_value=self.MAIN), \
             patch(f"{MOD}.get_workflow_execution_configuration_row", return_value={}), \
             patch(f"{MOD}.get_execution_input_assets", return_value=[("db", "a1")]), \
             patch(f"{MOD}._get_asset_details_cached",
                   side_effect=lambda d, a: {"databaseId": d, "assetId": a}), \
             patch(f"{MOD}.prewarm_asset_details"), \
             patch(f"{MOD}.get_pipeline_execution_rows", return_value=_steps("pA")), \
             patch(f"{MOD}.dynamodb"):
            resp = le.lambda_handler(dict(self.EVENT), MagicMock())
        assert resp["statusCode"] == 403

    def test_empty_tokens_fail_closed(self):
        assert self._run(_allow_all(), {"tokens": []})["statusCode"] == 403

    def test_invalid_collection_is_a_400(self):
        event = dict(self.EVENT)
        event["queryStringParameters"] = {"collection": "inputDatabase"}  # files has no inputDatabase
        with patch(f"{MOD}.request_to_claims", return_value={"tokens": ["u1"]}), \
             patch(f"{MOD}.CasbinEnforcer", return_value=_allow_all()), \
             patch(f"{MOD}.get_execution_main_row", return_value=self.MAIN), \
             patch(f"{MOD}.get_workflow_execution_configuration_row", return_value={}), \
             patch(f"{MOD}.get_execution_input_assets", return_value=[]), \
             patch(f"{MOD}.get_pipeline_execution_rows", return_value=_steps("pA")), \
             patch(f"{MOD}.dynamodb"):
            resp = le.lambda_handler(event, MagicMock())
        assert resp["statusCode"] == 400


# --------------------------------------------------------------------------- #
# 4. Full results handler
# --------------------------------------------------------------------------- #

def _result_row(pexec, rel="/r.txt", content="hello", truncated=False):
    return {
        "pipelineExecutionId": pexec,
        "relativeFilePath": rel,
        "resultsContent": content,
        "resultsContentTruncated": truncated,
        "s3Key": "secret/key.txt",
    }


class FakeResultsTable:
    def __init__(self, rows_by_pexec):
        self.rows_by_pexec = rows_by_pexec

    def query(self, **kwargs):
        cond = kwargs["KeyConditionExpression"]
        pexec = [v for v in getattr(cond, "_values", ()) if isinstance(v, str)][-1]
        return {"Items": list(self.rows_by_pexec.get(pexec, []))}


def _run_results(rows_by_pexec, steps, config_row=None):
    event = {
        "requestContext": {"http": {"method": "GET",
                                    "path": f"/workflows/executions/{EXEC_ID}/details/results"},
                           "authorizer": {}},
        "pathParameters": {"executionId": EXEC_ID},
        "queryStringParameters": {},
    }
    main = {"workflowExecutionId": EXEC_ID, "workflowId": "wf", "workflowDatabaseId": "db"}
    with patch(f"{MOD}.request_to_claims", return_value={"tokens": ["u1"]}), \
         patch(f"{MOD}.CasbinEnforcer", return_value=_allow_all()), \
         patch(f"{MOD}.get_execution_main_row", return_value=main), \
         patch(f"{MOD}.get_workflow_execution_configuration_row", return_value=config_row or {}), \
         patch(f"{MOD}.get_execution_input_assets", return_value=[]), \
         patch(f"{MOD}.get_pipeline_execution_rows", return_value=steps), \
         patch(f"{MOD}.dynamodb") as ddb:
        ddb.Table.return_value = FakeResultsTable(rows_by_pexec)
        resp = le.lambda_handler(event, MagicMock())
    return resp


@pytest.mark.unit
class TestResultsHandler:
    def test_concatenates_in_stable_step_order(self):
        rows = {"pe-pA": [_result_row("pe-pA", content="AAA")],
                "pe-pB": [_result_row("pe-pB", content="BBB")]}
        resp = _run_results(rows, _steps("pA", "pB"))
        assert resp["statusCode"] == 200
        body = json.loads(resp["body"])["message"]
        assert body["resultContent"] == "AAABBB"
        assert body["resultContentTruncated"] is False
        assert body["resultRowCount"] == 2
        assert body["resultContentBytes"] == 6

    def test_empty_run_returns_the_empty_shape(self):
        resp = _run_results({"pe-pA": []}, _steps("pA"))
        body = json.loads(resp["body"])["message"]
        assert body == {"resultContent": "", "resultContentTruncated": False,
                        "resultRowCount": 0, "resultContentBytes": 0}

    def test_legacy_run_with_no_result_rows_is_empty(self):
        # A pre-change run never wrote output-results rows; the read returns the empty shape, not 500.
        resp = _run_results({}, _steps("pA", "pB"), config_row={})
        body = json.loads(resp["body"])["message"]
        assert body["resultContent"] == ""
        assert body["resultRowCount"] == 0

    def test_internal_s3_key_is_never_surfaced(self):
        resp = _run_results({"pe-pA": [_result_row("pe-pA", content="x")]}, _steps("pA"))
        assert "secret/key.txt" not in resp["body"]
        assert "s3Key" not in json.loads(resp["body"])["message"]

    def test_a_row_stored_already_truncated_sets_the_flag(self):
        rows = {"pe-pA": [_result_row("pe-pA", content="partial", truncated=True)]}
        body = json.loads(_run_results(rows, _steps("pA"))["body"])["message"]
        assert body["resultContentTruncated"] is True

    def test_the_payload_budget_cut_fires_and_stays_under_the_limit(self):
        # Build a multi-row result larger than the 5 MiB budget; the response must be cut server-side,
        # flagged, and stay at (not over) the budget.
        budget = er.EXECUTION_RESULT_RESPONSE_MAX_BYTES
        big = "x" * (budget // 2)  # three such rows = ~1.5x the budget
        rows = {"pe-pA": [_result_row("pe-pA", rel=f"/r{i}.txt", content=big) for i in range(3)]}
        body = json.loads(_run_results(rows, _steps("pA"))["body"])["message"]
        assert body["resultContentTruncated"] is True
        assert body["resultContentBytes"] <= budget
        # The whole JSON response stays under the 6 MB Lambda-proxy ceiling (the point of the cut).
        assert len(json.dumps(body).encode("utf-8")) < 6 * 1024 * 1024

    def test_unknown_execution_is_404(self):
        event = {
            "requestContext": {"http": {"method": "GET",
                                        "path": f"/workflows/executions/{EXEC_ID}/details/results"},
                               "authorizer": {}},
            "pathParameters": {"executionId": EXEC_ID},
            "queryStringParameters": {},
        }
        with patch(f"{MOD}.request_to_claims", return_value={"tokens": ["u1"]}), \
             patch(f"{MOD}.CasbinEnforcer", return_value=_allow_all()), \
             patch(f"{MOD}.get_execution_main_row", return_value=None):
            resp = le.lambda_handler(event, MagicMock())
        assert resp["statusCode"] == 404

    def test_empty_tokens_fail_closed(self):
        event = {
            "requestContext": {"http": {"method": "GET",
                                        "path": f"/workflows/executions/{EXEC_ID}/details/results"},
                               "authorizer": {}},
            "pathParameters": {"executionId": EXEC_ID},
            "queryStringParameters": {},
        }
        main = {"workflowExecutionId": EXEC_ID, "workflowId": "wf", "workflowDatabaseId": "db"}
        enf = MagicMock()
        enf.enforceAPI.return_value = False
        with patch(f"{MOD}.request_to_claims", return_value={"tokens": []}), \
             patch(f"{MOD}.CasbinEnforcer", return_value=enf), \
             patch(f"{MOD}.get_execution_main_row", return_value=main):
            resp = le.lambda_handler(event, MagicMock())
        assert resp["statusCode"] == 403
