# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Paging of the API-key table reads (S2-BACKEND-090).

A DynamoDB query or scan returns at most 1 MB and reports more work only through
``LastEvaluatedKey``, so a single call is a partial answer that looks exactly like a complete
one (backend/CLAUDE.md Rule 14). Three reads in this handler depend on completeness:

* the listing — a key on a later page is reported as not existing, so the owner sees fewer
  keys than they hold and makes revocation decisions against a short list;
* the duplicate-name check on the admin create route;
* the duplicate-name check on the user self-service create route — the same defect at a second,
  newer site, which is why both are covered here.

Every read is also asserted to terminate against an un-stubbed reader: the loops test for the
PRESENCE of ``LastEvaluatedKey`` rather than reading its value, because a MagicMock answers
``.get('LastEvaluatedKey')`` with a truthy Mock forever and that form of the loop hung a full
backend run.
"""

import base64
import json
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

os.environ.setdefault('API_KEY_STORAGE_TABLE_NAME', 'test-api-key-table')
os.environ.setdefault('USER_ROLES_STORAGE_TABLE_NAME', 'test-user-roles-table')

from backend.backend.handlers.auth import apiKeyService
from backend.backend.handlers.auth.apiKeyService import lambda_handler

USER = "self-user"
OTHER_USER = "other-user"
PAGE_1_KEY_ID = "11111111-1111-4111-8111-111111111111"
PAGE_2_KEY_ID = "22222222-2222-4222-8222-222222222222"
LAST_KEY = {'apiKeyId': PAGE_1_KEY_ID}


def _iso(dt):
    return dt.replace(microsecond=0).isoformat().replace('+00:00', 'Z')


def _item(api_key_id, user_id, name='a-key'):
    now = _iso(datetime.now(timezone.utc))
    return {
        'apiKeyId': api_key_id,
        'apiKeyName': name,
        'apiKeyHash': 'hash',
        'description': 'd',
        'userId': user_id,
        'createdBy': user_id,
        'createdAt': now,
        'updatedAt': now,
        'expiresAt': '',
        'isActive': 'true',
    }


def _event(method, path, body=None, query=None):
    event = {
        'requestContext': {
            'http': {'method': method, 'path': path},
            'authorizer': {'jwt': {'claims': {
                'vams:tokens': json.dumps([USER]),
                'vams:roles': json.dumps(['someRole']),
            }}},
        },
        'headers': {'authorization': 'Bearer test-token'},
        # The REST proxy event sends explicit null when there are no query parameters
        # (backend/CLAUDE.md Rule 16), which is the shape the handler has to survive.
        'queryStringParameters': query,
    }
    if body is not None:
        event['body'] = json.dumps(body)
    return event


@pytest.fixture
def mock_env():
    """Claims, Casbin, audit logging and the two DynamoDB tables.

    Both readers are seeded with real single-page dicts so a test that does not set up paging
    still terminates; each paging test replaces the relevant reader's ``side_effect``.
    """
    claims = {"tokens": [USER], "roles": ["someRole"], "mfaEnabled": False}
    table = MagicMock()
    table.query.return_value = {'Items': []}
    table.scan.return_value = {'Items': []}
    roles_table = MagicMock()
    roles_table.query.return_value = {'Items': [{'userId': USER, 'roleName': 'someRole'}]}
    enforcer = MagicMock()
    enforcer.enforceAPI.return_value = True

    with patch.object(apiKeyService, 'request_to_claims', return_value=claims), \
         patch.object(apiKeyService, 'CasbinEnforcer', return_value=enforcer), \
         patch.object(apiKeyService, 'log_auth_changes'), \
         patch.object(apiKeyService, 'api_key_table', table), \
         patch.object(apiKeyService, 'user_roles_table', roles_table):
        yield {'table': table, 'roles_table': roles_table}


def _payload_of(response):
    body = json.loads(response['body'])
    return body['message'] if 'message' in body else body


def _items_of(response):
    return _payload_of(response)['Items']


def _token(exclusive_start_key):
    """The NextToken form of a DynamoDB cursor (base64 of its JSON)."""
    return base64.b64encode(json.dumps(exclusive_start_key).encode('utf-8')).decode('utf-8')


def _key_condition(condition):
    """(attribute name, operator, value) for a boto3 KeyConditionExpression."""
    expression = condition.get_expression()
    return expression['values'][0].name, expression['operator'], expression['values'][1]


def _cursor(exclusive_start_key):
    return json.dumps(exclusive_start_key, sort_keys=True)


class _Pager:
    """A paged reader that serves pages by cursor rather than by call order.

    Keyed on ``ExclusiveStartKey``, so the assertion is "the cursor is threaded" rather than
    "exactly N reads happened" — an extra or repeated read still resolves to the right page.
    ``MAX_READS`` turns a loop that never advances into a test failure instead of a hang.
    """

    MAX_READS = 10

    def __init__(self, *pages):
        self.pages = {_cursor(None): pages[0]}
        for previous, page in zip(pages, pages[1:]):
            self.pages[_cursor(previous['LastEvaluatedKey'])] = page
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        if len(self.calls) > self.MAX_READS:
            raise AssertionError(
                "the paged read did not advance: the previous page's LastEvaluatedKey is not "
                "being passed back as ExclusiveStartKey")
        return self.pages[_cursor(kwargs.get('ExclusiveStartKey'))]


CALLER_CURSOR = {'apiKeyId': PAGE_1_KEY_ID}
INTERNAL_CURSOR = {'apiKeyId': PAGE_2_KEY_ID}


def _reader_failing_at(fail_cursor):
    """A paged reader that raises ValidationException on the read at ``fail_cursor``.

    Keyed on ``ExclusiveStartKey`` like ``_Pager``, so which READ fails is chosen by cursor
    rather than by call order: an extra or repeated read still resolves to the same page and
    the same verdict. The first page is served under the caller's cursor and hands back
    ``INTERNAL_CURSOR``, which is a cursor this handler produced rather than one the caller
    supplied.
    """
    pages = {
        _cursor(CALLER_CURSOR): {'Items': [_item(PAGE_1_KEY_ID, USER)],
                                 'LastEvaluatedKey': INTERNAL_CURSOR},
        _cursor(INTERNAL_CURSOR): {'Items': [_item(PAGE_2_KEY_ID, OTHER_USER)]},
    }
    calls = []

    def reader(**kwargs):
        calls.append(kwargs)
        if len(calls) > _Pager.MAX_READS:
            raise AssertionError("the paged read did not advance")
        if _cursor(kwargs.get('ExclusiveStartKey')) == _cursor(fail_cursor):
            raise ClientError(
                {'Error': {'Code': 'ValidationException',
                           'Message': 'The provided starting key is invalid'}}, 'Scan')
        return pages[_cursor(kwargs.get('ExclusiveStartKey'))]

    reader.calls = calls
    return reader


@pytest.mark.unit
class TestListingPagesToExhaustion:
    def test_user_scope_listing_returns_a_key_from_the_second_page(self, mock_env):
        pager = _Pager(
            {'Items': [_item(PAGE_1_KEY_ID, USER)], 'LastEvaluatedKey': LAST_KEY},
            {'Items': [_item(PAGE_2_KEY_ID, USER)]},
        )
        mock_env['table'].query.side_effect = pager

        response = lambda_handler(_event('GET', '/auth/user/api-keys'), {})

        assert response['statusCode'] == 200
        assert sorted(item['apiKeyId'] for item in _items_of(response)) == \
            sorted([PAGE_1_KEY_ID, PAGE_2_KEY_ID])

    def test_user_scope_listing_resumes_from_the_previous_pages_key(self, mock_env):
        pager = _Pager(
            {'Items': [], 'LastEvaluatedKey': LAST_KEY},
            {'Items': [_item(PAGE_2_KEY_ID, USER)]},
        )
        mock_env['table'].query.side_effect = pager

        response = lambda_handler(_event('GET', '/auth/user/api-keys'), {})

        assert [item['apiKeyId'] for item in _items_of(response)] == [PAGE_2_KEY_ID]
        assert LAST_KEY in [call.get('ExclusiveStartKey') for call in pager.calls]

    def test_admin_listing_returns_a_key_from_the_second_page(self, mock_env):
        mock_env['table'].scan.side_effect = _Pager(
            {'Items': [_item(PAGE_1_KEY_ID, USER)], 'LastEvaluatedKey': LAST_KEY},
            {'Items': [_item(PAGE_2_KEY_ID, OTHER_USER)]},
        )

        response = lambda_handler(_event('GET', '/auth/api-keys'), {})

        assert response['statusCode'] == 200
        assert sorted(item['apiKeyId'] for item in _items_of(response)) == \
            sorted([PAGE_1_KEY_ID, PAGE_2_KEY_ID])

    def test_hash_is_stripped_from_every_page(self, mock_env):
        mock_env['table'].scan.side_effect = _Pager(
            {'Items': [_item(PAGE_1_KEY_ID, USER)], 'LastEvaluatedKey': LAST_KEY},
            {'Items': [_item(PAGE_2_KEY_ID, OTHER_USER)]},
        )

        response = lambda_handler(_event('GET', '/auth/api-keys'), {})

        assert len(_items_of(response)) == 2
        assert all('apiKeyHash' not in item for item in _items_of(response))

    @pytest.mark.parametrize("path", ['/auth/user/api-keys', '/auth/api-keys'])
    def test_listing_terminates_against_an_unstubbed_reader(self, mock_env, path):
        """Regression guard for the loop form, not for the handler's output.

        A bare Mock is what an under-specified fixture hands the reader. The loop must end
        (with whatever it read) rather than spin, which is what the ``.get()`` form did.
        """
        mock_env['table'].query.return_value = MagicMock()
        mock_env['table'].scan.return_value = MagicMock()

        response = lambda_handler(_event('GET', path), {})

        assert response['statusCode'] == 200

    def test_an_exhausted_listing_reports_no_continuation(self, mock_env):
        """Control for the truncation assertions below: a complete listing carries no token."""
        mock_env['table'].scan.side_effect = _Pager(
            {'Items': [_item(PAGE_1_KEY_ID, USER)], 'LastEvaluatedKey': LAST_KEY},
            {'Items': [_item(PAGE_2_KEY_ID, OTHER_USER)]},
        )

        payload = _payload_of(lambda_handler(_event('GET', '/auth/api-keys'), {}))

        assert len(payload['Items']) == 2
        assert 'NextToken' not in payload
        assert 'truncated' not in payload


@pytest.mark.unit
class TestListingResponseIsBounded:
    """The listing must not accumulate the whole table into one response (Rule 15).

    Paging every read to exhaustion fixed the truncation defect by trading it for an unbounded
    in-memory set: an API key record is a few hundred bytes, so a deployment with roughly twelve
    thousand keys reaches the 6 MB Lambda response limit and the listing fails outright — for
    everyone, with no way to ask for less. The response therefore stops at a record ceiling and
    hands back a cursor.
    """

    def _three_single_item_pages(self):
        return _Pager(
            {'Items': [_item(PAGE_1_KEY_ID, USER)], 'LastEvaluatedKey': LAST_KEY},
            {'Items': [_item(PAGE_2_KEY_ID, OTHER_USER)],
             'LastEvaluatedKey': {'apiKeyId': PAGE_2_KEY_ID}},
            {'Items': [_item("33333333-3333-4333-8333-333333333333", USER)]},
        )

    def test_admin_listing_stops_at_the_requested_ceiling_and_reports_a_token(self, mock_env):
        pager = self._three_single_item_pages()
        mock_env['table'].scan.side_effect = pager

        payload = _payload_of(
            lambda_handler(_event('GET', '/auth/api-keys', query={'maxItems': '1'}), {}))

        assert [item['apiKeyId'] for item in payload['Items']] == [PAGE_1_KEY_ID]
        assert payload['truncated'] is True
        # The token is the cursor the next request resumes from, not an opaque flag.
        assert json.loads(base64.b64decode(payload['NextToken']).decode('utf-8')) == LAST_KEY
        # No read asked DynamoDB for more records than the response could carry. Asserted as a
        # property of every read rather than as a call count, so an extra read is not a failure.
        # A bound stated over every read says nothing when no read happened.
        assert pager.calls, "no read reached the table, so the bound below asserts nothing"
        assert all(call['Limit'] <= 1 for call in pager.calls), pager.calls

    def test_a_next_token_resumes_where_the_previous_response_stopped(self, mock_env):
        pager = _Pager({'Items': [_item(PAGE_2_KEY_ID, OTHER_USER)]})
        # The pager serves its only page under the token's cursor and nothing under the null
        # one, so a read that did not thread the token through is not served at all.
        pager.pages = {_cursor(LAST_KEY): {'Items': [_item(PAGE_2_KEY_ID, OTHER_USER)]}}
        mock_env['table'].scan.side_effect = pager

        response = lambda_handler(
            _event('GET', '/auth/api-keys', query={'startingToken': _token(LAST_KEY)}), {})

        assert response['statusCode'] == 200, "the startingToken was not passed to the read"
        assert [item['apiKeyId'] for item in _items_of(response)] == [PAGE_2_KEY_ID]
        assert LAST_KEY in [call.get('ExclusiveStartKey') for call in pager.calls]

    def test_a_request_for_more_than_the_ceiling_is_clamped(self, mock_env):
        """maxItems is a ceiling, not just a default: a caller cannot ask past the bound."""
        pager = self._three_single_item_pages()
        mock_env['table'].scan.side_effect = pager

        response = lambda_handler(
            _event('GET', '/auth/api-keys', query={'maxItems': '99999999'}), {})

        assert response['statusCode'] == 200
        # A bound stated over every read says nothing when no read happened.
        assert pager.calls, "no read reached the table, so the bound below asserts nothing"
        assert all(call['Limit'] <= apiKeyService.API_KEY_LISTING_MAX_ITEMS
                   for call in pager.calls), pager.calls

    @pytest.mark.parametrize("bad", ['0', '-5', 'abc', ''])
    def test_an_unusable_page_hint_falls_back_to_the_default_bound(self, mock_env, bad):
        """A malformed page hint is not worth failing a read over, but must not disable the bound."""
        pager = self._three_single_item_pages()
        mock_env['table'].scan.side_effect = pager

        response = lambda_handler(
            _event('GET', '/auth/api-keys', query={'maxItems': bad, 'pageSize': bad}), {})

        assert response['statusCode'] == 200
        # A bound stated over every read says nothing when no read happened.
        assert pager.calls, "no read reached the table, so the bound below asserts nothing"
        assert all(1 <= call['Limit'] <= apiKeyService.API_KEY_LISTING_MAX_ITEMS
                   for call in pager.calls), pager.calls

    def test_an_unreadable_starting_token_is_rejected_without_echoing_it(self, mock_env):
        response = lambda_handler(
            _event('GET', '/auth/api-keys', query={'startingToken': 'not-a-token!!'}), {})

        assert response['statusCode'] == 400
        message = json.loads(response['body'])['message']
        assert 'not-a-token' not in message           # Rule 11: no request input echoed back
        mock_env['table'].scan.assert_not_called()

    def test_the_token_this_listing_emits_is_one_it_accepts_back(self, mock_env):
        """Round trip, on the token the handler itself produced rather than a hand-built one.

        The emitted ``NextToken`` is the only cursor a real client ever sends, so any validation
        of an incoming token is only correct if this holds.
        """
        first = self._three_single_item_pages()
        mock_env['table'].scan.side_effect = first
        first_payload = _payload_of(
            lambda_handler(_event('GET', '/auth/api-keys', query={'maxItems': '1'}), {}))
        emitted = first_payload['NextToken']

        resumed = _Pager({'Items': [_item(PAGE_2_KEY_ID, OTHER_USER)]})
        resumed.pages = {_cursor(LAST_KEY): {'Items': [_item(PAGE_2_KEY_ID, OTHER_USER)]}}
        mock_env['table'].scan.side_effect = resumed

        response = lambda_handler(
            _event('GET', '/auth/api-keys', query={'startingToken': emitted}), {})

        assert response['statusCode'] == 200, (
            f"the listing rejected the very token it emitted: {response}")
        assert [item['apiKeyId'] for item in _items_of(response)] == [PAGE_2_KEY_ID]

    def test_the_user_listing_is_bounded_too(self, mock_env):
        pager = self._three_single_item_pages()
        mock_env['table'].query.side_effect = pager

        payload = _payload_of(
            lambda_handler(_event('GET', '/auth/user/api-keys', query={'maxItems': '1'}), {}))

        assert [item['apiKeyId'] for item in payload['Items']] == [PAGE_1_KEY_ID]
        assert payload['truncated'] is True


#: A cursor from the userId index, which carries the index's partition key as well as the
#: table's — the shape a user-scoped continuation really travels as.
GSI_CURSOR = {'userId': USER, 'apiKeyId': PAGE_1_KEY_ID}


@pytest.mark.unit
class TestAUserScopeContinuationStaysOnTheOwnersPartition:
    """A resumed user listing is scoped by the key condition, never by the cursor.

    The user-scoped listing reads the userId index and stops at a record ceiling, so its
    continuation is the one read here whose starting point arrives from the caller. The two
    properties the first page establishes — the read is a query on that index, and ownership
    is the key condition — have to hold on the second page too, and the first-page assertions
    say nothing about either: they never see an ``ExclusiveStartKey``. A continuation that
    dropped the ``IndexName`` would send a GSI cursor to a base-table read, which DynamoDB
    rejects as a bad starting key and the handler reports as the caller's bad token; one that
    took ownership from the cursor instead of from the claims would read another user's
    partition.
    """

    def _two_pages_on_the_owners_index(self):
        return _Pager(
            {'Items': [_item(PAGE_1_KEY_ID, USER)], 'LastEvaluatedKey': GSI_CURSOR},
            {'Items': [_item(PAGE_2_KEY_ID, USER)]},
        )

    def test_the_token_a_user_listing_emits_resumes_on_the_userid_index(self, mock_env):
        """Round trip on the GSI cursor form, through the handler that produced it."""
        mock_env['table'].query.side_effect = self._two_pages_on_the_owners_index()
        first = _payload_of(
            lambda_handler(_event('GET', '/auth/user/api-keys', query={'maxItems': '1'}), {}))
        assert [item['apiKeyId'] for item in first['Items']] == [PAGE_1_KEY_ID]

        resumed = _Pager({'Items': [_item(PAGE_2_KEY_ID, USER)]})
        # Served only under the emitted cursor, so a read that did not thread it is not
        # served at all rather than quietly starting over at page one.
        resumed.pages = {_cursor(GSI_CURSOR): {'Items': [_item(PAGE_2_KEY_ID, USER)]}}
        mock_env['table'].query.side_effect = resumed

        response = lambda_handler(
            _event('GET', '/auth/user/api-keys',
                   query={'startingToken': first['NextToken']}), {})

        assert response['statusCode'] == 200, (
            f"the user listing did not accept the cursor it emitted: {response}")
        assert [item['apiKeyId'] for item in _items_of(response)] == [PAGE_2_KEY_ID]
        # A bound stated over every read says nothing when no read happened.
        assert resumed.calls, "no read reached the table, so the bound below asserts nothing"
        for call in resumed.calls:
            # 'userIdIndex' as a literal, matching the GSI declared on apiKeyStorageTable.
            assert call['IndexName'] == 'userIdIndex', call
            assert _key_condition(call['KeyConditionExpression']) == ('userId', '=', USER)
        mock_env['table'].scan.assert_not_called()

    def test_a_cursor_naming_another_user_does_not_move_the_listing_off_the_owner(self, mock_env):
        """Ownership comes from the claims on every page, including a resumed one.

        The cursor names a partition, so a caller can put another user's id in one. The read is
        still asked for the caller's own keys, and the row the stub hands back anyway does not
        reach the response.
        """
        foreign_cursor = {'userId': OTHER_USER, 'apiKeyId': PAGE_1_KEY_ID}
        pager = _Pager({'Items': [_item(PAGE_2_KEY_ID, OTHER_USER)]})
        pager.pages = {_cursor(foreign_cursor): {'Items': [_item(PAGE_2_KEY_ID, OTHER_USER)]}}
        mock_env['table'].query.side_effect = pager

        response = lambda_handler(
            _event('GET', '/auth/user/api-keys',
                   query={'startingToken': _token(foreign_cursor)}), {})

        assert response['statusCode'] == 200, response
        assert pager.calls, "the resumed read never happened"
        for call in pager.calls:
            assert _key_condition(call['KeyConditionExpression']) == ('userId', '=', USER), call
        assert _items_of(response) == []

    def test_an_admin_continuation_acquires_no_index(self, mock_env):
        """Control for the two above: the index is a property of the user scope alone.

        The admin listing resumes on a base-table scan, so the assertions above are describing
        the user-scoped read rather than every continuation this handler makes.
        """
        pager = _Pager({'Items': [_item(PAGE_2_KEY_ID, OTHER_USER)]})
        pager.pages = {_cursor(LAST_KEY): {'Items': [_item(PAGE_2_KEY_ID, OTHER_USER)]}}
        mock_env['table'].scan.side_effect = pager

        response = lambda_handler(
            _event('GET', '/auth/api-keys', query={'startingToken': _token(LAST_KEY)}), {})

        assert response['statusCode'] == 200, response
        # The page came back, so a read really happened and the two assertions below are
        # statements about it. Without this, "no read carried an IndexName" and "query was
        # never called" are both satisfied by a listing that read nothing at all.
        assert [item['apiKeyId'] for item in _items_of(response)] == [PAGE_2_KEY_ID]
        # A bound stated over every read says nothing when no read happened.
        assert pager.calls, "no read reached the table, so the bound below asserts nothing"
        assert all('IndexName' not in call for call in pager.calls), pager.calls
        mock_env['table'].query.assert_not_called()


@pytest.mark.unit
class TestAnUnusableStartingTokenIsRejectedNotAn500:
    """Every way a ``startingToken`` can be wrong is the caller's mistake, so every one is a 400.

    Decodability is not the same question as being a cursor. A token that base64/JSON decodes to
    a list, to ``null``, to a bare string, or to an object whose attribute values are themselves
    objects, decodes fine and is still not something DynamoDB can resume from -- it reaches the
    read and comes back as internal_error 500, while the undecodable form of the same mistake is
    correctly a 400. A caller cannot tell their own malformed parameter from a server fault, and
    a 500 is the wrong thing to page an operator about.

    Every rejection is paired with the accept cases below, because "reject everything" satisfies
    the whole first half on its own and would make the handler's own ``NextToken`` unusable.
    """

    @pytest.mark.parametrize("decoded,label", [
        ([], "a JSON list"),
        ([{'apiKeyId': PAGE_1_KEY_ID}], "a list of cursors"),
        (None, "JSON null"),
        ('apiKeyId', "a bare string"),
        (17, "a bare number"),
        ({}, "an empty object"),
        ({'apiKeyId': {'S': PAGE_1_KEY_ID}}, "the low-level typed attribute shape"),
        ({'apiKeyId': [PAGE_1_KEY_ID]}, "a list-valued attribute"),
        ({'apiKeyId': None}, "a null-valued attribute"),
        ({'apiKeyId': True}, "a boolean-valued attribute"),
        ({'a': '1', 'b': '2', 'c': '3', 'd': '4', 'e': '5'}, "more attributes than a key holds"),
    ])
    def test_a_decodable_non_cursor_is_a_400_and_reads_nothing(self, mock_env, decoded, label):
        response = lambda_handler(
            _event('GET', '/auth/api-keys', query={'startingToken': _token(decoded)}), {})

        assert response['statusCode'] == 400, f"{label}: {response}"
        message = json.loads(response['body'])['message']
        # Rule 11: the response says the token is unusable and nothing about its content.
        assert 'apiKeyId' not in message and str(decoded) not in message, message
        mock_env['table'].scan.assert_not_called()

    @pytest.mark.parametrize("decoded", [
        {'apiKeyId': PAGE_1_KEY_ID},
        {'userId': USER, 'apiKeyId': PAGE_1_KEY_ID},
        {'apiKeyId': PAGE_1_KEY_ID, 'createdAtEpoch': 1767225600},
    ])
    def test_a_well_formed_cursor_is_accepted(self, decoded):
        """Over-tightening control: the cursor shapes that really occur must survive the check.

        A GSI cursor carries the index key AND the base table's key, and a numeric key attribute
        arrives as an N -- rejecting either would make a legitimate continuation unusable.
        """
        assert apiKeyService._decoded_starting_token(_token(decoded)) == decoded

    def test_a_cursor_this_listing_cannot_resume_from_is_also_a_400(self, mock_env):
        """The shape check cannot see wrong ATTRIBUTE NAMES, so DynamoDB's own verdict is used.

        ``{"nosuchattr": "x"}`` is a well-formed cursor for some other table. It is still the
        caller's parameter, so the ValidationException it provokes on the first read is reported
        as a 400 rather than as an internal error.
        """
        mock_env['table'].scan.side_effect = ClientError(
            {'Error': {'Code': 'ValidationException',
                       'Message': 'The provided starting key is invalid'}}, 'Scan')

        response = lambda_handler(
            _event('GET', '/auth/api-keys',
                   query={'startingToken': _token({'nosuchattr': 'x'})}), {})

        assert response['statusCode'] == 400, response
        assert 'nosuchattr' not in json.loads(response['body'])['message']

    def test_the_same_error_without_a_starting_token_is_still_an_internal_error(self, mock_env):
        """Paired control: the 400 above is scoped to the caller's cursor, not to the error code.

        A ValidationException on a read the caller did not steer is a fault in this handler or in
        the table, and reporting it as a bad request would hide it.
        """
        mock_env['table'].scan.side_effect = ClientError(
            {'Error': {'Code': 'ValidationException', 'Message': 'something else entirely'}},
            'Scan')

        response = lambda_handler(_event('GET', '/auth/api-keys'), {})

        assert response['statusCode'] == 500, response

    def test_the_first_read_from_the_callers_cursor_reports_a_400(self, mock_env):
        """First half of the pair below: the read the caller steered is the caller's mistake.

        Same startingToken, same error, same reader helper as the 500 test that follows -- the
        only difference between them is WHICH read fails, which is exactly the distinction the
        handler has to draw.
        """
        mock_env['table'].scan.side_effect = _reader_failing_at(CALLER_CURSOR)

        response = lambda_handler(
            _event('GET', '/auth/api-keys',
                   query={'startingToken': _token(CALLER_CURSOR)}), {})

        assert response['statusCode'] == 400, response

    def test_a_later_read_the_handler_steered_is_still_an_internal_error(self, mock_env):
        """The 400 must stop applying once the caller's own cursor has been read from.

        After the first read, every ExclusiveStartKey is a LastEvaluatedKey this handler
        produced, so a ValidationException there is an internal paging fault -- a wrong index,
        a key schema change, a cursor mangled in flight. Reporting it as "Invalid pagination
        token" would blame the caller for a server-side break and hide it from the 5xx signal
        an operator watches, on a request whose token was demonstrably usable: the first read
        with it succeeded.
        """
        reader = _reader_failing_at(INTERNAL_CURSOR)
        mock_env['table'].scan.side_effect = reader

        response = lambda_handler(
            _event('GET', '/auth/api-keys',
                   query={'startingToken': _token(CALLER_CURSOR)}), {})

        assert response['statusCode'] == 500, response
        # Control: the caller's cursor really was read from first, so the failing read is a
        # later one and not the same read the test above covers.
        assert CALLER_CURSOR in [call.get('ExclusiveStartKey') for call in reader.calls], \
            reader.calls


@pytest.mark.unit
class TestDuplicateNameCheckPagesToExhaustion:
    ADMIN_BODY = {'apiKeyName': 'taken-name', 'userId': OTHER_USER, 'description': 'd'}

    def _user_body(self):
        return {
            'apiKeyName': 'taken-name',
            'description': 'd',
            'expiresAt': _iso(datetime.now(timezone.utc) + timedelta(days=30)),
        }

    def _match_on_page_two(self, table, owner):
        """A key named 'taken-name' owned by ``owner``, reachable only on the second page.

        Staged on BOTH readers, so which one the check uses decides the verdict rather than
        the fixture: the admin check scans the table, the user check queries the caller's own
        partition on the userId index.
        """
        def pager():
            return _Pager(
                {'Items': [], 'LastEvaluatedKey': LAST_KEY},
                {'Items': [_item(PAGE_2_KEY_ID, owner, name='taken-name')]},
            )

        table.scan.side_effect = pager()
        table.query.side_effect = pager()

    def test_admin_create_rejects_a_name_that_exists_on_a_later_page(self, mock_env):
        self._match_on_page_two(mock_env['table'], OTHER_USER)

        response = lambda_handler(_event('POST', '/auth/api-keys', body=self.ADMIN_BODY), {})

        assert response['statusCode'] == 400
        assert 'already exists' in json.loads(response['body'])['message']
        mock_env['table'].put_item.assert_not_called()

    def test_user_create_rejects_a_name_the_caller_already_holds_on_a_later_page(self, mock_env):
        """The paging property, on the scope the user route now checks: the caller's own keys."""
        self._match_on_page_two(mock_env['table'], USER)

        response = lambda_handler(
            _event('POST', '/auth/user/api-keys', body=self._user_body()), {})

        assert response['statusCode'] == 400
        assert 'already exists' in json.loads(response['body'])['message']
        mock_env['table'].put_item.assert_not_called()
        # The read that found it was on the caller's own partition of the userId index.
        index_reads = [call for call in mock_env['table'].query.side_effect.calls
                       if call.get('IndexName') == 'userIdIndex']
        assert index_reads, mock_env['table'].query.side_effect.calls
        for call in index_reads:
            assert _key_condition(call['KeyConditionExpression']) == ('userId', '=', USER)

    def test_user_create_accepts_a_name_another_user_holds(self, mock_env):
        """A name held by a DIFFERENT user must not block a self-service create.

        Paging the check to exhaustion made a deployment-wide uniqueness rule effective for the
        first time, and deployment-wide is the wrong scope for a route any authenticated user
        can call: the rejection answers whether another user holds a given key name (a
        membership oracle over other users' key names) and lets any caller deny a name to
        everyone else. Nothing resolves a key by name, so the name is scoped to its owner.

        The rejection above is this test's control: the check is narrowed, not removed.
        """
        self._match_on_page_two(mock_env['table'], OTHER_USER)
        mock_env['table'].query.side_effect = None
        mock_env['table'].query.return_value = {'Items': []}

        response = lambda_handler(
            _event('POST', '/auth/user/api-keys', body=self._user_body()), {})

        assert response['statusCode'] == 200
        assert mock_env['table'].put_item.call_args[1]['Item']['apiKeyName'] == 'taken-name'

    def test_user_create_duplicate_check_never_reads_the_whole_table(self, mock_env):
        """The oracle is closed at the read, not only at the verdict.

        A table-wide read would still have touched every user's keys even if the handler then
        ignored the foreign matches, so the assertion is that the scan does not happen at all.
        """
        response = lambda_handler(
            _event('POST', '/auth/user/api-keys', body=self._user_body()), {})

        assert response['statusCode'] == 200          # control: the create ran
        mock_env['table'].scan.assert_not_called()

    def test_admin_create_still_accepts_a_free_name_across_pages(self, mock_env):
        """Control for both rejections: paging the check must not reject every name."""
        mock_env['table'].scan.side_effect = _Pager(
            {'Items': [], 'LastEvaluatedKey': LAST_KEY},
            {'Items': []},
        )

        response = lambda_handler(_event('POST', '/auth/api-keys', body=self.ADMIN_BODY), {})

        assert response['statusCode'] == 200
        assert mock_env['table'].put_item.call_args[1]['Item']['apiKeyName'] == 'taken-name'

    @pytest.mark.parametrize("path", ['/auth/api-keys', '/auth/user/api-keys'])
    def test_duplicate_check_terminates_against_an_unstubbed_reader(self, mock_env, path):
        mock_env['table'].scan.return_value = MagicMock()
        mock_env['table'].query.return_value = MagicMock()
        body = self.ADMIN_BODY if path == '/auth/api-keys' else self._user_body()

        response = lambda_handler(_event('POST', path, body=body), {})

        # A Mock page reports items, so the name reads as taken; what matters is that the
        # check returned at all.
        assert response['statusCode'] in (200, 400)
