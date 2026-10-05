# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Unarchiving an asset whose database has been deleted.

Deleting a database is refused only while an asset is live in it, so an asset archived first
survives the delete in the `{databaseId}#deleted` asset partition, beside a database that exists
only as its `#deleted` record. Moving that asset back into the live partition leaves it under a
database no listing shows, still indexed, and inherited by any database later created with the
same id. The unarchive therefore refuses before any file, preview or record is touched, and only
after authorization, so a caller who may not unarchive the asset learns nothing about its
database.
"""

import json

import pytest
from unittest.mock import MagicMock
from botocore.exceptions import ClientError

from tests.handlers.assets.test_assetService_history import _load

_DB = "db1"
_ASSET = "asset-1"
_CLAIMS = {"tokens": ["u1"]}


def _archived_asset():
    return {
        "databaseId": f"{_DB}#deleted", "assetId": _ASSET, "assetName": "N1",
        "description": "d1", "isDistributable": True, "tags": [], "bucketId": "b1",
        "assetLocation": {"Key": f"{_ASSET}/"},
        "previewLocation": {"Key": f"previews/{_ASSET}/preview.png"},
        "status": "archived",
    }


@pytest.fixture
def svc(monkeypatch):
    """The loaded assetService with every collaborator of `unarchive_asset` replaced.

    The module is shared with the history tests, so each replacement goes through monkeypatch
    and is undone after the test.
    """
    m = _load()
    asset_table = MagicMock()
    asset_table.get_item.return_value = {"Item": _archived_asset()}
    monkeypatch.setattr(m, "asset_table", asset_table)
    monkeypatch.setattr(m, "db_table", MagicMock())
    monkeypatch.setattr(m, "get_asset_bucket_details",
                        MagicMock(return_value={"bucketName": "bucket-1"}))
    monkeypatch.setattr(m, "unarchive_multi_assetFiles", MagicMock(return_value=0))
    monkeypatch.setattr(m, "unarchive_file_preview", MagicMock())
    monkeypatch.setattr(m, "write_asset_history_record", MagicMock())
    monkeypatch.setattr(m, "update_asset_count", MagicMock())
    monkeypatch.setattr(m, "send_subscription_email", MagicMock())
    monkeypatch.setattr(m, "claims_and_roles", dict(_CLAIMS))
    return m


def _request(m, **fields):
    return m.UnarchiveAssetRequestModel(confirmUnarchive=True, **fields)


def _put_event(path_database_id=_DB):
    return {
        "requestContext": {
            "http": {
                "method": "PUT",
                "path": f"/database/{path_database_id}/assets/{_ASSET}/unarchiveAsset",
            }
        },
        "pathParameters": {"databaseId": path_database_id, "assetId": _ASSET},
        "queryStringParameters": None,
        "body": json.dumps({"confirmUnarchive": True, "unarchiveFiles": True}),
    }


def _assert_nothing_was_moved(m):
    m.get_asset_bucket_details.assert_not_called()
    m.unarchive_multi_assetFiles.assert_not_called()
    m.unarchive_file_preview.assert_not_called()
    m.asset_table.put_item.assert_not_called()
    m.asset_table.delete_item.assert_not_called()
    m.write_asset_history_record.assert_not_called()
    m.update_asset_count.assert_not_called()


@pytest.mark.unit
class TestUnarchiveIntoADeletedDatabaseIsRefused:
    def test_refuses_before_any_file_preview_or_record_is_touched(self, svc):
        svc.db_table.get_item.return_value = {}

        with pytest.raises(svc.VAMSGeneralErrorResponse) as refused:
            svc.unarchive_asset(_DB, _ASSET, _request(svc, unarchiveFiles=True), _CLAIMS)

        assert "database has been deleted" in str(refused.value), str(refused.value)
        _assert_nothing_was_moved(svc)

    def test_the_refusal_names_neither_the_database_nor_the_asset(self, svc):
        """backend/CLAUDE.md Rule 11: the reason is stated without echoing request input."""
        svc.db_table.get_item.return_value = {}

        with pytest.raises(svc.VAMSGeneralErrorResponse) as refused:
            svc.unarchive_asset(_DB, _ASSET, _request(svc), _CLAIMS)

        message = str(refused.value)
        assert _DB not in message and _ASSET not in message, message

    def test_a_deleted_suffix_on_the_path_reads_the_live_database_key(self, svc):
        """The live key is read, strongly consistent, so a delete moments earlier is seen."""
        svc.db_table.get_item.return_value = {}

        with pytest.raises(svc.VAMSGeneralErrorResponse):
            svc.unarchive_asset(f"{_DB}#deleted", _ASSET, _request(svc), _CLAIMS)

        svc.db_table.get_item.assert_called_once_with(
            Key={"databaseId": _DB}, ConsistentRead=True)

    def test_the_request_handler_answers_400_and_moves_nothing(self, svc):
        svc.db_table.get_item.return_value = {}

        response = svc.handle_put_request(_put_event())

        assert response["statusCode"] == 400, response
        assert "database has been deleted" in json.loads(response["body"])["message"]
        _assert_nothing_was_moved(svc)

    def test_a_failed_database_read_moves_nothing(self, svc):
        """A read that errors is not taken to mean the database exists."""
        svc.db_table.get_item.side_effect = ClientError(
            {"Error": {"Code": "ProvisionedThroughputExceededException", "Message": "slow down"}},
            "GetItem")

        response = svc.handle_put_request(_put_event())

        assert response["statusCode"] == 500, response
        _assert_nothing_was_moved(svc)


@pytest.mark.unit
class TestUnarchiveIntoALiveDatabaseStillWorks:
    def test_a_live_database_receives_the_asset(self, svc):
        """Positive control: every refusal above is also satisfied by a handler that refuses all."""
        svc.db_table.get_item.return_value = {"Item": {"databaseId": _DB}}

        result = svc.unarchive_asset(_DB, _ASSET, _request(svc, unarchiveFiles=True), _CLAIMS)

        assert result.success is True
        written = svc.asset_table.put_item.call_args.kwargs["Item"]
        assert written["databaseId"] == _DB
        assert "status" not in written
        svc.asset_table.delete_item.assert_called_once_with(
            Key={"databaseId": f"{_DB}#deleted", "assetId": _ASSET})
        svc.unarchive_multi_assetFiles.assert_called_once()
        svc.update_asset_count.assert_called_once()

    def test_the_request_handler_answers_200_for_a_live_database(self, svc):
        svc.db_table.get_item.return_value = {"Item": {"databaseId": _DB}}

        response = svc.handle_put_request(_put_event())

        assert response["statusCode"] == 200, response
        assert svc.asset_table.put_item.call_args.kwargs["Item"]["databaseId"] == _DB


@pytest.mark.unit
class TestAuthorizationStillDecidesFirst:
    def test_a_denied_caller_is_refused_before_the_database_is_read(self, svc, monkeypatch):
        enforcer = MagicMock()
        enforcer.return_value.enforce.return_value = False
        monkeypatch.setattr(svc, "CasbinEnforcer", enforcer)
        svc.db_table.get_item.return_value = {}

        with pytest.raises(svc.AuthorizationDenied):
            svc.unarchive_asset(_DB, _ASSET, _request(svc), _CLAIMS)

        svc.db_table.get_item.assert_not_called()
        _assert_nothing_was_moved(svc)
