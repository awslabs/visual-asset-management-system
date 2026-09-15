# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""S2-BACKEND-035 (HIGH): batch_write_item's UnprocessedItems must be re-driven, and an
exhausted retry bound must be reported as a failure rather than as a clean write.

DynamoDB answers a partially accepted `batch_write_item` with HTTP 200 and the declined
requests in `UnprocessedItems`. Nothing raises, so botocore's retry layer does not see it
and the handler's `except Exception` arms never fire -- the response claimed
``success=True, failureCount=0`` while a subset of the keys was never written. For
REPLACE_ALL the divergence is worse: the unlisted keys are deleted first, so the entity
ends up with fewer records than either the old or the new state.

Every batch write in the handler now runs through ``batch_write_with_retry``. The three
properties this file pins:

1. an unprocessed request is RE-SENT (the transient case ends in a complete write),
2. an unprocessed request that never clears is REPORTED (the caller's own failure
   handling runs instead of the success path), and
3. the retry loop TERMINATES.

Property 3 is asserted as an upper bound rather than an exact count. A stub whose
``.get()`` answers truthily forever is how a paging/retry loop hangs a whole suite, so
the always-unprocessed stub below must return a real dict and the loop must end; an exact
count would additionally fail if the bound were raised, which is not a defect.
"""

import contextlib
import sys
import time
import pytest
from unittest.mock import MagicMock, patch

from backend.backend.handlers.metadata import metadataService
from backend.backend.handlers.metadata.metadataService import (
    BATCH_WRITE_MAX_ATTEMPTS,
    BatchWriteIncompleteError,
    batch_write_with_retry,
)

TABLE = "test-metadata-table"


def _put(key):
    return {"PutRequest": {"Item": {"metadataKey": {"S": key}}}}


def _requests_sent(client, table=TABLE):
    """Every request list handed to batch_write_item, in call order."""
    return [call.kwargs["RequestItems"][table] for call in client.batch_write_item.call_args_list]


@pytest.mark.unit
class TestBatchWriteRetryHelper:
    def test_a_fully_accepted_batch_is_written_once(self):
        """Positive control: with nothing unprocessed the helper does not re-send."""
        client = MagicMock()
        client.batch_write_item.return_value = {"UnprocessedItems": {}}

        with patch.object(metadataService, "dynamodb_client", client):
            batch_write_with_retry(TABLE, [_put("a"), _put("b")])

        assert _requests_sent(client) == [[_put("a"), _put("b")]]

    def test_an_unprocessed_request_is_resent(self):
        """The declined request -- and only that request -- goes back to DynamoDB."""
        client = MagicMock()
        client.batch_write_item.side_effect = [
            {"UnprocessedItems": {TABLE: [_put("b")]}},
            {"UnprocessedItems": {}},
        ]

        with patch.object(metadataService, "dynamodb_client", client), \
                patch.object(time, "sleep"):
            batch_write_with_retry(TABLE, [_put("a"), _put("b")])

        sent = _requests_sent(client)
        assert sent[0] == [_put("a"), _put("b")]
        assert sent[-1] == [_put("b")], "the unprocessed request was never re-sent"

    def test_a_never_clearing_batch_raises_and_the_loop_terminates(self):
        """A batch DynamoDB keeps declining raises, bounded, instead of looping."""
        client = MagicMock()
        client.batch_write_item.return_value = {"UnprocessedItems": {TABLE: [_put("b")]}}

        with patch.object(metadataService, "dynamodb_client", client), \
                patch.object(time, "sleep"):
            with pytest.raises(BatchWriteIncompleteError):
                batch_write_with_retry(TABLE, [_put("a"), _put("b")])

        attempts = client.batch_write_item.call_count
        assert attempts >= 2, "no retry was attempted at all"
        assert attempts <= BATCH_WRITE_MAX_ATTEMPTS, (
            f"the retry loop is not bounded: {attempts} calls"
        )

    def test_an_empty_batch_issues_no_call(self):
        client = MagicMock()
        with patch.object(metadataService, "dynamodb_client", client):
            batch_write_with_retry(TABLE, [])
        assert client.batch_write_item.call_count == 0

    def test_every_batch_write_in_the_module_routes_through_the_helper(self):
        """One data-loss class, not 24 bugs -- so no call site may bypass the helper.

        Source-level, because a per-site behavioural test would need 24 fixtures and would
        still miss the 25th site somebody adds later.
        """
        import ast
        import inspect

        source = inspect.getsource(metadataService)
        tree = ast.parse(source)
        helper = next(
            node for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "batch_write_with_retry"
        )

        direct_calls = [
            node.lineno
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
            and node.attr == "batch_write_item"
            and not (helper.lineno <= node.lineno <= helper.end_lineno)
        ]
        assert direct_calls == [], (
            f"batch_write_item is called outside batch_write_with_retry at {direct_calls}; "
            "those sites discard UnprocessedItems"
        )

        routed = [
            node.lineno
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "batch_write_with_retry"
        ]
        assert len(routed) >= 24, (
            f"only {len(routed)} batch writes route through the helper; the finding counted 24"
        )


@pytest.mark.unit
class TestUpsertAssetMetadataReportsUnprocessedKeys:
    """The handler-level consequence: an unprocessed key must not be reported successful."""

    @staticmethod
    def _items(count):
        from backend.backend.models.metadata import MetadataItemModel
        return [
            MetadataItemModel(metadataKey=f"key{i}", metadataValue=f"v{i}")
            for i in range(count)
        ]

    def _run(self, batch_write_side_effect):
        client = MagicMock()
        client.batch_write_item.side_effect = batch_write_side_effect
        with patch.object(metadataService, "dynamodb_client", client), \
                patch.object(metadataService, "asset_file_metadata_table", MagicMock()), \
                patch.object(time, "sleep"):
            response = metadataService._upsert_asset_metadata(
                "db1", "asset1", self._items(3), {"tokens": ["user1"]}
            )
        return response, client

    def test_transient_unprocessed_items_still_report_success(self):
        """Positive control: a partial batch that clears on retry is a complete write."""
        table = metadataService.asset_file_metadata_table_name
        response, client = self._run([
            {"UnprocessedItems": {table: [_put("key2")]}},
            {"UnprocessedItems": {}},
        ])

        assert response.success is True
        assert response.failureCount == 0
        assert "key2" in response.successfulItems

    def test_permanently_unprocessed_items_are_reported_as_failures(self):
        table = metadataService.asset_file_metadata_table_name
        response, client = self._run(
            lambda **kwargs: {"UnprocessedItems": {table: [_put("key2")]}}
        )

        assert response.failureCount > 0, "an unwritten key was reported as a clean write"
        assert response.success is False
        assert "key2" not in response.successfulItems
        assert "key2" in [failure["key"] for failure in response.failedItems]


@pytest.mark.unit
class TestReplaceAllRollbackIsReachable:
    """REPLACE_ALL deletes the unlisted keys first, so its rollback is the whole point.

    A 200-with-UnprocessedItems raised nothing, so the ``except Exception as upsert_error``
    arm that restores the deleted keys was never entered -- the entity was left with fewer
    records than either the old or the new state while the response reported a clean
    replace. An exhausted retry bound now reaches it.
    """

    @staticmethod
    def _existing(key):
        return {
            "metadataKey": {"S": key},
            "databaseId:assetId:filePath": {"S": "db1:asset1:/"},
            "metadataValue": {"S": "old"},
            "metadataValueType": {"S": "string"},
        }

    def test_an_unwritable_upsert_restores_the_deleted_keys_and_reports_failure(self):
        from backend.backend.models.metadata import MetadataItemModel

        table = metadataService.asset_file_metadata_table_name
        client = MagicMock()
        paginator = MagicMock()
        paginator.paginate.return_value.build_full_result.return_value = {
            "Items": [self._existing("droppedKey")]
        }
        client.get_paginator.return_value = paginator

        def batch_write(**kwargs):
            requests = kwargs["RequestItems"][table]
            # Deletes and the restore succeed; the new upsert never clears.
            if all("DeleteRequest" in request for request in requests):
                return {"UnprocessedItems": {}}
            if any(
                request.get("PutRequest", {}).get("Item", {})
                .get("metadataKey", {}).get("S") == "droppedKey"
                for request in requests
            ):
                return {"UnprocessedItems": {}}
            return {"UnprocessedItems": {table: requests}}

        client.batch_write_item.side_effect = batch_write

        with patch.object(metadataService, "dynamodb_client", client), \
                patch.object(time, "sleep"):
            with pytest.raises(metadataService.VAMSGeneralErrorResponse) as raised:
                metadataService._replace_all_asset_metadata(
                    "db1",
                    "asset1",
                    [MetadataItemModel(metadataKey="newKey", metadataValue="v")],
                    {"tokens": ["user1"]},
                )

        assert "rolled back successfully" in str(raised.value)
        assert "may be inconsistent" not in str(raised.value), (
            "a completed rollback was reported as leaving inconsistent data"
        )
        restored = [
            request["PutRequest"]["Item"]["metadataKey"]["S"]
            for sent in _requests_sent(client, table)
            for request in sent
            if "PutRequest" in request
        ]
        assert "droppedKey" in restored, "the deleted key was never restored"

    def test_a_legacy_row_does_not_abort_the_whole_restore(self):
        """A stored row missing `metadataValueType` must not cost every OTHER row its restore.

        The rollback built the whole restore list before issuing any write and subscripted
        `item['metadataValueType']`, so one row written by an earlier release raised inside the loop,
        `batch_write_with_retry` was never reached, and NOT ONE row came back — the deleted metadata
        was permanently gone. Rows like that exist on any deployment upgraded from 2.5.x.

        The assertion is that the OTHER key is restored: it distinguishes "the loop survived the
        legacy row" from "the loop stopped at it", which a check on the legacy key alone cannot. A
        rollback reinstates what was there, so the legacy row is restored without inventing a type
        for it.
        """
        from backend.backend.models.metadata import MetadataItemModel

        legacy = {
            "metadataKey": {"S": "legacyKey"},
            "databaseId:assetId:filePath": {"S": "db1:asset1:/"},
            "metadataValue": {"S": "old"},
            # No metadataValueType — the shape a pre-upgrade write left behind.
        }

        table = metadataService.asset_file_metadata_table_name
        client = MagicMock()
        paginator = MagicMock()
        paginator.paginate.return_value.build_full_result.return_value = {
            "Items": [legacy, self._existing("healthyKey")]
        }
        client.get_paginator.return_value = paginator

        def batch_write(**kwargs):
            requests = kwargs["RequestItems"][table]
            if all("DeleteRequest" in request for request in requests):
                return {"UnprocessedItems": {}}
            restored_keys = {
                request.get("PutRequest", {}).get("Item", {}).get("metadataKey", {}).get("S")
                for request in requests
            }
            if restored_keys & {"legacyKey", "healthyKey"}:
                return {"UnprocessedItems": {}}
            return {"UnprocessedItems": {table: requests}}

        client.batch_write_item.side_effect = batch_write

        with patch.object(metadataService, "dynamodb_client", client), \
                patch.object(time, "sleep"):
            with pytest.raises(metadataService.VAMSGeneralErrorResponse):
                metadataService._replace_all_asset_metadata(
                    "db1",
                    "asset1",
                    [MetadataItemModel(metadataKey="newKey", metadataValue="v")],
                    {"tokens": ["user1"]},
                )

        put_items = [
            request["PutRequest"]["Item"]
            for sent in _requests_sent(client, table)
            for request in sent
            if "PutRequest" in request
        ]
        restored = {item.get("metadataKey", {}).get("S") for item in put_items}
        assert "healthyKey" in restored, (
            "the legacy row aborted the restore before any write, so a well-formed sibling row was "
            "lost too"
        )
        assert "legacyKey" in restored, "the legacy row itself was not restored"
        legacy_restored = next(
            item for item in put_items if item.get("metadataKey", {}).get("S") == "legacyKey"
        )
        assert "metadataValueType" not in legacy_restored, (
            "the restore invented a type for a row that had none, so the rollback did not reinstate "
            "what was there"
        )

    def test_a_rollback_that_also_fails_reports_inconsistent_data(self):
        """Negative control for the message above: a failed restore must still say so."""
        from backend.backend.models.metadata import MetadataItemModel

        table = metadataService.asset_file_metadata_table_name
        client = MagicMock()
        paginator = MagicMock()
        paginator.paginate.return_value.build_full_result.return_value = {
            "Items": [self._existing("droppedKey")]
        }
        client.get_paginator.return_value = paginator

        def batch_write(**kwargs):
            requests = kwargs["RequestItems"][table]
            if all("DeleteRequest" in request for request in requests):
                return {"UnprocessedItems": {}}
            # Neither the new upsert nor the restore ever clears.
            return {"UnprocessedItems": {table: requests}}

        client.batch_write_item.side_effect = batch_write

        with patch.object(metadataService, "dynamodb_client", client), \
                patch.object(time, "sleep"):
            with pytest.raises(metadataService.VAMSGeneralErrorResponse) as raised:
                metadataService._replace_all_asset_metadata(
                    "db1",
                    "asset1",
                    [MetadataItemModel(metadataKey="newKey", metadataValue="v")],
                    {"tokens": ["user1"]},
                )

        assert "may be inconsistent" in str(raised.value)


class _DeleteHarness:
    """Module globals a metadata delete touches before its batch delete.

    The schema query answers with no schemas, so deletion validation passes and the run
    reaches the batch delete -- which is the arm under test here.
    """

    _EXISTING_ROW = {
        "metadataKey": {"S": "governedKey"},
        "metadataValue": {"S": "v"},
        "metadataValueType": {"S": "string"},
    }

    def __init__(self, batch_write):
        self.client = MagicMock()
        page_iterator = MagicMock()
        page_iterator.build_full_result.return_value = {"Items": [self._EXISTING_ROW]}
        paginator = MagicMock()
        paginator.paginate.return_value = page_iterator
        self.client.get_paginator.return_value = paginator
        self.client.query.return_value = {"Items": []}
        self.client.batch_write_item.side_effect = batch_write

        self.asset_table = MagicMock()
        self.asset_table.get_item.return_value = {
            "Item": {"databaseId": "db1", "assetId": "asset1", "assetName": "A", "tags": []}
        }
        self.metadata_table = MagicMock()
        self.metadata_table.get_item.return_value = {"Item": {"metadataKey": "governedKey"}}

        enforcer = MagicMock()
        enforcer.enforce.return_value = True
        self.enforcer_cls = MagicMock(return_value=enforcer)

        self._stack = contextlib.ExitStack()

    def __enter__(self):
        for target, replacement in (
            ("dynamodb_client", self.client),
            ("asset_storage_table", self.asset_table),
            ("asset_file_metadata_table", self.metadata_table),
            ("CasbinEnforcer", self.enforcer_cls),
        ):
            self._stack.enter_context(patch.object(metadataService, target, replacement))
        self._stack.enter_context(patch.object(time, "sleep"))
        return self

    def __exit__(self, *exc):
        self._stack.close()
        return False


@pytest.fixture
def _clear_schema_cache():
    """The aggregate cache is a module global, and this directory's conftest loads
    `common.metadataSchemaValidation` as a separate module object from
    `backend.backend.common.metadataSchemaValidation`, so both are cleared."""
    modules = [
        sys.modules.get("common.metadataSchemaValidation"),
        sys.modules.get("backend.backend.common.metadataSchemaValidation"),
    ]
    for module in modules:
        if module is not None:
            module._schema_cache.clear()
    yield
    for module in modules:
        if module is not None:
            module._schema_cache.clear()


@pytest.mark.unit
@pytest.mark.usefixtures("_clear_schema_cache")
class TestDeleteReportsUnprocessedKeys:
    """The delete arm of the same class: a key is appended to `successfulItems` BEFORE its
    batch is issued and is only moved out inside `except Exception`, so a 200 carrying
    UnprocessedItems reported a key that is still readable as deleted."""

    @staticmethod
    def _delete():
        from backend.backend.models.metadata import DeleteAssetMetadataRequestModel
        return metadataService.delete_asset_metadata(
            "db1", "asset1",
            DeleteAssetMetadataRequestModel(metadataKeys=["governedKey"]),
            {"tokens": ["user1"]},
        )

    def test_a_never_deleted_key_is_reported_as_a_failure(self):
        table = metadataService.asset_file_metadata_table_name
        with _DeleteHarness(lambda **kwargs: {"UnprocessedItems": {table: kwargs["RequestItems"][table]}}):
            response = self._delete()

        assert response.failureCount == 1, "an undeleted key was reported as a clean delete"
        assert response.success is False
        assert "governedKey" not in response.successfulItems
        assert "governedKey" in [failure["key"] for failure in response.failedItems]

    def test_a_delete_that_clears_is_still_reported_successful(self):
        """Positive control: the same harness reports success when DynamoDB accepts the batch."""
        with _DeleteHarness(lambda **kwargs: {"UnprocessedItems": {}}) as harness:
            response = self._delete()

        assert response.success is True
        assert response.failureCount == 0
        assert "governedKey" in response.successfulItems
        assert harness.client.batch_write_item.call_count == 1
