# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A constraint update is refused when the constraint's existing rows could not be removed.

``create_or_update_constraint`` removes every ``<id>`` / ``<id>#group#...`` / ``<id>#user#...`` row with
``_delete_denormalized_items`` and then writes the rows for the submitted permissions. A row the
removal leaves behind -- one for a group or user the update dropped -- keeps granting, because the
enforcer reads rows by ``groupId`` / ``userId`` and uses each row's own permission JSON. So when the
removal fails the update fails too, rather than writing the new rows and reporting success.

``_delete_denormalized_items`` never raises: it returns the rows it removed (an empty list when the
constraint had none) and ``None`` when the scan or the batch delete failed. ``delete_constraint``
ignores that result and checks for surviving rows itself; only the update path reads it. The removal
scan reads with strong consistency, so a row written moments before the update is not missed.
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
_CONSTRAINT_ID = "update-target"
_BOTH_ROWS = {f"{_CONSTRAINT_ID}#group#g1", f"{_CONSTRAINT_ID}#group#g2"}
_SEEDED_DESCRIPTION = "constraint under update"
_UPDATED_DESCRIPTION = "constraint after update"
_REFUSED = "existing items could not be removed"


def _constraint(group_ids, description=_SEEDED_DESCRIPTION):
    """A constraint in the API shape ``create_or_update_constraint`` accepts."""
    return {
        'identifier': _CONSTRAINT_ID,
        'name': _CONSTRAINT_ID,
        'description': description,
        'objectType': 'asset',
        'criteriaAnd': [{'field': 'databaseId', 'operator': 'equals', 'value': 'db1'}],
        'criteriaOr': [],
        'groupPermissions': [
            {'groupId': group_id, 'permission': 'GET', 'permissionType': 'allow'}
            for group_id in group_ids
        ],
        'userPermissions': [],
    }


def _rows(table):
    """Every row's constraintId mapped to its description, read through the low-level client so a
    patched ``Table.scan`` or ``Table.batch_writer`` does not affect it."""
    client = boto3.client("dynamodb", region_name=table.meta.client.meta.region_name)
    rows = {}
    kwargs = {'TableName': table.name}
    while True:
        response = client.scan(**kwargs)
        for item in response.get('Items', []):
            rows[item['constraintId']['S']] = item['description']['S']
        if 'LastEvaluatedKey' not in response:
            return rows
        kwargs['ExclusiveStartKey'] = response['LastEvaluatedKey']


def _fail_scans(monkeypatch, table):
    def failing_scan(*args, **kwargs):
        raise ClientError(
            {'Error': {'Code': 'ProvisionedThroughputExceededException', 'Message': 'throttled'}},
            'Scan',
        )

    monkeypatch.setattr(table, 'scan', failing_scan)


def _fail_batch_writes(monkeypatch, table):
    def failing_batch_writer(*args, **kwargs):
        raise ClientError(
            {'Error': {'Code': 'ProvisionedThroughputExceededException', 'Message': 'throttled'}},
            'BatchWriteItem',
        )

    monkeypatch.setattr(table, 'batch_writer', failing_batch_writer)


def _event(body):
    return {
        'requestContext': {'http': {'method': 'PUT', 'path': f"/auth/constraints/{_CONSTRAINT_ID}"}},
        'pathParameters': {'constraintId': _CONSTRAINT_ID},
        'queryStringParameters': {},
        'headers': {'authorization': 'Bearer test-token'},
        'body': json.dumps(body),
    }


@pytest.fixture
def constraints_table(monkeypatch):
    """A moto constraints table holding one constraint granted to groups g1 and g2."""
    assert svc.constraints_table_name, "constraints table name did not resolve"
    assert svc.roles_table_name, "roles table name did not resolve"

    with mock_aws():
        resource = boto3.resource("dynamodb", region_name="us-east-1")
        constraints = resource.create_table(
            TableName=svc.constraints_table_name,
            KeySchema=[{"AttributeName": "constraintId", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "constraintId", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        roles = resource.create_table(
            TableName=svc.roles_table_name,
            KeySchema=[{"AttributeName": "roleName", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "roleName", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        for role_name in ("g1", "g2"):
            roles.put_item(Item={"roleName": role_name})

        monkeypatch.setattr(svc, "constraints_table", constraints)
        monkeypatch.setattr(svc, "roles_table", roles)
        svc.create_or_update_constraint(_constraint(("g1", "g2")), dict(_CLAIMS))
        yield constraints


def _seeded_rows():
    return {row_id: _SEEDED_DESCRIPTION for row_id in _BOTH_ROWS}


@pytest.mark.unit
class TestUpdateReplacesTheConstraintRows:

    def test_an_update_that_drops_a_group_removes_its_row(self, constraints_table):
        """Positive control: with a healthy table the dropped group's row is gone."""
        assert _rows(constraints_table) == _seeded_rows()

        result = svc.create_or_update_constraint(
            _constraint(("g1",), _UPDATED_DESCRIPTION), dict(_CLAIMS)
        )

        assert result.success is True
        assert _rows(constraints_table) == {f"{_CONSTRAINT_ID}#group#g1": _UPDATED_DESCRIPTION}

    def test_an_update_whose_row_scan_fails_is_refused(self, constraints_table, monkeypatch):
        _fail_scans(monkeypatch, constraints_table)

        with pytest.raises(svc.VAMSGeneralErrorResponse, match=_REFUSED):
            svc.create_or_update_constraint(
                _constraint(("g1",), _UPDATED_DESCRIPTION), dict(_CLAIMS)
            )

        # No new row was written: both rows are still the seeded ones
        assert _rows(constraints_table) == _seeded_rows()

    def test_an_update_whose_row_delete_fails_is_refused(self, constraints_table, monkeypatch):
        _fail_batch_writes(monkeypatch, constraints_table)

        with pytest.raises(svc.VAMSGeneralErrorResponse, match=_REFUSED):
            svc.create_or_update_constraint(
                _constraint(("g1",), _UPDATED_DESCRIPTION), dict(_CLAIMS)
            )

        assert _rows(constraints_table) == _seeded_rows()

    def test_the_new_rows_are_not_written_after_a_failed_removal(self, constraints_table, monkeypatch):
        monkeypatch.setattr(svc, '_delete_denormalized_items', lambda base_constraint_id: None)
        writer = MagicMock(wraps=constraints_table.batch_writer)
        monkeypatch.setattr(constraints_table, 'batch_writer', writer)

        with pytest.raises(svc.VAMSGeneralErrorResponse, match=_REFUSED):
            svc.create_or_update_constraint(
                _constraint(("g1",), _UPDATED_DESCRIPTION), dict(_CLAIMS)
            )

        writer.assert_not_called()
        assert _rows(constraints_table) == _seeded_rows()

    def test_the_handler_answers_400_and_records_no_write(self, constraints_table, monkeypatch):
        _fail_scans(monkeypatch, constraints_table)
        enforcer = MagicMock()
        enforcer.enforceAPI.return_value = True
        with patch.object(svc, 'request_to_claims', return_value=dict(_CLAIMS)), \
                patch.object(svc, 'CasbinEnforcer', return_value=enforcer), \
                patch.object(svc, 'log_auth_changes') as audit:
            response = lambda_handler(_event(_constraint(("g1",), _UPDATED_DESCRIPTION)), {})

        assert response['statusCode'] == 400
        assert _REFUSED in json.loads(response['body'])['message']
        audit.assert_not_called()
        assert _rows(constraints_table) == _seeded_rows()


@pytest.mark.unit
class TestDeleteHelperResult:

    def test_it_returns_the_rows_it_removed(self, constraints_table):
        removed = svc._delete_denormalized_items(_CONSTRAINT_ID)

        assert {item['constraintId'] for item in removed} == _BOTH_ROWS
        assert _rows(constraints_table) == {}

    def test_it_returns_an_empty_list_when_the_constraint_has_no_rows(self, constraints_table):
        assert svc._delete_denormalized_items("absent-constraint") == []
        # The other constraint's rows are untouched
        assert _rows(constraints_table) == _seeded_rows()

    def test_it_returns_none_instead_of_raising_when_the_scan_fails(self, constraints_table, monkeypatch):
        _fail_scans(monkeypatch, constraints_table)

        assert svc._delete_denormalized_items(_CONSTRAINT_ID) is None
        assert _rows(constraints_table) == _seeded_rows()

    def test_every_page_of_the_removal_scan_reads_with_strong_consistency(self, monkeypatch):
        first, second = sorted(_BOTH_ROWS)
        pager = Pager(
            {'Items': [{'constraintId': first}], 'LastEvaluatedKey': {'constraintId': first}},
            {'Items': [{'constraintId': second}]},
            name="constraint removal scan",
        )
        monkeypatch.setattr(svc, 'constraints_table', MagicMock(scan=pager))

        removed = svc._delete_denormalized_items(_CONSTRAINT_ID)

        assert {item['constraintId'] for item in removed} == _BOTH_ROWS
        pager.assert_paged_to_exhaustion()
        assert all(call.get('ConsistentRead') is True for call in pager.calls)
