# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A metadata write records itself on the owning asset row so the compliance trigger re-evaluates
the asset: an API write of asset metadata, file metadata or file attributes stamps the asset
`metadataEdit`; an asset-link metadata write stamps BOTH linked assets. A workflow execution's
metadata output (a `lambdaCrossCall` carrying `workflowExecutionId`, which
`processWorkflowExecutionOutput` sends) stamps `workflowExecution` with that id instead -- the
trigger skips that source, so a pipeline rule's own output never re-triggers its evaluation -- and
a system cross-call without an execution id stamps nothing. A write that changed no key stamps
nothing."""

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


@pytest.mark.unit
class TestWhoIsRecorded:

    def test_an_api_write_is_a_metadata_edit(self):
        assert metadataService.metadata_change_execution_id(_api_event()) == (True, None)

    def test_a_workflow_output_write_is_the_execution(self):
        assert metadataService.metadata_change_execution_id(_output_event()) == (True, "exec-42")

    @pytest.mark.parametrize("cross_call", [{"userName": "SYSTEM_USER"},
                                            {"userName": "SYSTEM_USER", "workflowExecutionId": ""},
                                            {"userName": "SYSTEM_USER", "workflowExecutionId": 7},
                                            "not-a-dict"])
    def test_a_system_cross_call_without_an_execution_records_nothing(self, cross_call):
        event = dict(_api_event(), lambdaCrossCall=cross_call)
        assert metadataService.metadata_change_execution_id(event) == (False, None)


@pytest.mark.unit
class TestRecordMetadataChange:

    def _record(self, event, response=None, endpoints=((DB, ASSET),)):
        with patch.object(metadataService, "stamp_asset_change") as stamp:
            metadataService.record_metadata_change(event, response or _response(),
                                                   list(endpoints))
        return stamp

    def test_an_api_write_stamps_metadata_edit(self):
        stamp = self._record(_api_event())
        stamp.assert_called_once_with(metadataService.asset_storage_table, DB, ASSET,
                                      "metadataEdit", workflow_execution_id=None)

    def test_a_workflow_output_write_stamps_the_execution_not_metadata_edit(self):
        stamp = self._record(_output_event())
        stamp.assert_called_once_with(metadataService.asset_storage_table, DB, ASSET,
                                      "metadataEdit", workflow_execution_id="exec-42")

    def test_a_system_cross_call_stamps_nothing(self):
        event = dict(_api_event(), lambdaCrossCall={"userName": "SYSTEM_USER"})
        self._record(event).assert_not_called()

    def test_a_write_that_changed_nothing_stamps_nothing(self):
        self._record(_api_event(), response=_response(success=False)).assert_not_called()

    def test_each_endpoint_is_stamped_once(self):
        stamp = self._record(_api_event(), endpoints=((DB, ASSET), ("db2", "valve"), (DB, ASSET)))
        assert [c.args[1:3] for c in stamp.call_args_list] == [(DB, ASSET), ("db2", "valve")]


@pytest.mark.unit
class TestTheWriteHandlersRecordTheChange:

    @pytest.mark.parametrize("handler,operation", [
        ("handle_asset_metadata_post", "create_asset_metadata"),
        ("handle_asset_metadata_put", "update_asset_metadata"),
        ("handle_asset_metadata_delete", "delete_asset_metadata"),
    ])
    def test_asset_metadata_writes(self, handler, operation):
        body = ({"metadataKeys": ["units"], "confirmDelete": True} if "delete" in handler
                else {"metadata": [{"metadataKey": "units", "metadataValue": "mm"}]})
        with patch.object(metadataService, operation, return_value=_response()), \
                patch.object(metadataService, "parse", side_effect=lambda value, model: (
                    model(**value) if model.__name__.endswith("PathRequestModel") else MagicMock())), \
                patch.object(metadataService, "stamp_asset_change") as stamp:
            result = getattr(metadataService, handler)(_api_event(body=body))
        assert result["statusCode"] == 200
        stamp.assert_called_once_with(metadataService.asset_storage_table, DB, ASSET,
                                      "metadataEdit", workflow_execution_id=None)

    @pytest.mark.parametrize("handler,operation", [
        ("handle_file_metadata_post", "create_file_metadata"),
        ("handle_file_metadata_put", "update_file_metadata"),
        ("handle_file_metadata_delete", "delete_file_metadata"),
    ])
    def test_file_metadata_and_attribute_writes(self, handler, operation):
        request = MagicMock(filePath="/models/part.glb")
        with patch.object(metadataService, operation, return_value=_response()), \
                patch.object(metadataService, "parse", side_effect=lambda value, model: (
                    model(**value) if model.__name__.endswith("PathRequestModel") else request)), \
                patch.object(metadataService, "stamp_asset_change") as stamp:
            result = getattr(metadataService, handler)(_output_event("exec-9"))
        assert result["statusCode"] == 200
        stamp.assert_called_once_with(metadataService.asset_storage_table, DB, ASSET,
                                      "metadataEdit", workflow_execution_id="exec-9")

    @pytest.mark.parametrize("handler,operation", [
        ("handle_asset_link_metadata_post", "create_asset_link_metadata"),
        ("handle_asset_link_metadata_put", "update_asset_link_metadata"),
        ("handle_asset_link_metadata_delete", "delete_asset_link_metadata"),
    ])
    def test_asset_link_metadata_writes_stamp_both_linked_assets(self, handler, operation):
        link = {"assetLinkId": "11111111-2222-3333-4444-555555555555",
                "fromAssetDatabaseId": DB, "fromAssetId": "assembly",
                "toAssetDatabaseId": "db2", "toAssetId": "part"}
        links_table = MagicMock()
        links_table.get_item.return_value = {"Item": link}
        event = _api_event(path_parameters={"assetLinkId": link["assetLinkId"]})
        with patch.object(metadataService, operation, return_value=_response()), \
                patch.object(metadataService, "parse", side_effect=lambda value, model: (
                    model(**value) if model.__name__.endswith("PathRequestModel") else MagicMock())), \
                patch.object(metadataService, "asset_links_table", links_table), \
                patch.object(metadataService, "stamp_asset_change") as stamp:
            result = getattr(metadataService, handler)(event)
        assert result["statusCode"] == 200
        assert [c.args[1:4] for c in stamp.call_args_list] == [
            (DB, "assembly", "metadataEdit"), ("db2", "part", "metadataEdit")]

    def test_an_unreadable_link_records_nothing(self):
        links_table = MagicMock()
        links_table.get_item.side_effect = RuntimeError("throttled")
        with patch.object(metadataService, "asset_links_table", links_table):
            assert metadataService.asset_link_endpoints("link-1") == []
