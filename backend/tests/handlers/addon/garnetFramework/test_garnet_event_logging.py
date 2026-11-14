# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The Garnet indexers log identifiers and counts, never the field values an event or entity carries.

The Garnet add-on indexers consume the same DynamoDB stream records as the core indexers, and the
NGSI-LD entity they send holds the asset's field and metadata values as attributes. Neither may reach
the function log: every logger call the entrypoint and the queue sender make is collected and searched
for the values.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from tests.handlers.addon.garnetFramework.test_garnet_batch_item_failures import _load

SECRET_VALUE = "s3cr3t-metadata-value-7f2a"

MODULES = ("garnetDataIndexAsset.py", "garnetDataIndexDatabase.py", "garnetDataIndexFile.py")

TABLE_NAME_ATTRIBUTES = (
    "asset_storage_table_name",
    "asset_file_metadata_storage_table_name",
    "asset_links_storage_table_v2_name",
    "asset_links_metadata_storage_table_name",
    "database_storage_table_name",
    "database_metadata_storage_table_name",
    "file_attribute_storage_table_name",
)


def _logged_text(mock_logger):
    return "\n".join(repr(call) for call in mock_logger.mock_calls)


def _event():
    """An SQS-wrapped stream record from a table no Garnet indexer routes, carrying a metadata value."""
    stream_record = {
        "eventName": "INSERT",
        "eventSource": "aws:dynamodb",
        "eventSourceARN": "arn:aws:dynamodb:us-east-1:111122223333:table/unrouted/stream/x",
        "dynamodb": {"NewImage": {
            "metadataKey": {"S": "password"},
            "metadataValue": {"S": SECRET_VALUE},
        }},
    }
    return {"Records": [{
        "messageId": "m-1",
        "eventSource": "aws:sqs",
        "body": json.dumps({"Type": "Notification", "Message": json.dumps(stream_record)}),
    }]}


def _patch_table_names(module):
    patches = [
        patch.object(module, name, f"test-{name}")
        for name in TABLE_NAME_ATTRIBUTES
        if hasattr(module, name)
    ]
    for p in patches:
        p.start()
    return patches


@pytest.mark.unit
@pytest.mark.parametrize("file_name", MODULES)
class TestGarnetIndexersLogNoFieldValues:
    def test_entrypoint_logs_counts_not_the_event(self, file_name):
        module = _load(file_name, "eventlog")
        mock_logger = MagicMock()
        patches = _patch_table_names(module)
        try:
            with patch.object(module, "logger", mock_logger):
                module.lambda_handler(_event(), MagicMock())
        finally:
            for p in patches:
                p.stop()
        text = _logged_text(mock_logger)
        assert SECRET_VALUE not in text
        assert "1 record(s)" in text

    def test_queue_sender_names_the_entity_not_its_values(self, file_name):
        module = _load(file_name, "eventlog_send")
        mock_logger = MagicMock()
        entity = {
            "id": "urn:vams:asset:db1:x-asset1",
            "type": "VAMSAsset",
            "password": {"type": "Property", "value": SECRET_VALUE},
        }
        with patch.object(module, "logger", mock_logger), patch.object(module, "sqs", MagicMock()):
            module.send_to_garnet_ingestion_queue(entity)
        text = _logged_text(mock_logger)
        assert SECRET_VALUE not in text
        assert "urn:vams:asset:db1:x-asset1" in text
