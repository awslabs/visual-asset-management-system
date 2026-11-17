# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""event_log_summary describes an event by its shape and counts and never repeats its content."""

import json

import pytest

from common.eventLogSummary import MAX_LISTED_SOURCES, event_log_summary

SECRET_VALUE = "s3cr3t-metadata-value-7f2a"


@pytest.mark.unit
class TestEventLogSummary:
    def test_counts_records_by_source(self):
        event = {"Records": [
            {"eventSource": "aws:sqs", "body": json.dumps({"metadataValue": SECRET_VALUE})},
            {"eventSource": "aws:sqs", "body": "{}"},
            {"eventSource": "aws:dynamodb", "dynamodb": {"NewImage": {"metadataValue": {"S": SECRET_VALUE}}}},
            {"EventSource": "aws:sns", "Sns": {"Message": SECRET_VALUE}},
        ]}
        summary = event_log_summary(event)
        assert summary == "4 record(s) [aws:sqs=2, aws:dynamodb=1, aws:sns=1]"
        assert SECRET_VALUE not in summary

    def test_empty_and_malformed_records(self):
        assert event_log_summary({"Records": []}) == "0 record(s)"
        assert event_log_summary({"Records": ["text", {"no": "source"}]}) == "2 record(s) [unknown=2]"

    def test_many_sources_are_counted_not_listed(self):
        records = [{"eventSource": f"source-{n}"} for n in range(MAX_LISTED_SOURCES + 3)]
        assert event_log_summary({"Records": records}).endswith(", 3 more source(s)]")

    def test_direct_invocation_names_only_the_ids(self):
        event = {"databaseId": "db1", "assetId": "x-asset1", "operation": "index",
                 "description": SECRET_VALUE}
        summary = event_log_summary(event)
        assert summary == "direct invocation (operation=index, databaseId=db1, assetId=x-asset1)"
        assert SECRET_VALUE not in summary

    def test_direct_invocation_without_ids_counts_fields(self):
        assert event_log_summary({"some": SECRET_VALUE}) == "direct invocation (1 field(s))"

    def test_non_string_id_and_non_object_event(self):
        assert event_log_summary({"assetId": {"S": SECRET_VALUE}}) == "direct invocation (assetId=dict)"
        assert event_log_summary([SECRET_VALUE]) == "non-object event (list)"

    def test_long_labels_are_cut(self):
        summary = event_log_summary({"Records": [{"eventSource": "s" * 1000}]})
        assert len(summary) < 300


@pytest.mark.unit
class TestExceptionLogSummary:
    def test_names_class_and_status_not_text(self):
        from common.eventLogSummary import exception_log_summary

        class RequestError(Exception):
            status_code = 400

        assert exception_log_summary(RequestError(SECRET_VALUE)) == "RequestError (HTTP 400)"

    def test_without_a_status(self):
        from common.eventLogSummary import exception_log_summary

        assert exception_log_summary(ValueError(SECRET_VALUE)) == "ValueError"
        error = ValueError("x")
        error.status_code = "not-a-number"
        assert exception_log_summary(error) == "ValueError"


@pytest.mark.unit
class TestRequestSummaries:
    """An API request and a search clause are described by shape; typed text never appears (#1552)."""

    def test_api_request_names_route_and_sizes_only(self):
        from common.eventLogSummary import event_log_summary as summarize

        event = {
            "routeKey": "POST /database/{databaseId}/assets/{assetId}/comments",
            "requestContext": {"http": {"method": "POST", "path": "/database/db1/assets/a1/comments"}},
            "pathParameters": {"databaseId": "db1", "assetId": "a1"},
            "queryStringParameters": {"filterPattern": SECRET_VALUE},
            "body": json.dumps({"commentBody": SECRET_VALUE}),
        }
        summary = summarize(event)
        assert summary.startswith("api request (POST route=POST /database/{databaseId}/assets/{assetId}/comments")
        assert "queryParameters=1" in summary
        assert SECRET_VALUE not in summary and "db1" not in summary

    def test_identity_provider_trigger_names_the_source_only(self):
        summary = event_log_summary({"triggerSource": "TokenGeneration_Authentication",
                                     "request": {"userAttributes": {"email": SECRET_VALUE}}})
        assert summary == "identity provider trigger (TokenGeneration_Authentication)"

    def test_query_clause_summary_has_no_values(self):
        from common.eventLogSummary import query_clause_log_summary

        clause = {"bool": {"must": [{"query_string": {"query": f"*{SECRET_VALUE}*"}},
                                    {"match": {"str_assetname": SECRET_VALUE}}],
                           "filter": [{"terms": {"str_databaseid": ["db1"]}}]}}
        summary = query_clause_log_summary(clause)
        assert SECRET_VALUE not in summary
        assert "bool=1" in summary and "query_string=1" in summary and "match=1" in summary


class _HostileObject:
    """An object whose length, text and equality all raise."""

    def __len__(self):
        raise RuntimeError("len")

    def __str__(self):
        raise RuntimeError("str")

    __repr__ = __str__

    def __eq__(self, other):
        raise RuntimeError("eq")

    __hash__ = None


def _cyclic_dict():
    cycle: dict = {"bool": {}}
    cycle["bool"]["must"] = [cycle]
    return cycle


@pytest.mark.unit
class TestSummariesNeverRaise:
    """Both summaries take hostile input without raising (#1552): they run before a handler's try block."""

    HOSTILE_VALUES = [None, 5, 1.5, "text", b"bytes", [], ["a", "b"], ("a",), {}, {"a": 1}, _HostileObject(), True]

    @pytest.mark.parametrize("value", HOSTILE_VALUES, ids=lambda v: type(v).__name__)
    @pytest.mark.parametrize("field", ["pathParameters", "queryStringParameters", "requestContext", "body",
                                       "routeKey", "httpMethod", "resource", "Records", "triggerSource",
                                       "request", "operation", "databaseId"])
    def test_event_with_a_hostile_field_value(self, field, value):
        for base in ({"routeKey": "GET /x", "requestContext": {"http": {"method": "GET"}}},
                     {"httpMethod": "GET", "requestContext": {}},
                     {"triggerSource": "T", "request": {}},
                     {}):
            summary = event_log_summary({**base, field: value})
            assert isinstance(summary, str) and summary

    @pytest.mark.parametrize("http", ["POST", 5, ["POST"], None, _HostileObject()])
    def test_request_context_http_of_any_type(self, http):
        summary = event_log_summary({"routeKey": "GET /x", "requestContext": {"http": http}})
        assert isinstance(summary, str) and summary

    @pytest.mark.parametrize("path_parameters", [5, ["secret-a", "secret-b"], "secret-text", _HostileObject()])
    def test_non_dict_path_parameters_list_no_names(self, path_parameters):
        summary = event_log_summary({"routeKey": "GET /x", "requestContext": {}, "pathParameters": path_parameters})
        assert "secret" not in summary and "pathParameters=[]" in summary

    @pytest.mark.parametrize("event", [_HostileObject(), [1, 2], "x", 7, None, {"Records": [_HostileObject(), 3, None]}])
    def test_any_event(self, event):
        assert isinstance(event_log_summary(event), str)

    def test_a_raising_internal_step_gives_the_fixed_fallback(self, monkeypatch):
        import common.eventLogSummary as module

        monkeypatch.setattr(module, "_event_log_summary", lambda event: (_ for _ in ()).throw(RuntimeError("x")))
        assert module.event_log_summary({"a": 1}) == module.FALLBACK_SUMMARY

    def test_path_parameter_names_are_capped_in_count_and_length(self):
        names = {f"{'n' * 5000}{i}": "v" for i in range(2000)}
        summary = event_log_summary({"routeKey": "GET /x", "requestContext": {}, "pathParameters": names})
        assert "+1995 more" in summary
        assert len(summary) < 2000

    @pytest.mark.parametrize("clause", [None, 5, "text", [], {}, [[]], _HostileObject()])
    def test_clause_of_any_shape(self, clause):
        from common.eventLogSummary import query_clause_log_summary

        assert isinstance(query_clause_log_summary(clause), str)

    def test_clause_ten_thousand_levels_deep(self):
        from common.eventLogSummary import MAX_CLAUSE_DEPTH, query_clause_log_summary

        clause: dict = {}
        node = clause
        for _ in range(10_000):
            node["bool"] = {}
            node = node["bool"]
        summary = query_clause_log_summary(clause)
        assert "(truncated)" in summary
        assert f"{MAX_CLAUSE_DEPTH + 1} node(s)" in summary

    def test_clause_ten_thousand_levels_deep_in_lists(self):
        from common.eventLogSummary import query_clause_log_summary

        clause: list = []
        node = clause
        for _ in range(10_000):
            child: list = []
            node.append(child)
            node = child
        assert isinstance(query_clause_log_summary({"bool": {"must": clause}}), str)

    def test_cyclic_clause(self):
        from common.eventLogSummary import query_clause_log_summary

        summary = query_clause_log_summary(_cyclic_dict())
        assert "bool=1" in summary and "must=1" in summary

    def test_a_very_wide_clause_is_bounded(self):
        from common.eventLogSummary import MAX_CLAUSE_NODES, query_clause_log_summary

        clause = {"bool": {"should": [{"match": {"f": str(i)}} for i in range(MAX_CLAUSE_NODES * 3)]}}
        summary = query_clause_log_summary(clause)
        assert "(truncated)" in summary and f"{MAX_CLAUSE_NODES} node(s)" in summary
