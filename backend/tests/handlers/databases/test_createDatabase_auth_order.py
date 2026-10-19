# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tier-1 route authorization on `POST /database` runs before the request body is read.

A caller who is not allowed on the route learns nothing about the request schema: an invalid
body and a valid body both answer 403. An authorized caller with an invalid body gets one
400 whose message describes the failing field, not a second message saying the field is
missing -- that contradiction came from the root validator running over a body whose field
had already failed.
"""

import json
from unittest.mock import MagicMock

import pytest

from backend.backend.handlers.databases import createDatabase as handler

VALID_BODY = {
    'databaseId': 'my-database',
    'description': 'A database for tests',
    'defaultBucketId': '550e8400-e29b-41d4-a716-446655440000',
}

SHORT_ID_BODY = dict(VALID_BODY, databaseId='ab')


def _event(body):
    return {
        'requestContext': {'http': {'method': 'POST', 'path': '/database'}},
        'body': json.dumps(body) if body is not None else None,
    }


@pytest.fixture
def enforcer(monkeypatch):
    """A Casbin stand-in whose two tiers answer independently; records whether Tier 1 was asked."""
    instance = MagicMock()
    instance.enforceAPI.return_value = True
    instance.enforce.return_value = True
    monkeypatch.setattr(handler, 'CasbinEnforcer', MagicMock(return_value=instance))
    monkeypatch.setattr(handler, 'request_to_claims', lambda event: {'tokens': ['tester'], 'roles': []})
    monkeypatch.setattr(handler, 'create_database', MagicMock(
        return_value=handler.CreateDatabaseResponseModel(
            databaseId=VALID_BODY['databaseId'], message='Database created successfully')))
    return instance


def _message(response):
    return json.loads(response['body'])['message']


@pytest.mark.unit
class TestRouteAuthorizationPrecedesTheBody:

    def test_an_unauthorized_caller_with_an_invalid_body_gets_403(self, enforcer):
        enforcer.enforceAPI.return_value = False

        response = handler.lambda_handler(_event(SHORT_ID_BODY), None)

        assert response['statusCode'] == 403
        assert 'characters' not in _message(response)

    def test_an_unauthorized_caller_with_a_valid_body_gets_403(self, enforcer):
        enforcer.enforceAPI.return_value = False

        response = handler.lambda_handler(_event(VALID_BODY), None)

        assert response['statusCode'] == 403

    def test_an_unauthorized_caller_with_no_body_gets_403_not_400(self, enforcer):
        enforcer.enforceAPI.return_value = False

        response = handler.lambda_handler(_event(None), None)

        assert response['statusCode'] == 403

    def test_a_caller_with_no_tokens_is_denied_before_the_body(self, monkeypatch, enforcer):
        monkeypatch.setattr(handler, 'request_to_claims', lambda event: {'tokens': [], 'roles': []})

        response = handler.lambda_handler(_event(SHORT_ID_BODY), None)

        assert response['statusCode'] == 403
        enforcer.enforceAPI.assert_not_called()

    def test_an_authorized_caller_with_an_invalid_body_gets_one_clear_400(self, enforcer):
        response = handler.lambda_handler(_event(SHORT_ID_BODY), None)

        assert response['statusCode'] == 400
        message = _message(response)
        assert 'databaseId' in message
        assert 'at least 4 characters' in message
        assert 'required' not in message, message

    def test_an_authorized_caller_with_a_valid_body_succeeds(self, enforcer):
        """POSITIVE CONTROL: the 403s above come from the enforcer, not from a broken happy path."""
        response = handler.lambda_handler(_event(VALID_BODY), None)

        assert response['statusCode'] == 200
        enforcer.enforceAPI.assert_called_once()
        enforcer.enforce.assert_called_once()
        database, action = enforcer.enforce.call_args.args
        assert database == {'object__type': 'database', 'databaseId': VALID_BODY['databaseId']}
        assert action == 'POST'

    def test_tier_two_denial_still_answers_403_after_a_valid_body(self, enforcer):
        enforcer.enforce.return_value = False

        response = handler.lambda_handler(_event(VALID_BODY), None)

        assert response['statusCode'] == 403
        handler.create_database.assert_not_called()


@pytest.mark.unit
class TestTheRootValidatorSkipsAFailedField:
    """`skip_on_failure=True`: a field that failed its own rule is not re-reported as missing."""

    def test_create_model_reports_a_short_id_once(self):
        from aws_lambda_powertools.utilities.parser import parse, ValidationError
        from backend.backend.models.common import validation_error_message
        from backend.backend.models.databases import CreateDatabaseRequestModel

        with pytest.raises(ValidationError) as raised:
            parse(SHORT_ID_BODY, model=CreateDatabaseRequestModel)

        message = validation_error_message(raised.value)
        assert 'at least 4 characters' in message
        assert 'required' not in message, message

    def test_update_model_reports_a_short_description_once(self):
        from aws_lambda_powertools.utilities.parser import parse, ValidationError
        from backend.backend.models.common import validation_error_message
        from backend.backend.models.databases import UpdateDatabaseRequestModel

        with pytest.raises(ValidationError) as raised:
            parse({'description': 'ab'}, model=UpdateDatabaseRequestModel)

        message = validation_error_message(raised.value)
        assert 'at least 4 characters' in message
        assert 'At least one field' not in message, message
