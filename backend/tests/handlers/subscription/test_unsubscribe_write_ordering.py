#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Write ordering and retry safety of the unsubscribe request (S2-BACKEND-040).

``unsubscribeService.delete_subscription`` performs two writes for an asset subscription: it
releases the caller's SNS subscription on the asset topic, and it removes the caller from the
subscription row. Neither write is transactional with the other, so the pair has to be safe to
retry from whichever side failed:

* **The SNS side goes first.** A row rewritten ahead of a failed SNS call leaves the caller
  absent from the row while still subscribed to the topic. The retry then finds no caller to
  remove, and a guard that reports that as "no such subscription" locks the half-applied state
  in: the user keeps receiving notifications, and no request can stop them.
* **An absent subscriber is not an error.** A row that no longer lists the caller is exactly
  what a retry after a failure between the two writes sees, so the request still runs the SNS
  cleanup and answers 200. A subscription RECORD that does not exist at all stays a 400.

The failing arms are paired with positive controls that complete both writes. The "row
unchanged" claim is stated over the row's stored content -- an update that rewrites the same
list is not a change -- so a stricter implementation does not fail it.
"""

import json
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from backend.backend.handlers.subscription import unsubscribeService

ASSET_ID = "test-asset-id"
DATABASE_ID = "test-database-id"
EVENT_NAME = "Asset Version Change"
TOPIC = "arn:aws:sns:us-east-1:123456789012:AssetTopic-test"

USER = "test-user@example.com"
OTHER_USER = "other-user@example.com"
SUBSCRIPTION_ARNS = {
    USER: f"{TOPIC}:11111111-1111-1111-1111-111111111111",
    OTHER_USER: f"{TOPIC}:22222222-2222-2222-2222-222222222222",
}
NOT_FOUND_MESSAGE = "Subscription does not exists for eventName."


def _body(subscriber=USER):
    return {
        "eventName": EVENT_NAME,
        "entityName": "Asset",
        "entityId": ASSET_ID,
        "subscribers": [subscriber],
    }


def _event():
    return {
        "requestContext": {"http": {"method": "DELETE", "path": "/unsubscribe"}},
        "body": _body(),
    }


def _row(*subscribers):
    """The attribute-value shape ``get_subscription_obj`` returns for a stored row."""
    return {
        "eventName": {"S": EVENT_NAME},
        "entityName_entityId": {"S": f"Asset#{ASSET_ID}"},
        "subscribers": {"L": [{"S": subscriber} for subscriber in subscribers]},
    }


def _sns_error(operation):
    return ClientError(
        {"Error": {"Code": "InternalError", "Message": "SNS unavailable"}}, operation)


class Wiring:
    """The module's collaborators, stubbed, sharing one call log so ordering can be asserted."""

    def __init__(self, monkeypatch, row, topic_subscribers=(USER, OTHER_USER)):
        self.row = row
        self.log = []

        self.table = MagicMock()
        self.table.update_item.side_effect = self._record("update_item")
        resource = MagicMock()
        resource.Table.return_value = self.table
        monkeypatch.setattr(unsubscribeService, "dynamodb", resource)

        self.sns = MagicMock()
        self.sns.list_subscriptions_by_topic.return_value = {
            "Subscriptions": [
                {
                    "SubscriptionArn": SUBSCRIPTION_ARNS[subscriber],
                    "Endpoint": subscriber,
                    "Protocol": "email",
                    "TopicArn": TOPIC,
                }
                for subscriber in topic_subscribers
            ]
        }
        self.sns.unsubscribe.side_effect = self._record("sns.unsubscribe")
        monkeypatch.setattr(unsubscribeService, "sns_client", self.sns)

        monkeypatch.setattr(
            unsubscribeService, "get_asset",
            lambda asset_id: {"databaseId": DATABASE_ID, "snsTopic": TOPIC})
        monkeypatch.setattr(
            unsubscribeService, "get_subscription_obj",
            lambda event_name, entity_name, entity_id: row)

    def _record(self, name):
        def side_effect(**kwargs):
            self.log.append(name)
            return {}
        return side_effect

    def fail_sns_unsubscribe(self):
        def side_effect(**kwargs):
            self.log.append("sns.unsubscribe")
            raise _sns_error("Unsubscribe")
        self.sns.unsubscribe.side_effect = side_effect

    def heal_sns_unsubscribe(self):
        self.sns.unsubscribe.side_effect = self._record("sns.unsubscribe")

    def stored_subscribers(self):
        """The subscriber list the row holds after the call: the last rewrite, else the original."""
        if not self.table.update_item.call_args_list:
            return [item["S"] for item in self.row["subscribers"]["L"]]
        last_write = self.table.update_item.call_args_list[-1].kwargs
        return last_write["ExpressionAttributeValues"][":subscribers"]

    def unsubscribed_arns(self):
        return [call.kwargs["SubscriptionArn"] for call in self.sns.unsubscribe.call_args_list]


def _patch_handler(monkeypatch):
    """Authorize the caller through both tiers and resolve the asset, so the handler reaches the writes."""
    monkeypatch.setattr(unsubscribeService, "request_to_claims", lambda event: {"tokens": [USER]})
    enforcer = MagicMock()
    enforcer.enforceAPI.return_value = True
    enforcer.enforce.return_value = True
    monkeypatch.setattr(unsubscribeService, "CasbinEnforcer", lambda claims_and_roles: enforcer)
    monkeypatch.setattr(
        unsubscribeService, "get_asset_object_from_id",
        lambda database_id, asset_id: {"assetId": ASSET_ID, "databaseId": DATABASE_ID})


@pytest.mark.unit
class TestSnsFailureLeavesTheRowUnchanged:
    """A failed SNS call surfaces as a failure and leaves the row still listing the caller."""

    def test_a_failed_sns_unsubscribe_does_not_rewrite_the_row(self, monkeypatch):
        wiring = Wiring(monkeypatch, _row(USER, OTHER_USER))
        wiring.fail_sns_unsubscribe()

        with pytest.raises(ClientError):
            unsubscribeService.delete_subscription(_body())

        assert wiring.stored_subscribers() == [USER, OTHER_USER]

    def test_a_failed_topic_listing_does_not_rewrite_the_row(self, monkeypatch):
        wiring = Wiring(monkeypatch, _row(USER, OTHER_USER))
        wiring.sns.list_subscriptions_by_topic.side_effect = _sns_error(
            "ListSubscriptionsByTopic")

        with pytest.raises(ClientError):
            unsubscribeService.delete_subscription(_body())

        assert wiring.stored_subscribers() == [USER, OTHER_USER]
        wiring.sns.unsubscribe.assert_not_called()

    def test_the_handler_answers_500_with_the_row_intact_and_the_retry_completes(self, monkeypatch):
        """Retry from the SNS side: 500 with nothing applied, then 200 with both writes done."""
        wiring = Wiring(monkeypatch, _row(USER, OTHER_USER))
        _patch_handler(monkeypatch)
        wiring.fail_sns_unsubscribe()

        first = unsubscribeService.lambda_handler(_event(), None)

        assert first["statusCode"] == 500
        assert wiring.stored_subscribers() == [USER, OTHER_USER]

        wiring.heal_sns_unsubscribe()
        second = unsubscribeService.lambda_handler(_event(), None)

        assert second["statusCode"] == 200
        assert wiring.unsubscribed_arns()[-1] == SUBSCRIPTION_ARNS[USER]
        assert wiring.stored_subscribers() == [OTHER_USER]

    def test_both_writes_complete_with_sns_released_first(self, monkeypatch):
        """Positive control: healthy SNS, the caller leaves the topic and then the row."""
        wiring = Wiring(monkeypatch, _row(USER, OTHER_USER))

        response = unsubscribeService.delete_subscription(_body())

        assert response["statusCode"] == 200
        assert json.loads(response["body"])["message"] == "success"
        assert wiring.unsubscribed_arns() == [SUBSCRIPTION_ARNS[USER]]
        assert wiring.stored_subscribers() == [OTHER_USER]
        assert wiring.log.index("sns.unsubscribe") < wiring.log.index("update_item"), (
            "the row must not be rewritten until the SNS subscription has been released")


@pytest.mark.unit
class TestRepeatAfterPartialCommitIsIdempotent:
    """A caller the row no longer lists is still released from the topic, and answered 200."""

    def test_absent_subscriber_still_releases_sns_and_answers_200(self, monkeypatch):
        """The row a half-applied earlier attempt left behind: the caller is gone from it, not from SNS."""
        wiring = Wiring(monkeypatch, _row(OTHER_USER))

        response = unsubscribeService.delete_subscription(_body())

        assert response["statusCode"] == 200
        assert wiring.unsubscribed_arns() == [SUBSCRIPTION_ARNS[USER]]
        assert wiring.stored_subscribers() == [OTHER_USER]

    def test_absent_subscriber_on_an_emptied_row(self, monkeypatch):
        wiring = Wiring(monkeypatch, _row())

        response = unsubscribeService.delete_subscription(_body())

        assert response["statusCode"] == 200
        assert wiring.unsubscribed_arns() == [SUBSCRIPTION_ARNS[USER]]
        assert wiring.stored_subscribers() == []

    def test_the_handler_answers_200_for_the_repeat(self, monkeypatch):
        wiring = Wiring(monkeypatch, _row(OTHER_USER))
        _patch_handler(monkeypatch)

        response = unsubscribeService.lambda_handler(_event(), None)

        assert response["statusCode"] == 200
        assert wiring.unsubscribed_arns() == [SUBSCRIPTION_ARNS[USER]]
        assert wiring.stored_subscribers() == [OTHER_USER]

    def test_a_subscriber_the_topic_never_carried_is_a_no_op(self, monkeypatch):
        wiring = Wiring(monkeypatch, _row(OTHER_USER), topic_subscribers=(OTHER_USER,))

        response = unsubscribeService.delete_subscription(_body())

        assert response["statusCode"] == 200
        assert wiring.unsubscribed_arns() == []
        assert wiring.stored_subscribers() == [OTHER_USER]

    def test_a_missing_subscription_record_stays_a_400(self, monkeypatch):
        wiring = Wiring(monkeypatch, None)

        response = unsubscribeService.delete_subscription(_body())

        assert response["statusCode"] == 400
        assert json.loads(response["body"])["message"] == NOT_FOUND_MESSAGE
        wiring.sns.list_subscriptions_by_topic.assert_not_called()
        wiring.sns.unsubscribe.assert_not_called()
        wiring.table.update_item.assert_not_called()
