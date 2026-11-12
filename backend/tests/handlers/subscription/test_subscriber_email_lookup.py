#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Subscriber email lookup for users who have not signed in to the web app (#1354).

The VAMS user-profile table gets a user's email at their first web sign-in. A user created through
the API (`vamscli user cognito create -u <id> -e <email>`) has no profile row until then, so a
subscription for them failed with "does not have a valid email to use" unless the user id was itself
an email address.

Pinned here:
- an API-created Cognito user is subscribed with the email their Cognito user carries;
- a signed-in user keeps the profile email, and Cognito is not read;
- a user that exists nowhere is refused as before (or, for an email-shaped id, subscribed by the id);
- a deployment without Cognito, and a Cognito read that fails, behave as before;
- the lookup runs only after both authorization tiers allow the request.
"""

import json
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from backend.backend.handlers.subscription import subscriptionService as svc

POOL_ID = "us-east-1_TESTPOOL"
ASSET_ID = "test-asset-id"
NO_EMAIL = "does not have a valid email to use"


def _profile_table(items):
    """User-profile table stand-in answering get_item from {userId: item}."""
    table = MagicMock()
    table.get_item.side_effect = lambda Key: (
        {"Item": items[Key["userId"]]} if Key["userId"] in items else {})
    return table


def _cognito(users):
    """Cognito client stand-in answering admin_get_user from {username: email}."""
    client = MagicMock()

    def admin_get_user(UserPoolId, Username):
        assert UserPoolId == POOL_ID
        if Username not in users:
            raise ClientError({"Error": {"Code": "UserNotFoundException", "Message": "User does not exist."}},
                              "AdminGetUser")
        return {"Username": Username, "UserAttributes": [
            {"Name": "sub", "Value": "0000"}, {"Name": "email", "Value": users[Username]}]}

    client.admin_get_user.side_effect = admin_get_user
    return client


@pytest.fixture
def deployment(monkeypatch):
    """A Cognito deployment with one signed-in user and one API-created user; SNS and the
    subscription table are stubbed."""
    profiles = _profile_table({"signed-in-1": {"userId": "signed-in-1", "email": "signed.in@example.com"}})
    cognito = _cognito({"reviewer-1": "reviewer@example.com",
                        "signed-in-1": "pool.address@example.com"})
    dynamodb = MagicMock()
    dynamodb.Table.side_effect = lambda name: profiles if name == svc.user_table_name else MagicMock()
    sns = MagicMock()
    monkeypatch.setattr(svc, "dynamodb", dynamodb)
    monkeypatch.setattr(svc, "cognito_client", cognito, raising=False)
    monkeypatch.setattr(svc, "user_pool_id", POOL_ID, raising=False)
    monkeypatch.setattr(svc, "get_subscription_obj", lambda *_: None)
    monkeypatch.setattr(svc, "create_sns_subscriptions", sns)
    return {"cognito": cognito, "sns": sns}


def _create(subscriber):
    return svc.create_subscription({"eventName": "Asset Version Change", "entityName": "Asset",
                                    "entityId": ASSET_ID, "subscribers": [subscriber]})


@pytest.mark.unit
class TestSubscriberEmail:
    def test_an_api_created_user_is_subscribed_with_their_cognito_email(self, deployment):
        response = _create("reviewer-1")
        assert response["statusCode"] == 200, response["body"]
        deployment["sns"].assert_called_once_with(ASSET_ID, ["reviewer@example.com"])

    def test_a_signed_in_user_keeps_the_profile_email(self, deployment):
        response = _create("signed-in-1")
        assert response["statusCode"] == 200, response["body"]
        deployment["sns"].assert_called_once_with(ASSET_ID, ["signed.in@example.com"])
        deployment["cognito"].admin_get_user.assert_not_called()

    def test_a_user_that_does_not_exist_is_refused(self, deployment):
        response = _create("nobody-1")
        assert response["statusCode"] == 400
        assert NO_EMAIL in json.loads(response["body"])["message"]
        deployment["sns"].assert_not_called()

    def test_an_email_shaped_id_with_no_user_is_subscribed_by_the_id(self, deployment):
        response = _create("someone@example.com")
        assert response["statusCode"] == 200, response["body"]
        deployment["sns"].assert_called_once_with(ASSET_ID, ["someone@example.com"])

    def test_a_deployment_without_cognito_does_not_read_a_pool(self, deployment, monkeypatch):
        monkeypatch.setattr(svc, "cognito_client", None)
        response = _create("reviewer-1")
        assert response["statusCode"] == 400
        assert NO_EMAIL in json.loads(response["body"])["message"]
        deployment["cognito"].admin_get_user.assert_not_called()

    def test_a_failed_cognito_read_is_refused_not_a_500(self, deployment):
        deployment["cognito"].admin_get_user.side_effect = ClientError(
            {"Error": {"Code": "AccessDeniedException", "Message": "denied"}}, "AdminGetUser")
        response = _create("reviewer-1")
        assert response["statusCode"] == 400
        assert NO_EMAIL in json.loads(response["body"])["message"]

    def test_an_added_subscriber_on_update_uses_the_cognito_email(self, deployment, monkeypatch):
        existing = {"subscribers": {"L": [{"S": "signed-in-1"}]}}
        monkeypatch.setattr(svc, "get_subscription_obj", lambda *_: existing)
        monkeypatch.setattr(svc, "delete_sns_subscriptions", MagicMock())
        response = svc.update_subscription({"eventName": "Asset Version Change", "entityName": "Asset",
                                            "entityId": ASSET_ID,
                                            "subscribers": ["signed-in-1", "reviewer-1"]})
        assert response["statusCode"] == 200, response["body"]
        deployment["sns"].assert_called_once_with(ASSET_ID, ["reviewer@example.com"])


class _Enforcer:
    def __init__(self, api_allowed, object_allowed):
        self.api_allowed, self.object_allowed = api_allowed, object_allowed

    def __call__(self, _claims):
        return self

    def enforceAPI(self, _event):
        return self.api_allowed

    def enforce(self, _obj, _action):
        return self.object_allowed


@pytest.mark.unit
@pytest.mark.parametrize("api_allowed,object_allowed", [(False, True), (True, False)])
def test_the_lookup_runs_only_after_authorization(deployment, monkeypatch, api_allowed, object_allowed):
    """Who may subscribe whom is decided before any user lookup: a caller either tier denies gets
    403 and no Cognito read happens."""
    monkeypatch.setattr(svc, "request_to_claims", lambda _e: {"tokens": ["caller-1"], "roles": []})
    monkeypatch.setattr(svc, "CasbinEnforcer", _Enforcer(api_allowed, object_allowed))
    monkeypatch.setattr(svc, "get_asset_object_from_id",
                        lambda *_: {"assetId": ASSET_ID, "databaseId": "db-1"})
    event = {"requestContext": {"http": {"method": "POST"}},
             "body": json.dumps({"eventName": "Asset Version Change", "entityName": "Asset",
                                 "entityId": ASSET_ID, "subscribers": ["reviewer-1"]})}

    response = svc.lambda_handler(event, None)

    assert response["statusCode"] == 403
    deployment["cognito"].admin_get_user.assert_not_called()
    deployment["sns"].assert_not_called()
