# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The log sources a pipeline execution can offer, and how the logs API reads and reports them.

A source is identified by WHERE it is (kind, group, stream, prefix) — never by its label — so the same
group spelled with and without the ':*' wildcard, or registered twice, is one source with one logId."""

import hashlib
import json
import os

import botocore
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

from backend.backend.common.workflows import availableLogs as al  # noqa: E402
from backend.backend.handlers.workflows import executionService as le  # noqa: E402

MOD = "backend.backend.handlers.workflows.executionService"
EXEC_ID = "e1000000000000000000000000000001"
REFERENCE = "arn:aws:logs:us-west-2:123456789012:log-group:/aws/vendedlogs/vamsPipelineWorkflowsabc:*"
BATCH_ARN = "arn:aws:logs:us-west-2:123456789012:log-group:/aws/batch/job"
SM_LOG_ARN = "arn:aws:logs:us-west-2:123456789012:log-group:/aws/vendedlogs/VAMSstateMachine-thumb:*"
LAMBDA_ARN = "arn:aws:logs:us-west-2:123456789012:log-group:/aws/lambda/vams-vamsExecuteThumb:*"


def _registered(arn=BATCH_ARN, name="", stream="", prefix="", stage="", label="", source=""):
    return {"logGroupArn": arn, "logGroupName": name, "logStreamName": stream, "logStreamPrefix": prefix,
            "stageName": stage, "label": label, "sourceType": source}


@pytest.mark.unit
class TestIdentityHelpers:
    def test_wildcard_and_name_extraction(self):
        assert al.strip_wildcard(SM_LOG_ARN) == SM_LOG_ARN[:-2]
        assert al.strip_wildcard(BATCH_ARN) == BATCH_ARN
        assert al.log_group_name_from_arn(SM_LOG_ARN) == "/aws/vendedlogs/VAMSstateMachine-thumb"
        assert al.log_group_name_from_arn("not-an-arn") == ""

    def test_a_name_only_entry_derives_its_arn_from_the_reference(self):
        assert al.derive_log_group_arn("/aws/batch/job", REFERENCE) == BATCH_ARN
        assert al.derive_log_group_arn("/aws/batch/job", "") == ""
        assert al.derive_log_group_arn("", REFERENCE) == ""
        gov = "arn:aws-us-gov:logs:us-gov-west-1:123456789012:log-group:/g"
        assert al.derive_log_group_arn("/x", gov).startswith("arn:aws-us-gov:logs:us-gov-west-1:123456789012:")

    def test_log_id_is_deterministic_and_ignores_the_wildcard_spelling(self):
        with_star = al.log_id("registered", SM_LOG_ARN, "", "")
        without = al.log_id("registered", SM_LOG_ARN[:-2], "", "")
        assert with_star == without
        assert len(with_star) == 16 and int(with_star, 16) >= 0
        expected = hashlib.sha256(json.dumps(
            ["registered", SM_LOG_ARN[:-2], "", ""]).encode("utf-8")).hexdigest()[:16]
        assert with_star == expected

    def test_log_id_changes_with_kind_stream_and_prefix(self):
        base = al.log_id("registered", BATCH_ARN, "", "")
        assert al.log_id("invocation", BATCH_ARN, "", "") != base
        assert al.log_id("registered", BATCH_ARN, "s", "") != base
        assert al.log_id("registered", BATCH_ARN, "", "p/") != base

    @pytest.mark.parametrize("name,source_type", [
        ("/aws/lambda/vams-fn", "lambda"),
        ("/aws/vendedlogs/VAMSstateMachine-thumb", "stateMachine"),
        ("/aws/vendedlogs/VAMSStateMachine-coord", "stateMachine"),
        ("/aws/batch/job", "batch"),
        ("/aws/vendedlogs/Pipelines/rapid", "container"),
        ("/custom/group", "custom"),
    ])
    def test_source_type_is_derived_from_the_group_name(self, name, source_type):
        assert al.derive_source_type(name) == source_type

    def test_label_is_the_group_name_tail(self):
        assert al.derive_label("/aws/vendedlogs/VAMSstateMachine-thumb") == "VAMSstateMachine-thumb"
        assert al.derive_label("/aws/batch/job") == "job"
        assert al.derive_label("flat") == "flat"


@pytest.mark.unit
class TestBuildAvailableLogs:
    def test_three_kinds_in_order_with_derived_fields_and_no_arns(self):
        entries = al.build_available_logs(
            LAMBDA_ARN,
            [_registered(prefix="thumb-jd/default/", stage="Preview3dThumbnailBatchJob", source="batch",
                         label="Preview3dThumbnailBatchJob container")],
            [{"logGroupArn": SM_LOG_ARN, "stageName": "", "label": "thumb state machine",
              "executionArn": "arn:aws:states:us-west-2:123456789012:execution:sm:x"}],
            REFERENCE)
        public = [al.public_entry(e) for e in entries]
        assert [e["kind"] for e in public] == ["invocation", "registered", "subStateMachine"]
        assert public[0]["logGroupName"] == "/aws/lambda/vams-vamsExecuteThumb"
        assert public[0]["sourceType"] == "lambda" and public[0]["label"] == "vams-vamsExecuteThumb"
        assert public[1]["sourceType"] == "batch" and public[1]["stageName"] == "Preview3dThumbnailBatchJob"
        assert public[1]["logStreamPrefix"] == "thumb-jd/default/"
        assert public[2]["sourceType"] == "stateMachine" and public[2]["label"] == "thumb state machine"
        for entry in public:
            assert set(entry) == set(al.PUBLIC_KEYS)
            assert "arn:" not in json.dumps(entry)
        assert entries[2]["_executionArns"] == ["arn:aws:states:us-west-2:123456789012:execution:sm:x"]

    def test_a_name_only_registration_is_listed_through_the_derived_arn(self):
        entries = al.build_available_logs("", [_registered(arn="", name="/aws/batch/job")], [], REFERENCE)
        assert len(entries) == 1
        assert entries[0]["_logGroupArn"] == BATCH_ARN
        assert entries[0]["logGroupName"] == "/aws/batch/job"

    def test_a_name_only_registration_without_a_reference_is_skipped(self):
        assert al.build_available_logs("", [_registered(arn="", name="/aws/batch/job")], [], "") == []

    def test_a_sub_state_machine_group_already_registered_is_one_source_with_the_registered_log_id(self):
        entries = al.build_available_logs(
            "", [_registered(arn=SM_LOG_ARN[:-2], source="stateMachine")],
            [{"logGroupArn": SM_LOG_ARN, "stageName": "", "label": "",
              "executionArn": "arn:aws:states:us-west-2:123456789012:execution:sm:x"}], REFERENCE)
        assert len(entries) == 1
        assert entries[0]["kind"] == "registered"
        assert entries[0]["logId"] == al.log_id("registered", SM_LOG_ARN, "", "")
        # The sub-execution still hangs off the merged entry so its history can be served under it.
        assert entries[0]["_executionArns"] == ["arn:aws:states:us-west-2:123456789012:execution:sm:x"]

    def test_stored_duplicates_collapse_and_fill_empty_descriptive_fields(self):
        legacy = _registered(prefix="jd/default/")
        described = _registered(prefix="jd/default/", stage="Batch", label="Batch container", source="batch")
        entries = al.build_available_logs("", [legacy, described], [], REFERENCE)
        assert len(entries) == 1
        assert entries[0]["stageName"] == "Batch" and entries[0]["label"] == "Batch container"
        assert entries[0]["sourceType"] == "batch"
        assert len({e["logId"] for e in entries}) == 1

    def test_a_stored_label_is_not_overwritten_by_a_duplicate(self):
        first = _registered(stream="s", label="First")
        second = _registered(stream="s", label="Second")
        entries = al.build_available_logs("", [first, second], [], REFERENCE)
        assert entries[0]["label"] == "First"

    def test_non_dict_registered_entries_are_ignored(self):
        assert al.build_available_logs("", ["junk", None, {"logGroupArn": ""}], [], REFERENCE) == []


def _entry(kind="registered", arn=BATCH_ARN, stream="", prefix="", stage="", source=""):
    return {"kind": kind, "_logGroupArn": arn, "_executionArns": [], "logStreamName": stream,
            "logStreamPrefix": prefix, "stageName": stage, "label": "", "sourceType": source,
            "logGroupName": al.log_group_name_from_arn(arn), "logId": al.log_id(kind, arn, stream, prefix)}


@pytest.mark.unit
class TestReadPlanning:
    PREFIXES = ["thumb-jd/default/", "potree-jd/default/"]

    def test_a_registered_exact_stream_is_read_without_scope_terms(self):
        plan = al.plan_source_read(_entry(stream="thumb-jd/default/abc", source="batch"), self.PREFIXES)
        assert plan == {"logStreamName": "thumb-jd/default/abc", "logStreamPrefix": "",
                        "scoped": False, "unscoped": False}

    def test_a_batch_prefix_entry_uses_a_resolved_stream_under_a_registered_prefix_unscoped(self):
        plan = al.plan_source_read(_entry(prefix="thumb-jd/default/", source="batch", stage="B"),
                                   self.PREFIXES, resolved_stream="thumb-jd/default/task-9")
        assert plan == {"logStreamName": "thumb-jd/default/task-9", "logStreamPrefix": "",
                        "scoped": False, "unscoped": False}

    def test_a_resolved_stream_outside_every_registered_prefix_keeps_the_scope_terms(self):
        plan = al.plan_source_read(_entry(prefix="thumb-jd/default/", source="batch"), self.PREFIXES,
                                   resolved_stream="other-jd/default/task-9")
        assert plan["logStreamName"] == "other-jd/default/task-9"
        assert plan["scoped"] is True and plan["unscoped"] is False

    def test_a_batch_prefix_entry_with_no_resolved_stream_is_the_unscoped_fallback(self):
        plan = al.plan_source_read(_entry(prefix="thumb-jd/default/", source="batch"), self.PREFIXES)
        assert plan == {"logStreamName": "", "logStreamPrefix": "thumb-jd/default/",
                        "scoped": True, "unscoped": True}

    def test_every_other_entry_keeps_todays_scoped_read(self):
        assert al.plan_source_read(_entry(kind="invocation", arn=LAMBDA_ARN), self.PREFIXES) == {
            "logStreamName": "", "logStreamPrefix": "", "scoped": True, "unscoped": False}
        assert al.plan_source_read(_entry(kind="subStateMachine", arn=SM_LOG_ARN), self.PREFIXES)["scoped"] is True
        non_batch_prefix = al.plan_source_read(_entry(prefix="ecs/", source="container"), self.PREFIXES)
        assert non_batch_prefix == {"logStreamName": "", "logStreamPrefix": "ecs/", "scoped": True,
                                    "unscoped": False}
        # A resolved stream is only consulted for batch sources.
        assert al.plan_source_read(_entry(prefix="ecs/", source="container"), self.PREFIXES,
                                   resolved_stream="ecs/x")["logStreamName"] == ""

    def test_registered_prefixes_lists_every_non_empty_stored_prefix(self):
        assert al.registered_prefixes([_registered(prefix="a/"), _registered(), _registered(prefix="b/"), "x"]) \
            == ["a/", "b/"]

    def test_batch_stream_resolves_from_the_stage_of_the_same_name_first(self):
        summaries = [
            {"resourceType": "stepFunctionsExecution", "stageName": "", "stages": [
                {"stageName": "Other", "batch": {"jobId": "j1", "logStreamName": "jd/default/other"}},
                {"stageName": "Preview3dThumbnailBatchJob",
                 "batch": {"jobId": "j2", "logStreamName": "jd/default/mine"}}]},
            {"resourceType": "batchJob", "stageName": "", "stages": [],
             "batch": {"jobId": "j3", "logStreamName": "jd/default/self-submitted"}},
        ]
        assert al.resolve_batch_stream(_entry(prefix="jd/default/", stage="Preview3dThumbnailBatchJob"),
                                       summaries) == "jd/default/mine"

    def test_batch_stream_falls_back_to_a_registered_batch_job(self):
        summaries = [{"resourceType": "batchJob", "stageName": "ExecuteBatchJobState", "stages": [],
                      "batch": {"jobId": "j3", "logStreamName": "jd/default/self"}}]
        assert al.resolve_batch_stream(_entry(prefix="jd/default/", stage="ExecuteBatchJobState"), summaries) \
            == "jd/default/self"
        # An unlabelled entry accepts any registered job; a labelled one only a matching or unlabelled job.
        assert al.resolve_batch_stream(_entry(prefix="jd/default/"), summaries) == "jd/default/self"
        assert al.resolve_batch_stream(_entry(prefix="jd/default/", stage="Elsewhere"), summaries) == ""
        unlabelled_job = [{"resourceType": "batchJob", "stageName": "", "stages": [],
                           "batch": {"jobId": "j3", "logStreamName": "jd/default/self"}}]
        assert al.resolve_batch_stream(_entry(prefix="jd/default/", stage="Elsewhere"), unlabelled_job) \
            == "jd/default/self"

    def test_no_summary_no_stream(self):
        assert al.resolve_batch_stream(_entry(prefix="jd/default/", stage="X"), []) == ""
        assert al.resolve_batch_stream(_entry(prefix="jd/default/", stage="X"), None) == ""


@pytest.mark.unit
class TestClassification:
    def test_failures_map_to_denied_not_found_or_error(self):
        assert al.classify_read(False, "AccessDeniedException", None) == "denied"
        assert al.classify_read(False, "ResourceNotFoundException", None) == "notFound"
        # Any other failure -- a throttle, a CloudWatch token that belongs to another source, a location
        # that is not a log-group ARN -- is an error, not a denial; the warning names the code.
        assert al.STATUS_ERROR == "error"
        assert al.classify_read(False, "ThrottlingException", None) == "error"
        assert al.classify_read(False, "InvalidParameterException", None) == "error"
        assert al.classify_read(False, "unparseable log group ARN", None) == "error"

    def test_empty_means_no_events_and_no_token(self):
        assert al.classify_read(True, [], None) == "empty"
        # FilterLogEvents may return an empty page WITH a token; that is a read, not an empty source.
        assert al.classify_read(True, [], "tok") == "read"
        assert al.classify_read(True, [{"timestamp": 1, "message": "m"}], None) == "read"

    def test_an_unscoped_prefix_read_is_reported_unscoped_whatever_it_returned(self):
        assert al.classify_read(True, [], None, unscoped=True) == "unscoped"
        assert al.classify_read(True, [{"timestamp": 1, "message": "m"}], None, unscoped=True) == "unscoped"
        # A failure still wins.
        assert al.classify_read(False, "AccessDeniedException", None, unscoped=True) == "denied"

    def test_events_sort_by_timestamp_and_the_sort_is_stable(self):
        events = [{"timestamp": 3, "message": "c"}, {"timestamp": 1, "message": "a"},
                  {"timestamp": 3, "message": "c2"}, {"message": "no ts"}, {"timestamp": 2, "message": "b"}]
        assert [e["message"] for e in al.sort_events(events)] == ["no ts", "a", "b", "c", "c2"]
        assert al.sort_events([]) == []


@pytest.mark.unit
class TestAvailableLogsForPipeline:
    SM = "arn:aws:states:us-west-2:123456789012:stateMachine:VAMSstateMachine-thumb"
    EX = "arn:aws:states:us-west-2:123456789012:execution:VAMSstateMachine-thumb:run"

    def setup_method(self):
        le._state_machine_describe_cache.clear()

    def _describe(self, log_group=SM_LOG_ARN):
        return {"name": "VAMSstateMachine-thumb", "loggingConfiguration": {"destinations": [
            {"cloudWatchLogsLogGroup": {"logGroupArn": log_group}}]}}

    def test_invocation_registered_and_sub_state_machine_sources_are_listed(self):
        prow = {"pipelineExecutionType": "Lambda", "pipelineResourceArn": "vams-vamsExecuteThumb",
                "registeredLogs": [_registered(prefix="thumb-jd/default/", stage="Preview3dThumbnailBatchJob",
                                               source="batch")],
                "registeredSubExecutions": [{"resourceType": "stepFunctionsExecution",
                                             "stateMachineArn": self.SM, "executionArn": self.EX}]}
        with patch.object(le.sfn, "describe_state_machine", return_value=self._describe()) as describe:
            entries = le._available_logs_for_pipeline(prow, REFERENCE)
        describe.assert_called_once_with(stateMachineArn=self.SM)
        kinds = [e["kind"] for e in entries]
        assert kinds == ["invocation", "registered", "subStateMachine"]
        assert entries[0]["logGroupName"] == "/aws/lambda/vams-vamsExecuteThumb"
        assert entries[2]["logGroupName"] == "/aws/vendedlogs/VAMSstateMachine-thumb"
        assert entries[2]["_executionArns"] == [self.EX]
        assert entries[2]["label"] == "VAMSstateMachine-thumb" and entries[2]["sourceType"] == "stateMachine"

    def test_a_registered_copy_of_the_sub_state_machine_group_is_one_source(self):
        prow = {"registeredLogs": [_registered(arn=SM_LOG_ARN[:-2], label="thumb state machine",
                                               source="stateMachine")],
                "registeredSubExecutions": [{"resourceType": "stepFunctionsExecution",
                                             "stateMachineArn": self.SM, "executionArn": self.EX}]}
        with patch.object(le.sfn, "describe_state_machine", return_value=self._describe()):
            entries = le._available_logs_for_pipeline(prow, REFERENCE)
        assert len(entries) == 1 and entries[0]["kind"] == "registered"
        assert entries[0]["label"] == "thumb state machine"
        assert entries[0]["_executionArns"] == [self.EX]

    def test_the_state_machine_arn_is_derived_when_only_an_execution_arn_was_registered(self):
        prow = {"registeredLogs": [],
                "registeredSubExecutions": [{"resourceType": "stepFunctionsExecution", "executionArn": self.EX}]}
        with patch.object(le.sfn, "describe_state_machine", return_value=self._describe()) as describe:
            entries = le._available_logs_for_pipeline(prow, REFERENCE)
        describe.assert_called_once_with(stateMachineArn=self.SM)
        assert [e["kind"] for e in entries] == ["subStateMachine"]

    def test_a_machine_that_cannot_be_described_contributes_nothing_and_raises_nothing(self):
        prow = {"registeredLogs": [_registered(arn="", name="/aws/batch/job")],
                "registeredSubExecutions": [{"resourceType": "stepFunctionsExecution",
                                             "stateMachineArn": self.SM, "executionArn": self.EX}]}
        with patch.object(le.sfn, "describe_state_machine",
                          side_effect=botocore.exceptions.ClientError(
                              {"Error": {"Code": "AccessDeniedException"}}, "DescribeStateMachine")):
            entries = le._available_logs_for_pipeline(prow, REFERENCE)
        assert [e["kind"] for e in entries] == ["registered"]
        assert entries[0]["_logGroupArn"] == BATCH_ARN

    def test_two_subs_on_one_machine_describe_it_once(self):
        prow = {"registeredLogs": [],
                "registeredSubExecutions": [
                    {"resourceType": "stepFunctionsExecution", "stateMachineArn": self.SM, "executionArn": self.EX},
                    {"resourceType": "stepFunctionsExecution", "stateMachineArn": self.SM,
                     "executionArn": self.EX + "-2"}]}
        with patch.object(le.sfn, "describe_state_machine", return_value=self._describe()) as describe:
            entries = le._available_logs_for_pipeline(prow, REFERENCE)
        assert describe.call_count == 1
        assert len(entries) == 1 and entries[0]["_executionArns"] == [self.EX, self.EX + "-2"]

    def test_non_step_functions_subs_and_empty_rows_are_tolerated(self):
        with patch.object(le.sfn, "describe_state_machine") as describe:
            assert le._available_logs_for_pipeline({"registeredSubExecutions": [
                {"resourceType": "batchJob", "jobId": "j"}]}, REFERENCE) == []
            assert le._available_logs_for_pipeline(None, REFERENCE) == []
            assert le._available_logs_for_pipeline({}, "") == []
        describe.assert_not_called()


def _main_row():
    return {"workflowId": "wf", "workflowDatabaseId": "db", "executionLogGroupArn": REFERENCE,
            "workflow_execution_arn": "arn:aws:states:us-west-2:123456789012:execution:main:E"}


def _run_logs(prow, query, fetch_side_effect=None, fetch_return=(True, [], None), history=None,
              full_search=None, summaries=None):
    """get_execution_logs against one pipeline row with every AWS read stubbed. Returns (resp, fetch,
    history_mock, full_search_mock)."""
    le.claims_and_roles = {"tokens": ["u1"]}
    fetch_kwargs = {"side_effect": fetch_side_effect} if fetch_side_effect else {"return_value": fetch_return}
    with patch(f"{MOD}.get_execution_main_row", return_value=_main_row()), \
         patch(f"{MOD}.authorize_execution_access", return_value=(True, "")), \
         patch(f"{MOD}.get_pipeline_execution_rows", return_value=[prow]), \
         patch(f"{MOD}._full_log_search",
               return_value=full_search or {"events": [], "nextToken": None}) as search, \
         patch(f"{MOD}._sfn_execution_history_events",
               return_value=history or {"events": [], "nextToken": None}) as hist, \
         patch(f"{MOD}._batch_stream_summaries", return_value=summaries or []), \
         patch.object(le.sfn, "describe_state_machine", return_value={
             "name": "VAMSstateMachine-thumb",
             "loggingConfiguration": {"destinations": [{"cloudWatchLogsLogGroup": {"logGroupArn": SM_LOG_ARN}}]}}), \
         patch(f"{MOD}._fetch_registered_log_events", **fetch_kwargs) as fetch:
        le._state_machine_describe_cache.clear()
        resp = le.get_execution_logs({}, EXEC_ID, dict({"mode": "full", "pipelineExecutionId": "pe-1"}, **query))
    return resp, fetch, hist, search


def _prow(**overrides):
    row = {"pipelineExecutionId": "pe-1", "pipelineExecutionType": "Lambda",
           "pipelineResourceArn": "vams-vamsExecuteThumb",
           "registeredLogs": [_registered(prefix="thumb-jd/default/", stage="Preview3dThumbnailBatchJob",
                                          source="batch")],
           "registeredSubExecutions": [{"resourceType": "stepFunctionsExecution",
                                        "stateMachineArn": "arn:aws:states:us-west-2:123456789012:stateMachine:VAMSstateMachine-thumb",
                                        "executionArn": "arn:aws:states:us-west-2:123456789012:execution:VAMSstateMachine-thumb:run"}]}
    row.update(overrides)
    return row


def _body(resp):
    return json.loads(resp["body"])["message"]


@pytest.mark.unit
class TestLogsFanOut:
    def test_log_sources_report_every_available_log_with_a_status_and_count(self):
        def _fetch(arn, stream, params, **kwargs):
            if "/aws/lambda/" in arn:
                return True, [{"timestamp": 5, "message": "START", "logGroupArn": arn}], None
            if "/aws/batch/job" in arn:
                return False, "AccessDeniedException", None
            return True, [], "more"
        resp, _fetch_mock, _hist, _search = _run_logs(_prow(), {}, fetch_side_effect=_fetch)
        assert resp["statusCode"] == 200
        body = _body(resp)
        by_kind = {s["kind"]: s for s in body["logSources"]}
        assert by_kind["invocation"]["status"] == "read" and by_kind["invocation"]["eventCount"] == 1
        assert by_kind["registered"]["status"] == "denied" and by_kind["registered"]["eventCount"] == 0
        # An empty page WITH a continuation token is a read, not an empty source.
        assert by_kind["subStateMachine"]["status"] == "read" and by_kind["subStateMachine"]["eventCount"] == 0
        for source in body["logSources"]:
            assert set(source) == set(al.PUBLIC_KEYS) | {"status", "eventCount"}
            assert "arn:" not in json.dumps({k: v for k, v in source.items()})
        assert any("Sub-process log retrieval failed for" in w and "AccessDeniedException" in w
                   for w in body["warnings"])

    def test_sub_process_events_carry_log_ids_name_their_group_and_are_sorted(self):
        def _fetch(arn, stream, params, **kwargs):
            name = al.log_group_name_from_arn(arn)
            if "/aws/lambda/" in arn:
                return True, [{"timestamp": 50, "message": "late lambda", "logGroupName": name}], None
            return True, [{"timestamp": 10, "message": "early", "logGroupName": name}], None
        history = {"events": [{"timestamp": 30, "message": "TaskStateEntered: Batch"}], "nextToken": None}
        resp, _f, _h, _s = _run_logs(_prow(), {}, fetch_side_effect=_fetch, history=history)
        body = _body(resp)
        events = body["subProcessEvents"]
        assert [e["timestamp"] for e in events] == sorted(e["timestamp"] for e in events)
        ids = {s["kind"]: s["logId"] for s in body["logSources"]}
        by_message = {e["message"]: e for e in events}
        assert by_message["late lambda"]["logId"] == ids["invocation"]
        assert by_message["late lambda"]["logGroupName"] == "/aws/lambda/vams-vamsExecuteThumb"
        # Names only: no event, source or warning of the response carries a log-group ARN.
        assert "arn:" not in json.dumps(body)
        # The sub-SFN history line is stamped with the logId of the source its state machine logs to.
        assert by_message["TaskStateEntered: Batch"]["logId"] == ids["subStateMachine"]

    def test_sub_sfn_history_is_still_read_when_the_machine_has_no_log_destination(self):
        history = {"events": [{"timestamp": 30, "message": "ExecutionStarted"}], "nextToken": None}
        le.claims_and_roles = {"tokens": ["u1"]}
        with patch(f"{MOD}.get_execution_main_row", return_value=_main_row()), \
             patch(f"{MOD}.authorize_execution_access", return_value=(True, "")), \
             patch(f"{MOD}.get_pipeline_execution_rows", return_value=[_prow(registeredLogs=[])]), \
             patch(f"{MOD}._full_log_search", return_value={"events": [], "nextToken": None}), \
             patch(f"{MOD}._sfn_execution_history_events", return_value=history) as hist, \
             patch(f"{MOD}._batch_stream_summaries", return_value=[]), \
             patch.object(le.sfn, "describe_state_machine",
                          return_value={"name": "x", "loggingConfiguration": {"destinations": []}}), \
             patch(f"{MOD}._fetch_registered_log_events", return_value=(True, [], None)):
            le._state_machine_describe_cache.clear()
            resp = le.get_execution_logs({}, EXEC_ID, {"mode": "full", "pipelineExecutionId": "pe-1"})
        body = _body(resp)
        assert hist.call_count == 1
        assert body["subProcessEvents"][0]["message"] == "ExecutionStarted"
        assert body["subProcessEvents"][0]["logId"] == ""
        assert [s["kind"] for s in body["logSources"]] == ["invocation"]

    def test_registered_logs_past_the_inspect_cap_are_skipped_and_warned(self):
        logs = [_registered(stream=f"s{i}") for i in range(le.MAX_REGISTERED_LOGS_INSPECTED + 3)]
        resp, fetch, _h, _s = _run_logs(_prow(registeredLogs=logs, registeredSubExecutions=[]), {})
        body = _body(resp)
        registered = [s for s in body["logSources"] if s["kind"] == "registered"]
        assert len(registered) == le.MAX_REGISTERED_LOGS_INSPECTED + 3
        assert [s["status"] for s in registered].count("skipped") == 3
        assert registered[-1]["status"] == "skipped"
        # One read for the invocation log plus twenty registered reads, none for the skipped ones.
        assert fetch.call_count == 1 + le.MAX_REGISTERED_LOGS_INSPECTED
        assert any(f"Only the first {le.MAX_REGISTERED_LOGS_INSPECTED} of" in w for w in body["warnings"])

    def test_a_log_id_read_of_one_source_is_not_warned_about_the_registered_cap(self):
        # The cap is counted over the sources actually iterated: selecting one source out of a long
        # registration list skips nothing, so nothing is warned about, on every poll of that source.
        logs = [_registered(stream=f"s{i}") for i in range(le.MAX_REGISTERED_LOGS_INSPECTED + 5)]
        one = al.log_id("registered", BATCH_ARN, "s3", "")
        resp, fetch, _h, _s = _run_logs(_prow(registeredLogs=logs, registeredSubExecutions=[]), {"logId": one})
        body = _body(resp)
        assert fetch.call_count == 1
        assert [s["logId"] for s in body["logSources"]] == [one]
        assert body["logSources"][0]["status"] != "skipped"
        assert not any("Only the first" in w for w in body.get("warnings", []))

    def test_a_stage_name_matching_more_registered_sources_than_the_cap_still_skips_and_warns(self):
        # Positive control: a stage whose registered sources exceed the cap really is capped, and the
        # warning counts that stage's sources, not the whole registration list.
        logs = [_registered(stream=f"s{i}", stage="Batch") for i in range(le.MAX_REGISTERED_LOGS_INSPECTED + 3)]
        resp, fetch, _h, _s = _run_logs(_prow(registeredLogs=logs, registeredSubExecutions=[]), {"stageName": "Batch"})
        body = _body(resp)
        assert [s["status"] for s in body["logSources"]].count("skipped") == 3
        assert fetch.call_count == le.MAX_REGISTERED_LOGS_INSPECTED
        assert any(f"Only the first {le.MAX_REGISTERED_LOGS_INSPECTED} of {le.MAX_REGISTERED_LOGS_INSPECTED + 3}" in w
                   for w in body["warnings"])

    def test_a_stage_name_matching_fewer_sources_than_the_cap_is_not_warned_about_the_cap(self):
        logs = [_registered(stream=f"s{i}", stage="Batch" if i < 3 else "Other")
                for i in range(le.MAX_REGISTERED_LOGS_INSPECTED + 5)]
        resp, fetch, _h, _s = _run_logs(_prow(registeredLogs=logs, registeredSubExecutions=[]), {"stageName": "Batch"})
        body = _body(resp)
        assert len(body["logSources"]) == 3
        assert all(s["status"] != "skipped" for s in body["logSources"])
        assert not any("Only the first" in w for w in body.get("warnings", []))

    def test_a_throttled_or_misaddressed_read_is_an_error_not_a_denial(self):
        # A read that fails for a reason other than a missing group or a denied permission -- a
        # throttle, a nextToken minted for another source -- is `error`; `denied` is reserved for
        # AccessDenied so the UI does not report a permission problem that does not exist.
        def _fetch(arn, stream, params, **kwargs):
            if "/aws/batch/job" in arn:
                return False, "InvalidParameterException", None
            return True, [], None
        resp, _f, _h, _s = _run_logs(_prow(), {}, fetch_side_effect=_fetch)
        body = _body(resp)
        by_kind = {s["kind"]: s for s in body["logSources"]}
        assert by_kind["registered"]["status"] == "error" and by_kind["registered"]["eventCount"] == 0
        assert by_kind["invocation"]["status"] == "empty"
        assert any("InvalidParameterException" in w for w in body["warnings"])

    def test_the_whole_execution_view_is_unchanged_apart_from_no_log_sources(self):
        le.claims_and_roles = {"tokens": ["u1"]}
        with patch(f"{MOD}.get_execution_main_row", return_value=_main_row()), \
             patch(f"{MOD}.authorize_execution_access", return_value=(True, "")), \
             patch(f"{MOD}._full_log_search", return_value={"events": [{"timestamp": 1, "message": "m"}],
                                                            "nextToken": "cw"}), \
             patch(f"{MOD}._sfn_execution_history_events",
                   return_value={"events": [{"timestamp": 1, "message": "ExecutionStarted"}], "nextToken": None}):
            resp = le.get_execution_logs({}, EXEC_ID, {"mode": "full"})
        body = _body(resp)
        assert body["events"] == [{"timestamp": 1, "message": "m"}] and body["nextToken"] == "cw"
        assert body["sfnHistoryEvents"][0]["message"] == "ExecutionStarted"
        assert "logSources" not in body and "subProcessEvents" not in body

    def test_batch_stream_summaries_derive_stages_only_for_a_prefix_only_batch_source(self):
        # The one caller path that spends history pages on a logs GET: a Batch source registered with
        # a prefix and no stream. The summaries come back with the request-wide page budget applied.
        prow = _prow()
        sources = [_entry(prefix="thumb-jd/default/", stage="Preview3dThumbnailBatchJob", source="batch")]
        pending = [({"status": "SUCCEEDED"}, {"resourceType": "stepFunctionsExecution"})]
        with patch(f"{MOD}._pipeline_sub_executions", return_value=(pending, False, [])) as subs, \
             patch(f"{MOD}._fill_sub_execution_stages") as fill:
            out = le._batch_stream_summaries(prow, sources)
        subs.assert_called_once_with(prow)
        assert fill.call_args.args[0] == [({"status": "SUCCEEDED"}, {"resourceType": "stepFunctionsExecution"}, [])]
        assert fill.call_args.args[1] == [le.MAX_SUB_STAGE_HISTORY_PAGES_PER_REQUEST]
        assert out == [{"status": "SUCCEEDED"}]

    def test_batch_stream_summaries_cost_nothing_without_a_prefix_only_batch_source(self):
        sources = [_entry(kind="invocation", arn=LAMBDA_ARN),
                   _entry(stream="thumb-jd/default/task-1", source="batch"),
                   _entry(prefix="ecs/", source="container")]
        with patch(f"{MOD}._pipeline_sub_executions") as subs, \
             patch.object(le.sfn, "describe_execution") as describe:
            assert le._batch_stream_summaries(_prow(), sources) == []
        subs.assert_not_called()
        describe.assert_not_called()

    def test_a_prefix_only_batch_source_is_read_as_the_stream_describe_jobs_resolves(self):
        # End to end without the _batch_stream_summaries stub: the sub-state-machine history carries
        # only the submitted JobId (the built-in machines discard the SubmitJob result, as verified live on prod5),
        # DescribeJobs on that id names the stream, and the fan-out reads that exact stream without
        # scope terms because it lies under the prefix registered on this pipeline execution. The
        # source is `read`, not `unscoped`.
        import datetime
        t = datetime.datetime(2026, 9, 11, 10, 0, 0, tzinfo=datetime.timezone.utc)
        history = {"events": [
            {"id": 1, "previousEventId": 0, "type": "TaskStateEntered", "timestamp": t,
             "stateEnteredEventDetails": {"name": "Preview3dThumbnailBatchJob"}},
            {"id": 2, "previousEventId": 1, "type": "TaskScheduled", "timestamp": t,
             "taskScheduledEventDetails": {"resource": "submitJob.sync", "resourceType": "batch"}},
            {"id": 3, "previousEventId": 2, "type": "TaskSubmitted", "timestamp": t,
             "taskSubmittedEventDetails": {"resource": "submitJob.sync", "resourceType": "batch",
                                           "output": json.dumps({"JobArn": "arn:j", "JobId": "job-42",
                                                                 "JobName": "Preview3dThumbnailJob"})}},
            {"id": 4, "previousEventId": 3, "type": "TaskFailed", "timestamp": t,
             "taskFailedEventDetails": {"resource": "submitJob.sync", "resourceType": "batch",
                                        "error": "Pipeline Failure: Failed to load 3D file (.glb): incorrect header on GLB file",
                                        "cause": "See AWS cloudwatch logs for full error log and cause."}},
            {"id": 5, "previousEventId": 4, "type": "TaskStateExited", "timestamp": t,
             "stateExitedEventDetails": {"name": "Preview3dThumbnailBatchJob"}},
            {"id": 6, "previousEventId": 5, "type": "ExecutionFailed", "timestamp": t,
             "executionFailedEventDetails": {"error": "PipelineFailed", "cause": "x"}},
        ]}

        def _fetch(arn, stream, params, **kwargs):
            if "/aws/batch/job" in arn:
                return True, [{"timestamp": 9, "message": "Pipeline Failure: Failed to load 3D file",
                               "logGroupArn": arn}], None
            return True, [], None

        le.claims_and_roles = {"tokens": ["u1"]}
        le._state_machine_describe_cache.clear()
        le._batch_job_describe_cache.clear()
        with patch(f"{MOD}.get_execution_main_row", return_value=_main_row()), \
             patch(f"{MOD}.authorize_execution_access", return_value=(True, "")), \
             patch(f"{MOD}.get_pipeline_execution_rows", return_value=[_prow()]), \
             patch(f"{MOD}._full_log_search", return_value={"events": [], "nextToken": None}), \
             patch(f"{MOD}._sfn_execution_history_events", return_value={"events": [], "nextToken": None}), \
             patch.object(le.sfn, "describe_state_machine", return_value={
                 "name": "VAMSstateMachine-thumb",
                 "loggingConfiguration": {"destinations": [{"cloudWatchLogsLogGroup": {"logGroupArn": SM_LOG_ARN}}]}}), \
             patch.object(le.sfn, "describe_execution",
                          return_value={"status": "FAILED", "startDate": t, "stopDate": t}), \
             patch.object(le.sfn, "get_execution_history", return_value=history), \
             patch.object(le.batch_client, "describe_jobs", return_value={"jobs": [{
                 "jobId": "job-42", "status": "FAILED",
                 "container": {"logStreamName": "thumb-jd/default/task-42"}}]}) as describe_jobs, \
             patch(f"{MOD}._fetch_registered_log_events", side_effect=_fetch) as fetch:
            resp = le.get_execution_logs({}, EXEC_ID, {"mode": "full", "pipelineExecutionId": "pe-1"})
        describe_jobs.assert_called_once_with(jobs=["job-42"])
        call = next(c for c in fetch.call_args_list if "/aws/batch/job" in c.args[0])
        assert call.args[1] == "thumb-jd/default/task-42"
        assert call.kwargs.get("log_stream_prefix") == "" and not call.kwargs.get("scope_terms")
        body = _body(resp)
        batch_source = next(s for s in body["logSources"] if s["sourceType"] == "batch")
        assert batch_source["status"] == "read" and batch_source["eventCount"] == 1


@pytest.mark.unit
class TestLogIdAndStageNameParams:
    def _ids(self, prow=None):
        le._state_machine_describe_cache.clear()
        with patch.object(le.sfn, "describe_state_machine", return_value={
                "name": "VAMSstateMachine-thumb",
                "loggingConfiguration": {"destinations": [{"cloudWatchLogsLogGroup": {"logGroupArn": SM_LOG_ARN}}]}}):
            entries = le._available_logs_for_pipeline(prow or _prow(), REFERENCE)
        return {e["kind"]: e["logId"] for e in entries}

    def test_log_id_reads_only_that_source_with_its_token_and_skips_the_shared_search(self):
        # The registered source is a prefix-only Batch entry, so the read is exact (and `read`) only
        # when the sub-execution stages resolve its stream — the same plan the fan-out uses.
        batch_id = self._ids()["registered"]
        fetch_return = (True, [{"timestamp": 7, "message": "container line",
                                "logGroupName": al.log_group_name_from_arn(BATCH_ARN)}], "cw-2")
        summaries = [{"resourceType": "stepFunctionsExecution", "stageName": "", "stages": [
            {"stageName": "Preview3dThumbnailBatchJob",
             "batch": {"jobId": "j", "logStreamName": "thumb-jd/default/task-1"}}]}]
        resp, fetch, hist, search = _run_logs(_prow(), {"logId": batch_id, "nextToken": "cw-1"},
                                              fetch_return=fetch_return, summaries=summaries)
        assert resp["statusCode"] == 200
        body = _body(resp)
        search.assert_not_called()
        assert fetch.call_count == 1
        assert fetch.call_args.args[1] == "thumb-jd/default/task-1"
        assert not fetch.call_args.kwargs.get("scope_terms")
        assert fetch.call_args.kwargs["next_token"] == "cw-1"
        assert body["events"] == [{"timestamp": 7, "message": "container line",
                                   "logGroupName": al.log_group_name_from_arn(BATCH_ARN), "logId": batch_id}]
        assert "arn:" not in json.dumps(body)
        assert body["nextToken"] == "cw-2"
        assert [s["logId"] for s in body["logSources"]] == [batch_id]
        assert body["logSources"][0]["status"] == "read" and body["logSources"][0]["eventCount"] == 1
        assert "subProcessEvents" not in body and "sfnHistoryEvents" not in body
        # A Batch source's sub-execution history is not this source's; nothing was asked of Step Functions.
        hist.assert_not_called()

    def test_log_id_of_a_prefix_only_batch_source_with_no_resolved_stream_is_unscoped_yet_returns_its_events(self):
        # Positive control for the test above: without a resolved stream the same source is read by
        # prefix with scope terms, reported `unscoped`, and its events are still the response's events.
        batch_id = self._ids()["registered"]
        fetch_return = (True, [{"timestamp": 7, "message": "container line",
                                "logGroupName": al.log_group_name_from_arn(BATCH_ARN)}], "cw-2")
        resp, fetch, _hist, search = _run_logs(_prow(), {"logId": batch_id}, fetch_return=fetch_return)
        assert resp["statusCode"] == 200
        body = _body(resp)
        search.assert_not_called()
        assert fetch.call_args.args[1] == "" and fetch.call_args.kwargs["log_stream_prefix"] == "thumb-jd/default/"
        assert fetch.call_args.kwargs["scope_terms"] == [EXEC_ID, "pe-1"]
        assert body["logSources"][0]["status"] == "unscoped" and body["logSources"][0]["eventCount"] == 1
        assert [e["message"] for e in body["events"]] == ["container line"]
        assert body["nextToken"] == "cw-2"

    def test_log_id_of_a_sub_state_machine_source_adds_its_history_first_page(self):
        sm_id = self._ids()["subStateMachine"]
        history = {"events": [{"timestamp": 30, "message": "TaskStateEntered: Batch"},
                              {"timestamp": 10, "message": "ExecutionStarted"}], "nextToken": None}
        resp, _fetch, hist, _search = _run_logs(_prow(), {"logId": sm_id, "nextToken": "cw-1"}, history=history)
        body = _body(resp)
        assert [e["message"] for e in body["sfnHistoryEvents"]] == ["ExecutionStarted", "TaskStateEntered: Batch"]
        assert all(e["logId"] == sm_id for e in body["sfnHistoryEvents"])
        # The helper receives the whole query-params object, CloudWatch token included; that it never
        # forwards the token to Step Functions is pinned by
        # test_the_cloudwatch_next_token_is_never_handed_to_step_functions (test_executionService_wb53.py),
        # not here.
        assert hist.call_args.args[1].get("nextToken") == "cw-1"
        assert hist.call_args.kwargs.get("stage_name") == ""

    def test_an_unknown_log_id_is_a_404_naming_the_pipeline_execution(self):
        resp, fetch, _h, search = _run_logs(_prow(), {"logId": "0123456789abcdef"})
        assert resp["statusCode"] == 404
        assert _body(resp) == "Log source not found for this pipeline execution"
        fetch.assert_not_called()
        search.assert_not_called()

    def test_log_id_or_stage_name_outside_the_full_pipeline_view_is_a_400_naming_the_rule(self):
        le.claims_and_roles = {"tokens": ["u1"]}
        with patch(f"{MOD}.get_execution_main_row", return_value=_main_row()), \
             patch(f"{MOD}.authorize_execution_access", return_value=(True, "")), \
             patch(f"{MOD}.get_pipeline_execution_rows", return_value=[_prow()]), \
             patch(f"{MOD}._query_all", return_value=[]), \
             patch(f"{MOD}._full_log_search", return_value={"events": [], "nextToken": None}):
            truncated = le.get_execution_logs({}, EXEC_ID, {"mode": "truncated", "pipelineExecutionId": "pe-1",
                                                            "logId": "0123456789abcdef"})
            unscoped = le.get_execution_logs({}, EXEC_ID, {"mode": "full", "stageName": "Batch"})
        for resp in (truncated, unscoped):
            assert resp["statusCode"] == 400
            assert json.loads(resp["body"])["message"] == le.LOG_SOURCE_PARAMS_RULE

    def test_stage_name_restricts_sources_and_sub_sfn_history(self):
        history = {"events": [{"timestamp": 30, "message": "TaskStateEntered: Preview3dThumbnailBatchJob"}],
                   "nextToken": None}
        resp, fetch, hist, _search = _run_logs(_prow(), {"stageName": "Preview3dThumbnailBatchJob"}, history=history)
        body = _body(resp)
        assert [s["kind"] for s in body["logSources"]] == ["registered"]
        assert body["logSources"][0]["stageName"] == "Preview3dThumbnailBatchJob"
        assert fetch.call_count == 1
        assert hist.call_args.kwargs["stage_name"] == "Preview3dThumbnailBatchJob"
        assert body["subProcessEvents"][0]["message"] == "TaskStateEntered: Preview3dThumbnailBatchJob"

    def test_stage_name_with_no_matching_source_returns_an_empty_source_list_not_an_error(self):
        resp, fetch, _h, _s = _run_logs(_prow(), {"stageName": "Nowhere"})
        assert resp["statusCode"] == 200
        assert _body(resp)["logSources"] == []
        fetch.assert_not_called()
