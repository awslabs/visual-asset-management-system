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
