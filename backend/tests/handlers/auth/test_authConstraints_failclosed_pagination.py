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

*   **S2-BACKEND-029** -- the listing paged at the DynamoDB cursor over DENORMALIZED rows while
    reporting deduplicated constraints, which broke it in two ways. ``NextToken`` was emitted as the
    raw ``LastEvaluatedKey`` dict but consumed as a ``startingToken`` string, so every client
    serialized the object on the way back (``apiClient.buildUrl`` renders it ``"[object Object]"``),
    that string reached ``ExclusiveStartKey``, boto3 raised ``ParamValidationError`` and the handler
    answered a generic 400. And deduplication ran over the page just read, so ``pageSize`` counted
    rows rather than constraints and a constraint whose ``#group#``/``#user#`` rows landed on more
    than one page was emitted by each of them.

    The listing reads the constraints table to exhaustion, dedups across the whole set, orders it by
    base constraintId and serves an offset slice. ``NextToken`` is the opaque base64 of the next
    decimal offset -- the convention ``paginate_metadata_records`` uses -- and ``pageSize`` counts
    constraints. The ordering is load-bearing: every page re-reads the table and scan order is not
    contractual, so without it an offset would drop and repeat items.

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
from backend.tests.pagingStub import BareMockReader, Pager


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

    moto evaluates the real DynamoDB Limit / LastEvaluatedKey semantics, so the offset walk over the
    deduplicated set is exercised rather than re-implemented by the test. Ten small rows fit one scan
    call, so the listing's own read-to-exhaustion loop is NOT exercised here -- that is what
    ``TestTheScanReadsEveryTablePage`` drives through the scripted pager.
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
        assert factory.called, "it was never called at all"
        assert factory.call_count <= 1, factory.call_count
        assert factory.call_args[0][0] == _CLAIMS
        assert enforcer.enforceAPI.called, "it was never called at all"
        assert enforcer.enforceAPI.call_count <= 1, enforcer.enforceAPI.call_count
        assert enforcer.enforceAPI.call_args[0][0] is event

    def test_authenticated_request_still_denies_when_casbin_denies(
            self, constraints_table, authenticated, enforcer_spy):
        """The 403 is not produced by the token check alone."""
        factory, enforcer = enforcer_spy
        enforcer.enforceAPI.return_value = False

        response = lambda_handler(_list_event({'pageSize': '3'}), {})

        assert response['statusCode'] == 403
        assert enforcer.enforceAPI.called, "it was never called at all"
        assert enforcer.enforceAPI.call_count <= 1, enforcer.enforceAPI.call_count


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
        # The KEY is the claim; the count is not. A retry or a safety re-read of the same key is a
        # safe change that assert_called_once_with would fail.
        assert exploding_roles_table.get_item.called, "the roles table was never read"
        exploding_roles_table.get_item.assert_any_call(Key={'roleName': 'g1'})

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

    def test_next_token_carries_the_offset_of_the_next_constraint(
            self, constraints_table, authenticated, enforcer_spy):
        """The one test that pins the convention rather than the property: base64 of the decimal
        offset into the deduplicated constraint list, as the metadata listing emits it.

        Not a DynamoDB key: pageSize counts constraints while the cursor addresses denormalized
        rows, so the two cannot be the same value.
        """
        page = self._page({'pageSize': '3'})

        decoded = base64.b64decode(page['NextToken']).decode('utf-8')
        assert decoded == '3', "the token must address the 4th constraint, not a table cursor"

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

    def test_the_emitted_token_survives_the_request_model_it_comes_back_through(
            self, constraints_table, authenticated, enforcer_spy):
        """The emit side and the accept side are two different declarations, and only the model
        the token comes back through says whether they agree.

        ``GetConstraintsRequestModel.startingToken`` is a bounded ``str``, so a token that is not
        one -- or is longer than the bound -- is altered or refused on the way in while the emitting
        page still looks perfect. Assert the value the model hands the handler is the value the
        handler emitted, then page from *that* value rather than from the raw token.
        """
        first = self._page({'pageSize': '3'})
        token = first['NextToken']

        bound = svc.GetConstraintsRequestModel.__fields__['startingToken'].field_info.max_length
        assert bound, "startingToken no longer declares a max_length to check the token against"
        assert len(token) <= bound

        parsed = svc.parse(
            {'pageSize': '3', 'startingToken': token}, model=svc.GetConstraintsRequestModel
        )
        assert parsed.startingToken == token

        second = self._page({'pageSize': '3', 'startingToken': parsed.startingToken})
        assert self._base_ids(second), "the model-parsed token did not reach page 2"
        assert self._base_ids(second) != self._base_ids(first)

    @pytest.mark.parametrize(
        "decoded_text",
        ['"cons-one"', '["cons-one"]', 'null', '{"constraintId": "cons-one#group#g1"}', '-1'],
    )
    def test_a_token_that_decodes_to_something_other_than_an_offset_names_the_token(
            self, constraints_table, authenticated, enforcer_spy, decoded_text):
        """Well-formed base64 is still not necessarily an offset.

        The decode raises for none of these, so without the ``int()`` and the sign check the value
        reaches the slice and the failure surfaces as the same generic retrieval failure a broken
        table gives, leaving the caller unable to tell a bad token from a bad table. The dict case is
        the shape the OLD convention emitted, so an old token in a bookmarked URL is refused rather
        than silently serving page one.
        """
        token = base64.b64encode(decoded_text.encode('utf-8')).decode('utf-8')

        response = lambda_handler(_list_event({'pageSize': '3', 'startingToken': token}), {})

        assert response['statusCode'] == 400
        assert 'Invalid pagination token' in json.loads(response['body'])['message']
        # Rule 11: the rejected token is caller input and must not come back out.
        assert token not in response['body']

    def test_a_hand_built_offset_token_is_accepted(
            self, constraints_table, authenticated, enforcer_spy):
        """Positive control for the class above, and the arm that pins base64 of a decimal as the
        accepted form: the reject path must not have become reject-everything."""
        token = base64.b64encode(b'3').decode('utf-8')

        page = self._page({'pageSize': '3', 'startingToken': token})

        assert self._base_ids(page) == set(sorted(_CONSTRAINT_IDS)[3:])
        assert 'NextToken' not in page


class _WalkPremiseFailure(Exception):
    """A walk that never got far enough to say anything about repeats.

    Deliberately not an ``AssertionError``: the repeat test below expects one of those and only one
    of those, so a listing that 500s or a walk that never ends has to raise something the ``xfail``
    marker does not absorb.
    """


@pytest.mark.unit
class TestDeduplicationSpansTheWholeWalk:
    """No constraint may be emitted by more than one page of a walk.

    The rows being deduplicated are not neighbours: ``constraintId`` is the table's partition key and
    every denormalized ``#group#``/``#user#`` row carries a distinct one, so one constraint's rows are
    spread through scan order rather than sitting together. Deduplicating within the page just read
    therefore emitted a constraint from every page holding any of its rows -- up to one page per row,
    not merely the two either side of a boundary -- and every client concatenates the pages (the web
    listing ``fetchConstraints`` and ``vamscli role constraint list --auto-paginate`` both do), so the
    constraint repeated within one assembled list. Deduplicating across the whole set before slicing
    is what makes the page a page of constraints.

    Ten rows at three per page could not align under the old shape: the walk ended on a page of one
    row, and one row cannot also hold that constraint's other row, so at least one constraint was
    emitted twice whatever order the scan returned.
    """

    @staticmethod
    def _pages(page_size):
        """Every page of the walk, as a list of base-constraintId sets."""
        pages = []
        query_parameters = {'pageSize': page_size}
        for _ in range(20):
            response = lambda_handler(_list_event(query_parameters), {})
            if response['statusCode'] != 200:
                raise _WalkPremiseFailure(f"listing failed mid-walk: {response['body']}")
            page = json.loads(response['body'])['message']
            pages.append({item['constraintId'] for item in page['Items']})
            token = page.get('NextToken')
            if not token:
                return pages
            query_parameters = {'pageSize': page_size, 'startingToken': token}
        raise _WalkPremiseFailure("pagination walk did not terminate")

    @staticmethod
    def _repeated(pages):
        counts = {}
        for page in pages:
            for base_id in page:
                counts[base_id] = counts.get(base_id, 0) + 1
        return {base_id for base_id, count in counts.items() if count > 1}

    def test_no_constraint_is_emitted_by_more_than_one_page(
            self, constraints_table, authenticated, enforcer_spy):
        pages = self._pages('3')
        if len(pages) <= 1:
            raise _WalkPremiseFailure("fixture did not produce more than one page")

        assert self._repeated(pages) == set()

    def test_every_non_final_page_holds_exactly_page_size_constraints(
            self, constraints_table, authenticated, enforcer_spy):
        """pageSize counts constraints, not the denormalized rows behind them.

        This is the discriminating direction, and the upper bound is not: two rows dedup to AT MOST
        two constraints, so "no page held more than pageSize" was true of the row-limited shape as
        well. A non-final page short of pageSize is the observable symptom of a page bounded by rows
        -- five constraints at three per page must come back 3 then 2, never 1 then 1 then ....
        """
        pages = self._pages('3')
        if len(pages) <= 1:
            raise _WalkPremiseFailure("fixture did not produce more than one page")

        assert [len(page) for page in pages] == [3, 2]

    def test_max_items_bounds_the_page(
            self, constraints_table, authenticated, enforcer_spy):
        """``maxItems`` was accepted by the request model and then ignored entirely; the page is now
        the smaller of the two, which is what makes it agree with ``validate_pagination_info``'s
        fallback path (it caps pageSize to maxItems there)."""
        response = lambda_handler(_list_event({'pageSize': '3', 'maxItems': '2'}), {})

        assert response['statusCode'] == 200
        page = json.loads(response['body'])['message']
        assert len(page['Items']) == 2
        assert 'NextToken' in page

    def test_the_page_is_bounded_by_the_payload_ceiling(
            self, constraints_table, authenticated, enforcer_spy):
        """Rule 15: dropping the scan ``Limit`` removed the 1 MB-per-call bound that used to hold the
        response down, so the listing carries its own ceiling.

        Asserted on the resolved bound rather than on a page of 3000 seeded constraints, which is not
        a unit fixture. The request model's default is that same ceiling, so a caller that sends no
        pageSize -- which is every web request -- gets a bounded page.
        """
        page_size_field = svc.GetConstraintsRequestModel.__fields__['pageSize'].field_info
        assert page_size_field.default == svc.MAX_CONSTRAINT_LIST_PAGE_SIZE
        assert svc.MAX_CONSTRAINT_LIST_PAGE_SIZE < page_size_field.le, (
            "the ceiling must sit below the accepted maximum, or it clamps nothing"
        )

        # Must-still-work: a pageSize above the ceiling is clamped rather than refused, so the
        # request that used to work still answers 200 and the caller still reaches every constraint.
        response = lambda_handler(_list_event({'pageSize': str(page_size_field.le)}), {})
        assert response['statusCode'] == 200
        served = json.loads(response['body'])['message']
        assert {item['constraintId'] for item in served['Items']} == set(_CONSTRAINT_IDS)

    def test_a_walk_that_fits_one_page_repeats_nothing(
            self, constraints_table, authenticated, enforcer_spy):
        """Control on the instrument above: the repeat detector reports nothing when every row is on
        one page, so a repeat it does report is a repeat and not an artefact of the detector."""
        pages = self._pages('500')

        assert len(pages) == 1
        assert pages[0] == set(_CONSTRAINT_IDS)
        assert self._repeated(pages) == set()


@pytest.mark.unit
class TestTheScanReadsEveryTablePage:
    """The listing's own read must page to exhaustion, which nothing else here can show.

    The seeded fixture is ten small rows, so moto answers every scan in ONE call with no
    ``LastEvaluatedKey``: a regression to a single un-looped scan would satisfy every other test in
    this file. These two drive the read through the shared scripted pager instead, where the second
    table page is reachable only by threading the cursor.
    """

    @staticmethod
    def _row(constraint_id, group_id):
        return _seed_item(f"{constraint_id}#group#{group_id}")

    def test_a_constraint_whose_rows_sit_on_a_later_table_page_is_still_listed(
            self, authenticated, enforcer_spy, monkeypatch):
        pager = Pager(
            {'Items': [self._row('cons-one', 'g1')],
             'LastEvaluatedKey': {'constraintId': 'cons-one#group#g1'}},
            {'Items': [self._row('cons-two', 'g1')]},
            name="constraints scan",
        )
        monkeypatch.setattr(svc, 'constraints_table', MagicMock(scan=pager))

        response = lambda_handler(_list_event({'pageSize': '10'}), {})

        assert response['statusCode'] == 200
        page = json.loads(response['body'])['message']
        assert {item['constraintId'] for item in page['Items']} == {'cons-one', 'cons-two'}
        pager.assert_paged_to_exhaustion()

    def test_a_reader_that_never_signals_the_end_is_not_walked_forever(
            self, authenticated, enforcer_spy, monkeypatch):
        """The loop tests for the PRESENCE of ``LastEvaluatedKey``, not its value.

        ``BareMockReader`` answers every page with a bare ``MagicMock``, whose ``.get()`` is truthy
        for every key -- the shape that hangs a value-form loop. A presence-form loop reads once and
        stops, which is why this test finishing is part of the assertion.
        """
        reader = BareMockReader(name="constraints scan")
        monkeypatch.setattr(svc, 'constraints_table', MagicMock(scan=reader))

        response = lambda_handler(_list_event({'pageSize': '10'}), {})

        assert response['statusCode'] == 200
        assert reader.calls, "the reader was never consulted, so the loop form is unverified"
        assert len(reader.calls) <= 1, (
            f"the loop read {len(reader.calls)} times off a reader that never omits the key, so it "
            "is deciding on the key's value")


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
        assert factory.called, "it was never called at all"
        assert factory.call_count <= 1, factory.call_count
        assert factory.call_args[0][0]['tokens'] == ['test-user-id']
        assert 'cons-one#group#g1' not in _all_row_ids(constraints_table)
