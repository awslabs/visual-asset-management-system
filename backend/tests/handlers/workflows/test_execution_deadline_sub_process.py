# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A registered Deadline Cloud farm job as a pipeline-execution sub-process: its live status in the
details view, its queue session log group in availableLogs, and the logs view's read of that group.

The queue's log group is shared by every job of the queue and its session lines carry no VAMS id, so
the only correct read is by the exact stream names of the job's own sessions (one stream per session,
named by the session id) — never by the `session-` prefix and never with execution-id scope terms. The
job's parameters carry the Step Functions task token, so nothing of GetJob beyond the summary fields
may reach a response."""

import json
import os
from datetime import datetime, timezone

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
from backend.backend.common.workflows import subExecutionStages as ses  # noqa: E402
from backend.backend.handlers.workflows import executionService as le  # noqa: E402

MOD = "backend.backend.handlers.workflows.executionService"
EXEC_ID = "e1000000000000000000000000000001"
REFERENCE = "arn:aws:logs:us-west-2:123456789012:log-group:/aws/vendedlogs/vamsPipelineWorkflowsabc:*"
FARM = "farm-0a1b2c3d4e5f60718293a4b5c6d7e8f9"
QUEUE = "queue-1b2c3d4e5f60718293a4b5c6d7e8f9a0"
JOB = "job-8b527c1d2e3f40516273849a5b6c7d8e"
JOB_NAME = "step1-8b527-dcfix-pipe-a1"
GROUP_NAME = f"/aws/deadline/{FARM}/{QUEUE}"
GROUP_ARN = f"arn:aws:logs:us-west-2:123456789012:log-group:{GROUP_NAME}"
TASK_TOKEN = "AAAAKgAAAAIAAAAAAAAAAWxvb2tzLWxpa2UtYS10YXNrLXRva2Vu"
T0 = datetime(2026, 9, 12, 10, 0, 0, tzinfo=timezone.utc)
WINDOW_START = 1_700_000_000_000


def _ts(seconds):
    return datetime.fromtimestamp(T0.timestamp() + seconds, tz=timezone.utc)


def _iso(seconds):
    return _ts(seconds).strftime("%Y-%m-%dT%H:%M:%SZ")


def _client_error(code, operation="Op"):
    return botocore.exceptions.ClientError({"Error": {"Code": code, "Message": code}}, operation)


def _sub(job_id=JOB, farm_id=FARM, queue_id=QUEUE, **extra):
    sub = {"resourceType": "deadlineCloudJob", "farmId": farm_id, "queueId": queue_id, "jobId": job_id}
    sub.update(extra)
    return sub


def _get_job(task_run_status="RUNNING", lifecycle_status="CREATE_COMPLETE", started=5, ended=None,
             message="", job_id=JOB):
    """A GetJob response as boto3 returns it, parameters included: the reserved pipeline-execution id
    and the task token that the API must never echo."""
    job = {
        "jobId": job_id, "name": JOB_NAME, "lifecycleStatus": lifecycle_status,
        "lifecycleStatusMessage": message, "priority": 50,
        "taskRunStatus": task_run_status, "targetTaskRunStatus": "READY",
        "createdAt": _ts(0), "createdBy": "arn:aws:sts::123456789012:assumed-role/x/y",
        "parameters": {"VamsPipelineExecutionId": {"string": "pe-1"},
                       "VamsTaskToken": {"string": TASK_TOKEN}},
    }
    if started is not None:
        job["startedAt"] = _ts(started)
    if ended is not None:
        job["endedAt"] = _ts(ended)
    return job


def _session(index, started, ended=None, status="ENDED"):
    session = {"sessionId": f"session-{index:02d}", "fleetId": "fleet-1", "workerId": "worker-1",
               "lifecycleStatus": status, "startedAt": _ts(started)}
    if ended is not None:
        session["endedAt"] = _ts(ended)
    return session


def _reset_memos():
    le._state_machine_describe_cache.clear()
    le._batch_job_describe_cache.clear()
    le._deadline_job_cache.clear()
    le._deadline_sessions_cache.clear()


@pytest.mark.unit
class TestDeadlineStatusMap:
    @pytest.mark.parametrize("raw,mapped", [
        ("PENDING", "RUNNING"), ("READY", "RUNNING"), ("ASSIGNED", "RUNNING"), ("STARTING", "RUNNING"),
        ("SCHEDULED", "RUNNING"), ("INTERRUPTING", "RUNNING"), ("RUNNING", "RUNNING"),
        ("SUSPENDED", "RUNNING"),
        ("SUCCEEDED", "SUCCEEDED"),
        ("FAILED", "FAILED"), ("NOT_COMPATIBLE", "FAILED"),
        ("CANCELED", "ABORTED"),
        ("", "UNKNOWN"), (None, "UNKNOWN"), ("WEIRD", "UNKNOWN")])
    def test_every_task_run_status_folds_onto_the_execution_vocabulary(self, raw, mapped):
        assert ses.map_deadline_status(raw) == mapped
        assert ses.map_deadline_status(raw, "CREATE_COMPLETE") == mapped

    def test_the_map_names_exactly_the_documented_task_run_statuses(self):
        assert set(ses.DEADLINE_TASK_RUN_STATUS_MAP) == {
            "PENDING", "READY", "ASSIGNED", "STARTING", "SCHEDULED", "INTERRUPTING", "RUNNING",
            "SUSPENDED", "CANCELED", "FAILED", "SUCCEEDED", "NOT_COMPATIBLE"}

    @pytest.mark.parametrize("lifecycle", ["CREATE_FAILED", "UPDATE_FAILED", "UPLOAD_FAILED"])
    @pytest.mark.parametrize("task_run", ["PENDING", "RUNNING", "SUCCEEDED", "CANCELED", "", "WEIRD"])
    def test_a_failed_lifecycle_status_is_failed_whatever_the_task_run_status(self, lifecycle, task_run):
        assert ses.map_deadline_status(task_run, lifecycle) == "FAILED"

    @pytest.mark.parametrize("lifecycle", [
        "CREATE_IN_PROGRESS", "CREATE_COMPLETE", "UPDATE_IN_PROGRESS", "UPDATE_SUCCEEDED",
        "UPLOAD_IN_PROGRESS", "ARCHIVED", "", None])
    def test_every_other_lifecycle_status_defers_to_the_task_run_status(self, lifecycle):
        assert ses.map_deadline_status("SUCCEEDED", lifecycle) == "SUCCEEDED"
        assert ses.map_deadline_status("CANCELED", lifecycle) == "ABORTED"


@pytest.mark.unit
class TestDeadlineSubExecutionSummary:
    def setup_method(self):
        _reset_memos()

    def test_a_running_job_is_summarised_with_ids_only_and_nothing_of_its_parameters(self):
        with patch.object(le, "deadline_client") as m_deadline:
            m_deadline.get_job.return_value = _get_job(message="m" * 1000)
            summary, warnings = le._sub_execution_summary(_sub(label="render", stageName=""))
        m_deadline.get_job.assert_called_once_with(farmId=FARM, queueId=QUEUE, jobId=JOB)
        assert warnings == []
        assert summary["resourceType"] == "deadlineCloudJob" and summary["label"] == "render"
        assert summary["resourceName"] == JOB_NAME
        assert summary["status"] == "RUNNING"
        assert summary["startDate"] == _iso(5) and summary["stopDate"] == ""
        assert summary["error"] == "" and len(summary["cause"]) == 256
        assert summary["deadline"] == {"farmId": FARM, "queueId": QUEUE, "jobId": JOB}
        assert summary["stages"] == [] and summary["stageSource"] == "none"
        assert summary["stagesTruncated"] is False and summary["historyTruncated"] is False
        rendered = json.dumps(summary)  # no datetime survives capture
        assert "arn:" not in rendered
        assert "parameters" not in rendered and "VamsTaskToken" not in rendered
        assert TASK_TOKEN not in rendered and "VamsPipelineExecutionId" not in rendered
        assert "createdBy" not in rendered and "targetTaskRunStatus" not in rendered

    def test_a_finished_job_reports_its_end_date_and_folded_status(self):
        with patch.object(le, "deadline_client") as m_deadline:
            m_deadline.get_job.return_value = _get_job("SUCCEEDED", started=5, ended=95)
            succeeded, _w1 = le._sub_execution_summary(_sub())
            _reset_memos()
            m_deadline.get_job.return_value = _get_job("CANCELED", started=5, ended=40)
            canceled, _w2 = le._sub_execution_summary(_sub())
            _reset_memos()
            m_deadline.get_job.return_value = _get_job("PENDING", "CREATE_FAILED", started=None,
                                                       message="bundle upload failed")
            failed, _w3 = le._sub_execution_summary(_sub())
        assert succeeded["status"] == "SUCCEEDED" and succeeded["stopDate"] == _iso(95)
        assert canceled["status"] == "ABORTED" and canceled["stopDate"] == _iso(40)
        assert failed["status"] == "FAILED" and failed["startDate"] == "" and failed["stopDate"] == ""
        assert failed["cause"] == "bundle upload failed"

    def test_the_job_id_names_the_job_when_get_job_returns_no_name(self):
        with patch.object(le, "deadline_client") as m_deadline:
            job = _get_job()
            del job["name"]
            m_deadline.get_job.return_value = job
            summary, _ = le._sub_execution_summary(_sub())
        assert summary["resourceName"] == JOB

    def test_two_sub_processes_naming_one_job_describe_it_once_per_request(self):
        with patch.object(le, "deadline_client") as m_deadline:
            m_deadline.get_job.return_value = _get_job()
            first, _w1 = le._sub_execution_summary(_sub())
            second, _w2 = le._sub_execution_summary(_sub())
            other, _w3 = le._sub_execution_summary(_sub(job_id="job-other"))
        assert m_deadline.get_job.call_count == 2
        assert sorted(c.kwargs["jobId"] for c in m_deadline.get_job.call_args_list) == [JOB, "job-other"]
        assert first["status"] == second["status"] == other["status"] == "RUNNING"
        assert first["deadline"] == second["deadline"] and other["deadline"]["jobId"] == "job-other"

    def test_a_get_job_failure_is_unknown_plus_a_warning_never_an_exception(self):
        with patch.object(le, "deadline_client") as m_deadline:
            m_deadline.get_job.side_effect = _client_error("AccessDeniedException", "GetJob")
            summary, warnings = le._sub_execution_summary(_sub())
            again, more = le._sub_execution_summary(_sub())
        # The failure is memoised too: one call per request, not one per sub-process.
        assert m_deadline.get_job.call_count == 1
        assert summary["status"] == again["status"] == "UNKNOWN"
        assert summary["resourceName"] == JOB and summary["startDate"] == "" and summary["stopDate"] == ""
        assert summary["deadline"] == {"farmId": FARM, "queueId": QUEUE, "jobId": JOB}
        assert len(warnings) == 1 and "AccessDeniedException" in warnings[0] and JOB in warnings[0]
        assert more == warnings

    def test_a_partition_without_the_client_is_unknown_plus_the_unavailable_warning(self):
        with patch.object(le, "deadline_client", None):
            summary, warnings = le._sub_execution_summary(_sub())
        assert summary["status"] == "UNKNOWN"
        assert summary["deadline"] == {"farmId": FARM, "queueId": QUEUE, "jobId": JOB}
        assert len(warnings) == 1 and "Deadline Cloud client unavailable" in warnings[0] and JOB in warnings[0]

    def test_a_registration_without_a_farm_or_queue_is_unknown_with_a_warning_and_no_call(self):
        with patch.object(le, "deadline_client") as m_deadline:
            summary, warnings = le._sub_execution_summary(_sub(farm_id=""))
        m_deadline.get_job.assert_not_called()
        assert summary["status"] == "UNKNOWN" and summary["resourceName"] == JOB
        assert summary["deadline"] == {"farmId": "", "queueId": QUEUE, "jobId": JOB}
        assert len(warnings) == 1 and JOB in warnings[0]

    def test_a_registration_without_a_job_id_is_unknown_without_a_call_or_a_block(self):
        with patch.object(le, "deadline_client") as m_deadline:
            summary, warnings = le._sub_execution_summary(_sub(job_id=""))
        m_deadline.get_job.assert_not_called()
        assert summary["status"] == "UNKNOWN" and "deadline" not in summary and warnings == []

    def test_the_summary_pass_over_a_row_runs_the_job_through_phase_one(self):
        prow = {"registeredSubExecutions": [_sub(stageName="")]}
        with patch.object(le, "deadline_client") as m_deadline, patch.object(le, "sfn") as m_sfn:
            m_deadline.get_job.return_value = _get_job("SUCCEEDED", ended=90)
            pending, truncated, warnings = le._pipeline_sub_executions(prow)
            le._fill_sub_execution_stages([(s, sub, warnings) for s, sub in pending], [10])
        m_sfn.get_execution_history.assert_not_called()
        assert truncated is False and warnings == []
        assert [s["status"] for s, _ in pending] == ["SUCCEEDED"]
        assert pending[0][0]["stages"] == [] and pending[0][0]["stageSource"] == "none"


@pytest.mark.unit
class TestDeadlineAvailableLogs:
    def setup_method(self):
        _reset_memos()

    def test_a_registered_job_contributes_its_queue_session_group_with_a_stable_log_id(self):
        prow = {"pipelineExecutionType": "DeadlineCloud", "registeredLogs": [],
                "registeredSubExecutions": [_sub()]}
        with patch.object(le, "deadline_client") as m_deadline, patch.object(le, "sfn") as m_sfn:
            entries = le._available_logs_for_pipeline(prow, REFERENCE)
            again = le._available_logs_for_pipeline(prow, REFERENCE)
        # Listing sources costs no AWS call; the sessions are listed only when the source is read.
        m_deadline.list_sessions.assert_not_called()
        m_deadline.get_job.assert_not_called()
        m_sfn.describe_state_machine.assert_not_called()
        assert len(entries) == 1
        entry = entries[0]
        assert entry["kind"] == "deadlineCloudJob" and entry["sourceType"] == "deadlineCloud"
        assert entry["label"] == "Deadline Cloud job sessions" and entry["stageName"] == ""
        assert entry["logGroupName"] == GROUP_NAME
        assert entry["logStreamName"] == "" and entry["logStreamPrefix"] == "session-"
        assert entry["_logGroupArn"] == GROUP_ARN
        assert entry["_deadline"] == {"farmId": FARM, "queueId": QUEUE, "jobId": JOB}
        assert entry["_deadlineJobs"] == [{"farmId": FARM, "queueId": QUEUE, "jobId": JOB}]
        assert entry["logId"] == al.log_id("deadlineCloudJob", GROUP_ARN, "", "session-")
        assert entry["logId"] == again[0]["logId"]

    def test_the_public_projection_is_the_unchanged_entry_shape_with_no_ids_or_arns(self):
        prow = {"registeredSubExecutions": [_sub()]}
        entry = le._available_logs_for_pipeline(prow, REFERENCE)[0]
        public = al.public_entry(entry)
        assert set(public) == set(al.PUBLIC_KEYS)
        assert public == {"logId": entry["logId"], "kind": "deadlineCloudJob",
                          "label": "Deadline Cloud job sessions", "sourceType": "deadlineCloud",
                          "stageName": "", "logGroupName": GROUP_NAME, "logStreamName": "",
                          "logStreamPrefix": "session-"}
        rendered = json.dumps(public)
        assert "arn:" not in rendered and "_deadline" not in rendered and JOB not in rendered

    def test_the_group_arn_follows_the_reference_partition_region_and_account(self):
        gov = "arn:aws-us-gov:logs:us-gov-west-1:210987654321:log-group:/g:*"
        entries = al.build_available_logs("", [], [], gov, deadline_jobs=[_sub()])
        assert entries[0]["_logGroupArn"] == \
            f"arn:aws-us-gov:logs:us-gov-west-1:210987654321:log-group:{GROUP_NAME}"
        assert entries[0]["logGroupName"] == GROUP_NAME
        # Without a reference there is no partition to place the group in, as for a name-only entry.
        assert al.build_available_logs("", [], [], "", deadline_jobs=[_sub()]) == []

    def test_a_job_missing_an_id_and_the_same_job_registered_twice_yield_one_source_or_none(self):
        jobs = [_sub(farm_id=""), _sub(queue_id=""), _sub(job_id=""), _sub(), _sub()]
        entries = al.build_available_logs("", [], [], REFERENCE, deadline_jobs=jobs)
        assert len(entries) == 1
        assert entries[0]["_deadlineJobs"] == [{"farmId": FARM, "queueId": QUEUE, "jobId": JOB}]

    def test_two_jobs_on_one_queue_are_one_source_carrying_both_jobs(self):
        entries = al.build_available_logs("", [], [], REFERENCE,
                                          deadline_jobs=[_sub(), _sub(job_id="job-2")])
        assert len(entries) == 1
        assert entries[0]["_deadline"]["jobId"] == JOB
        assert [j["jobId"] for j in entries[0]["_deadlineJobs"]] == [JOB, "job-2"]

    def test_existing_call_sites_of_the_builder_are_unchanged(self):
        assert al.build_available_logs("", [], [], REFERENCE) == []
        entries = al.build_available_logs(
            "arn:aws:logs:us-west-2:123456789012:log-group:/aws/lambda/fn:*", [], [], REFERENCE)
        assert [e["kind"] for e in entries] == ["invocation"] and "_deadline" not in entries[0]

    def test_the_read_plan_is_exact_streams_with_no_prefix_and_no_scope(self):
        entry = al.build_available_logs("", [], [], REFERENCE, deadline_jobs=[_sub()])[0]
        plan = al.plan_source_read(entry, ["thumb-jd/default/"], stream_names=["session-01", "session-02"])
        assert plan == {"logStreamName": "", "logStreamNames": ["session-01", "session-02"],
                        "logStreamPrefix": "", "scoped": False, "unscoped": False}
        # The other kinds' plans are untouched: no stream list, prefix and scope as before.
        other = al.plan_source_read({"kind": "registered", "logStreamName": "", "logStreamPrefix": "p/",
                                     "sourceType": "container"}, [])
        assert other == {"logStreamName": "", "logStreamPrefix": "p/", "scoped": True, "unscoped": False}

    def test_the_registered_sub_execution_cap_bounds_the_jobs_listed(self):
        many = [_sub(job_id=f"job-{i}", queue_id=f"queue-{i}")
                for i in range(le.MAX_REGISTERED_SUB_EXECUTIONS_INSPECTED + 5)]
        entries = le._available_logs_for_pipeline({"registeredSubExecutions": many}, REFERENCE)
        assert len(entries) == le.MAX_REGISTERED_SUB_EXECUTIONS_INSPECTED

    def test_a_session_group_the_pipeline_also_registered_by_name_stays_the_job_source(self):
        # The registered copy of the same location lends its stage name and nothing else: the kind
        # stays deadlineCloudJob, so the read is by exact session streams and not by the prefix.
        prow = {"registeredLogs": [{"logGroupName": GROUP_NAME, "logStreamName": "",
                                    "logStreamPrefix": "session-", "stageName": "Render"}],
                "registeredSubExecutions": [_sub()]}
        entries = le._available_logs_for_pipeline(prow, REFERENCE)
        assert len(entries) == 1
        entry = entries[0]
        assert entry["kind"] == "deadlineCloudJob" and entry["sourceType"] == "deadlineCloud"
        assert entry["stageName"] == "Render" and entry["label"] == "Deadline Cloud job sessions"
        assert entry["_deadline"] == {"farmId": FARM, "queueId": QUEUE, "jobId": JOB}
        assert entry["logId"] == al.log_id("deadlineCloudJob", GROUP_ARN, "", "session-")
        plan = al.plan_source_read(entry, al.registered_prefixes(prow["registeredLogs"]),
                                   stream_names=["session-01"])
        assert plan["logStreamNames"] == ["session-01"] and plan["logStreamPrefix"] == ""
        assert plan["scoped"] is False


@pytest.mark.unit
class TestFetchRegisteredLogEventsStreamNames:
    def test_an_explicit_stream_list_is_passed_as_log_stream_names(self):
        with patch.object(le.logs_client, "filter_log_events", return_value={"events": []}) as m_filter:
            ok, events, token = le._fetch_registered_log_events(
                GROUP_ARN, "", {}, log_stream_names=["session-01", "session-02"], default_start_time=5)
        assert ok is True and events == [] and token is None
        kwargs = m_filter.call_args.kwargs
        assert kwargs["logGroupName"] == GROUP_NAME
        assert kwargs["logStreamNames"] == ["session-01", "session-02"]
        assert "logStreamNamePrefix" not in kwargs and "filterPattern" not in kwargs
        assert kwargs["startTime"] == 5

    def test_the_existing_single_stream_and_prefix_calls_are_unchanged(self):
        with patch.object(le.logs_client, "filter_log_events", return_value={"events": []}) as m_filter:
            le._fetch_registered_log_events(GROUP_ARN, "one-stream", {}, scope_terms=["e1", "pe-1"])
            le._fetch_registered_log_events(GROUP_ARN, "", {}, log_stream_prefix="fam/", scope_terms=["e1"])
        single, prefixed = [c.kwargs for c in m_filter.call_args_list]
        assert single["logStreamNames"] == ["one-stream"] and single["filterPattern"] == '"e1" "pe-1"'
        assert prefixed["logStreamNamePrefix"] == "fam/" and "logStreamNames" not in prefixed


def _main_row():
    return {"workflowId": "wf", "workflowDatabaseId": "db", "executionLogGroupArn": REFERENCE,
            "workflow_execution_arn": "arn:aws:states:us-west-2:123456789012:execution:main:E"}


def _prow(subs=None):
    return {"pipelineExecutionId": "pe-1", "pipelineExecutionType": "DeadlineCloud",
            "registeredLogs": [], "registeredSubExecutions": subs if subs is not None else [_sub()]}


def _body(resp):
    return json.loads(resp["body"])["message"]


def _run_logs(prow, query=None, sessions=None, sessions_error=None, filter_return=None, filter_error=None,
              client_none=False):
    """get_execution_logs in full mode against one pipeline row, with the row, authorization, the shared
    search and the Step Functions history stubbed, and the Deadline and CloudWatch clients mocked so
    the exact ListSessions and FilterLogEvents calls can be asserted. Returns (resp, deadline_mock,
    filter_mock)."""
    le.claims_and_roles = {"tokens": ["u1"]}
    _reset_memos()
    filter_kwargs = {"side_effect": filter_error} if filter_error else \
        {"return_value": filter_return if filter_return is not None else {"events": []}}
    deadline_patch = patch.object(le, "deadline_client", None) if client_none \
        else patch.object(le, "deadline_client")
    with patch(f"{MOD}.get_execution_main_row", return_value=_main_row()), \
         patch(f"{MOD}.authorize_execution_access", return_value=(True, "")), \
         patch(f"{MOD}.get_pipeline_execution_rows", return_value=[prow]), \
         patch(f"{MOD}._full_log_search", return_value={"events": [], "nextToken": None}), \
         patch(f"{MOD}._sfn_execution_history_events", return_value={"events": [], "nextToken": None}), \
         patch(f"{MOD}._log_search_window_start", return_value=WINDOW_START), \
         patch.object(le.logs_client, "filter_log_events", **filter_kwargs) as m_filter, \
         deadline_patch as m_deadline:
        if not client_none:
            if sessions_error is not None:
                m_deadline.list_sessions.side_effect = sessions_error
            else:
                m_deadline.list_sessions.return_value = {"sessions": sessions or []}
        resp = le.get_execution_logs({}, EXEC_ID, dict({"mode": "full", "pipelineExecutionId": "pe-1"},
                                                       **(query or {})))
    return resp, m_deadline, m_filter


@pytest.mark.unit
class TestDeadlineSessionLogRead:
    def test_the_source_is_read_by_the_exact_session_streams_with_no_prefix_and_no_scope_terms(self):
        sessions = [_session(1, 10, 50), _session(2, 60, 120), _session(3, 130)]
        events = [{"timestamp": 1_700_000_100_000, "message": "worker: rendering frame 1",
                   "logStreamName": "session-01", "eventId": "1"},
                  {"timestamp": 1_700_000_200_000, "message": "worker: rendering frame 2",
                   "logStreamName": "session-02", "eventId": "2"}]
        resp, m_deadline, m_filter = _run_logs(_prow(), sessions=sessions, filter_return={"events": events})
        assert resp["statusCode"] == 200
        m_deadline.list_sessions.assert_called_once()
        assert m_deadline.list_sessions.call_args.kwargs["farmId"] == FARM
        assert m_deadline.list_sessions.call_args.kwargs["queueId"] == QUEUE
        assert m_deadline.list_sessions.call_args.kwargs["jobId"] == JOB
        m_deadline.get_job.assert_not_called()
        m_filter.assert_called_once()
        kwargs = m_filter.call_args.kwargs
        assert kwargs["logGroupName"] == GROUP_NAME
        assert sorted(kwargs["logStreamNames"]) == ["session-01", "session-02", "session-03"]
        assert "logStreamNamePrefix" not in kwargs
        assert "filterPattern" not in kwargs
        assert kwargs["startTime"] == WINDOW_START
        body = _body(resp)
        assert len(body["logSources"]) == 1
        source = body["logSources"][0]
        assert source["kind"] == "deadlineCloudJob" and source["status"] == "read" and source["eventCount"] == 2
        assert set(source) == set(al.PUBLIC_KEYS) | {"status", "eventCount"}
        assert "arn:" not in json.dumps(source)
        # The events are stamped with the source's logId like every other source's.
        assert [e["logId"] for e in body["subProcessEvents"]] == [source["logId"]] * 2
        assert [e["message"] for e in body["subProcessEvents"]] == [
            "worker: rendering frame 1", "worker: rendering frame 2"]
        assert [e["logGroupName"] for e in body["subProcessEvents"]] == [GROUP_NAME] * 2
        assert "warnings" not in body
        assert "arn:" not in json.dumps(body)

    def test_only_the_ten_most_recent_sessions_are_read(self):
        # Twelve sessions started a minute apart, listed oldest first: the two oldest are not named.
        sessions = [_session(i, i * 60, i * 60 + 30) for i in range(12)]
        _resp, _m_deadline, m_filter = _run_logs(_prow(), sessions=sessions)
        names = m_filter.call_args.kwargs["logStreamNames"]
        assert len(names) == le.MAX_DEADLINE_SESSIONS_READ == 10
        assert set(names) == {f"session-{i:02d}" for i in range(2, 12)}

    def test_a_job_with_no_session_yet_is_not_found_without_a_cloudwatch_call(self):
        resp, m_deadline, m_filter = _run_logs(_prow(), sessions=[])
        assert resp["statusCode"] == 200
        m_deadline.list_sessions.assert_called_once()
        m_filter.assert_not_called()
        body = _body(resp)
        assert body["logSources"][0]["status"] == "notFound" and body["logSources"][0]["eventCount"] == 0
        assert "warnings" not in body and "subProcessEvents" not in body

    def test_an_access_denial_on_list_sessions_is_denied_with_a_named_warning_and_no_cloudwatch_call(self):
        resp, _m_deadline, m_filter = _run_logs(
            _prow(), sessions_error=_client_error("AccessDeniedException", "ListSessions"))
        m_filter.assert_not_called()
        body = _body(resp)
        assert body["logSources"][0]["status"] == "denied" and body["logSources"][0]["eventCount"] == 0
        assert any("Deadline Cloud session log retrieval failed for" in w and "AccessDeniedException" in w
                   and GROUP_NAME in w for w in body["warnings"])
        # The warning names the group, not its ARN.
        assert "arn:" not in json.dumps(body)

    def test_any_other_list_sessions_failure_is_an_error(self):
        resp, _m_deadline, m_filter = _run_logs(
            _prow(), sessions_error=_client_error("ThrottlingException", "ListSessions"))
        m_filter.assert_not_called()
        body = _body(resp)
        assert body["logSources"][0]["status"] == "error"
        assert any("ThrottlingException" in w for w in body["warnings"])

    def test_an_access_denial_on_the_log_group_is_denied(self):
        resp, _m_deadline, m_filter = _run_logs(
            _prow(), sessions=[_session(1, 10)],
            filter_error=_client_error("AccessDeniedException", "FilterLogEvents"))
        m_filter.assert_called_once()
        body = _body(resp)
        assert body["logSources"][0]["status"] == "denied"
        assert any("Deadline Cloud session log retrieval failed for" in w for w in body["warnings"])

    def test_a_partition_without_the_client_is_an_error_naming_the_unavailable_client(self):
        resp, _none, m_filter = _run_logs(_prow(), client_none=True)
        m_filter.assert_not_called()
        body = _body(resp)
        assert body["logSources"][0]["status"] == "error"
        assert any("Deadline Cloud client unavailable" in w for w in body["warnings"])

    def test_a_log_id_read_of_the_source_returns_its_events_and_its_source_alone(self):
        source_id = le._available_logs_for_pipeline(_prow(), REFERENCE)[0]["logId"]
        events = [{"timestamp": 1_700_000_100_000, "message": "line", "logStreamName": "session-01"}]
        resp, _m_deadline, m_filter = _run_logs(
            _prow(), query={"logId": source_id, "nextToken": "cw-1"},
            sessions=[_session(1, 10)], filter_return={"events": events, "nextToken": "cw-2"})
        assert resp["statusCode"] == 200
        assert m_filter.call_args.kwargs["logStreamNames"] == ["session-01"]
        assert m_filter.call_args.kwargs["nextToken"] == "cw-1"
        body = _body(resp)
        assert [e["message"] for e in body["events"]] == ["line"]
        assert body["events"][0]["logId"] == source_id and body["nextToken"] == "cw-2"
        assert [s["logId"] for s in body["logSources"]] == [source_id]
        assert body["logSources"][0]["status"] == "read" and body["logSources"][0]["eventCount"] == 1
        assert "subProcessEvents" not in body and "sfnHistoryEvents" not in body

    def test_a_log_id_read_with_no_session_yet_is_not_found_with_no_events(self):
        source_id = le._available_logs_for_pipeline(_prow(), REFERENCE)[0]["logId"]
        resp, _m_deadline, m_filter = _run_logs(_prow(), query={"logId": source_id}, sessions=[])
        m_filter.assert_not_called()
        body = _body(resp)
        assert body["events"] == [] and body["nextToken"] is None
        assert body["logSources"] == [dict(al.public_entry(le._available_logs_for_pipeline(_prow(), REFERENCE)[0]),
                                          status="notFound", eventCount=0)]

    def test_a_stage_name_selection_excludes_the_stage_less_source_without_any_call(self):
        resp, m_deadline, m_filter = _run_logs(_prow(), query={"stageName": "Render"}, sessions=[_session(1, 10)])
        m_deadline.list_sessions.assert_not_called()
        m_filter.assert_not_called()
        assert _body(resp)["logSources"] == []

    def test_a_session_group_the_pipeline_also_registered_is_read_by_exact_session_streams(self):
        prow = _prow()
        prow["registeredLogs"] = [{"logGroupName": GROUP_NAME, "logStreamName": "",
                                   "logStreamPrefix": "session-", "stageName": "Render"}]
        resp, m_deadline, m_filter = _run_logs(prow, sessions=[_session(1, 10), _session(2, 20)])
        assert resp["statusCode"] == 200
        m_deadline.list_sessions.assert_called_once()
        m_filter.assert_called_once()
        kwargs = m_filter.call_args.kwargs
        assert kwargs["logGroupName"] == GROUP_NAME
        assert sorted(kwargs["logStreamNames"]) == ["session-01", "session-02"]
        assert "logStreamNamePrefix" not in kwargs and "filterPattern" not in kwargs
        body = _body(resp)
        assert [(s["kind"], s["stageName"], s["status"]) for s in body["logSources"]] == [
            ("deadlineCloudJob", "Render", "empty")]

    def test_two_jobs_on_one_queue_list_each_job_and_read_the_union_of_their_sessions(self):
        prow = _prow(subs=[_sub(), _sub(job_id="job-2")])
        by_job = {JOB: [_session(1, 10, 50)], "job-2": [_session(2, 60, 90), _session(3, 100)]}
        le.claims_and_roles = {"tokens": ["u1"]}
        _reset_memos()
        with patch(f"{MOD}.get_execution_main_row", return_value=_main_row()), \
             patch(f"{MOD}.authorize_execution_access", return_value=(True, "")), \
             patch(f"{MOD}.get_pipeline_execution_rows", return_value=[prow]), \
             patch(f"{MOD}._full_log_search", return_value={"events": [], "nextToken": None}), \
             patch(f"{MOD}._sfn_execution_history_events", return_value={"events": [], "nextToken": None}), \
             patch(f"{MOD}._log_search_window_start", return_value=WINDOW_START), \
             patch.object(le.logs_client, "filter_log_events", return_value={"events": []}) as m_filter, \
             patch.object(le, "deadline_client") as m_deadline:
            m_deadline.list_sessions.side_effect = lambda **kw: {"sessions": by_job[kw["jobId"]]}
            resp = le.get_execution_logs({}, EXEC_ID, {"mode": "full", "pipelineExecutionId": "pe-1"})
        assert resp["statusCode"] == 200
        assert {c.kwargs["jobId"] for c in m_deadline.list_sessions.call_args_list} == {JOB, "job-2"}
        m_filter.assert_called_once()
        assert sorted(m_filter.call_args.kwargs["logStreamNames"]) == ["session-01", "session-02", "session-03"]
        assert len(_body(resp)["logSources"]) == 1

    def test_sessions_are_paged_and_a_session_without_an_id_is_skipped(self):
        pages = [{"sessions": [_session(1, 10), {"lifecycleStatus": "ENDED"}], "nextToken": "p2"},
                 {"sessions": [_session(2, 20)]}]
        le.claims_and_roles = {"tokens": ["u1"]}
        _reset_memos()
        with patch.object(le, "deadline_client") as m_deadline:
            m_deadline.list_sessions.side_effect = pages
            sessions, error = le._list_deadline_sessions_cached(FARM, QUEUE, JOB)
            again, _ = le._list_deadline_sessions_cached(FARM, QUEUE, JOB)
        assert error == "" and [s["sessionId"] for s in sessions] == ["session-01", "session-02"]
        assert m_deadline.list_sessions.call_count == 2
        assert m_deadline.list_sessions.call_args_list[1].kwargs["nextToken"] == "p2"
        assert again is sessions

    def test_the_handler_resets_the_deadline_memos_per_invocation(self):
        le._deadline_job_cache[(FARM, QUEUE, JOB)] = ({"name": "stale"}, "")
        le._deadline_sessions_cache[(FARM, QUEUE, JOB)] = ([{"sessionId": "stale"}], "")
        event = {"requestContext": {"http": {"method": "OPTIONS", "path": "/workflows/executions"}},
                 "headers": {}, "queryStringParameters": None, "pathParameters": None}
        with patch(f"{MOD}.request_to_claims", return_value={"tokens": ["u1"], "roles": []}), \
             patch(f"{MOD}.CasbinEnforcer"):
            le.lambda_handler(event, None)
        assert le._deadline_job_cache == {} and le._deadline_sessions_cache == {}


def _details_main_row():
    return {"workflowExecutionId": EXEC_ID, "workflowId": "wf", "workflowDatabaseId": "db",
            "workflow_execution_arn": "arn:aws:states:us-west-2:123456789012:execution:main:E",
            "executionLogGroupArn": REFERENCE, "executionStatus": "ABORTED",
            "executionStartDate": _iso(0), "executionStopDate": _iso(300),
            "triggerType": "Manual", "triggeredByUserId": "u1", "executionError": ""}


def _details_row():
    return {"pipelineExecutionId": "pe-1", "pipelineId": "render", "pipelineDatabaseId": "GLOBAL",
            "executionStatus": "ABORTED", "executionStartDate": _iso(0), "executionStopDate": _iso(300),
            "endStatePipeline": "true", "pipelineExecutionType": "DeadlineCloud",
            "pipelineResourceArn": f"arn:aws:deadline:us-west-2:123456789012:farm/{FARM}/queue/{QUEUE}",
            "registeredLogs": [], "registeredSubExecutions": [_sub(label="render", stageName="")]}


def _run_details(job=None, job_error=None):
    """GET .../details?includeSubExecutions=true through the handler with every AWS read stubbed.
    Returns (resp, deadline_mock, describe_state_machine_mock, get_execution_history_mock)."""
    _reset_memos()
    event = {"requestContext": {"http": {"method": "GET", "path": "/workflows/executions/EabcId/details"},
                                "authorizer": {}},
             "pathParameters": {"executionId": EXEC_ID},
             "queryStringParameters": {"includeSubExecutions": "true"}}
    with patch(f"{MOD}.request_to_claims", return_value={"tokens": ["u1"], "roles": [], "mfaEnabled": False}), \
         patch(f"{MOD}.CasbinEnforcer") as m_enforcer, \
         patch(f"{MOD}.get_execution_main_row", return_value=_details_main_row()), \
         patch(f"{MOD}.get_workflow_execution_configuration_row", return_value={}), \
         patch(f"{MOD}.get_execution_input_assets", return_value=[]), \
         patch(f"{MOD}.get_pipeline_execution_rows", return_value=[_details_row()]), \
         patch(f"{MOD}.get_workflow_definition", return_value={}), \
         patch(f"{MOD}.get_pipeline_definition", return_value={"pipelineId": "render"}), \
         patch(f"{MOD}._query_all", return_value=[]), \
         patch(f"{MOD}._query_capped", return_value=([], False)), \
         patch.object(le.sfn, "describe_execution", return_value={
             "status": "ABORTED", "startDate": T0, "stopDate": _ts(300), "error": "", "cause": ""}), \
         patch.object(le.sfn, "describe_state_machine") as m_describe, \
         patch.object(le.sfn, "get_execution_history") as m_history, \
         patch.object(le, "deadline_client") as m_deadline:
        m_enforcer.return_value.enforceAPI.return_value = True
        m_enforcer.return_value.enforce.return_value = True
        if job_error is not None:
            m_deadline.get_job.side_effect = job_error
        else:
            m_deadline.get_job.return_value = job
        resp = le.lambda_handler(event, MagicMock())
    return resp, m_deadline, m_describe, m_history


@pytest.mark.unit
class TestDeadlineDetailsRoute:
    """The job's summary and its session log source as the details route's HTTP body carries them."""

    def test_details_with_the_flag_reports_the_job_and_its_session_log_source(self):
        resp, m_deadline, m_describe, m_history = _run_details(
            _get_job("CANCELED", started=5, ended=40, message="cancelled by operator"))
        assert resp["statusCode"] == 200, resp["body"]
        message = json.loads(resp["body"])["message"]
        pipeline = message["pipelines"][0]
        m_deadline.get_job.assert_called_once_with(farmId=FARM, queueId=QUEUE, jobId=JOB)
        # Listing sources costs no session listing, and a Deadline job has no state machine to read.
        m_deadline.list_sessions.assert_not_called()
        m_describe.assert_not_called()
        m_history.assert_not_called()
        assert pipeline["subExecutionsTruncated"] is False and pipeline["subExecutionWarnings"] == []
        sub = pipeline["subExecutions"][0]
        assert sub["resourceType"] == "deadlineCloudJob" and sub["label"] == "render"
        assert sub["resourceName"] == JOB_NAME and sub["status"] == "ABORTED"
        assert sub["startDate"] == _iso(5) and sub["stopDate"] == _iso(40)
        assert sub["cause"] == "cancelled by operator" and sub["error"] == ""
        assert sub["stages"] == [] and sub["stageSource"] == "none"
        assert sub["deadline"] == {"farmId": FARM, "queueId": QUEUE, "jobId": JOB}
        assert [(e["kind"], e["sourceType"], e["label"]) for e in pipeline["availableLogs"]] == [
            ("deadlineCloudJob", "deadlineCloud", "Deadline Cloud job sessions")]
        source = pipeline["availableLogs"][0]
        assert source["logGroupName"] == GROUP_NAME and source["logStreamPrefix"] == "session-"
        assert set(source) == set(al.PUBLIC_KEYS)
        # Names and identifiers only: no ARN and nothing of the job's parameters reaches the caller.
        rendered = json.dumps(message)
        assert "arn:" not in rendered
        assert TASK_TOKEN not in rendered and "VamsTaskToken" not in rendered

    def test_details_names_a_refused_get_job_in_the_warnings_and_still_answers(self):
        resp, m_deadline, _describe, _history = _run_details(
            job_error=_client_error("AccessDeniedException", "GetJob"))
        assert resp["statusCode"] == 200, resp["body"]
        pipeline = json.loads(resp["body"])["message"]["pipelines"][0]
        m_deadline.get_job.assert_called_once()
        sub = pipeline["subExecutions"][0]
        assert sub["status"] == "UNKNOWN" and sub["resourceName"] == JOB
        assert sub["deadline"] == {"farmId": FARM, "queueId": QUEUE, "jobId": JOB}
        assert any(JOB in w and "AccessDeniedException" in w for w in pipeline["subExecutionWarnings"])
        assert [e["kind"] for e in pipeline["availableLogs"]] == ["deadlineCloudJob"]
