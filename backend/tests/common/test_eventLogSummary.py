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
