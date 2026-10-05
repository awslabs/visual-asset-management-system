# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""What an asset operation writes after its Tier-2 check, and after its asset recount fails.

**The Casbin object type stays off the stored record.** `CasbinEnforcer.enforce` reads
`object__type` off the object it is handed, so the single-asset check and the tag-change gate
annotate a copy of the stored record. Archive and unarchive write that same record back with
`put_item`, so an annotation made on the record itself would be stored with it. Each case is paired
with an assertion that the check still saw `object__type: asset`, because a check that lost the
annotation would satisfy the storage assertion while evaluating no asset rule at all.

**A failed recount does not fail a committed operation.** Archive, unarchive, permanent delete and
create recount the database's assets after their write has committed. A recount that raises leaves
the operation done, so the operation reports success and still sends its subscription email and
writes its history record.
"""

from unittest.mock import MagicMock

import pytest

from tests.handlers.assets.test_assetService_history import _asset, _load
from tests.handlers.assets.test_assetService_tag_mutation_authz import (
    _tag_existence_validation_stubbed,
)
from tests.handlers.assets.test_createAsset_history import _load as _load_create_asset


class _RecordingEnforcer:
    """A CasbinEnforcer stand-in that allows everything and records what it was asked about."""

    def __init__(self):
        self.calls = []

    def __call__(self, claims_and_roles):
        return self

    def enforce(self, obj, action):
        self.calls.append((dict(obj), action))
        return True


def _prepare(m, asset):
    m.asset_table = MagicMock()
    m.asset_table.get_item.return_value = {"Item": dict(asset)}
    m.db_table = MagicMock()
    m.db_table.get_item.return_value = {"Item": {"databaseId": "db1"}}
    m.write_asset_history_record = MagicMock()
    m.send_subscription_email = MagicMock()
    m.get_asset_bucket_details = MagicMock(return_value={"bucketName": "bucket"})
    m.archive_multi_assetFiles = MagicMock()
    m.archive_file_preview = MagicMock()
    m.unarchive_multi_assetFiles = MagicMock(return_value=0)
    m.unarchive_file_preview = MagicMock()
    m.update_asset_count = MagicMock()
    enforcer = _RecordingEnforcer()
    m.CasbinEnforcer = enforcer
    return enforcer


def _prepare_permanent_delete(m):
    m.claims_and_roles = {"tokens": ["u1"]}
    m.delete_s3_prefix_all_versions = MagicMock(return_value=[])
    m.delete_assetAuxiliary_files = MagicMock()
    m.delete_asset_metadata_for_permanent_deletion = MagicMock()
    m.sns_client = MagicMock()
    m.subscription_table = MagicMock()
    m.asset_links_table = None
    m.asset_upload_table = None
    m.comment_table = None
    m.versions_table = None
    m.asset_versions_files_table = None
    m.asset_file_metadata_versions_table = None


def _archive(m):
    request = MagicMock()
    request.reason = "cleanup"
    return m.archive_asset("db1", "a1", request, {"tokens": ["u1"]})


def _unarchive(m):
    request = MagicMock()
    request.reason = "restore"
    request.unarchiveFiles = False
    return m.unarchive_asset("db1", "a1", request, {"tokens": ["u1"]})


def _stored_items(m):
    return [call.kwargs["Item"] for call in m.asset_table.put_item.call_args_list]


@pytest.mark.unit
class TestNoObjectTypeOnStoredRecords:
    def test_archive_stores_the_record_without_an_object_type(self):
        m = _load()
        enforcer = _prepare(m, _asset())

        _archive(m)

        stored = _stored_items(m)
        assert [item["databaseId"] for item in stored] == ["db1#deleted"]
        assert "object__type" not in stored[0]
        assert enforcer.calls == [(dict(_asset(), object__type="asset"), "DELETE")]

    def test_unarchive_stores_the_record_without_an_object_type(self):
        m = _load()
        enforcer = _prepare(m, _asset(db="db1#deleted", status="archived"))

        _unarchive(m)

        stored = _stored_items(m)
        assert stored, "unarchive wrote no record"
        assert all("object__type" not in item for item in stored)
        assert [(obj["object__type"], action) for obj, action in enforcer.calls] == [("asset", "PUT")]

    def test_the_tag_change_gate_still_evaluates_asset_objects(self):
        """The gate builds its own copies, so it does not depend on the earlier check's annotation."""
        m = _load()
        enforcer = _prepare(m, _asset(tags=["kept"]))
        m.claims_and_roles = {"tokens": ["u1"]}

        with _tag_existence_validation_stubbed():
            m.update_asset("db1", "a1", {"tags": ["kept", "added"]}, {"tokens": ["u1"]})

        assert [(obj["object__type"], action) for obj, action in enforcer.calls] == [
            ("asset", "PUT"), ("asset", "GET"), ("asset", "GET"), ("asset", "PUT")
        ]
        assert [obj["tags"] for obj, _ in enforcer.calls[1:]] == [
            ["kept"], ["kept", "added"], ["kept", "added"]
        ]


@pytest.mark.unit
class TestRecountIsBestEffortAfterCommit:
    @pytest.mark.parametrize("operation", ["archive", "unarchive"])
    def test_a_failed_recount_still_reports_success_and_notifies(self, operation):
        m = _load()
        archived = operation == "unarchive"
        _prepare(m, _asset(db="db1#deleted", status="archived") if archived else _asset())
        m.update_asset_count = MagicMock(side_effect=RuntimeError("throttled"))

        result = (_unarchive if archived else _archive)(m)

        m.update_asset_count.assert_called_once()
        assert result.success is True
        assert result.operation == operation
        m.write_asset_history_record.assert_called_once()
        m.send_subscription_email.assert_called_once_with("db1", "a1")

    def test_a_failed_recount_after_permanent_delete_still_writes_history(self):
        m = _load()
        _prepare(m, _asset())
        _prepare_permanent_delete(m)
        m.update_asset_count = MagicMock(side_effect=RuntimeError("throttled"))
        request = MagicMock()
        request.confirmPermanentDelete = True

        result = m.delete_asset_permanent("db1", "a1", request, {"tokens": ["u1"]})

        m.update_asset_count.assert_called_once()
        assert result.success is True
        m.write_asset_history_record.assert_called_once()
        assert m.write_asset_history_record.call_args[0][2] == m.CHANGE_SOURCE_PERMANENT_DELETE

    def test_a_failed_recount_after_create_still_returns_the_asset(self):
        m = _load_create_asset()
        m.asset_table = MagicMock()
        m.asset_table.get_item.return_value = {}
        m.database_table = MagicMock()
        m.database_table.get_item.return_value = {"Item": {"databaseId": "testdb1"}}
        m.validate_tags_exist = MagicMock(return_value=True)
        m.verify_all_required_tags_satisfied = MagicMock(return_value=True)
        m.get_default_bucket_details = MagicMock(return_value={
            "bucketId": "b1", "bucketName": "bucket", "baseAssetsPrefix": ""
        })
        m.check_s3_prefix_exists = MagicMock(return_value=False)
        m.create_prefix_folder = MagicMock()
        m.create_sns_topic_for_asset = MagicMock(return_value="arn:sns:topic")
        m.create_initial_version_record = MagicMock(return_value="0")
        m.save_asset_details = MagicMock()
        m.update_asset_count = MagicMock(side_effect=RuntimeError("throttled"))
        m.write_asset_history_record = MagicMock()
        request = m.CreateAssetRequestModel(
            databaseId="testdb1", assetName="Asset One",
            description="test description", isDistributable=True, tags=[]
        )

        response = m.create_asset(request, {"tokens": ["user1"]}, False)

        m.save_asset_details.assert_called_once()
        m.update_asset_count.assert_called_once()
        assert response.assetId == m.save_asset_details.call_args[0][0]["assetId"]
        m.write_asset_history_record.assert_called_once()
