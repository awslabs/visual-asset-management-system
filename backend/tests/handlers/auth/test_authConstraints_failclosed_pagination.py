# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""S2-BACKEND-158 / S2-BACKEND-091 / S2-BACKEND-029 -- the auth constraints service must fail
closed, and its pagination token must round-trip.

Three defects, the first two order-dependent:

*   **S2-BACKEND-158** -- module load swallowed a resource-name resolution failure to ``None`` and
    built ``constraints_table``/``roles_table`` conditionally, so a missing SSM prefix, an IAM
    denial or SSM throttling at cold start produced a module that imported cleanly and then
    ``AttributeError``-ed on ``None.scan`` inside every request, surfacing as an opaque 500. The
    module-load contract (``backend/CLAUDE.md`` Rule 10) is to log and re-raise so the Lambda fails
    at init. The consequence of that choice is deliberate: a cold-start failure that names itself,
    instead of a silently degraded request path.

*   **S2-BACKEND-091** -- ``validate_constraint_role_exists`` returned ``True`` both when
    ``roles_table`` was ``None`` and when the lookup raised, i.e. "cannot check" was treated as
    "allowed". ``create_or_update_constraint`` gates the write on that return, so a constraint whose
    ``groupPermissions[].groupId`` names a role that does not exist was accepted and stored inert --
    the dangerous direction being a DENY constraint typo'd onto a nonexistent role, which an
    operator believes is blocking access while the Casbin policy text emits nothing for it. Fixing
    this before 158 would have converted a cold-start failure into a per-request write failure, so
    158 lands first and makes the ``roles_table is None`` branch unreachable.

*   **S2-BACKEND-029** -- ``NextToken`` was emitted as the raw DynamoDB ``LastEvaluatedKey`` dict but
    consumed as a ``startingToken`` string. Every client serializes the object on the way back
    (``apiClient.buildUrl`` renders it ``"[object Object]"``), that string reached
    ``ExclusiveStartKey``, boto3 raised ``ParamValidationError`` and the handler answered a generic
    400. The token is now the opaque base64 of the ``LastEvaluatedKey`` that the asset and metadata
    listings already use, decoded back to a dict on input.

Every deny assertion here is paired with a positive control on the same fixture, because "denied" is
also satisfied by a handler that denies everything. The Tier-1 tests assert the enforcer was never
*constructed* -- ``CasbinEnforcer`` is a truthy ``MagicMock`` in this suite, so asserting only on a
403 would pass for the wrong reason.
"""

import ast
import base64
import importlib.util
import json
import sys
import types

import boto3
import pytest
from moto import mock_aws
from unittest.mock import MagicMock, patch

from backend.backend.handlers.auth import request_to_claims as real_request_to_claims
from backend.backend.handlers.auth import authConstraintsService as svc
from backend.backend.handlers.auth.authConstraintsService import lambda_handler


_CLAIMS = {"tokens": ["test-user-id"], "roles": ["admin"], "mfaEnabled": False}
_NO_CLAIMS = {"tokens": [], "roles": [], "mfaEnabled": False}

# Five constraints, ten denormalized rows, so a small pageSize straddles constraints and the walk
# needs more than two pages.
_CONSTRAINT_IDS = ("cons-one", "cons-two", "cons-three", "cons-four", "cons-five")
_SEEDED_ROWS = tuple(
    f"{constraint_id}#group#{group_id}"
    for constraint_id in _CONSTRAINT_IDS
    for group_id in ("g1", "g2")
)


class _ResourceNameFailure(Exception):
    """Stands in for the KeyError / ClientError that ``get_table_name`` raises for real."""


def _seed_item(row_id):
    base = row_id.split('#group#')[0]
    return {
        'constraintId': row_id,
        'name': base,
        'description': f"seeded constraint {base}",
        'objectType': 'asset',
        'criteriaAnd': json.dumps([{'field': 'databaseId', 'operator': 'equals', 'value': base}]),
        'criteriaOr': json.dumps([]),
        'groupPermissions': json.dumps(
            [{'groupId': 'admin', 'permission': 'GET', 'permissionType': 'allow'}]
        ),
        'userPermissions': json.dumps([]),
        'groupId': row_id.split('#group#')[1],
    }


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


def _list_event(query_parameters=None):
    return {
        'requestContext': {'http': {'method': 'GET', 'path': '/auth/constraints'}},
        'pathParameters': {},
        'queryStringParameters': dict(query_parameters or {}),
        'headers': {'authorization': 'Bearer test-token'},
    }


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


def _write_body(identifier, group_ids=("g1",)):
    return {
        'identifier': identifier,
        'name': identifier,
        'description': f"written constraint {identifier}",
        'objectType': 'asset',
        'criteriaAnd': [{'field': 'databaseId', 'operator': 'equals', 'value': 'db1'}],
        'groupPermissions': [
            {'groupId': group_id, 'permission': 'GET', 'permissionType': 'allow'}
            for group_id in group_ids
        ],
        'userPermissions': [],
    }


@pytest.fixture
def constraints_table(monkeypatch):
    """moto-backed constraints + roles tables, seeded with ten denormalized rows.

    moto evaluates the real DynamoDB Limit / LastEvaluatedKey semantics, so the pagination walk is
    exercised rather than re-implemented by the test.
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
        for row_id in _SEEDED_ROWS:
            constraints.put_item(Item=_seed_item(row_id))

        monkeypatch.setattr(svc, "constraints_table", constraints)
        monkeypatch.setattr(svc, "roles_table", roles)
        yield constraints


@pytest.fixture
def enforcer_spy():
    """Tier-1 granted, with the constructor and the enforceAPI call both observable.

    Yields ``(factory, enforcer)``: ``factory`` is what the handler calls as ``CasbinEnforcer(...)``,
    so ``factory.assert_not_called()`` is the "authorization was never consulted" assertion.
    """
    enforcer = MagicMock()
    enforcer.enforceAPI.return_value = True
    factory = MagicMock(return_value=enforcer)
    with patch.object(svc, 'CasbinEnforcer', factory):
        yield factory, enforcer


@pytest.fixture
def authenticated():
    with patch.object(svc, 'request_to_claims', return_value=dict(_CLAIMS)):
        yield


@pytest.fixture
def unauthenticated():
    """A request that carried no usable identity: an empty token list."""
    with patch.object(svc, 'request_to_claims', return_value=dict(_NO_CLAIMS)):
        yield


@pytest.fixture
def rest_claims():
    """Swap the suite-wide claims mock for the real thing, so a REST event is really normalized."""
    assert svc.request_to_claims is not real_request_to_claims, (
        "the suite no longer mocks request_to_claims; this fixture is redundant"
    )
    with patch.object(svc, 'request_to_claims', real_request_to_claims):
        yield


@pytest.fixture
def reimportable(monkeypatch):
    """Make a fresh execution of the handler source resolve its ``from`` imports.

    The root ``conftest`` autouse fixture replaces ``sys.modules['handlers.authz']`` and friends
    with blank stand-ins before every test, so a second execution of the source cannot re-run
    ``from handlers.authz import CasbinEnforcer`` even though the first one (at collection time)
    could. Rather than hand-listing stubs, walk the source's own ``from`` statements and back-fill
    any missing name from the live module's namespace -- so the fresh execution binds the *same*
    objects the real import bound, and a renamed import fails loudly here instead of silently
    turning these tests into a no-op.
    """
    with open(svc.__file__, 'rb') as source_file:
        tree = ast.parse(source_file.read())

    for node in tree.body:
        if not isinstance(node, ast.ImportFrom) or node.level:
            continue
        target = sys.modules.get(node.module)
        missing = [
            alias.name for alias in node.names
            if target is None or not hasattr(target, alias.name)
        ]
        if not missing:
            continue
        stub = types.ModuleType(node.module)
        if target is not None:
            for key, value in vars(target).items():
                setattr(stub, key, value)
        for attr in missing:
            setattr(stub, attr, getattr(svc, attr))
        monkeypatch.setitem(sys.modules, node.module, stub)

    yield


def _exec_fresh_module():
    """Execute a second, throwaway copy of the handler module from the same source file.

    Deliberately not registered in ``sys.modules``: the already-imported ``svc`` must keep working
    for every other test in the file.
    """
    spec = importlib.util.spec_from_file_location(
        "authConstraintsService_failclosed_probe", svc.__file__
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _exec_fresh_module_expecting_failure():
    spec = importlib.util.spec_from_file_location(
        "authConstraintsService_failclosed_probe", svc.__file__
    )
    module = importlib.util.module_from_spec(spec)
    with pytest.raises(_ResourceNameFailure):
        spec.loader.exec_module(module)
    return module


@pytest.mark.unit
class TestModuleLoadFailsFastOnUnresolvableTable:
    """S2-BACKEND-158 -- an unresolvable resource name kills the cold start, it does not degrade.

    The handler binds ``get_table_name`` with a ``from`` import, so patching the attribute on
    ``common.resourceNames`` only affects a *fresh* execution of the module source -- which is what
    a Lambda cold start does.
    """

    @staticmethod
    def _resource_names():
        return sys.modules['common.resourceNames']

    def test_unresolvable_constraints_table_raises_at_import(self, reimportable, monkeypatch):
        def fail(key):
            raise _ResourceNameFailure(f"not found in SSM: {key.param_key}")

        monkeypatch.setattr(self._resource_names(), 'get_table_name', fail)
        module = _exec_fresh_module_expecting_failure()

        # No degraded request path exists: execution stopped before any table -- or any request
        # entry point -- was bound, so there is nothing for a request to reach and no None table
        # for boto3 to choke on.
        assert not hasattr(module, 'constraints_table')
        assert not hasattr(module, 'roles_table')
        assert not hasattr(module, 'lambda_handler')

    def test_unresolvable_roles_table_alone_also_raises_at_import(self, reimportable, monkeypatch):
        """The roles table is not optional: 091's fail-open branch is only unreachable if this
        raises too."""
        real_get_table_name = self._resource_names().get_table_name
        # Compared by param_key, not identity: the root conftest re-executes
        # common.resourceNames for every test, so a fresh execution of the handler binds a
        # different ResourceKeys object than the already-imported svc holds.
        roles_param_key = svc.ResourceKeys.ROLES_STORAGE_TABLE.param_key
        refused = []

        def fail_roles_only(key):
            if key.param_key == roles_param_key:
                refused.append(key.param_key)
                raise _ResourceNameFailure(f"not found in SSM: {key.param_key}")
            return real_get_table_name(key)

        monkeypatch.setattr(self._resource_names(), 'get_table_name', fail_roles_only)
        module = _exec_fresh_module_expecting_failure()

        # The injected failure is the one that fired, not an unrelated import error.
        assert refused == [roles_param_key]
        assert not hasattr(module, 'roles_table')
        assert not hasattr(module, 'lambda_handler')

    def test_module_loads_and_binds_both_tables_when_names_resolve(self, reimportable):
        """Positive control: the same fresh execution succeeds on a healthy deployment."""
        module = _exec_fresh_module()

        assert module.constraints_table is not None
        assert module.roles_table is not None
        assert module.constraints_table.name == module.constraints_table_name
        assert module.roles_table.name == module.roles_table_name
        assert callable(module.lambda_handler)


@pytest.mark.unit
class TestEmptyTokensDenyBeforeCasbinIsConsulted:
    """Rule 4 Tier 1 -- an empty token list denies, and the enforcer is never built.

    ``CasbinEnforcer`` is a truthy MagicMock throughout this suite, so a 403 alone does not prove
    the deny came from the token check.
    """

    def test_list_denies_and_never_constructs_the_enforcer(
            self, constraints_table, unauthenticated, enforcer_spy):
        factory, enforcer = enforcer_spy

        response = lambda_handler(_list_event({'pageSize': '3'}), {})

        assert response['statusCode'] == 403
        factory.assert_not_called()
        enforcer.enforceAPI.assert_not_called()

    def test_get_by_id_denies_and_never_constructs_the_enforcer(
            self, constraints_table, unauthenticated, enforcer_spy):
        factory, _ = enforcer_spy

        response = lambda_handler(_event('GET', 'cons-one'), {})

        assert response['statusCode'] == 403
        factory.assert_not_called()

    def test_write_denies_without_touching_a_single_row(
            self, constraints_table, unauthenticated, enforcer_spy):
        factory, _ = enforcer_spy
        before = _all_row_ids(constraints_table)

        response = lambda_handler(_event('PUT', 'cons-one', _write_body('cons-one')), {})

        assert response['statusCode'] == 403
        factory.assert_not_called()
        assert _all_row_ids(constraints_table) == before

    def test_delete_denies_without_removing_a_single_row(
            self, constraints_table, unauthenticated, enforcer_spy):
        factory, _ = enforcer_spy
        before = _all_row_ids(constraints_table)

        response = lambda_handler(_event('DELETE', 'cons-one'), {})

        assert response['statusCode'] == 403
        factory.assert_not_called()
        assert _all_row_ids(constraints_table) == before

    def test_authenticated_request_is_served_and_hands_casbin_the_claims_and_event(
            self, constraints_table, authenticated, enforcer_spy):
        """Positive control, plus the arguments: a handler that denied everything would also pass
        the four tests above."""
        factory, enforcer = enforcer_spy
        event = _list_event({'pageSize': '3'})

        response = lambda_handler(event, {})

        assert response['statusCode'] == 200
        factory.assert_called_once()
        assert factory.call_args[0][0] == _CLAIMS
        enforcer.enforceAPI.assert_called_once()
        assert enforcer.enforceAPI.call_args[0][0] is event

    def test_authenticated_request_still_denies_when_casbin_denies(
            self, constraints_table, authenticated, enforcer_spy):
        """The 403 is not produced by the token check alone."""
        factory, enforcer = enforcer_spy
        enforcer.enforceAPI.return_value = False

        response = lambda_handler(_list_event({'pageSize': '3'}), {})

        assert response['statusCode'] == 403
        enforcer.enforceAPI.assert_called_once()


@pytest.mark.unit
class TestRoleValidationFailsClosed:
    """S2-BACKEND-091 -- "cannot check the role" must refuse the write, not allow it."""

    def test_existing_role_is_accepted_and_written(
            self, constraints_table, authenticated, enforcer_spy):
        """Positive control: the whole class below is satisfied by a handler that refuses every
        write, so prove the permitted write still lands."""
        response = lambda_handler(_event('PUT', 'cons-new', _write_body('cons-new')), {})

        assert response['statusCode'] == 200
        assert 'cons-new#group#g1' in _all_row_ids(constraints_table)

    def test_nonexistent_role_is_refused(self, constraints_table, authenticated, enforcer_spy):
        before = _all_row_ids(constraints_table)

        response = lambda_handler(
            _event('PUT', 'cons-new', _write_body('cons-new', group_ids=("ghost-role",))), {}
        )

        assert response['statusCode'] == 400
        assert _all_row_ids(constraints_table) == before

    def test_lookup_failure_refuses_the_write(
            self, constraints_table, authenticated, enforcer_spy, monkeypatch):
        """A throttled / denied roles lookup used to return True and write the constraint."""
        exploding_roles_table = MagicMock()
        exploding_roles_table.get_item.side_effect = Exception("ProvisionedThroughputExceeded")
        monkeypatch.setattr(svc, 'roles_table', exploding_roles_table)
        before = _all_row_ids(constraints_table)

        response = lambda_handler(_event('PUT', 'cons-new', _write_body('cons-new')), {})

        assert response['statusCode'] != 200
        assert _all_row_ids(constraints_table) == before
        exploding_roles_table.get_item.assert_called_once_with(Key={'roleName': 'g1'})

    def test_absent_roles_table_refuses_the_write(
            self, constraints_table, authenticated, enforcer_spy, monkeypatch):
        """The removed ``if not roles_table: return True`` branch, proved behaviourally.

        Module load now raises when the roles table name cannot resolve, so this state is
        unreachable in production -- asserting on it keeps the fail-open from being reintroduced as
        a "defensive" guard.
        """
        monkeypatch.setattr(svc, 'roles_table', None)
        before = _all_row_ids(constraints_table)

        response = lambda_handler(_event('PUT', 'cons-new', _write_body('cons-new')), {})

        assert response['statusCode'] != 200
        assert _all_row_ids(constraints_table) == before

    def test_refusal_message_does_not_echo_the_submitted_role(
            self, constraints_table, authenticated, enforcer_spy):
        """Rule 11: the caller gets a generic message, the groupId goes to the log."""
        response = lambda_handler(
            _event('PUT', 'cons-new', _write_body('cons-new', group_ids=("ghost-role",))), {}
        )

        assert response['statusCode'] == 400
        assert 'ghost-role' not in response['body']


@pytest.mark.unit
class TestPaginationTokenRoundTrips:
    """S2-BACKEND-029 -- emit a token, feed it back, reach the end."""

    @staticmethod
    def _page(query_parameters):
        response = lambda_handler(_list_event(query_parameters), {})
        assert response['statusCode'] == 200, response['body']
        return json.loads(response['body'])['message']

    @staticmethod
    def _base_ids(page):
        return {item['constraintId'] for item in page['Items']}

    def test_next_token_is_a_string_that_survives_query_string_serialization(
            self, constraints_table, authenticated, enforcer_spy):
        """The defect: a dict here becomes the literal "[object Object]" on the way back."""
        page = self._page({'pageSize': '3'})

        assert 'NextToken' in page, "seeded table did not straddle a page"
        assert isinstance(page['NextToken'], str)
        assert str(page['NextToken']) == page['NextToken']

    def test_next_token_carries_the_last_evaluated_key(
            self, constraints_table, authenticated, enforcer_spy):
        """The one test that pins the convention rather than the property: base64 of the JSON
        LastEvaluatedKey, as the asset and metadata listings emit it."""
        page = self._page({'pageSize': '3'})

        decoded = json.loads(base64.b64decode(page['NextToken']).decode('utf-8'))
        assert isinstance(decoded, dict)
        assert decoded['constraintId'] in _SEEDED_ROWS

    def test_page_two_is_served_and_differs_from_page_one(
            self, constraints_table, authenticated, enforcer_spy):
        first = self._page({'pageSize': '3'})
        second = self._page({'pageSize': '3', 'startingToken': first['NextToken']})

        assert self._base_ids(second), "page 2 came back empty"
        assert self._base_ids(second) != self._base_ids(first)

    def test_walk_terminates_and_covers_every_constraint(
            self, constraints_table, authenticated, enforcer_spy):
        """Ten rows at three per page: the walk must end, and no constraint may be lost at a page
        boundary (per-page dedup makes that the property worth asserting)."""
        seen = set()
        pages = 0
        query_parameters = {'pageSize': '3'}

        for _ in range(20):
            page = self._page(query_parameters)
            pages += 1
            seen |= self._base_ids(page)
            token = page.get('NextToken')
            if not token:
                break
            query_parameters = {'pageSize': '3', 'startingToken': token}
        else:
            pytest.fail("pagination walk did not terminate")

        assert pages > 1, "fixture did not produce more than one page"
        assert seen == set(_CONSTRAINT_IDS)

    def test_a_single_page_walk_emits_no_token(
            self, constraints_table, authenticated, enforcer_spy):
        """Positive control on termination: a page large enough for the whole table ends at once."""
        page = self._page({'pageSize': '500'})

        assert 'NextToken' not in page
        assert self._base_ids(page) == set(_CONSTRAINT_IDS)

    @pytest.mark.parametrize(
        "bad_token",
        [
            "[object Object]",                                   # what the web client used to send
            "{'constraintId': 'cons-one#group#g1'}",             # a str()'d LastEvaluatedKey
            "not-base64-at-all!!",
            base64.b64encode(b'"a bare string"').decode('utf-8'),
        ],
    )
    def test_a_corrupt_token_is_a_generic_400_not_a_500(
            self, constraints_table, authenticated, enforcer_spy, bad_token):
        response = lambda_handler(_list_event({'pageSize': '3', 'startingToken': bad_token}), {})

        assert response['statusCode'] == 400
        # Rule 11: the rejected token is caller input and must not come back out.
        assert bad_token not in response['body']

    def test_a_valid_token_from_this_handler_is_not_rejected(
            self, constraints_table, authenticated, enforcer_spy):
        """Positive control for the class above: the reject path is not rejecting everything."""
        first = self._page({'pageSize': '3'})

        response = lambda_handler(
            _list_event({'pageSize': '3', 'startingToken': first['NextToken']}), {}
        )

        assert response['statusCode'] == 200


@pytest.mark.unit
class TestRestShapedEventFailsClosedAndPaginates:
    """The same properties on the event shape API Gateway REST (v1) actually delivers (Rule 16).

    These tests run the **real** ``request_to_claims``, so the token list is derived from the real
    authorizer context and the real ``normalize_event`` shim runs in place -- the two things a
    hand-built v2 event silently supplies. ``tests/conftest.py:86`` replaces the handler's
    ``request_to_claims`` with ``lambda event: {"tokens": ["test_token"]}``, which normalizes
    nothing and is never empty, so an empty-token assertion resting on the suite default would
    prove nothing at all. ``queryStringParameters`` / ``pathParameters`` arrive here as explicit
    JSON ``null``, the shape that 500s an un-normalized handler.
    """

    @staticmethod
    def _rest_event(method, path, query_parameters=None, path_parameters=None,
                    tokens=("test-user-id",), body=None):
        event = {
            'httpMethod': method,
            'path': path,
            'queryStringParameters': query_parameters,
            'pathParameters': path_parameters,
            'headers': {},
            'requestContext': {
                'identity': {'sourceIp': '203.0.113.7'},
                'authorizer': {
                    'sub': 'test-user-id',
                    'vams:tokens': json.dumps(list(tokens)),
                    'vams:roles': json.dumps(['admin']),
                },
            },
        }
        if body is not None:
            event['body'] = json.dumps(body)
        return event

    def test_null_query_parameters_still_list_every_constraint(
            self, constraints_table, rest_claims, enforcer_spy):
        response = lambda_handler(self._rest_event('GET', '/auth/constraints'), {})

        assert response['statusCode'] == 200
        page = json.loads(response['body'])['message']
        assert {item['constraintId'] for item in page['Items']} == set(_CONSTRAINT_IDS)

    def test_pagination_round_trips_on_a_rest_event(
            self, constraints_table, rest_claims, enforcer_spy):
        first = lambda_handler(
            self._rest_event('GET', '/auth/constraints', {'pageSize': '3'}), {}
        )
        assert first['statusCode'] == 200
        first_page = json.loads(first['body'])['message']
        assert isinstance(first_page['NextToken'], str)

        second = lambda_handler(
            self._rest_event(
                'GET', '/auth/constraints',
                {'pageSize': '3', 'startingToken': first_page['NextToken']},
            ),
            {},
        )
        assert second['statusCode'] == 200
        second_page = json.loads(second['body'])['message']

        first_ids = {item['constraintId'] for item in first_page['Items']}
        second_ids = {item['constraintId'] for item in second_page['Items']}
        assert second_ids and second_ids != first_ids

    def test_the_authorizer_context_under_test_really_yields_an_empty_token_list(self):
        """Control on the *input* to the two deny tests below: the 403 must be attributable to an
        empty token list, not to an event the real claims extractor could not read at all."""
        empty = real_request_to_claims(
            self._rest_event('GET', '/auth/constraints', {'pageSize': '3'}, tokens=())
        )
        populated = real_request_to_claims(
            self._rest_event('GET', '/auth/constraints', {'pageSize': '3'})
        )

        assert empty['tokens'] == []
        assert populated['tokens'] == ['test-user-id']

    def test_an_empty_authorizer_token_list_denies_without_building_the_enforcer(
            self, constraints_table, rest_claims, enforcer_spy):
        """The real fail-closed property: real claims extraction, no enforcer, no 200."""
        factory, enforcer = enforcer_spy

        response = lambda_handler(
            self._rest_event('GET', '/auth/constraints', {'pageSize': '3'}, tokens=()), {}
        )

        assert response['statusCode'] == 403
        factory.assert_not_called()
        enforcer.enforceAPI.assert_not_called()

    def test_normalization_really_runs_on_the_event_the_handler_receives(
            self, constraints_table, rest_claims, enforcer_spy):
        """Rule 16: the v2-shaped block the handler reads is injected into the REST event in place,
        so this class is exercising the normalizer rather than a pre-shaped event."""
        event = self._rest_event('GET', '/auth/constraints')
        assert 'http' not in event['requestContext']

        response = lambda_handler(event, {})

        assert response['statusCode'] == 200
        assert event['requestContext']['http']['path'] == '/auth/constraints'
        assert event['requestContext']['http']['method'] == 'GET'
        # The explicit JSON nulls were coerced, which is what kept them from crashing the handler.
        assert event['queryStringParameters'] == {}
        assert event['pathParameters'] == {}

    def test_an_empty_authorizer_token_list_denies_a_delete_without_removing_a_row(
            self, constraints_table, rest_claims, enforcer_spy):
        factory, _ = enforcer_spy
        before = _all_row_ids(constraints_table)

        response = lambda_handler(
            self._rest_event('DELETE', '/auth/constraints/cons-one',
                             path_parameters={'constraintId': 'cons-one'}, tokens=()),
            {},
        )

        assert response['statusCode'] == 403
        factory.assert_not_called()
        assert _all_row_ids(constraints_table) == before

    def test_a_populated_authorizer_token_list_is_served(
            self, constraints_table, rest_claims, enforcer_spy):
        """Positive control: the two tests above are not passing because every REST request 403s."""
        factory, enforcer = enforcer_spy

        response = lambda_handler(
            self._rest_event('DELETE', '/auth/constraints/cons-one',
                             path_parameters={'constraintId': 'cons-one'}),
            {},
        )

        assert response['statusCode'] == 200
        factory.assert_called_once()
        assert factory.call_args[0][0]['tokens'] == ['test-user-id']
        assert 'cons-one#group#g1' not in _all_row_ids(constraints_table)
