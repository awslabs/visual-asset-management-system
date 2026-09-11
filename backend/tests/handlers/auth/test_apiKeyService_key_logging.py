# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""FIX-005 -- creating an API key must not write the plaintext key to CloudWatch.

Both create paths mint ``raw_key = "vams_" + secrets.token_urlsafe(48)``, put its SHA-256 in DynamoDB,
then hand the plaintext back through ``success()`` -- which logs the whole response body. The key is a
long-lived bearer credential for the VAMS API, so a log reader gains the creating user's identity.

The leak exists on BOTH paths and fixing one leaves the other leaking:
  * ``create_api_key``      (admin,        POST /auth/api-keys)
  * ``create_user_api_key`` (self-service, POST /auth/user/api-keys)

Two properties are asserted together on each path, because either alone is satisfiable by a wrong fix:
  * the plaintext key is STILL RETURNED to the caller -- only its hash is stored, so a fix that drops
    the field from the response makes the key unrecoverable and breaks `vamscli api-key create`;
  * the plaintext key appears in NO recorded log call -- on the handler's own logger, on the response
    helper's, or in any record handed to the audit sink.

The suite's ``safeLogger`` mock is a no-op whose ``info()`` does ``pass``, so a caplog-based assertion
here would pass before any fix. Each test therefore spies on the logger objects the modules actually
hold and first proves, through a control line, that the same scan sees a secret when one is logged.
"""

import json
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest
from unittest.mock import MagicMock, patch

os.environ.setdefault('API_KEY_STORAGE_TABLE_NAME', 'test-api-key-table')
os.environ.setdefault('USER_ROLES_STORAGE_TABLE_NAME', 'test-user-roles-table')

from backend.backend.handlers.auth import apiKeyService  # noqa: E402
from backend.backend.handlers.auth.apiKeyService import lambda_handler  # noqa: E402

USER = "key-owner"
_SENTINEL = "vams_CONTROLSENTINEL"

# A user-scope key must expire in the future and within USER_API_KEY_MAX_EXPIRATION_DAYS of creation, so
# the expiration is derived from the run date; a literal date would silently turn these tests red once it
# passes. The `expiresAt` field caps at 30 characters, which this 20-character form stays under.
_USER_KEY_EXPIRES_AT = (datetime.now(timezone.utc) + timedelta(days=30)).strftime('%Y-%m-%dT%H:%M:%SZ')

# `success` is bound from the module the handler imported it from. The repo puts both `backend/` and
# `backend/backend/` on sys.path, so `models.common` and `backend.backend.models.common` are two
# distinct module objects; resolve the one this handler's `success` actually closes over so the spy
# lands on the logger that really runs.
_RESPONSE_MODULE = sys.modules[apiKeyService.success.__module__]


def _make_event(path, body):
    return {
        'requestContext': {
            'http': {'method': 'POST', 'path': path},
            'authorizer': {'jwt': {'claims': {
                'vams:tokens': json.dumps([USER]),
                'vams:roles': json.dumps(['someRole']),
            }}},
        },
        'headers': {'authorization': 'Bearer test-token'},
        'body': json.dumps(body),
    }


def _recorded_text(*spies):
    """Every argument of every call recorded on the given MagicMock loggers, as one string."""
    return "\n".join(repr(call) for spy in spies for call in spy.mock_calls)


@pytest.fixture
def key_service():
    """Patch claims, Casbin, the DynamoDB tables, and spy on both loggers involved in a response."""
    claims = {"tokens": [USER], "roles": ["someRole"], "mfaEnabled": False}
    table = MagicMock()
    # Both readers return a real dict. The handler reads the key table through `scan` (admin
    # scope) and through the userId GSI `query` (user scope), and both reads page on the
    # PRESENCE of LastEvaluatedKey — a bare MagicMock answers `.get('Items')` with a truthy
    # mock, which would read as "this name is already taken" on whichever path is unstubbed.
    table.scan.return_value = {'Items': []}
    table.query.return_value = {'Items': []}
    roles_table = MagicMock()
    roles_table.query.return_value = {'Items': [{'userId': USER, 'roleName': 'someRole'}]}
    enforcer = MagicMock()
    enforcer.enforceAPI.return_value = True
    handler_logger = MagicMock()
    response_logger = MagicMock()
    audit = MagicMock()

    with patch.object(apiKeyService, 'request_to_claims', return_value=claims), \
            patch.object(apiKeyService, 'CasbinEnforcer', return_value=enforcer), \
            patch.object(apiKeyService, 'log_auth_changes', audit), \
            patch.object(apiKeyService, 'api_key_table', table), \
            patch.object(apiKeyService, 'user_roles_table', roles_table), \
            patch.object(apiKeyService, 'logger', handler_logger), \
            patch.object(_RESPONSE_MODULE, 'logger', response_logger):
        yield {
            'table': table,
            'audit': audit,
            'handler_logger': handler_logger,
            'response_logger': response_logger,
        }


def _detector_control(env):
    """Prove the spies record and the scan detects, on the exact objects the code under test uses."""
    env['handler_logger'].info(f"control {_SENTINEL}")
    env['response_logger'].info(f"control {_SENTINEL}")
    text = _recorded_text(env['handler_logger'], env['response_logger'])
    assert text.count(_SENTINEL) == 2, "the logger spies are not wired to the objects in use"
    env['handler_logger'].reset_mock()
    env['response_logger'].reset_mock()


def _payload(response):
    body = json.loads(response['body'])
    return body['message'] if 'message' in body else body


def _audit_text(audit):
    """Every argument of every recorded audit call, rendered as one string.

    Deliberately blind to call shape and to which record carries what. The two per-record
    checks below are filtered to ('apiKeyCreate', this key's id), so on their own they say
    nothing about a plaintext key written into some OTHER record -- a second event type, a
    companion record, a record about a different key. This scan is the unfiltered form of the
    same question.
    """
    return "\n".join(repr(call) for call in audit.mock_calls)


def _audit_custom_data(call):
    """The custom-data payload of one recorded audit call, or None for an unfamiliar shape.

    ``log_auth_changes(event, secondary_type, custom_data=None)`` -- read positionally with a
    keyword fallback, and answered with None rather than an exception when the call does not
    carry one. Indexing ``call.args[2]`` unguarded raises IndexError on a call with fewer
    positional arguments, which fails a strictly safer implementation for its call SHAPE the
    same way a count pin failed it for its call COUNT.
    """
    args, kwargs = call.args, call.kwargs
    custom_data = args[2] if len(args) > 2 else kwargs.get('custom_data')
    return custom_data if isinstance(custom_data, dict) else None


def _audit_call_subject(call):
    """(secondary_type, apiKeyId) for one recorded audit call, or None for an unfamiliar shape."""
    args, kwargs = call.args, call.kwargs
    secondary_type = args[1] if len(args) > 1 else kwargs.get('secondary_type')
    custom_data = _audit_custom_data(call)
    if secondary_type is None or custom_data is None:
        return None
    return secondary_type, custom_data.get('apiKeyId')


def _audit_subjects(audit):
    """The (secondary_type, apiKeyId) pairs the audit hook was handed, as a set.

    A set, and not a call count: an implementation that emits an ADDITIONAL record for the same
    create -- a second event type, a retry, a companion record -- is not less safe than one that
    emits exactly one, and a count pin would fail it. What matters is that a record identifying
    this create is present, which set containment states directly. Calls in a shape this helper
    does not model are skipped rather than raising (see ``_audit_call_subject``).
    """
    return {subject for subject in map(_audit_call_subject, audit.call_args_list)
            if subject is not None}


# The audit half of each property below must be as strong as the logger half, which rejects the
# `vams_` prefix and not only the whole key. A record that carried a TRUNCATED key -- an excerpt, a
# first-N-characters "hint", a value cut by a field width -- would satisfy a whole-string check
# while still handing a log reader most of the credential.
#
# Size, not identity: any contiguous run of this many characters of the raw key counts as leaked,
# wherever in the key it starts. Stated as a SET of leaked runs rather than a count or an offset, so
# a safer implementation that redacts the value, hashes it, or keeps a short masked tail (under this
# many characters) passes, while any longer excerpt fails.
_LEAKED_FRAGMENT_CHARS = 8


def _leaked_fragments(text, raw_key, size=_LEAKED_FRAGMENT_CHARS):
    """Every run of `size` consecutive characters of `raw_key` that appears verbatim in `text`."""
    return sorted({raw_key[i:i + size] for i in range(len(raw_key) - size + 1)
                   if raw_key[i:i + size] in text})


def _audit_records_for(audit, secondary_type, api_key_id):
    """Every recorded custom-data payload naming this event type and this key."""
    return [_audit_custom_data(call) for call in audit.call_args_list
            if _audit_call_subject(call) == (secondary_type, api_key_id)]


@pytest.mark.unit
class TestAdminCreateDoesNotLogThePlaintextKey:
    """POST /auth/api-keys (create_api_key)."""

    def test_key_is_returned_but_never_logged(self, key_service):
        _detector_control(key_service)
        response = lambda_handler(
            _make_event('/auth/api-keys',
                        {'apiKeyName': 'admin-key', 'userId': USER, 'description': 'd'}), {})

        assert response['statusCode'] == 200
        payload = _payload(response)
        # POSITIVE CONTROL: the plaintext key is still returned exactly once, on the response.
        assert payload['apiKey'].startswith('vams_')
        assert 'apiKeyHash' not in payload

        logged = _recorded_text(key_service['handler_logger'], key_service['response_logger'])
        assert payload['apiKey'] not in logged
        assert 'vams_' not in logged
        # The prefix guard above catches a key logged from its start; an excerpt taken from the
        # middle carries no prefix and no whole key, so the same fragment scan the audit half
        # uses applies here too.
        assert _leaked_fragments(logged, payload['apiKey']) == []

        # The audit sink is the third place a create writes to. The removed call-count pin
        # said "exactly one record was emitted" and only covered this incidentally; the
        # property is that NO record -- any event type, any key -- carries the secret, which
        # keeps holding when an implementation emits extra records.
        audit = key_service['audit']
        assert ('apiKeyCreate', payload['apiKeyId']) in _audit_subjects(audit), (
            "the audit sink recorded nothing for this create, so the scan below would be "
            "vacuous")
        secret = payload['apiKey'].split('vams_', 1)[1]
        audit_text = _audit_text(audit)
        assert payload['apiKey'] not in audit_text
        assert secret not in audit_text
        # As strong as the logger half above: the prefix, and any substantial run of the key,
        # not just the whole string. A truncated key in a record fails here.
        assert 'vams_' not in audit_text
        assert _leaked_fragments(audit_text, payload['apiKey']) == []
        # POSITIVE CONTROL: the same scans do see key material once a record carries it, and the
        # fragment scan fires on a TRUNCATED key -- the case the whole-string checks miss.
        audit(None, 'apiKeyProbe', {'apiKeyId': 'probe', 'apiKey': payload['apiKey']})
        assert payload['apiKey'] in _audit_text(audit)
        assert _leaked_fragments(_audit_text(audit), payload['apiKey']) != []
        truncated = MagicMock()
        # An excerpt from the MIDDLE of the key: no whole key, no whole secret body, and no
        # `vams_` prefix, so every check above it is satisfied and only the fragment scan is
        # left to catch it. That is what makes the fragment scan load-bearing rather than a
        # restatement of the checks above.
        truncated(None, 'apiKeyProbe', {'apiKeyId': 'probe', 'apiKey': payload['apiKey'][8:32]})
        excerpt_text = _audit_text(truncated)
        assert payload['apiKey'] not in excerpt_text
        assert secret not in excerpt_text
        assert 'vams_' not in excerpt_text
        assert _leaked_fragments(excerpt_text, payload['apiKey']) != [], (
            "the fragment scan cannot see a truncated key, so it adds nothing to the "
            "whole-string checks above")


@pytest.mark.unit
class TestUserCreateDoesNotLogThePlaintextKey:
    """POST /auth/user/api-keys (create_user_api_key) -- the second, independently leaking path."""

    def test_key_is_returned_but_never_logged(self, key_service):
        _detector_control(key_service)
        response = lambda_handler(
            _make_event('/auth/user/api-keys',
                        {'apiKeyName': 'self-key', 'description': 'd',
                         'expiresAt': _USER_KEY_EXPIRES_AT}), {})

        assert response['statusCode'] == 200
        payload = _payload(response)
        assert payload['apiKey'].startswith('vams_')
        assert 'apiKeyHash' not in payload

        logged = _recorded_text(key_service['handler_logger'], key_service['response_logger'])
        assert payload['apiKey'] not in logged
        assert 'vams_' not in logged
        # The prefix guard above catches a key logged from its start; an excerpt taken from the
        # middle carries no prefix and no whole key, so the same fragment scan the audit half
        # uses applies here too.
        assert _leaked_fragments(logged, payload['apiKey']) == []

        # The audit sink is the third place a create writes to. The removed call-count pin
        # said "exactly one record was emitted" and only covered this incidentally; the
        # property is that NO record -- any event type, any key -- carries the secret, which
        # keeps holding when an implementation emits extra records.
        audit = key_service['audit']
        assert ('apiKeyCreate', payload['apiKeyId']) in _audit_subjects(audit), (
            "the audit sink recorded nothing for this create, so the scan below would be "
            "vacuous")
        secret = payload['apiKey'].split('vams_', 1)[1]
        audit_text = _audit_text(audit)
        assert payload['apiKey'] not in audit_text
        assert secret not in audit_text
        # As strong as the logger half above: the prefix, and any substantial run of the key,
        # not just the whole string. A truncated key in a record fails here.
        assert 'vams_' not in audit_text
        assert _leaked_fragments(audit_text, payload['apiKey']) == []
        # POSITIVE CONTROL: the same scans do see key material once a record carries it, and the
        # fragment scan fires on a TRUNCATED key -- the case the whole-string checks miss.
        audit(None, 'apiKeyProbe', {'apiKeyId': 'probe', 'apiKey': payload['apiKey']})
        assert payload['apiKey'] in _audit_text(audit)
        assert _leaked_fragments(_audit_text(audit), payload['apiKey']) != []
        truncated = MagicMock()
        # An excerpt from the MIDDLE of the key: no whole key, no whole secret body, and no
        # `vams_` prefix, so every check above it is satisfied and only the fragment scan is
        # left to catch it. That is what makes the fragment scan load-bearing rather than a
        # restatement of the checks above.
        truncated(None, 'apiKeyProbe', {'apiKeyId': 'probe', 'apiKey': payload['apiKey'][8:32]})
        excerpt_text = _audit_text(truncated)
        assert payload['apiKey'] not in excerpt_text
        assert secret not in excerpt_text
        assert 'vams_' not in excerpt_text
        assert _leaked_fragments(excerpt_text, payload['apiKey']) != [], (
            "the fragment scan cannot see a truncated key, so it adds nothing to the "
            "whole-string checks above")


@pytest.mark.unit
class TestApiKeyAuditTrailStaysReadable:
    """OVER-TIGHTENING CATCHER. The audit entry must keep the key's IDENTIFIERS in the clear, or the
    API-key audit trail becomes unusable -- while never carrying the plaintext key itself."""

    def test_admin_create_audit_records_identifiers_in_the_clear(self, key_service):
        response = lambda_handler(
            _make_event('/auth/api-keys',
                        {'apiKeyName': 'admin-key', 'userId': USER, 'description': 'd'}), {})
        payload = _payload(response)

        subjects = _audit_subjects(key_service['audit'])
        assert ('apiKeyCreate', payload['apiKeyId']) in subjects, subjects

        records = _audit_records_for(key_service['audit'], 'apiKeyCreate', payload['apiKeyId'])
        assert records, "no apiKeyCreate record named this key; the loop below would be vacuous"
        for custom_data in records:
            assert custom_data['apiKeyName'] == 'admin-key'
            assert custom_data['userId'] == USER
            assert 'expiresAt' in custom_data
            assert payload['apiKey'] not in json.dumps(custom_data)

    def test_user_create_audit_records_identifiers_in_the_clear(self, key_service):
        response = lambda_handler(
            _make_event('/auth/user/api-keys',
                        {'apiKeyName': 'self-key', 'description': 'd',
                         'expiresAt': _USER_KEY_EXPIRES_AT}), {})
        payload = _payload(response)

        subjects = _audit_subjects(key_service['audit'])
        assert ('apiKeyCreate', payload['apiKeyId']) in subjects, subjects

        records = _audit_records_for(key_service['audit'], 'apiKeyCreate', payload['apiKeyId'])
        assert records, "no apiKeyCreate record named this key; the loop below would be vacuous"
        for custom_data in records:
            assert custom_data['apiKeyName'] == 'self-key'
            assert custom_data['userId'] == USER
            assert custom_data['expiresAt'] == _USER_KEY_EXPIRES_AT
            assert payload['apiKey'] not in json.dumps(custom_data)

    def test_only_the_sha256_hash_is_stored(self, key_service):
        """The stored record must never gain the plaintext key as a side effect of the fix."""
        response = lambda_handler(
            _make_event('/auth/api-keys',
                        {'apiKeyName': 'admin-key', 'userId': USER, 'description': 'd'}), {})
        payload = _payload(response)
        stored = key_service['table'].put_item.call_args[1]['Item']
        assert len(stored['apiKeyHash']) == 64
        assert payload['apiKey'] not in json.dumps(stored)
