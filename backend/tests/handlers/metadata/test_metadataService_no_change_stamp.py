# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A metadata request writes the metadata and nothing on the asset or links tables: no change stamp,
no link lookup. The compliance trigger records metadata changes on the asset row from the metadata
tables' streams (`common.assetProvenance.record_indexed_change`), so an asset-metadata, file-metadata,
file-attribute or asset-link-metadata write costs exactly what it cost before compliance. These tests
fail on the request-path stamps they replace (one asset-table UpdateItem per write, and a links-table
GetItem plus one UpdateItem per linked asset for a link's metadata)."""

import json
from unittest.mock import MagicMock, patch

import pytest

from backend.backend.handlers.metadata import metadataService
from backend.backend.handlers.metadata.metadataService import BulkOperationResponseModel

DB = "db1"
ASSET = "pump.glb"


def _response(success=True):
    return BulkOperationResponseModel(
        success=success, totalItems=1, successCount=1 if success else 0,
        failureCount=0 if success else 1, message="m", timestamp="t")


def _api_event(method="PUT", path_parameters=None, body=None):
    return {"requestContext": {"http": {"method": method, "path": "/x"}},
            "pathParameters": path_parameters or {"databaseId": DB, "assetId": ASSET},
            "body": json.dumps(body or {"metadata": [{"metadataKey": "units",
                                                     "metadataValue": "mm"}]})}


def _output_event(execution_id="exec-42"):
    """The event shape processWorkflowExecutionOutput.process_metadata_file sends."""
    event = _api_event()
    event["lambdaCrossCall"] = {"userName": "SYSTEM_USER", "workflowExecutionId": execution_id}
    return event


def _call(handler, operation, event, request=None):
    """Run a write handler with the write itself stubbed; returns the result and the asset and
    links table handles, which must see no call at all."""
    asset_table = MagicMock(name="asset_storage_table")
    links_table = MagicMock(name="asset_links_table")
    with patch.object(metadataService, operation, return_value=_response()), \
            patch.object(metadataService, "parse", side_effect=lambda value, model: (
                model(**value) if model.__name__.endswith("PathRequestModel")
                else (request or MagicMock()))), \
            patch.object(metadataService, "asset_storage_table", asset_table), \
            patch.object(metadataService, "asset_links_table", links_table):
        result = getattr(metadataService, handler)(event)
    return result, asset_table, links_table


@pytest.mark.unit
class TestAMetadataWriteMakesNoExtraCall:

    @pytest.mark.parametrize("handler,operation", [
        ("handle_asset_metadata_post", "create_asset_metadata"),
        ("handle_asset_metadata_put", "update_asset_metadata"),
        ("handle_asset_metadata_delete", "delete_asset_metadata"),
    ])
    def test_asset_metadata_writes(self, handler, operation):
        body = ({"metadataKeys": ["units"], "confirmDelete": True} if "delete" in handler
                else {"metadata": [{"metadataKey": "units", "metadataValue": "mm"}]})
        result, asset_table, links_table = _call(handler, operation, _api_event(body=body))
        assert result["statusCode"] == 200
        assert asset_table.mock_calls == []
        assert links_table.mock_calls == []

    @pytest.mark.parametrize("event", [_api_event(), _output_event("exec-9")],
                             ids=["api-request", "workflow-write-back"])
    @pytest.mark.parametrize("handler,operation", [
        ("handle_file_metadata_post", "create_file_metadata"),
        ("handle_file_metadata_put", "update_file_metadata"),
        ("handle_file_metadata_delete", "delete_file_metadata"),
    ])
    def test_file_metadata_and_attribute_writes(self, handler, operation, event):
        result, asset_table, links_table = _call(handler, operation, event,
                                                 request=MagicMock(filePath="/models/part.glb"))
        assert result["statusCode"] == 200
        assert asset_table.mock_calls == []
        assert links_table.mock_calls == []

    @pytest.mark.parametrize("handler,operation", [
        ("handle_asset_link_metadata_post", "create_asset_link_metadata"),
        ("handle_asset_link_metadata_put", "update_asset_link_metadata"),
        ("handle_asset_link_metadata_delete", "delete_asset_link_metadata"),
    ])
    def test_asset_link_metadata_writes_read_no_link_and_stamp_no_asset(self, handler, operation):
        event = _api_event(path_parameters={"assetLinkId": "11111111-2222-3333-4444-555555555555"})
        result, asset_table, links_table = _call(handler, operation, event)
        assert result["statusCode"] == 200
        assert asset_table.mock_calls == []
        assert links_table.mock_calls == []


@pytest.mark.unit
def test_the_metadata_service_holds_no_change_stamp_helper():
    """The request path carries no compliance work: the stamp and link-lookup helpers are gone."""
    for name in ("record_metadata_change", "asset_link_endpoints", "metadata_change_execution_id",
                 "stamp_asset_change"):
        assert not hasattr(metadataService, name), name
