# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A permanent delete sends no subscription email.

Subscribers are notified when the asset is archived. A permanent delete removes the asset's SNS
topic and its subscription record before anything else, so a notification sent afterwards
publishes to a topic that no longer exists. The delete still removes the topic.
"""

from unittest.mock import MagicMock

import pytest

from tests.handlers.assets.test_assetService_history import _asset, _load


def _prepare(m, monkeypatch, asset):
    asset_table = MagicMock()
    asset_table.get_item.return_value = {"Item": dict(asset)}
    db_table = MagicMock()
    db_table.get_item.return_value = {"Item": {"databaseId": "db1"}}
    enforcer = MagicMock()
    enforcer.return_value.enforce.return_value = True
    stubs = {
        "asset_table": asset_table,
        "db_table": db_table,
        "CasbinEnforcer": enforcer,
        "write_asset_history_record": MagicMock(),
        "send_subscription_email": MagicMock(),
        "lambda_client": MagicMock(),
        "get_asset_bucket_details": MagicMock(return_value={"bucketName": "bucket"}),
        "update_asset_count": MagicMock(),
        "delete_s3_prefix_all_versions": MagicMock(return_value=[]),
        "delete_assetAuxiliary_files": MagicMock(),
        "delete_asset_metadata_for_permanent_deletion": MagicMock(),
        "sns_client": MagicMock(),
        "subscription_table": MagicMock(),
        "asset_links_table": None,
        "asset_upload_table": None,
        "comment_table": None,
        "versions_table": None,
        "asset_versions_files_table": None,
        "asset_file_metadata_versions_table": None,
    }
    for name, value in stubs.items():
        monkeypatch.setattr(m, name, value, raising=False)


def _delete_permanently(m):
    request = MagicMock()
    request.confirmPermanentDelete = True
    return m.delete_asset_permanent("db1#deleted", "a1", request, {"tokens": ["u1"]})


@pytest.mark.unit
class TestPermanentDeleteSendsNoNotification:
    def test_no_email_is_sent_after_the_topic_is_deleted(self, monkeypatch):
        m = _load()
        topic = "arn:aws:sns:us-east-1:111122223333:a1-topic"
        _prepare(m, monkeypatch, _asset(db="db1#deleted", status="archived", snsTopic=topic))

        result = _delete_permanently(m)

        assert result.success is True
        m.sns_client.delete_topic.assert_called_once_with(TopicArn=topic)
        m.send_subscription_email.assert_not_called()
        m.lambda_client.invoke.assert_not_called()

    def test_an_asset_without_a_topic_sends_no_email_either(self, monkeypatch):
        m = _load()
        _prepare(m, monkeypatch, _asset(db="db1#deleted", status="archived"))

        _delete_permanently(m)

        m.sns_client.delete_topic.assert_not_called()
        m.send_subscription_email.assert_not_called()
