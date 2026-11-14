# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The indexers and the bucket sync log identifiers and counts, never the field values an event carries.

A DynamoDB stream record carries the whole row: an asset's field values in its image, and a metadata
row's field NAME as a value (`metadataKey`) next to its value (`metadataValue`). Key-driven redaction
in `safeLogger` cannot recognise either, and an event interpolated into a log message bypasses
redaction altogether. So every line the entrypoints write is collected here and searched for the
values the event carried: a metadata field named `password`, an ordinary asset field, and the caller
details an S3 notification carries. The identifiers (database id, asset id, file key, event name) are
expected in the log, so the test also proves it is reading the lines it should.
"""

from unittest.mock import MagicMock, patch

import json
import pytest

from tests.handlers.indexing.test_indexer_batch_failure_reporting import _load_indexer, _response
from tests.handlers.indexing.test_sqsBucketSync_recreation_guard import _load as _load_bucket_sync

ASSET_TABLE = "test-asset-table"
METADATA_TABLE = "test-file-metadata-table"
LINKS_TABLE = "test-links-table"
STREAM_ARN_PREFIX = "arn:aws:dynamodb:us-east-1:111122223333:table/"

SECRET_VALUE = "s3cr3t-metadata-value-7f2a"
ORDINARY_VALUE = "ordinary-description-value-91c4"
PRINCIPAL = "AWS:AIDAPRINCIPALVALUE5521"
SOURCE_IP = "203.0.113.77"


def _logged_text(mock_logger):
    """Everything the handler passed to its logger, positional and keyword, at any level."""
    return "\n".join(repr(call) for call in mock_logger.mock_calls)


def _asset_stream_record():
    return {
        "eventID": "1",
        "eventName": "MODIFY",
        "eventSource": "aws:dynamodb",
        "eventSourceARN": STREAM_ARN_PREFIX + ASSET_TABLE + "/stream/x",
        "dynamodb": {
            "Keys": {"databaseId": {"S": "db1"}, "assetId": {"S": "x-asset1"}},
            "NewImage": {
                "databaseId": {"S": "db1"},
                "assetId": {"S": "x-asset1"},
                "description": {"S": ORDINARY_VALUE},
            },
            "SequenceNumber": "100",
        },
    }


def _metadata_stream_record(file_path):
    composite = "db1:x-asset1:" + file_path
    return {
        "eventID": "2",
        "eventName": "INSERT",
        "eventSource": "aws:dynamodb",
        "eventSourceARN": STREAM_ARN_PREFIX + METADATA_TABLE + "/stream/x",
        "dynamodb": {
            "Keys": {"databaseId:assetId:filePath": {"S": composite}, "metadataKey": {"S": "password"}},
            "NewImage": {
                "databaseId:assetId:filePath": {"S": composite},
                "metadataKey": {"S": "password"},
                "metadataValue": {"S": SECRET_VALUE},
                "metadataValueType": {"S": "string"},
            },
            "SequenceNumber": "101",
        },
    }


def _sqs_wrapped(message_id, stream_record):
    """The deployed path: stream -> snsQueuing -> SNS -> SQS -> indexer."""
    return {
        "messageId": message_id,
        "eventSource": "aws:sqs",
        "body": json.dumps({"Type": "Notification", "Message": json.dumps(stream_record)}),
    }


def _assert_no_values(text):
    assert SECRET_VALUE not in text, "a metadata value reached the function log"
    assert ORDINARY_VALUE not in text, "an asset field value reached the function log"


@pytest.mark.unit
class TestAssetIndexerLogsNoFieldValues:
    def _run(self, event):
        m = _load_indexer("assetIndexer")
        mock_logger = MagicMock()
        with patch.object(m, "logger", mock_logger), \
                patch.object(m, "asset_storage_table_name", ASSET_TABLE), \
                patch.object(m, "asset_file_metadata_table_name", METADATA_TABLE), \
                patch.object(m, "asset_links_table_name", LINKS_TABLE), \
                patch.object(m, "process_asset_index_request", return_value=_response(m, True)) as process:
            m.lambda_handler(event, MagicMock())
        return _logged_text(mock_logger), process

    def test_direct_stream_records(self):
        text, process = self._run({"Records": [_asset_stream_record(), _metadata_stream_record("/")]})
        _assert_no_values(text)
        # Both records were handled, and the lines name them by their ids.
        assert process.call_count == 2
        assert "2 record(s)" in text
        assert "db1/x-asset1" in text

    def test_sqs_wrapped_stream_records(self):
        text, process = self._run({"Records": [
            _sqs_wrapped("m-1", _asset_stream_record()),
            _sqs_wrapped("m-2", _metadata_stream_record("/")),
        ]})
        _assert_no_values(text)
        assert process.call_count == 2
        assert "aws:sqs=2" in text


@pytest.mark.unit
class TestFileIndexerLogsNoFieldValues:
    def _run(self, event):
        m = _load_indexer("fileIndexer")
        mock_logger = MagicMock()
        with patch.object(m, "logger", mock_logger), \
                patch.object(m, "asset_storage_table_name", ASSET_TABLE), \
                patch.object(m, "asset_file_metadata_table_name", METADATA_TABLE), \
                patch.object(m, "get_asset_details", return_value=None):
            m.lambda_handler(event, MagicMock())
        return _logged_text(mock_logger)

    def test_direct_metadata_stream_record(self):
        text = self._run({"Records": [_metadata_stream_record("/part.glb")]})
        _assert_no_values(text)
        assert "db1/x-asset1/part.glb" in text

    def test_sqs_wrapped_metadata_stream_record(self):
        text = self._run({"Records": [_sqs_wrapped("m-1", _metadata_stream_record("/part.glb"))]})
        _assert_no_values(text)
        assert "db1/x-asset1/part.glb" in text


def _s3_record(key):
    return {
        "eventSource": "aws:s3",
        "eventName": "ObjectCreated:Put",
        "userIdentity": {"principalId": PRINCIPAL},
        "requestParameters": {"sourceIPAddress": SOURCE_IP},
        "s3": {"bucket": {"name": "bucket"}, "object": {"key": key}},
    }


def _sqs_s3_event(key):
    return {"Records": [{
        "messageId": "m-1",
        "eventSource": "aws:sqs",
        "body": json.dumps({"Type": "Notification", "Message": json.dumps({"Records": [_s3_record(key)]})}),
    }]}


def _assert_no_caller_details(text):
    assert PRINCIPAL not in text, "the uploader's principal id reached the function log"
    assert SOURCE_IP not in text, "the uploader's source address reached the function log"


@pytest.mark.unit
class TestBucketSyncLogsNoEventContent:
    def test_created_handler(self):
        sbs = _load_bucket_sync()
        mock_logger = MagicMock()
        with patch.object(sbs, "logger", mock_logger), \
                patch.object(sbs, "process_s3_record", return_value=(True, False, "skipped for test")):
            sbs.lambda_handler_created(_sqs_s3_event("x-asset1/part.glb"), MagicMock())
        text = _logged_text(mock_logger)
        _assert_no_caller_details(text)
        assert "1 record(s)" in text

    def test_deleted_handler(self):
        sbs = _load_bucket_sync()
        mock_logger = MagicMock()
        with patch.object(sbs, "logger", mock_logger), \
                patch.object(sbs, "parse_event", return_value={}):
            sbs.lambda_handler_deleted(_sqs_s3_event("x-asset1/part.glb"), MagicMock())
        text = _logged_text(mock_logger)
        _assert_no_caller_details(text)
        assert "1 record(s)" in text

    def test_unparseable_sns_message_is_not_echoed(self):
        sbs = _load_bucket_sync()
        mock_logger = MagicMock()
        message = f"not json {SECRET_VALUE}"
        with patch.object(sbs, "logger", mock_logger):
            sbs.parse_event({"Records": [{"EventSource": "aws:sns", "Sns": {"Message": message}}]})
        text = _logged_text(mock_logger)
        assert SECRET_VALUE not in text
        assert f"{len(message)} characters" in text


# What OpenSearch answers for a metadata value longer than the index term limit: the reason quotes
# the first bytes of the value.
VALUE_FRAGMENT = "s3cr3t-fragment-4d1e"
IMMENSE_TERM_REASON = (
    "Document contains at least one immense term in field=\"MD_note\" (whose UTF8 encoding is longer "
    "than the max length 32766), all of which were skipped. The prefix of the first immense term is: "
    "'[" + VALUE_FRAGMENT + "...]'"
)


def _immense_term_error():
    from opensearchpy.exceptions import RequestError
    return RequestError(400, "illegal_argument_exception", {"error": {"reason": IMMENSE_TERM_REASON}})


def _logged_calls(mock_logger):
    """Every logger call with its keyword arguments, so exception info would show up too."""
    return "\n".join(repr(call) for call in mock_logger.mock_calls)


@pytest.mark.unit
class TestIndexerSearchServiceErrorsCarryNoValues:
    def _client(self):
        client = MagicMock()
        client.index.side_effect = _immense_term_error()
        manager = MagicMock()
        manager.is_available.return_value = True
        manager.get_client.return_value = client
        return manager

    def test_asset_document_index_failure(self):
        m = _load_indexer("assetIndexer")
        mock_logger = MagicMock()
        document = MagicMock(str_databaseid="db1", str_assetid="x-asset1", bool_archived=False)
        document.dict.return_value = {"MD_note": VALUE_FRAGMENT}
        with patch.object(m, "logger", mock_logger), patch.object(m, "opensearch_manager", self._client()):
            assert m.index_asset_document(document) is False
        text = _logged_calls(mock_logger)
        assert VALUE_FRAGMENT not in text
        assert "db1#x-asset1" in text and "RequestError" in text and "HTTP 400" in text
        mock_logger.exception.assert_not_called()

    def test_file_document_index_failure(self):
        m = _load_indexer("fileIndexer")
        mock_logger = MagicMock()
        document = MagicMock(str_databaseid="db1", str_assetid="x-asset1", str_key="/part.glb")
        document.dict.return_value = {"MD_note": VALUE_FRAGMENT}
        with patch.object(m, "logger", mock_logger), patch.object(m, "opensearch_manager", self._client()):
            assert m.index_file_document(document) is False
        text = _logged_calls(mock_logger)
        assert VALUE_FRAGMENT not in text
        assert "RequestError" in text and "HTTP 400" in text
        mock_logger.exception.assert_not_called()

    @pytest.mark.parametrize("module_name, function, document_kwargs", [
        ("assetIndexer", "index_asset_document", {"str_databaseid": "db1", "str_assetid": "a1", "bool_archived": False}),
        ("fileIndexer", "index_file_document", {"str_databaseid": "db1", "str_assetid": "a1", "str_key": "/p.glb"}),
    ])
    def test_rejected_geo_shape_is_not_echoed(self, module_name, function, document_kwargs):
        from opensearchpy.exceptions import RequestError
        bad_shape = "POLYGON((" + VALUE_FRAGMENT + "))"
        geo_error = RequestError(
            400, "mapper_parsing_exception",
            {"error": {"root_cause": [{"type": "mapper_parsing_exception",
                                       "reason": "invalid_shape_exception " + bad_shape}]}})
        client = MagicMock()
        client.index.side_effect = [geo_error, {"result": "created"}]
        manager = MagicMock()
        manager.is_available.return_value = True
        manager.get_client.return_value = client
        m = _load_indexer(module_name)
        mock_logger = MagicMock()
        document = MagicMock(**document_kwargs)
        document.dict.return_value = {"geo_MD_location": bad_shape, "MD_note": "x"}
        with patch.object(m, "logger", mock_logger), patch.object(m, "opensearch_manager", manager):
            assert getattr(m, function)(document) is True
        assert VALUE_FRAGMENT not in _logged_calls(mock_logger)
        assert client.index.call_count == 2
