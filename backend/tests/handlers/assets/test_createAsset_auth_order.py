# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tier-1 route authorization on `POST /assets` runs before the request body is read.

A caller who is not allowed on the route learns nothing about the request schema: an invalid
body, a valid body and no body all answer 403. The object-level check still runs on the parsed
asset once the route check has passed.
"""

import json
from unittest.mock import MagicMock

import pytest

from tests.handlers.assets.test_createAsset_conditional_put import _load

VALID_BODY = {
    'databaseId': 'testdb1',
    'assetName': 'asset-1',
    'description': 'An asset for tests',
    'isDistributable': True,
    'tags': [],
}

SHORT_NAME_BODY = dict(VALID_BODY, assetName='a')


def _event(body):
    return {
        'requestContext': {'http': {'method': 'POST', 'path': '/assets'}},
        'body': json.dumps(body) if body is not None else None,
    }


@pytest.fixture
def handler():
    m = _load(fresh=True)
    instance = MagicMock()
    instance.enforceAPI.return_value = True
    instance.enforce.return_value = True
    m.CasbinEnforcer = MagicMock(return_value=instance)
    m.request_to_claims = lambda event: {'tokens': ['tester'], 'roles': []}
    m.create_asset = MagicMock(return_value=m.CreateAssetResponseModel(
        assetId='asset-1', message='Asset created successfully'))
    m.enforcer = instance
    return m


@pytest.mark.unit
class TestRouteAuthorizationPrecedesTheBody:

    def test_an_unauthorized_caller_with_an_invalid_body_gets_403(self, handler):
        handler.enforcer.enforceAPI.return_value = False

        response = handler.lambda_handler(_event(SHORT_NAME_BODY), None)

        assert response['statusCode'] == 403

    def test_an_unauthorized_caller_with_a_valid_body_gets_403(self, handler):
        handler.enforcer.enforceAPI.return_value = False

        response = handler.lambda_handler(_event(VALID_BODY), None)

        assert response['statusCode'] == 403

    def test_an_unauthorized_caller_with_no_body_gets_403_not_400(self, handler):
        handler.enforcer.enforceAPI.return_value = False

        response = handler.lambda_handler(_event(None), None)

        assert response['statusCode'] == 403

    def test_a_caller_with_no_tokens_is_denied_before_the_body(self, handler):
        handler.request_to_claims = lambda event: {'tokens': [], 'roles': []}

        response = handler.lambda_handler(_event(SHORT_NAME_BODY), None)

        assert response['statusCode'] == 403
        handler.enforcer.enforceAPI.assert_not_called()

    def test_an_authorized_caller_with_a_valid_body_succeeds(self, handler):
        """POSITIVE CONTROL: the 403s above come from the enforcer, not from a broken happy path."""
        response = handler.lambda_handler(_event(VALID_BODY), None)

        assert response['statusCode'] == 200, response['body']
        handler.enforcer.enforceAPI.assert_called_once()
        handler.enforcer.enforce.assert_called_once()
        asset, action = handler.enforcer.enforce.call_args.args
        assert asset['object__type'] == 'asset'
        assert asset['databaseId'] == VALID_BODY['databaseId']
        assert action == 'PUT'

    def test_tier_two_denial_still_answers_403_after_a_valid_body(self, handler):
        handler.enforcer.enforce.return_value = False

        response = handler.lambda_handler(_event(VALID_BODY), None)

        assert response['statusCode'] == 403
        handler.create_asset.assert_not_called()
