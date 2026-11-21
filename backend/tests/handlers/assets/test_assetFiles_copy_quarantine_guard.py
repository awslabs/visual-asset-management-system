# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""assetFiles copy: the quarantine block applies to a copy into another asset.

A copy into another asset or database puts the source file's bytes in an asset that is not
quarantined, so with ``app.compliance.quarantineBlocksDownload`` on it is refused for a quarantined
source, as a download is. The check runs after both assets are authorized, so a caller who may not
read the source never learns its state. A copy within the asset keeps the bytes where the block
still applies, and is not refused. With the block off nothing changes.
"""

import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "test-s3-buckets-table")
os.environ.setdefault("ASSET_STORAGE_TABLE_NAME", "test-asset-table")
os.environ.setdefault("ASSET_FILE_VERSIONS_STORAGE_TABLE_NAME", "test-asset-file-versions-table")

from backend.backend.handlers.assets import assetFiles  # noqa: E402
from backend.tests.quarantineRefusalLog import assert_refusal_logged_once, captured_logs  # noqa: E402

# The guard module the handler bound at import (patched in place, so the handler sees the patch).
guard = sys.modules[assetFiles.check_quarantine_block.__module__]

COPY_MESSAGE = "Asset is quarantined and its files cannot be copied to another asset"
CLAIMS = {"tokens": ["alice"], "roles": []}
SOURCE = ("db1", "a1")


def _state_table(item):
    table = MagicMock(name="compliance_asset_state_table")
    table.get_item.return_value = {"Item": item} if item is not None else {}
    return table


def _quarantined(exception_granted=False):
    row = {"databaseId": SOURCE[0], "assetId": SOURCE[1], "complianceState": "quarantined"}
    if exception_granted:
        row["exceptionGranted"] = True
    return row


def _block(table):
    return patch.multiple(guard, quarantine_blocks_download=True, compliance_asset_state_table=table)


@pytest.fixture
def af(monkeypatch):
    """assetFiles with asset, permission and S3 access stubbed: the source exists, the destination
    does not, and the copy succeeds."""
    module = assetFiles
    monkeypatch.setattr(module, "get_asset_with_permissions",
                        lambda databaseId, assetId, op, claims: {"databaseId": databaseId,
                                                                 "assetId": assetId})
    monkeypatch.setattr(module, "validate_cross_asset_permissions", MagicMock(return_value=True))
    monkeypatch.setattr(module, "get_asset_s3_location",
                        lambda asset: ("bucket", f"{asset['databaseId']}/{asset['assetId']}/"))
    monkeypatch.setattr(module, "validateUnallowedFileExtensionAndContentType",
                        MagicMock(return_value=True))
    s3_client = MagicMock()
    s3_client.head_object.return_value = {}
    monkeypatch.setattr(module, "s3_client", s3_client)
    monkeypatch.setattr(module, "check_destination_file_exists", MagicMock(return_value=False))
    monkeypatch.setattr(module, "copy_s3_object", MagicMock(return_value=True))
    monkeypatch.setattr(module, "process_preview_files", MagicMock(return_value=[]))
    monkeypatch.setattr(module, "copy_auxiliary_files", MagicMock())
    monkeypatch.setattr(module, "_copy_file_metadata_to_destination", MagicMock(return_value=0))
    monkeypatch.setattr(module, "send_subscription_email", MagicMock())
    return module


def _copy(af, dest_asset_id="a2", dest_database_id=None):
    return af.copy_file(SOURCE[0], SOURCE[1], "/model.glb", "/model.glb", dest_asset_id,
                        dest_database_id, CLAIMS)


@pytest.mark.unit
class TestCopyToAnotherAssetIsQuarantineGuarded:
    def test_a_quarantined_source_is_refused_before_s3_is_read(self, af):
        table = _state_table(_quarantined())
        with _block(table), pytest.raises(af.QuarantineBlockedResponse) as refused:
            _copy(af)

        assert str(refused.value) == COPY_MESSAGE
        assert refused.value.status_code == 400
        table.get_item.assert_called_once_with(Key={"databaseId": "db1", "assetId": "a1"})
        af.s3_client.head_object.assert_not_called()
        af.copy_s3_object.assert_not_called()

    def test_a_copy_into_another_database_is_refused_too(self, af):
        table = _state_table(_quarantined())
        with _block(table), pytest.raises(af.QuarantineBlockedResponse):
            _copy(af, dest_asset_id="a1", dest_database_id="db2")
        af.copy_s3_object.assert_not_called()

    def test_a_granted_exception_lets_the_copy_proceed(self, af):
        with _block(_state_table(_quarantined(exception_granted=True))):
            response = _copy(af)
        assert response.success
        af.copy_s3_object.assert_called_once()

    def test_a_source_without_a_compliance_record_is_copied(self, af):
        with _block(_state_table(None)):
            assert _copy(af).success

    def test_another_state_is_copied(self, af):
        row = dict(_quarantined(), complianceState="non_compliant")
        with _block(_state_table(row)):
            assert _copy(af).success

    def test_the_destination_asset_state_is_not_read(self, af):
        """Only the source's bytes leave an asset; a quarantined destination receives them."""
        table = _state_table(None)
        with _block(table):
            _copy(af)
        assert [c.kwargs["Key"]["assetId"] for c in table.get_item.call_args_list] == ["a1"]

    def test_a_caller_refused_on_either_asset_gets_403_and_no_state_read(self, af, monkeypatch):
        monkeypatch.setattr(af, "validate_cross_asset_permissions",
                            MagicMock(side_effect=af.VAMSAuthorizationDeniedResponse()))
        table = _state_table(_quarantined())
        with _block(table), pytest.raises(af.VAMSAuthorizationDeniedResponse):
            _copy(af)
        table.get_item.assert_not_called()


@pytest.mark.unit
class TestCopyWithinTheAssetAndWithTheBlockOff:
    def test_a_copy_within_the_quarantined_asset_is_not_refused(self, af):
        table = _state_table(_quarantined())
        with _block(table):
            response = af.copy_file("db1", "a1", "/model.glb", "/copy/model.glb", None, None, CLAIMS)
        assert response.success
        table.get_item.assert_not_called()

    def test_with_the_block_off_a_quarantined_source_is_copied_and_no_table_is_read(self, af):
        table = _state_table(_quarantined())
        with patch.multiple(guard, quarantine_blocks_download=False,
                            compliance_asset_state_table=table):
            assert _copy(af).success
        table.get_item.assert_not_called()


def _copy_event(body):
    path = "/database/db1/assets/a1/copyFile"
    return {
        "requestContext": {"http": {"method": "POST", "path": path}},
        "pathParameters": {"databaseId": "db1", "assetId": "a1"},
        "body": json.dumps(body),
    }


@pytest.mark.unit
class TestTheCopyRouteAnswersTheRefusal:
    def test_400_with_the_copy_message_logged_once_without_a_traceback(self, af, monkeypatch):
        enforcer = MagicMock()
        enforcer.return_value.enforceAPI.return_value = True
        monkeypatch.setattr(af, "CasbinEnforcer", enforcer)
        monkeypatch.setattr(af, "request_to_claims", MagicMock(return_value=dict(CLAIMS)))
        monkeypatch.setattr(af, "log_action", MagicMock())
        event = _copy_event({"sourcePath": "/model.glb", "destinationPath": "/model.glb",
                             "destinationAssetId": "a2"})

        with _block(_state_table(_quarantined())), captured_logs(af) as loggers:
            response = af.handle_copy_file(event, MagicMock())

        assert response["statusCode"] == 400
        assert json.loads(response["body"]) == {"message": COPY_MESSAGE}
        assert_refusal_logged_once(loggers, "db1", "a1", "POST", "/database/db1/assets/a1/copyFile")
        af.log_action.assert_not_called()
