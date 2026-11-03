# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""An execution logs read follows FilterLogEvents' nextToken past pages that hold no events.

CloudWatch Logs returns a FilterLogEvents page per slice of the group it searched: "a returned page
might only be partially full, or even empty ... If the results don't include a nextToken, then
pagination is finished" (FilterLogEvents API reference). Both live reads -- the search of the shared
workflow log group and the read of a registered sub-process log -- issued ONE call, so a run whose
events sat past an empty first page read as having no log at all.

The stub serves pages by the token it is asked for, never by call order, and caps the reads it will
serve, so a loop that never advances fails with a message instead of hanging (backend/tests/CLAUDE.md,
"A MagicMock never ends a paging loop").
"""

import json
import os
import time
from unittest.mock import MagicMock, patch

import botocore
import pytest

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

from backend.backend.handlers.workflows import executionService as es  # noqa: E402
from backend.tests.pagingStub import PagingLoopDidNotTerminate  # noqa: E402

MOD = "backend.backend.handlers.workflows.executionService"
EXEC_ID = "e1000000000000000000000000000001"
GROUP_ARN = "arn:aws:logs:us-west-2:123456789012:log-group:/aws/vendedlogs/vamsPipelineWorkflowsabc:*"
REGISTERED_ARN = "arn:aws:logs:us-west-2:123456789012:log-group:/aws/batch/job"


class LogPager:
    """A ``filter_log_events`` stub serving scripted pages keyed on the ``nextToken`` it is sent.

    Each page but the last carries the ``nextToken`` that reaches the next one. The last page MAY carry
    one: CloudWatch Logs returns a token whenever more may follow, and a bounded read is entitled to
    stop on it. Reads past ``max_reads`` raise, as do reads from a token the stub never handed out.
    """

    def __init__(self, *pages, max_reads=None):
        for page in pages[:-1]:
            assert "nextToken" in page, "every page a later page continues from needs a nextToken"
        self.pages = {None: pages[0]}
        for previous, page in zip(pages, pages[1:]):
            self.pages[previous["nextToken"]] = page
        self.max_reads = max_reads if max_reads is not None else len(pages) + 2
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        if len(self.calls) > self.max_reads:
            raise PagingLoopDidNotTerminate(
                f"filter_log_events: did not terminate after {self.max_reads} reads")
        token = kwargs.get("nextToken")
        if token not in self.pages:
            raise PagingLoopDidNotTerminate(f"filter_log_events: read from a token never handed out: {token!r}")
        return self.pages[token]

    @property
    def tokens_sent(self):
        return [call.get("nextToken") for call in self.calls]


def _event(message, timestamp=1):
    return {"timestamp": timestamp, "message": message, "logStreamName": "s"}


def _empty_pages(count, prefix="t"):
    """``count`` empty pages, page i carrying token ``{prefix}{i+1}``."""
    return [{"events": [], "nextToken": f"{prefix}{i + 1}"} for i in range(count)]


def _deadline_kwargs(walk_deadline):
    return {} if walk_deadline is None else {"walk_deadline": walk_deadline}


def _search(pager, walk_deadline=None, query=None):
    with patch.object(es.logs_client, "filter_log_events", side_effect=pager):
        return es._full_log_search(GROUP_ARN, [EXEC_ID], query or {}, **_deadline_kwargs(walk_deadline))


def _registered(pager, walk_deadline=None, next_token=None):
    with patch.object(es.logs_client, "filter_log_events", side_effect=pager):
        return es._fetch_registered_log_events(REGISTERED_ARN, "", {}, log_stream_prefix="jd/default/",
                                               scope_terms=[EXEC_ID], next_token=next_token,
                                               **_deadline_kwargs(walk_deadline))


@pytest.mark.unit
class TestTheSharedGroupSearch:
    def test_events_past_empty_pages_are_returned(self):
        pager = LogPager(*_empty_pages(2), {"events": [_event("step started")], "nextToken": "t3"},
                         {"events": [_event("step finished")]})
        result = _search(pager)
        assert [e["message"] for e in result["events"]] == ["step started"]
        # The page that held events ends the walk; its own token is handed back for the next page.
        assert result["nextToken"] == "t3"
        assert pager.tokens_sent == [None, "t1", "t2"]
        # Every follow-up read repeats the original scope and window, changing only the token.
        first = {k: v for k, v in pager.calls[0].items() if k != "nextToken"}
        for call in pager.calls[1:]:
            assert {k: v for k, v in call.items() if k != "nextToken"} == first

    def test_the_page_bound_stops_the_walk_and_returns_the_outstanding_token(self):
        cap = es.MAX_LOG_PAGES_PER_READ
        # Served well past the cap, so the walk stopping at the cap is the loop's bound, not the stub's.
        pager = LogPager(*_empty_pages(cap + 5), {"events": [_event("late")]}, max_reads=cap + 10)
        result = _search(pager)
        assert len(pager.calls) <= cap
        assert result["events"] == []
        # Token t{cap} is only on the cap-th page, so the walk read up to the bound before it stopped.
        assert result["nextToken"] == f"t{cap}"

    def test_a_spent_budget_stops_after_the_first_read_with_its_token(self):
        pager = LogPager(*_empty_pages(3), {"events": [_event("late")]})
        result = _search(pager, walk_deadline=time.monotonic() - 1)
        assert pager.tokens_sent == [None]
        assert result == {"events": [], "nextToken": "t1"}

    def test_a_single_page_is_one_read(self):
        pager = LogPager({"events": []})
        assert _search(pager) == {"events": [], "nextToken": None}
        assert pager.calls, "no read was issued"
        assert len(pager.calls) <= 1

    def test_a_page_with_events_is_not_followed(self):
        pager = LogPager({"events": [_event("first")], "nextToken": "t1"}, {"events": [_event("second")]})
        result = _search(pager)
        assert [e["message"] for e in result["events"]] == ["first"]
        assert result["nextToken"] == "t1"
        assert len(pager.calls) <= 1

    def test_the_callers_token_starts_the_walk(self):
        pager = LogPager({"events": [], "nextToken": "c1"}, {"events": [], "nextToken": "c2"},
                         {"events": [_event("found")]})
        with patch.object(es.logs_client, "filter_log_events", side_effect=pager):
            result = es._full_log_search(GROUP_ARN, [EXEC_ID], {"nextToken": "c1"})
        assert pager.tokens_sent == ["c1", "c2"]
        assert [e["message"] for e in result["events"]] == ["found"]

    def test_a_failed_follow_up_read_returns_the_token_it_was_continuing_from(self):
        calls = []

        def _reader(**kwargs):
            calls.append(kwargs.get("nextToken"))
            if kwargs.get("nextToken") is None:
                return {"events": [], "nextToken": "t1"}
            raise botocore.exceptions.ClientError(
                {"Error": {"Code": "ThrottlingException", "Message": "Rate exceeded"}}, "FilterLogEvents")

        with patch.object(es.logs_client, "filter_log_events", side_effect=_reader):
            result = es._full_log_search(GROUP_ARN, [EXEC_ID], {})
        # The walk started without a token and attempted the follow-up read that then failed.
        assert calls[0] is None and "t1" in calls
        assert result == {"events": [], "nextToken": "t1"}

    def test_an_under_stubbed_reader_is_read_once(self):
        """A bare MagicMock page answers ``in`` with False, so the walk cannot run away on one."""
        reader = MagicMock(return_value=MagicMock())
        with patch.object(es.logs_client, "filter_log_events", reader):
            es._full_log_search(GROUP_ARN, [EXEC_ID], {})
        assert reader.called
        assert reader.call_count <= 1


@pytest.mark.unit
class TestTheRegisteredLogRead:
    def test_events_past_empty_pages_are_returned(self):
        pager = LogPager(*_empty_pages(3), {"events": [_event("container line")]})
        ok, events, token = _registered(pager)
        assert ok is True
        assert [e["message"] for e in events] == ["container line"]
        assert events[0]["logGroupName"] == "/aws/batch/job"
        assert token is None
        assert pager.tokens_sent == [None, "t1", "t2", "t3"]
        assert {call["logStreamNamePrefix"] for call in pager.calls} == {"jd/default/"}

    def test_the_page_bound_returns_the_outstanding_token(self):
        cap = es.MAX_LOG_PAGES_PER_READ
        pager = LogPager(*_empty_pages(cap + 5), {"events": [_event("late")]}, max_reads=cap + 10)
        ok, events, token = _registered(pager)
        assert (ok, events, token) == (True, [], f"t{cap}")
        assert len(pager.calls) <= cap

    def test_a_single_page_is_one_read(self):
        pager = LogPager({"events": [_event("only")]})
        ok, events, token = _registered(pager)
        assert ok is True and [e["message"] for e in events] == ["only"] and token is None
        assert len(pager.calls) <= 1

    def test_a_first_read_failure_is_still_reported_as_a_failure(self):
        error = botocore.exceptions.ClientError(
            {"Error": {"Code": "AccessDeniedException", "Message": "no"}}, "FilterLogEvents")
        with patch.object(es.logs_client, "filter_log_events", side_effect=error):
            ok, payload, token = es._fetch_registered_log_events(REGISTERED_ARN, "s", {})
        assert (ok, payload, token) == (False, "AccessDeniedException", None)


@pytest.mark.unit
class TestOneRequestSharesOneBudget:
    """Every read of one full-mode logs request draws on the same deadline, so a pipeline with many
    registered sources cannot multiply the walk past the API Gateway ceiling."""

    def test_the_search_and_every_registered_read_get_the_same_deadline(self):
        prow = {"pipelineExecutionId": "pe-1", "pipelineExecutionType": "Lambda",
                "pipelineResourceArn": "vams-vamsExecuteThumb",
                "registeredLogs": [
                    {"logGroupArn": REGISTERED_ARN, "logGroupName": "", "logStreamName": f"s{i}",
                     "logStreamPrefix": "", "stageName": "", "label": "", "sourceType": ""}
                    for i in range(3)],
                "registeredSubExecutions": []}
        main_row = {"workflowId": "wf", "workflowDatabaseId": "db", "executionLogGroupArn": GROUP_ARN,
                    "workflow_execution_arn": "arn:aws:states:us-west-2:123456789012:execution:main:E"}
        deadline = time.monotonic() + 123.0
        es.claims_and_roles = {"tokens": ["u1"]}
        with patch(f"{MOD}.get_execution_main_row", return_value=main_row), \
             patch(f"{MOD}.authorize_execution_access", return_value=(True, "")), \
             patch(f"{MOD}.get_pipeline_execution_rows", return_value=[prow]), \
             patch(f"{MOD}._log_walk_deadline", return_value=deadline), \
             patch(f"{MOD}._full_log_search", return_value={"events": [], "nextToken": None}) as search, \
             patch(f"{MOD}._sfn_execution_history_events", return_value={"events": [], "nextToken": None}), \
             patch(f"{MOD}._batch_stream_summaries", return_value=[]), \
             patch(f"{MOD}._fetch_registered_log_events", return_value=(True, [], None)) as fetch:
            resp = es.get_execution_logs({}, EXEC_ID, {"mode": "full", "pipelineExecutionId": "pe-1"})
        assert resp["statusCode"] == 200, json.loads(resp["body"])
        assert search.call_args.kwargs["walk_deadline"] == deadline
        # Every registered stream was read, and every read drew on the one request deadline.
        assert {"s0", "s1", "s2"} <= {call.args[1] for call in fetch.call_args_list}
        assert {call.kwargs["walk_deadline"] for call in fetch.call_args_list} == {deadline}
