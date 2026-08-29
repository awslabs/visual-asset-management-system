# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The launch-time guards of the execute handler: the concurrency restriction, the render limit, and
the escaping of a template body under its own declared format.

S2-BACKEND-042 — the concurrency guard answered "no conflict" once its inspection budget was spent,
and the budget was one counter shared across every asset partition and fed by executions of EVERY
workflow (the by-asset index is not partitioned by workflow). Both halves silently disabled a
restriction the workflow declared, exactly on the assets with enough history to need it.

The budget is spent round-robin across the selected assets, and a spent budget is reported as an
unconfirmed limit rather than answered with a denial: exhausting it is a fact about the request, not
evidence of a conflict, and a 400 there permanently blocked every later launch on a deep-history asset
(the SYSTEM_USER trigger-dispatch path included). What the guard reports instead is asserted in
test_r2_concurrency_and_abort_bounds.py.

S2-BACKEND-103 — RenderedConfigTooLargeError reached the handler's terminal `except Exception` and
answered 500 for a caller-input problem the error class exists to report as 400.

S2-BACKEND-041 — the launch render never received the template's declared configFormat, so an `xml`
body's system-tag values took the JSON escape and left `&`, `<` and `>` raw.

The stubs here hand back REAL dicts and TERMINATE: a MagicMock answers `.get('LastEvaluatedKey')`
truthily forever, so any paging loop built on one never exits. Each fake counts its queries and fails
the test rather than hanging if the walk does not end.
"""

import json
import os
import sys
import types

import pytest
from unittest.mock import MagicMock, patch

# executeWorkflow loads these at import (mirrors test_executeWorkflow.py).
os.environ.setdefault("ASSET_STORAGE_TABLE_NAME", "t-assets")
os.environ.setdefault("WORKFLOW_STORAGE_TABLE_V2_NAME", "t-wf-v2")
os.environ.setdefault("PIPELINE_STORAGE_TABLE_V2_NAME", "t-pipe-v2")
os.environ.setdefault("PIPELINE_TEMPLATES_STORAGE_TABLE_NAME", "t-templates")
os.environ.setdefault("PIPELINE_TEMPLATE_TAG_SCHEMA_STORAGE_TABLE_NAME", "t-tagschema")
os.environ.setdefault("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "t-buckets")
os.environ.setdefault("S3_ASSETAUXILIARY_STORAGE_BUCKET", "t-aux")
os.environ.setdefault("METADATA_SERVICE_LAMBDA_FUNCTION_NAME", "t-md-svc")
os.environ.setdefault("WORKFLOW_EXECUTION_STORAGE_TABLE_V2_NAME", "t-exec-v2")
os.environ.setdefault("PIPELINE_EXECUTIONS_STORAGE_TABLE_NAME", "t-pexec")
os.environ.setdefault("PIPELINE_EXECUTION_INPUT_METADATA_STORAGE_TABLE_NAME", "t-pin-md")
os.environ.setdefault("PIPELINE_EXECUTION_INPUT_CONFIGURATION_STORAGE_TABLE_NAME", "t-pin-cfg")
os.environ.setdefault("WORKFLOW_EXECUTION_INPUTS_STORAGE_TABLE_NAME", "t-wf-inputs")
os.environ.setdefault("WORKFLOW_EXECUTION_CONFIGURATION_STORAGE_TABLE_NAME", "t-wf-cfg")

# handlers.workflows package __init__ imports get_task_builder at import time; the shared mock package
# does not provide it, so register a lightweight stub before importing the handler.
if "common.workflows.stepfunctions_builder" not in sys.modules:
    _stub = types.ModuleType("common.workflows.stepfunctions_builder")
    _stub.get_task_builder = lambda *a, **k: None
    sys.modules["common.workflows.stepfunctions_builder"] = _stub

from backend.backend.handlers.workflows import executeWorkflow as ewv2  # noqa: E402

# Taken from the handler's own module attributes, never re-imported here. The test tree puts both
# `backend/` and `backend/backend/` on sys.path, so `models.common` and `backend.backend.models.common`
# are DIFFERENT module objects with different class identities: a `pytest.raises` on a re-imported
# exception class never matches the one the handler raises, and a `patch` on a re-imported module never
# reaches the one the handler calls.
er = ewv2.er
tr = ewv2.tr
VAMSGeneralErrorResponse = ewv2.VAMSGeneralErrorResponse

MOD = "backend.backend.handlers.workflows.executeWorkflow"

WF_DB, WF_ID = "wfdb", "wf1"
COMPOSITE = f"{WF_DB}:{WF_ID}"
OTHER_COMPOSITE = f"{WF_DB}:other"


def _condition_values(condition):
    """Every literal string inside a boto3 condition tree (the objects have an opaque repr)."""
    values, stack = [], [condition]
    while stack:
        node = stack.pop()
        operands = getattr(node, "_values", None)
        if operands is None:
            values.append(node)
            continue
        stack.extend(operands)
    return [v for v in values if isinstance(v, str)]


class _InputsTable:
    """The workflow-execution-inputs table, queried through WorkflowExecInputsByAssetGSI.

    Pages are served from an explicit row list per partition and the last page carries no
    LastEvaluatedKey, so the generator's `while True` terminates. Queries are counted and capped:
    a walk that fails to terminate fails the test instead of hanging the suite.
    """

    PAGE = 100

    def __init__(self, rows_by_partition):
        self.rows_by_partition = rows_by_partition
        self.partitions_queried = []
        self.queries = 0

    def query(self, **kwargs):
        self.queries += 1
        assert self.queries < 100, "the candidate walk did not terminate"
        partition = next(v for v in _condition_values(kwargs["KeyConditionExpression"]) if ":" in v)
        self.partitions_queried.append(partition)
        rows = self.rows_by_partition.get(partition, [])
        start = (kwargs.get("ExclusiveStartKey") or {}).get("offset", 0)
        page = rows[start:start + self.PAGE]
        resp = {"Items": page}
        if start + self.PAGE < len(rows):
            resp["LastEvaluatedKey"] = {"offset": start + self.PAGE}
        return resp


class _MainTable:
    """The V2 main execution table, read by primary key one execution at a time."""

    def __init__(self, rows_by_id):
        self.rows_by_id = rows_by_id
        self.reads = []

    def query(self, **kwargs):
        execution_id = _condition_values(kwargs["KeyConditionExpression"])[0]
        self.reads.append(execution_id)
        row = self.rows_by_id.get(execution_id)
        return {"Items": [row] if row else []}


def _input_row(index, partition, composite=COMPOSITE, file_key="/f.glb"):
    """One input row as build_workflow_execution_input_record writes it."""
    database_id, asset_id = partition.split(":")
    workflow_database_id, workflow_id = (composite.split(":") if composite else ("", ""))
    return {
        "workflowExecutionId": f"e{index:031d}",
        "databaseId:assetId": partition,
        "databaseId:assetId:inputAssetFileKey": f"{partition}:{file_key}",
        "databaseId": database_id, "assetId": asset_id,
        "inputAssetFileKey": file_key,
        "workflowDatabaseId": workflow_database_id, "workflowId": workflow_id,
        "executionStartDate": f"2026-01-01T00:00:{index % 60:02d}Z",
    }


def _running_main_row(execution_id):
    return {"workflowExecutionId": execution_id,
            "workflowDatabaseId:workflowId": COMPOSITE,
            "workflow_execution_arn": "arn:ex", "executionStopDate": ""}


def _guard(rows_by_partition, main_rows, restriction="perAsset", selected=None):
    """Run _running_execution_exists over the fakes; returns (result_or_raise, inputs, main)."""
    inputs_table = _InputsTable(rows_by_partition)
    main_table = _MainTable(main_rows)
    selected_inputs = selected if selected is not None else [
        {"databaseId": p.split(":")[0], "assetId": p.split(":")[1], "relativeFileKey": "/f.glb"}
        for p in rows_by_partition]
    asset_records = {(i["databaseId"], i["assetId"]): {"assetLocation": {"Key": ""}}
                     for i in selected_inputs}

    def _table(name):
        return {ewv2.workflow_execution_inputs_table: inputs_table,
                ewv2.workflow_execution_database_v2: main_table}.get(name, MagicMock())

    with patch.object(ewv2.dynamodb, "Table", side_effect=_table), \
            patch(f"{MOD}.sfn_client") as sfn:
        # No stopDate in the describe response == still running.
        sfn.describe_execution.return_value = {"status": "RUNNING"}
        result = ewv2._running_execution_exists(
            WF_DB, WF_ID, selected_inputs, asset_records, restriction)
    return result, inputs_table, main_table


@pytest.mark.unit
class TestConcurrencyGuardBudgetIsPerPartition:
    """S2-BACKEND-042: one shared counter let the first asset's history spend the whole budget."""

    def test_candidates_are_inspected_from_every_selected_asset(self):
        # Two assets, each with far more executions of THIS workflow than one partition's share. With a
        # single shared counter the first asset spent all of it and the second contributed no candidate
        # at all — so a run conflicting on that second asset was invisible. Asserted on the executions
        # actually CONFIRMED (a main-row read), not on the queries issued: entering a partition's
        # generator issues one query even when there is no budget left to inspect its rows.
        #
        # Which asset a set iterates first is not fixed, so the claim is deliberately symmetric: both
        # ranges contribute. Counts are not pinned — a smaller or larger share still satisfies it.
        # More than the WHOLE budget per asset, derived from the constant rather than a literal, so
        # the bound still binds if the budget is retuned.
        deep = 2 * ewv2.MAX_CONCURRENCY_CANDIDATES_INSPECTED + 10
        partitions = {"db1:a1": [_input_row(i, "db1:a1") for i in range(deep)],
                      "db1:a2": [_input_row(1000 + i, "db1:a2") for i in range(deep)]}
        # Built here rather than through `_guard` so the fakes can be inspected afterwards.
        inputs_table = _InputsTable(partitions)
        main_table = _MainTable({})
        selected = [{"databaseId": "db1", "assetId": a, "relativeFileKey": "/f.glb"}
                    for a in ("a1", "a2")]

        def _table(name):
            return {ewv2.workflow_execution_inputs_table: inputs_table,
                    ewv2.workflow_execution_database_v2: main_table}.get(name, MagicMock())

        with patch.object(ewv2.dynamodb, "Table", side_effect=_table), \
                patch(f"{MOD}.sfn_client"):
            ewv2._running_execution_exists(WF_DB, WF_ID, selected, {
                ("db1", "a1"): {}, ("db1", "a2"): {}}, "perAsset")
        assert set(inputs_table.partitions_queried) == {"db1:a1", "db1:a2"}
        # The ids encode which asset a candidate came from (a2's start at 1000).
        indexes = [int(execution_id.lstrip("e0") or "0") for execution_id in main_table.reads]
        assert any(i < 1000 for i in indexes), "no candidate from the first asset was confirmed"
        assert any(i >= 1000 for i in indexes), (
            "no candidate from the second asset was confirmed: its share of the budget was spent "
            "elsewhere, which is what made a conflict there invisible")

    def test_a_spent_budget_does_not_deny_the_launch(self):
        # An exhausted budget is a fact about this request, not evidence of a conflict. Denying on it
        # was unrecoverable — an asset's execution count only grows, so every later launch of the
        # workflow failed identically, with nothing the caller (or the SYSTEM_USER trigger dispatcher)
        # could change. The guard reports the incompleteness instead; that half is asserted in
        # test_r2_concurrency_and_abort_bounds.py, which also holds the running-execution controls.
        deep = 2 * ewv2.MAX_CONCURRENCY_CANDIDATES_INSPECTED + 10
        partitions = {"db1:a1": [_input_row(i, "db1:a1") for i in range(deep)]}
        result, _inputs, _main = _guard(partitions, {})
        assert result is False

    def test_a_conflict_that_is_provable_is_still_reported_as_a_conflict(self):
        # The raise must not pre-empt a real answer: a running execution found in ANY partition is a
        # conflict, reported as True so the caller gets the "already running" 400 rather than the
        # could-not-confirm one.
        rows = [_input_row(i, "db1:a1") for i in range(3)]
        result, _inputs, main = _guard(
            {"db1:a1": rows}, {rows[1]["workflowExecutionId"]: _running_main_row(
                rows[1]["workflowExecutionId"])})
        assert result is True

    def test_no_conflict_within_the_budget_answers_false(self):
        # Control for the two above: an ordinary asset with a short history and nothing running must
        # still launch. Without this, a guard that raised unconditionally would pass them both.
        rows = [_input_row(i, "db1:a1") for i in range(3)]
        result, _inputs, _main = _guard({"db1:a1": rows}, {})
        assert result is False

    def test_a_restriction_of_none_never_reads_anything(self):
        result, inputs_table, _main = _guard(
            {"db1:a1": [_input_row(0, "db1:a1")]}, {}, restriction="none")
        assert result is False
        assert inputs_table.queries == 0


@pytest.mark.unit
class TestConcurrencyCandidatesAreThisWorkflowsOnly:
    """S2-BACKEND-042: the by-asset index is not partitioned by workflow, so an unrelated workflow's
    executions consumed the budget before a conflicting run of THIS workflow was ever examined."""

    def test_another_workflows_executions_do_not_consume_the_budget(self):
        # One asset, 300 rows belonging to a DIFFERENT workflow, then this workflow's own row whose
        # execution is running. Deliberately a single partition, so the outcome does not depend on
        # which partition a set happens to iterate first.
        other = [_input_row(i, "db1:a1", composite=OTHER_COMPOSITE)
                 for i in range(2 * ewv2.MAX_CONCURRENCY_CANDIDATES_INSPECTED + 10)]
        mine = _input_row(999, "db1:a1")
        result, _inputs, main = _guard(
            {"db1:a1": other + [mine]},
            {mine["workflowExecutionId"]: _running_main_row(mine["workflowExecutionId"])})
        assert result is True, "the conflicting run of this workflow was never inspected"
        # And the unrelated rows cost no main-row read at all.
        assert main.reads == [mine["workflowExecutionId"]]

    def test_a_row_without_workflow_ids_is_still_a_candidate(self):
        # A row written before the workflow ids were stored cannot be judged from the index, so it
        # keeps its main-row read: an unlabelled row costs a read rather than a missed conflict.
        legacy = _input_row(7, "db1:a1", composite="")
        result, _inputs, main = _guard(
            {"db1:a1": [legacy]},
            {legacy["workflowExecutionId"]: _running_main_row(legacy["workflowExecutionId"])})
        assert result is True
        assert main.reads == [legacy["workflowExecutionId"]]

    def test_per_input_file_still_narrows_to_the_selected_keys(self):
        # Control on the other filter in the same generator: perInputFile must keep discarding rows for
        # files the run did not select, whatever the workflow filter does.
        selected = [{"databaseId": "db1", "assetId": "a1", "relativeFileKey": "/wanted.glb"}]
        rows = [_input_row(1, "db1:a1", file_key="/other.glb"),
                _input_row(2, "db1:a1", file_key="/wanted.glb")]
        inputs_table = _InputsTable({"db1:a1": rows})
        main_table = _MainTable({rows[1]["workflowExecutionId"]: _running_main_row(
            rows[1]["workflowExecutionId"])})

        def _table(name):
            return {ewv2.workflow_execution_inputs_table: inputs_table,
                    ewv2.workflow_execution_database_v2: main_table}.get(name, MagicMock())

        with patch.object(ewv2.dynamodb, "Table", side_effect=_table), \
                patch(f"{MOD}.sfn_client") as sfn:
            sfn.describe_execution.return_value = {"status": "RUNNING"}
            result = ewv2._running_execution_exists(
                WF_DB, WF_ID, selected,
                {("db1", "a1"): {"assetLocation": {"Key": ""}}}, "perInputFile")
        assert result is True
        assert main_table.reads == [rows[1]["workflowExecutionId"]], (
            "a row for an unselected file must not be inspected under perInputFile")


@pytest.mark.unit
class TestRenderedConfigTooLargeIsACallerError:
    """S2-BACKEND-103: the error class carries the limit so the caller can be told; nothing caught it."""

    @staticmethod
    def _event():
        return {"requestContext": {"http": {"method": "POST", "path": "/workflows/db/wf/execute"}},
                "pathParameters": {"workflowDatabaseId": "db", "workflowId": "wf"},
                "queryStringParameters": None, "body": "{}",
                "headers": {"authorization": "Bearer t"}}

    def _run(self, error):
        enforcer = MagicMock()
        enforcer.enforceAPI.return_value = True
        with patch(f"{MOD}.request_to_claims", return_value={"tokens": ["u1"], "roles": []}), \
                patch(f"{MOD}.CasbinEnforcer", return_value=enforcer), \
                patch(f"{MOD}.handle_post_request", side_effect=error):
            return ewv2.lambda_handler(self._event(), MagicMock())

    def test_an_oversized_render_answers_400_naming_the_limit(self):
        resp = self._run(tr.RenderedConfigTooLargeError())
        assert resp["statusCode"] == 400
        assert str(tr.MAX_RENDERED_CONFIG_LENGTH) in json.loads(resp["body"])["message"]

    def test_an_unexpected_failure_still_answers_500(self):
        # Positive control: the new arm must not have widened into a blanket 400, which would hide a
        # real server fault behind a caller-error response.
        assert self._run(RuntimeError("boom"))["statusCode"] == 500

    def test_the_output_path_extension_render_reports_it_as_a_client_error(self):
        # The other render site in the launch path caught only MissingTemplateTagError, so the same
        # amplification in an output-path extension reached the terminal handler arm.
        with patch.object(tr, "render_config", side_effect=tr.RenderedConfigTooLargeError()):
            with pytest.raises(VAMSGeneralErrorResponse) as raised:
                ewv2._render_output_path_extension("{{inputMetadataObject}}", {}, {})
        assert str(tr.MAX_RENDERED_CONFIG_LENGTH) in str(raised.value)


@pytest.mark.unit
class TestLaunchRendersUnderTheDeclaredConfigFormat:
    """S2-BACKEND-041: the launch render used the default JSON escape for every format, so an `xml`
    body emitted bare markup while stage 1 had escaped the same body's user tags for `xml`."""

    # A file name whose characters are legal in an S3 key and in a VAMS file name, and which the XML
    # escape must convert: '&' alone makes the document ill-formed, and '</path><path>' would inject
    # elements into the configuration the pipeline runs against.
    FILE_KEY = "/parts/gear&pinion</path><path>.step"
    BODY = "<input><path>{{firstAssetFileKey}}</path></input>"

    def _launch(self, config_format):
        pipeline = {
            "pipelineId": "p1", "databaseId": "GLOBAL", "_jobName": "p1",
            "systemConfig": {"inputFileArity": "one"},
            "executionConfig": {"executionType": "Lambda", "waitForCallback": "Disabled"},
        }
        workflow = {
            "workflowId": WF_ID, "databaseId": WF_DB,
            "workflow_arn": "arn:aws:states:us-east-1:1:stateMachine:wf1",
            "jobNames": ["uuid-p1"], "systemConfig": {"metadataInputs": {}},
        }
        selected_inputs = [{"databaseId": "db1", "assetId": "a1",
                            "relativeFileKey": self.FILE_KEY, "versionId": ""}]
        asset_records = {("db1", "a1"): {
            "databaseId": "db1", "assetId": "a1",
            "assetLocation": {"Key": "a1/"}, "bucketId": "b1"}}
        resolved = {"GLOBAL:p1": {"renderedConfig": self.BODY, "configFormat": config_format}}
        with patch(f"{MOD}.s3c") as s3, \
                patch(f"{MOD}.sfn_client") as sfn, \
                patch(f"{MOD}._asset_bucket_details", return_value={"bucketName": "asset-bucket"}), \
                patch(f"{MOD}._persist_execution_records"):
            sfn.start_execution.return_value = {"executionArn": "arn:exec"}
            execution_id = ewv2._launch_workflow(
                workflow, [pipeline], resolved, selected_inputs, asset_records,
                None, "db1", "a1", "run-bucket", {}, "Manual", "", "user@example.com", "")
            puts = {c.kwargs["Key"]: c.kwargs["Body"] for c in s3.put_object.call_args_list}
        return puts[er.pipeline_input_config_key(execution_id, 1)].decode("utf-8")

    def test_an_xml_body_gets_xml_character_references(self):
        body = self._launch("xml")
        assert "&amp;" in body and "&lt;/path&gt;" in body
        assert "gear&pinion</path>" not in body, (
            "raw markup from a file name reached the configuration document")

    def test_a_json_body_keeps_the_json_escape(self):
        # Control: threading the format must not push XML escapes into the 30 shipped json templates,
        # where '&' and '<' are ordinary characters inside a JSON string.
        body = self._launch("json")
        assert "&amp;" not in body
        assert "gear&pinion" in body
