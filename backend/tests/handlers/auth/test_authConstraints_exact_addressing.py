# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""FIX-006 (S2-BACKEND-009 / S2-BACKEND-028) -- a constraintId must address exactly one constraint.

``get_constraint_details``, ``_delete_denormalized_items`` and ``delete_constraint``'s post-delete
probe all reach their rows with a table ``scan`` + ``FilterExpression``. When that filter was an
unanchored ``begins_with(constraint_id)``, any caller-supplied prefix matched every constraint whose
ID merely started with the same characters: ``DELETE /auth/constraints/abc`` wiped ``abcdef`` too, and
``GET /auth/constraints/a`` answered with whichever row the scan happened to hit first. The path
parameter is validated with ``OBJECT_NAME`` (one character minimum), so a single character was enough.

The denormalized layout is ``<base>``, ``<base>#group#<groupId>`` and ``<base>#user#<userId>``, so the
filter is anchored as ``eq(<base>) OR begins_with("<base>#")``. A **UUID** validator on the path
parameter is deliberately NOT the fix: the CDK-seeded constraints carry human-readable IDs
(``initial_admin_allow_all_web_paths``, ``initial_admin_allow_all_tags``, ...), which a UUID check
would make unreadable, uneditable and undeletable through every client.

Every "the wrong constraint is not touched" assertion here is paired with a positive control -- the
same call addressed exactly, proving the scan really does find rows and that the test is not passing
on an empty table. ``TestUnanchoredFilterReproducesTheDefect`` is the negative control: it restores
the pre-fix filter and asserts the sibling IS destroyed.
"""

import inspect
import json

import boto3
import pytest
from boto3.dynamodb.conditions import Attr
from moto import mock_aws
from unittest.mock import MagicMock, patch

from backend.backend.handlers.auth import authConstraintsService as svc
from backend.backend.handlers.auth.authConstraintsService import lambda_handler


_CLAIMS = {"tokens": ["test-user-id"], "roles": ["admin"], "mfaEnabled": False}

# The collision shape that matters in a real deployment: two constraints where one ID is a strict
# prefix of the other, plus the shipped seeded IDs that share the long `initial_admin_allow_all_`
# stem. `abcde` is one character shorter than `abcdef`; `abc` is a prefix of `abcdef`.
_SEEDED_IDS = (
    "abc",
    "abc#group#g1",
    "abc#user#u1",
    "abcdef",
    "abcdef#group#g2",
    "initial_admin_allow_all_tags",
    "initial_admin_allow_all_tagtypes",
    "initial_admin_allow_all_web_paths",
    "initial_admin_allow_all_web_paths#group#admin",
)

_ABC_ROWS = {"abc", "abc#group#g1", "abc#user#u1"}
_ABCDEF_ROWS = {"abcdef", "abcdef#group#g2"}
_SEEDED_WEB_PATHS_ROWS = {
    "initial_admin_allow_all_web_paths",
    "initial_admin_allow_all_web_paths#group#admin",
}


def _base_of(constraint_id):
    return constraint_id.split('#group#')[0].split('#user#')[0]


def _seed_item(constraint_id):
    """One denormalized row, with content that identifies which constraint it belongs to."""
    base = _base_of(constraint_id)
    item = {
        'constraintId': constraint_id,
        'name': base,
        'description': f"seeded constraint {base}",
        'objectType': 'asset',
        'criteriaAnd': json.dumps([{'field': 'databaseId', 'operator': 'equals', 'value': base}]),
        'criteriaOr': json.dumps([]),
        'groupPermissions': json.dumps(
            [{'groupId': 'admin', 'permission': 'GET', 'permissionType': 'allow'}]
        ),
        'userPermissions': json.dumps([]),
    }
    if '#group#' in constraint_id:
        item['groupId'] = constraint_id.split('#group#')[1]
    if '#user#' in constraint_id:
        item['userId'] = constraint_id.split('#user#')[1]
    return item


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


def _event(method, constraint_id=None, body=None):
    path = '/auth/constraints' if constraint_id is None else f"/auth/constraints/{constraint_id}"
    event = {
        'requestContext': {'http': {'method': method, 'path': path}},
        'pathParameters': {} if constraint_id is None else {'constraintId': constraint_id},
        'queryStringParameters': {},
        'headers': {'authorization': 'Bearer test-token'},
    }
    if body is not None:
        event['body'] = json.dumps(body)
    return event


def _update_body(identifier, group_ids=("g1",), user_ids=()):
    return {
        'identifier': identifier,
        'name': identifier,
        'description': f"rewritten constraint {identifier}",
        'objectType': 'asset',
        'criteriaAnd': [{'field': 'databaseId', 'operator': 'equals', 'value': 'db1'}],
        'groupPermissions': [
            {'groupId': group_id, 'permission': 'GET', 'permissionType': 'allow'}
            for group_id in group_ids
        ],
        'userPermissions': [
            {'userId': user_id, 'permission': 'GET', 'permissionType': 'allow'}
            for user_id in user_ids
        ],
    }


@pytest.fixture
def constraints_table(monkeypatch):
    """A moto-backed constraints table seeded with the collision shape.

    moto evaluates the real DynamoDB FilterExpression semantics, so the anchored filter is exercised
    rather than re-implemented by the test.
    """
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
        for role_name in ("admin", "g1", "g2"):
            roles.put_item(Item={"roleName": role_name})
        for constraint_id in _SEEDED_IDS:
            constraints.put_item(Item=_seed_item(constraint_id))

        monkeypatch.setattr(svc, "constraints_table", constraints)
        monkeypatch.setattr(svc, "roles_table", roles)
        yield constraints


@pytest.fixture
def api_allowed():
    """Tier-1 authorization granted; this module is about row addressing, not authorization."""
    enforcer = MagicMock()
    enforcer.enforceAPI.return_value = True
    with patch.object(svc, 'request_to_claims', return_value=dict(_CLAIMS)), \
            patch.object(svc, 'CasbinEnforcer', return_value=enforcer):
        yield


@pytest.mark.unit
class TestConstraintIdFilterIsAnchored:
    """The filter itself: base ID exactly, or the `<base>#` denormalized namespace."""

    def test_filter_is_eq_base_or_begins_with_base_hash(self):
        expression = svc._constraint_id_filter('abc').get_expression()
        assert expression['operator'] == 'OR'

        exact, denormalized = expression['values']
        exact_expression = exact.get_expression()
        assert exact_expression['operator'] == '='
        assert exact_expression['values'][1] == 'abc'

        denormalized_expression = denormalized.get_expression()
        assert denormalized_expression['operator'] == 'begins_with'
        # The trailing '#' is the whole fix: 'abc' would also match 'abcdef'.
        assert denormalized_expression['values'][1] == 'abc#'

    def test_every_addressing_site_uses_the_helper(self):
        """A partially applied fix is the dangerous outcome, so guard all three call sites."""
        for function in (svc.get_constraint_details, svc._delete_denormalized_items,
                         svc.delete_constraint):
            source = inspect.getsource(function)
            assert '_constraint_id_filter(' in source, f"{function.__name__} does not use the helper"
            assert 'begins_with' not in source, f"{function.__name__} still scans by bare prefix"

    def test_begins_with_appears_only_inside_the_helper(self):
        module_source = inspect.getsource(svc)
        assert module_source.count('.begins_with(') == 1
        assert inspect.getsource(svc._constraint_id_filter).count('.begins_with(') == 1

    def test_denormalized_ids_have_no_suffix_form_outside_the_anchor(self):
        """`#group#` / `#user#` are the only suffixes, so `<base>#` covers the whole constraint."""
        items = svc._transform_to_denormalized_format({
            'identifier': 'abc',
            'groupPermissions': [
                {'groupId': 'g1', 'permission': 'GET', 'permissionType': 'allow'},
                {'groupId': 'g2', 'permission': 'PUT', 'permissionType': 'allow'},
            ],
            'userPermissions': [
                {'userId': 'usr1', 'permission': 'GET', 'permissionType': 'allow'},
            ],
        })
        generated = {item['constraintId'] for item in items}
        assert generated == {'abc#group#g1', 'abc#group#g2', 'abc#user#usr1'}
        for constraint_id in generated:
            assert constraint_id == 'abc' or constraint_id.startswith('abc#')

        # The no-permissions safety item is the bare base ID, which the eq() half matches.
        base_only = svc._transform_to_denormalized_format({'identifier': 'abc'})
        assert [item['constraintId'] for item in base_only] == ['abc']


@pytest.mark.unit
class TestGetAddressesOneConstraint:

    def test_exact_id_resolves_and_prefix_does_not_reach_the_longer_sibling(self, constraints_table):
        exact = svc.get_constraint_details('abc')
        assert exact is not None
        assert exact['constraintId'] == 'abc'
        assert exact['description'] == "seeded constraint abc"
        assert exact['criteriaAnd'][0]['value'] == 'abc'

        # Positive control: the longer sibling is reachable, so the scan is not simply finding nothing.
        sibling = svc.get_constraint_details('abcdef')
        assert sibling is not None
        assert sibling['constraintId'] == 'abcdef'
        assert sibling['criteriaAnd'][0]['value'] == 'abcdef'

    def test_one_character_shorter_id_does_not_resolve(self, constraints_table):
        assert svc.get_constraint_details('abcde') is None
        # Positive control on the same table: one more character resolves.
        assert svc.get_constraint_details('abcdef') is not None

    def test_single_character_id_does_not_resolve_to_the_first_row_scanned(self, constraints_table):
        assert svc.get_constraint_details('a') is None
        assert svc.get_constraint_details('ab') is None
        assert svc.get_constraint_details('initial_admin_allow_all_tag') is None
        # Positive control: each of those becomes resolvable once addressed exactly.
        assert svc.get_constraint_details('abc') is not None
        assert svc.get_constraint_details('initial_admin_allow_all_tags') is not None

    def test_get_handler_returns_404_for_a_prefix_and_200_for_the_exact_id(
            self, constraints_table, api_allowed):
        response = lambda_handler(_event('GET', 'a'), {})
        assert response['statusCode'] == 404

        # Positive control: the permitted GET path still works.
        response = lambda_handler(_event('GET', 'abc'), {})
        assert response['statusCode'] == 200
        assert json.loads(response['body'])['constraint']['constraintId'] == 'abc'

        response = lambda_handler(_event('GET', 'abcdef'), {})
        assert response['statusCode'] == 200
        assert json.loads(response['body'])['constraint']['constraintId'] == 'abcdef'


@pytest.mark.unit
class TestDeleteAddressesOneConstraint:

    def test_delete_removes_only_the_addressed_constraints_rows(self, constraints_table):
        result = svc.delete_constraint('abc', dict(_CLAIMS))
        assert result.success is True
        assert result.operation == 'delete'

        remaining = _all_row_ids(constraints_table)
        assert remaining.isdisjoint(_ABC_ROWS)
        assert _ABCDEF_ROWS <= remaining
        assert _SEEDED_WEB_PATHS_ROWS <= remaining
        assert {"initial_admin_allow_all_tags", "initial_admin_allow_all_tagtypes"} <= remaining

    def test_post_delete_probe_succeeds_while_the_prefix_sibling_remains(self, constraints_table):
        """The Limit=1 probe re-scans with the same filter; an unanchored one would see `abcdef`."""
        result = svc.delete_constraint('abc', dict(_CLAIMS))
        assert result.success is True
        assert 'may still exist' not in result.message
        assert 'abcdef' in _all_row_ids(constraints_table)

    def test_one_character_shorter_id_deletes_nothing(self, constraints_table):
        svc.delete_constraint('abcde', dict(_CLAIMS))
        assert _ABCDEF_ROWS <= _all_row_ids(constraints_table)

        # Positive control: the same call with the full ID does delete those rows.
        svc.delete_constraint('abcdef', dict(_CLAIMS))
        remaining = _all_row_ids(constraints_table)
        assert remaining.isdisjoint(_ABCDEF_ROWS)
        assert _ABC_ROWS <= remaining

    def test_truncated_seeded_id_deletes_nothing(self, constraints_table):
        svc.delete_constraint('initial_admin_allow_all_tag', dict(_CLAIMS))
        remaining = _all_row_ids(constraints_table)
        assert "initial_admin_allow_all_tags" in remaining
        assert "initial_admin_allow_all_tagtypes" in remaining

        # Positive control: the exact ID deletes itself and leaves the stem-sharing sibling.
        svc.delete_constraint('initial_admin_allow_all_tags', dict(_CLAIMS))
        remaining = _all_row_ids(constraints_table)
        assert "initial_admin_allow_all_tags" not in remaining
        assert "initial_admin_allow_all_tagtypes" in remaining

    def test_delete_handler_deletes_the_exact_constraint(self, constraints_table, api_allowed):
        response = lambda_handler(_event('DELETE', 'abc'), {})
        assert response['statusCode'] == 200
        body = json.loads(response['body'])
        assert body['success'] is True
        assert body['constraintId'] == 'abc'

        remaining = _all_row_ids(constraints_table)
        assert remaining.isdisjoint(_ABC_ROWS)
        assert _ABCDEF_ROWS <= remaining


@pytest.mark.unit
class TestUpdateAddressesOneConstraint:

    def test_update_rewrites_only_the_addressed_constraints_rows(self, constraints_table, api_allowed):
        response = lambda_handler(
            _event('PUT', 'abc', _update_body('abc', group_ids=("g1", "g2"), user_ids=("usr1",))),
            {},
        )
        assert response['statusCode'] == 200

        remaining = _all_row_ids(constraints_table)
        # Every group/user suffix is rewritten -- a guard too strict to match its own rows would
        # orphan or drop some of these.
        assert {'abc#group#g1', 'abc#group#g2', 'abc#user#usr1'} <= remaining
        # The stale rows of the same constraint are gone.
        assert 'abc' not in remaining
        assert 'abc#user#u1' not in remaining
        # The prefix sibling is untouched.
        assert _ABCDEF_ROWS <= remaining
        assert svc.get_constraint_details('abcdef')['description'] == "seeded constraint abcdef"

    def test_update_of_a_prefix_id_does_not_destroy_the_longer_constraints(
            self, constraints_table, api_allowed):
        response = lambda_handler(_event('PUT', 'ab', _update_body('ab')), {})
        assert response['statusCode'] == 200

        remaining = _all_row_ids(constraints_table)
        # Positive control: the write half really ran.
        assert 'ab#group#g1' in remaining
        # Neither constraint that 'ab' is a prefix of lost a row.
        assert _ABC_ROWS <= remaining
        assert _ABCDEF_ROWS <= remaining


@pytest.mark.unit
class TestSeededHumanReadableIdsStillWork:
    """No UUID-shaped validator was introduced: the CDK-seeded IDs stay fully manageable."""

    def test_get_update_and_delete_a_seeded_id(self, constraints_table, api_allowed):
        seeded_id = 'initial_admin_allow_all_web_paths'

        response = lambda_handler(_event('GET', seeded_id), {})
        assert response['statusCode'] == 200
        assert json.loads(response['body'])['constraint']['constraintId'] == seeded_id

        response = lambda_handler(
            _event('PUT', seeded_id, _update_body(seeded_id, group_ids=("admin",))), {}
        )
        assert response['statusCode'] == 200
        assert f"{seeded_id}#group#admin" in _all_row_ids(constraints_table)

        response = lambda_handler(_event('DELETE', seeded_id), {})
        assert response['statusCode'] == 200
        remaining = _all_row_ids(constraints_table)
        assert remaining.isdisjoint(_SEEDED_WEB_PATHS_ROWS)
        # The other seeded constraints are unaffected.
        assert {"initial_admin_allow_all_tags", "initial_admin_allow_all_tagtypes"} <= remaining


@pytest.mark.unit
class TestUnanchoredFilterReproducesTheDefect:
    """Negative control -- with the pre-fix filter the fixture must destroy the sibling.

    Without this, every assertion above would also pass against a filter that matches nothing at all.
    """

    def test_pre_fix_prefix_scan_deletes_the_longer_sibling(self, constraints_table, monkeypatch):
        monkeypatch.setattr(
            svc, '_constraint_id_filter',
            lambda base_constraint_id: Attr('constraintId').begins_with(base_constraint_id),
        )

        svc.delete_constraint('abc', dict(_CLAIMS))

        remaining = _all_row_ids(constraints_table)
        assert remaining.isdisjoint(_ABC_ROWS)
        assert remaining.isdisjoint(_ABCDEF_ROWS), "fixture does not reproduce the over-delete"

    def test_pre_fix_prefix_scan_resolves_a_single_character_id(self, constraints_table, monkeypatch):
        monkeypatch.setattr(
            svc, '_constraint_id_filter',
            lambda base_constraint_id: Attr('constraintId').begins_with(base_constraint_id),
        )

        assert svc.get_constraint_details('a') is not None

    def test_pre_fix_prefix_scan_wipes_the_stem_sharing_seeded_constraints(
            self, constraints_table, monkeypatch):
        monkeypatch.setattr(
            svc, '_constraint_id_filter',
            lambda base_constraint_id: Attr('constraintId').begins_with(base_constraint_id),
        )

        svc.delete_constraint('initial_admin_allow_all_tag', dict(_CLAIMS))

        remaining = _all_row_ids(constraints_table)
        assert "initial_admin_allow_all_tags" not in remaining
        assert "initial_admin_allow_all_tagtypes" not in remaining
