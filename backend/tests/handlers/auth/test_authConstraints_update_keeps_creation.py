# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Updating a constraint keeps its creation metadata and reports the write as an update.

Create and update share one write: the constraint's denormalized rows are deleted and rewritten.
`CreateConstraintRequestModel` has no `dateCreated` or `createdBy` field, so a constraint's creation
metadata can only come from the rows the rewrite replaces, and whether any rows were replaced is
what separates an update from a create, both in the response `operation` and in the
`constraintCreateUpdate` audit record.
"""

import json

import boto3
import pytest
from moto import mock_aws
from unittest.mock import MagicMock, patch

from backend.backend.handlers.auth import authConstraintsService as svc
from backend.backend.handlers.auth.authConstraintsService import lambda_handler


_ID = "keep-created"
_CREATOR = {"tokens": ["creator"], "roles": ["admin"], "mfaEnabled": False}
_EDITOR = {"tokens": ["editor"], "roles": ["admin"], "mfaEnabled": False}


def _body(description):
    return {
        'identifier': _ID,
        'name': _ID,
        'description': description,
        'objectType': 'asset',
        'criteriaAnd': [{'field': 'databaseId', 'operator': 'equals', 'value': 'db1'}],
        'groupPermissions': [{'groupId': 'g1', 'permission': 'GET', 'permissionType': 'allow'}],
        'userPermissions': [{'userId': 'usr1', 'permission': 'GET', 'permissionType': 'allow'}],
    }


def _event(body):
    return {
        'requestContext': {'http': {'method': 'PUT', 'path': f"/auth/constraints/{_ID}"}},
        'pathParameters': {'constraintId': _ID},
        'queryStringParameters': {},
        'headers': {'authorization': 'Bearer test-token'},
        'body': json.dumps(body),
    }


def _rows(table):
    """Every row in the table, paged to exhaustion."""
    rows = []
    scan_kwargs = {'ConsistentRead': True}
    while True:
        response = table.scan(**scan_kwargs)
        rows.extend(response.get('Items', []))
        if 'LastEvaluatedKey' not in response:
            return rows
        scan_kwargs['ExclusiveStartKey'] = response['LastEvaluatedKey']


@pytest.fixture
def constraints_table(monkeypatch):
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
        roles.put_item(Item={"roleName": "g1"})

        monkeypatch.setattr(svc, "constraints_table", constraints)
        monkeypatch.setattr(svc, "roles_table", roles)
        yield constraints


@pytest.mark.unit
class TestUpdateKeepsCreationMetadata:

    def test_a_first_write_is_a_create_stamped_with_its_writer(self, constraints_table):
        result = svc.create_or_update_constraint(_body("first"), _CREATOR)

        assert result.operation == "create"
        rows = _rows(constraints_table)
        assert len(rows) == 2
        assert {row['createdBy'] for row in rows} == {"creator"}

    def test_an_update_keeps_date_created_and_created_by(self, constraints_table):
        svc.create_or_update_constraint(_body("first"), _CREATOR)
        (date_created,) = {row['dateCreated'] for row in _rows(constraints_table)}

        result = svc.create_or_update_constraint(_body("second"), _EDITOR)

        assert result.operation == "update"
        rows = _rows(constraints_table)
        # Positive control: the rewrite really ran and stamped the editor as modifier.
        assert {row['description'] for row in rows} == {"second"}
        assert {row['modifiedBy'] for row in rows} == {"editor"}
        assert {row['dateCreated'] for row in rows} == {date_created}
        assert {row['createdBy'] for row in rows} == {"creator"}

    def test_an_update_of_rows_without_creation_metadata_stamps_the_updater(self, constraints_table):
        constraints_table.put_item(Item={
            'constraintId': f"{_ID}#group#g1",
            'groupId': 'g1',
            'name': _ID,
            'description': 'seeded',
            'objectType': 'asset',
            'criteriaAnd': json.dumps([]),
            'criteriaOr': json.dumps([]),
            'groupPermissions': json.dumps(
                [{'groupId': 'g1', 'permission': 'GET', 'permissionType': 'allow'}]
            ),
            'userPermissions': json.dumps([]),
        })

        result = svc.create_or_update_constraint(_body("second"), _EDITOR)

        assert result.operation == "update"
        assert {row['createdBy'] for row in _rows(constraints_table)} == {"editor"}


@pytest.mark.unit
class TestTheAuditRecordNamesTheOperation:

    def test_create_then_update_through_the_handler(self, constraints_table):
        enforcer = MagicMock()
        enforcer.enforceAPI.return_value = True
        with patch.object(svc, 'request_to_claims', side_effect=[dict(_CREATOR), dict(_EDITOR)]), \
                patch.object(svc, 'CasbinEnforcer', return_value=enforcer), \
                patch.object(svc, 'log_auth_changes') as audit:
            first = lambda_handler(_event(_body("first")), {})
            second = lambda_handler(_event(_body("second")), {})

        assert first['statusCode'] == 200
        assert second['statusCode'] == 200
        assert json.loads(first['body'])['operation'] == "create"
        assert json.loads(second['body'])['operation'] == "update"
        assert [call.args[2]['operation'] for call in audit.call_args_list] == ["create", "update"]
