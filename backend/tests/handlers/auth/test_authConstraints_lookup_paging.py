# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A constraint lookup and the post-delete existence check read past an empty filtered scan page.

``get_constraint_details`` (``GET /auth/constraints/{constraintId}``) and the existence check that
``delete_constraint`` runs after ``_delete_denormalized_items`` both reach a constraint's rows with a
table ``scan`` + ``FilterExpression``. The table's only key is ``constraintId`` and no index is keyed
on the base ID, so a scan is the only read that finds the ``<base>#group#...``/``<base>#user#...``
rows. DynamoDB applies the filter after each page is read -- a page is at most 1 MB, or ``Limit``
items -- so a page can hold no match while a later page does (``backend/CLAUDE.md`` Rule 14):

*   a lookup that reads one page answers ``404 Constraint not found`` for a constraint whose rows sit
    on a later page, while the listing, which reads the whole table, still returns it;
*   an existence check that reads one page with ``Limit=1`` evaluates a single row, so it almost
    never sees a surviving row. ``_delete_denormalized_items`` swallows its own failures, which makes
    that check the only signal a failed delete has: without it ``DELETE`` reports success while the
    constraint's rows -- and the permissions they grant -- stay in the table.

Both reads continue on ``LastEvaluatedKey`` until a page yields a row or the key is absent, and the
existence check reads with ``ConsistentRead`` so rows a completed batch delete removed are not
reported as survivors.

The table grows faster than its constraint count because each ``#group#``/``#user#`` row repeats the
constraint's full criteria and permission JSON: a constraint granted to N groups writes N rows, each
carrying all N entries. ``oversized_table`` builds that shape with the production transform in moto,
which ends a scan page at 1 MB and scans in partition-key order, so the target constraint's rows sit
on the second page. ``TestTheFixtureSpansScanPages`` pins that premise; without it every test on the
fixture could pass against a table that fits one page. ``TestScanCursorIsThreaded`` drives the same
reads through the shared scripted pager, which asserts that the cursor itself is threaded.
"""

import json

import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws
from unittest.mock import MagicMock, patch

from backend.backend.handlers.auth import authConstraintsService as svc
from backend.backend.handlers.auth.authConstraintsService import lambda_handler
from backend.tests.pagingStub import Pager


_CLAIMS = {"tokens": ["test-user-id"], "roles": ["admin"], "mfaEnabled": False}

_TARGET_ID = "target-constraint"
_TARGET_ROWS = {"target-constraint#group#g1", "target-constraint#user#u1"}

# Three constraints each granted to 80 groups: 240 rows of about 6 KB, about 1.4 MB in all. Their IDs
# sort before the target's and moto scans in partition-key order, so the first 1 MB page is all filler.
_FILLER_CONSTRAINTS = 3
_GROUPS_PER_FILLER = 80
_FILLER_IDS = tuple(f"filler-{index}" for index in range(_FILLER_CONSTRAINTS))

_STILL_EXISTS = "items may still exist"


def _constraint(identifier, group_ids=(), user_ids=()):
    """A constraint in the API shape ``_transform_to_denormalized_format`` accepts."""
    return {
        'identifier': identifier,
        'name': identifier,
        'description': f"seeded constraint {identifier}",
        'objectType': 'asset',
        'criteriaAnd': [{'field': 'databaseId', 'operator': 'equals', 'value': identifier}],
        'criteriaOr': [],
        'groupPermissions': [
            {'groupId': group_id, 'permission': 'GET', 'permissionType': 'allow'}
            for group_id in group_ids
        ],
        'userPermissions': [
            {'userId': user_id, 'permission': 'GET', 'permissionType': 'allow'}
            for user_id in user_ids
        ],
    }


def _target_constraint():
    return _constraint(_TARGET_ID, group_ids=("g1",), user_ids=("u1",))


def _filler_constraints():
    group_ids = tuple("group-{:03d}".format(index) for index in range(_GROUPS_PER_FILLER))
    return [_constraint(filler_id, group_ids=group_ids) for filler_id in _FILLER_IDS]


def _target_row():
    """The target's ``#group#g1`` row, as the production transform writes it."""
    return svc._transform_to_denormalized_format(_target_constraint())[0]


def _all_row_ids(table):
    """Every constraintId in the table, paged to exhaustion."""
    row_ids = set()
    scan_kwargs = {}
    while True:
        response = table.scan(**scan_kwargs)
        row_ids.update(item['constraintId'] for item in response.get('Items', []))
        if 'LastEvaluatedKey' not in response:
            return row_ids
        scan_kwargs['ExclusiveStartKey'] = response['LastEvaluatedKey']


def _event(method, constraint_id):
    return {
        'requestContext': {'http': {'method': method, 'path': f"/auth/constraints/{constraint_id}"}},
        'pathParameters': {'constraintId': constraint_id},
        'queryStringParameters': {},
        'headers': {'authorization': 'Bearer test-token'},
    }


def _fail_batch_writes(monkeypatch, table):
    """Make every batch write on ``table`` raise, the failure ``_delete_denormalized_items`` swallows."""
    def failing_batch_writer(*args, **kwargs):
        raise ClientError(
            {'Error': {'Code': 'ProvisionedThroughputExceededException', 'Message': 'throttled'}},
            'BatchWriteItem',
        )

    monkeypatch.setattr(table, 'batch_writer', failing_batch_writer)


@pytest.fixture
def oversized_table(monkeypatch):
    """A moto constraints table whose first filtered scan page holds none of the target's rows."""
    assert svc.constraints_table_name, "constraints table name did not resolve"

    with mock_aws():
        resource = boto3.resource("dynamodb", region_name="us-east-1")
        constraints = resource.create_table(
            TableName=svc.constraints_table_name,
            KeySchema=[{"AttributeName": "constraintId", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "constraintId", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        with constraints.batch_writer() as batch:
            for constraint in _filler_constraints() + [_target_constraint()]:
                for item in svc._transform_to_denormalized_format(constraint):
                    batch.put_item(Item=item)

        monkeypatch.setattr(svc, "constraints_table", constraints)
        yield constraints


@pytest.fixture
def api_allowed():
    """Tier-1 authorization granted; this module is about reading the table, not authorization."""
    enforcer = MagicMock()
    enforcer.enforceAPI.return_value = True
    with patch.object(svc, 'request_to_claims', return_value=dict(_CLAIMS)), \
            patch.object(svc, 'CasbinEnforcer', return_value=enforcer):
        yield


@pytest.mark.unit
class TestTheFixtureSpansScanPages:
    """Controls on the fixture: the target's rows exist, and one filtered page does not reach them."""

    def test_the_target_rows_are_in_the_table(self, oversized_table):
        row_ids = _all_row_ids(oversized_table)
        assert _TARGET_ROWS <= row_ids
        assert len(row_ids) == _FILLER_CONSTRAINTS * _GROUPS_PER_FILLER + len(_TARGET_ROWS)

    def test_one_filtered_scan_page_holds_none_of_the_target_rows(self, oversized_table):
        response = oversized_table.scan(FilterExpression=svc._constraint_id_filter(_TARGET_ID))
        assert response['Items'] == []
        assert 'LastEvaluatedKey' in response

    def test_a_limit_of_one_evaluates_a_single_row(self, oversized_table):
        response = oversized_table.scan(
            FilterExpression=svc._constraint_id_filter(_TARGET_ID), Limit=1
        )
        assert response['Items'] == []
        assert response['ScannedCount'] == 1


@pytest.mark.unit
class TestLookupReadsPastTheFirstScanPage:

    def test_a_constraint_stored_beyond_the_first_page_resolves(self, oversized_table):
        constraint = svc.get_constraint_details(_TARGET_ID)
        assert constraint is not None
        assert constraint['constraintId'] == _TARGET_ID
        assert constraint['criteriaAnd'][0]['value'] == _TARGET_ID
        assert constraint['groupPermissions'][0]['groupId'] == 'g1'
        assert constraint['userPermissions'][0]['userId'] == 'u1'

    def test_the_get_handler_returns_200_for_it(self, oversized_table, api_allowed):
        response = lambda_handler(_event('GET', _TARGET_ID), {})
        assert response['statusCode'] == 200
        assert json.loads(response['body'])['constraint']['constraintId'] == _TARGET_ID

    def test_every_constraint_the_listing_returns_resolves_by_id(self, oversized_table):
        listed = [item['constraintId'] for item in svc.get_all_constraints({'pageSize': 100})['Items']]
        assert sorted(listed) == sorted(_FILLER_IDS + (_TARGET_ID,))
        for constraint_id in listed:
            assert svc.get_constraint_details(constraint_id) is not None, constraint_id

    def test_an_absent_constraint_is_still_not_found(self, oversized_table, api_allowed):
        response = lambda_handler(_event('GET', 'absent-constraint'), {})
        assert response['statusCode'] == 404


@pytest.mark.unit
class TestPostDeleteCheckReadsTheWholeTable:

    def test_rows_that_survive_a_failed_batch_delete_are_reported(self, oversized_table, monkeypatch):
        _fail_batch_writes(monkeypatch, oversized_table)

        with pytest.raises(svc.VAMSGeneralErrorResponse, match=_STILL_EXISTS):
            svc.delete_constraint(_TARGET_ID, dict(_CLAIMS))

        assert _TARGET_ROWS <= _all_row_ids(oversized_table)

    def test_the_delete_handler_answers_400_while_the_rows_remain(
            self, oversized_table, api_allowed, monkeypatch):
        _fail_batch_writes(monkeypatch, oversized_table)

        response = lambda_handler(_event('DELETE', _TARGET_ID), {})

        assert response['statusCode'] == 400
        assert _STILL_EXISTS in json.loads(response['body'])['message']

    def test_a_delete_that_removes_every_row_succeeds(self, oversized_table, api_allowed):
        """Positive control: the whole-table check does not report rows a successful delete removed."""
        response = lambda_handler(_event('DELETE', _TARGET_ID), {})

        assert response['statusCode'] == 200
        body = json.loads(response['body'])
        assert body['success'] is True
        assert body['constraintId'] == _TARGET_ID
        remaining = _all_row_ids(oversized_table)
        assert remaining.isdisjoint(_TARGET_ROWS)
        assert len(remaining) == _FILLER_CONSTRAINTS * _GROUPS_PER_FILLER


@pytest.mark.unit
class TestScanCursorIsThreaded:
    """The same reads through the shared scripted pager, which serves pages keyed on the cursor."""

    _FILLER_CURSOR = {'constraintId': 'filler-0#group#group-079'}

    def test_the_lookup_resumes_the_same_filtered_scan_after_an_empty_page(self, monkeypatch):
        pager = Pager(
            {'Items': [], 'LastEvaluatedKey': self._FILLER_CURSOR},
            {'Items': [_target_row()]},
            name="constraint lookup scan",
        )
        monkeypatch.setattr(svc, 'constraints_table', MagicMock(scan=pager))

        constraint = svc.get_constraint_details(_TARGET_ID)

        assert constraint is not None
        assert constraint['constraintId'] == _TARGET_ID
        pager.assert_paged_to_exhaustion()
        assert all(
            call['FilterExpression'] == svc._constraint_id_filter(_TARGET_ID) for call in pager.calls
        )

    def test_the_lookup_stops_at_the_first_page_that_matches(self, monkeypatch):
        """A page that yields a row ends the lookup, even with a LastEvaluatedKey outstanding."""
        pager = Pager(
            {'Items': [_target_row()], 'LastEvaluatedKey': {'constraintId': 'target-constraint#group#g1'}},
            name="constraint lookup scan",
        )
        monkeypatch.setattr(svc, 'constraints_table', MagicMock(scan=pager))

        assert svc.get_constraint_details(_TARGET_ID)['constraintId'] == _TARGET_ID
        assert pager.resumed_from == []

    def test_an_absent_constraint_reads_every_page_before_answering_none(self, monkeypatch):
        pager = Pager(
            {'Items': [], 'LastEvaluatedKey': self._FILLER_CURSOR},
            {'Items': [], 'LastEvaluatedKey': {'constraintId': 'filler-1#group#group-079'}},
            {'Items': []},
            name="constraint lookup scan",
        )
        monkeypatch.setattr(svc, 'constraints_table', MagicMock(scan=pager))

        assert svc.get_constraint_details(_TARGET_ID) is None
        pager.assert_paged_to_exhaustion()

    def test_the_post_delete_check_finds_a_row_on_a_later_page(self, monkeypatch):
        pager = Pager(
            {'Items': [], 'LastEvaluatedKey': self._FILLER_CURSOR},
            {'Items': [_target_row()]},
            name="post-delete existence scan",
        )
        monkeypatch.setattr(svc, 'constraints_table', MagicMock(scan=pager))
        # Stands in for a delete that removed nothing and swallowed its failure
        monkeypatch.setattr(svc, '_delete_denormalized_items', lambda base_constraint_id: None)

        with pytest.raises(svc.VAMSGeneralErrorResponse, match=_STILL_EXISTS):
            svc.delete_constraint(_TARGET_ID, dict(_CLAIMS))

        pager.assert_paged_to_exhaustion()
        assert all(call.get('ConsistentRead') is True for call in pager.calls)
        assert all(
            call['FilterExpression'] == svc._constraint_id_filter(_TARGET_ID) for call in pager.calls
        )

    def test_a_clean_post_delete_check_reads_every_page_consistently(self, monkeypatch):
        pager = Pager(
            {'Items': [], 'LastEvaluatedKey': self._FILLER_CURSOR},
            {'Items': []},
            name="post-delete existence scan",
        )
        monkeypatch.setattr(svc, 'constraints_table', MagicMock(scan=pager))
        monkeypatch.setattr(svc, '_delete_denormalized_items', lambda base_constraint_id: None)

        result = svc.delete_constraint(_TARGET_ID, dict(_CLAIMS))

        assert result.success is True
        pager.assert_paged_to_exhaustion()
        assert all(call.get('ConsistentRead') is True for call in pager.calls)
