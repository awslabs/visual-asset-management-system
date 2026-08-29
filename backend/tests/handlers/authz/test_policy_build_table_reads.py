# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""How the Casbin policy build reads DynamoDB (S2-BACKEND-051).

The policy build runs on every 60-second cache miss, for every user, in every handler Lambda,
so the shape of its reads is the cost of authorization itself:

* A user's role assignments are read with a Query on ``userId``, that table's partition key.
  A Scan with a ``userId`` filter returns the same rows while reading every row in the table,
  so its cost grows with the deployment's total user count rather than with the caller.
* The roles referenced by those assignments are read by key. Scanning the whole roles table
  with an ``mfaRequired`` filter read every role in the deployment to decide about the handful
  one caller holds.
* An empty policy text is the legitimate answer for a caller with no effective assignments, so
  it is returned as a deny-all rather than retried. Retrying re-ran both reads three times and
  slept after each attempt, spending seconds of Lambda wall time to reach the same deny.

Every "this read no longer happens" assertion below is paired with a control that the read
which replaced it did happen and produced the right policy, so a change that stopped reading
anything at all would not pass.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from backend.backend.handlers.authz import CasbinEnforcerService
import backend.backend.handlers.authz as authz


USER_ROLES_TABLE = "user-roles-table"
ROLES_TABLE = "roles-table"


MAX_PAGED_READS = 10


def _cursor(exclusive_start_key):
    return json.dumps(exclusive_start_key, sort_keys=True)


class _FakeDynamoClient:
    """Records every low-level call the policy build makes and serves canned pages.

    Pages are keyed on ``ExclusiveStartKey``, not on call order, so the assertions say "the
    cursor is threaded" rather than "exactly N reads happened" — an extra or repeated read
    still resolves to the right page. A read that never advances trips ``MAX_PAGED_READS`` and
    fails the test instead of hanging it.

    ``scan`` and ``get_paginator`` are present but record only: the assertions are that they
    stay unused, and a recording stub reports that as a test assertion rather than as an
    exception raised from inside the code under test.
    """

    def __init__(self, user_role_pages=None, roles=None):
        pages = user_role_pages if user_role_pages is not None else [{"Items": []}]
        self.user_role_pages = {_cursor(None): pages[0]}
        for previous, page in zip(pages, pages[1:]):
            self.user_role_pages[_cursor(previous["LastEvaluatedKey"])] = page
        self.roles = roles or {}
        self.query_calls = []
        self.get_item_calls = []
        self.scan_calls = []
        self.paginator_calls = []

    def query(self, **kwargs):
        self.query_calls.append(kwargs)
        if len(self.query_calls) > MAX_PAGED_READS:
            raise AssertionError(
                "the paged read did not advance: the previous page's LastEvaluatedKey is not "
                "being passed back as ExclusiveStartKey")
        return self.user_role_pages[_cursor(kwargs.get("ExclusiveStartKey"))]

    def get_item(self, **kwargs):
        self.get_item_calls.append(kwargs)
        role_name = kwargs["Key"]["roleName"]["S"]
        if role_name not in self.roles:
            return {}
        return {"Item": self.roles[role_name]}

    def scan(self, **kwargs):
        self.scan_calls.append(kwargs)
        return {"Items": []}

    def get_paginator(self, operation_name):
        self.paginator_calls.append(operation_name)
        return MagicMock()

    # Convenience views used by the assertions

    def role_names_read(self):
        return [call["Key"]["roleName"]["S"] for call in self.get_item_calls]

    def tables_queried(self):
        return [call["TableName"] for call in self.query_calls]


def _service(user_id="u1", mfa=False):
    """A CasbinEnforcerService without __init__, wired for policy-text building only."""
    svc = CasbinEnforcerService.__new__(CasbinEnforcerService)
    svc._user_id = user_id
    svc._mfaEnabled = mfa
    svc._roles_table_name = ROLES_TABLE
    svc._user_roles_table_name = USER_ROLES_TABLE
    svc._constraints_table_name = "constraints-table"
    svc._model_text = authz.PERMISSION_CONSTRAINT_POLICY
    svc._dateTime_Cached = None
    svc._enforcer = None
    # The constraint rules are a separate read path; these tests assert on the role grants.
    svc._read_policies_batch_optimized = MagicMock(return_value=[])
    return svc


def _assignment(user_id, role_name):
    return {"userId": {"S": user_id}, "roleName": {"S": role_name}}


def _old_scan_filter_admits(stored_flag):
    """What the roles-table filter this code replaced answered for a stored attribute.

    The filter was ``attribute_exists(roleName) AND (attribute_not_exists(mfaRequired) OR
    mfaRequired = :false)`` with ``:false`` bound as ``{"BOOL": false}``. DynamoDB's ``=``
    compares the attribute TYPE as well as the value, so exactly two stored shapes satisfied
    it: no attribute at all, and a boolean false.

    Evaluated on the RAW typed attribute, before deserialization, because that is where the
    distinction lives that the replacement has to preserve: ``{"NULL": true}`` and an absent
    attribute both deserialize to Python ``None``, so a predicate that reads only the
    deserialized value cannot tell them apart and grants the null one.
    """
    if "mfaRequired" not in stored_flag:
        return True
    return stored_flag["mfaRequired"] == {"BOOL": False}


# Every shape DynamoDB can hold in this attribute, with the verdict the replaced filter gave it.
# A role admitted here is ACTIVE for a session that presented no MFA, so a shape wrongly
# admitted is a privilege widening and not a cosmetic difference.
MFA_FLAG_SHAPES = [
    ({}, True),                                     # absent -> the attribute was never set
    ({"mfaRequired": {"BOOL": False}}, True),       # explicit boolean false
    ({"mfaRequired": {"BOOL": True}}, False),
    ({"mfaRequired": {"NULL": True}}, False),       # stored JSON null -> None once deserialized
    ({"mfaRequired": {"N": "0"}}, False),           # Decimal("0") == False but is not False
    ({"mfaRequired": {"N": "1"}}, False),
    ({"mfaRequired": {"S": "false"}}, False),       # a string is not a boolean to DynamoDB
    ({"mfaRequired": {"S": "true"}}, False),
    ({"mfaRequired": {"S": ""}}, False),
    ({"mfaRequired": {"L": []}}, False),
    ({"mfaRequired": {"M": {}}}, False),
]


def _roles_granted(policy_text):
    """Role names appearing as Casbin grouping lines in the generated policy."""
    return [
        line.split("'role::")[1].rstrip("'")
        for line in policy_text.splitlines()
        if line.startswith("g, ")
    ]


@pytest.fixture(autouse=True)
def _no_default_role():
    """The default-role feature is a separate path; keep it out of these assertions."""
    with patch.object(authz, "DEFAULT_ROLE_NAME", ""):
        yield


@pytest.mark.unit
class TestUserRoleAssignmentReads:
    def test_assignments_are_read_with_a_query_on_the_userid_partition(self):
        client = _FakeDynamoClient(user_role_pages=[{"Items": [_assignment("u1", "roleA")]}])
        svc = _service(mfa=True)

        with patch.object(authz, "_dynamodb_client", client):
            items = svc._read_current_user_roles_from_table()

        # The control: the Query is what produced the assignment, so the absence assertions
        # below cannot be satisfied by reading nothing.
        assert items == [{"userId": "u1", "roleName": "roleA"}]
        assert set(client.tables_queried()) == {USER_ROLES_TABLE}

        # Asserted over every call rather than the first one, so an extra or repeated read
        # does not change the claim: no read of this table is a filtered full-table pass.
        for call in client.query_calls:
            assert call["KeyConditionExpression"] == "userId = :userId"
            assert call["ExpressionAttributeValues"] == {":userId": {"S": "u1"}}
            # A filter would mean the read is still touching rows belonging to other users.
            assert "FilterExpression" not in call

    def test_assignments_are_not_read_with_a_scan(self):
        client = _FakeDynamoClient(user_role_pages=[{"Items": [_assignment("u1", "roleA")]}])
        svc = _service(mfa=True)

        with patch.object(authz, "_dynamodb_client", client):
            items = svc._read_current_user_roles_from_table()

        assert items  # positive control: the read happened
        assert client.scan_calls == []
        assert client.paginator_calls == []

    def test_query_pages_to_exhaustion(self):
        page_one_cursor = {"userId": {"S": "u1"}, "roleName": {"S": "roleA"}}
        client = _FakeDynamoClient(user_role_pages=[
            {"Items": [_assignment("u1", "roleA")], "LastEvaluatedKey": page_one_cursor},
            {"Items": [_assignment("u1", "roleB")]},
        ])
        svc = _service(mfa=True)

        with patch.object(authz, "_dynamodb_client", client):
            items = svc._read_current_user_roles_from_table()

        assert [item["roleName"] for item in items] == ["roleA", "roleB"]
        # A later call must carry the first page's LastEvaluatedKey, or it re-reads page one.
        assert page_one_cursor in [call.get("ExclusiveStartKey") for call in client.query_calls]

    def test_paging_terminates_against_an_unstubbed_response(self):
        """A MagicMock answers ``.get('LastEvaluatedKey')`` with a truthy Mock forever.

        The loop therefore tests key PRESENCE, which a MagicMock reports as False, so an
        under-stubbed reader ends the loop instead of hanging the suite. This test exists
        because the ``.get()`` form of the same loop is what hung a full backend run.
        """
        client = MagicMock()
        svc = _service(mfa=True)

        with patch.object(authz, "_dynamodb_client", client):
            assert svc._read_current_user_roles_from_table() == []


@pytest.mark.unit
class TestRoleRecordReads:
    ROLES = {
        "roleA": {"roleName": {"S": "roleA"}},
        "roleB": {"roleName": {"S": "roleB"}, "mfaRequired": {"BOOL": True}},
        "roleC": {"roleName": {"S": "roleC"}},
        "unassigned": {"roleName": {"S": "unassigned"}},
    }

    def _client(self, assigned):
        return _FakeDynamoClient(
            user_role_pages=[{"Items": [_assignment("u1", name) for name in assigned]}],
            roles=self.ROLES,
        )

    def test_non_mfa_session_reads_only_the_assigned_roles_by_key(self):
        client = self._client(["roleA", "roleB"])
        svc = _service(mfa=False)

        with patch.object(authz, "_dynamodb_client", client):
            policy_text = svc._create_policy_text_helper()

        # Control: the by-key reads produced the correct filtered grant set.
        assert _roles_granted(policy_text) == ["roleA"]
        # A role the caller does not hold is never read, which is what a table Scan did.
        assert "unassigned" not in client.role_names_read()
        assert "roleC" not in client.role_names_read()
        assert client.scan_calls == []
        assert client.paginator_calls == []

    def test_mfa_required_role_is_excluded_from_a_non_mfa_session(self):
        client = self._client(["roleB"])
        svc = _service(mfa=False)

        with patch.object(authz, "_dynamodb_client", client):
            policy_text = svc._create_policy_text_helper()

        assert _roles_granted(policy_text) == []

    def test_mfa_session_keeps_every_assignment_without_reading_the_roles_table(self):
        client = self._client(["roleA", "roleB"])
        svc = _service(mfa=True)

        with patch.object(authz, "_dynamodb_client", client):
            policy_text = svc._create_policy_text_helper()

        assert _roles_granted(policy_text) == ["roleA", "roleB"]
        assert client.get_item_calls == []
        assert client.scan_calls == []

    def test_assignment_to_a_missing_role_record_grants_nothing(self):
        client = _FakeDynamoClient(
            user_role_pages=[{"Items": [_assignment("u1", "ghostRole")]}],
            roles=self.ROLES,
        )
        svc = _service(mfa=False)

        with patch.object(authz, "_dynamodb_client", client):
            policy_text = svc._create_policy_text_helper()

        assert _roles_granted(policy_text) == []

    def test_a_duplicated_assignment_reads_no_other_role(self):
        client = self._client(["roleA", "roleA"])
        svc = _service(mfa=False)

        with patch.object(authz, "_dynamodb_client", client):
            policy_text = svc._create_policy_text_helper()

        # The set is the claim, not the number of reads: nothing beyond the assigned role.
        assert set(client.role_names_read()) == {"roleA"}
        # The duplicate assignment still grants the role.
        assert "roleA" in _roles_granted(policy_text)

    @pytest.mark.parametrize("stored_flag,granted", MFA_FLAG_SHAPES)
    def test_only_absent_or_boolean_false_counts_as_mfa_not_required(self, stored_flag, granted):
        role = {"roleName": {"S": "roleA"}}
        role.update(stored_flag)
        client = _FakeDynamoClient(
            user_role_pages=[{"Items": [_assignment("u1", "roleA")]}],
            roles={"roleA": role},
        )
        svc = _service(mfa=False)

        with patch.object(authz, "_dynamodb_client", client):
            policy_text = svc._create_policy_text_helper()

        assert (_roles_granted(policy_text) == ["roleA"]) is granted
        # The expected column above is not a fresh opinion: it is what the filter this code
        # replaced answered for the same stored bytes.
        assert granted is _old_scan_filter_admits(stored_flag)

    def test_a_failed_role_read_is_not_read_as_a_missing_role(self):
        """A throttled by-key read must not look like "this role does not exist".

        Omitting the role would build a policy that silently drops a role the caller holds --
        a quieter and longer-lived failure than denying. The read propagates instead, which
        puts it on the _create_policy_text retry path and then on its deny-all.
        """
        class _FailingRoleRead(_FakeDynamoClient):
            def get_item(self, **kwargs):
                self.get_item_calls.append(kwargs)
                raise RuntimeError("throttled")

        client = _FailingRoleRead(
            user_role_pages=[{"Items": [_assignment("u1", "roleA")]}], roles=self.ROLES)
        svc = _service(mfa=False)

        with patch.object(authz, "_dynamodb_client", client):
            with pytest.raises(RuntimeError):
                svc._create_policy_text_helper()

        # Control: the same read, working, is what grants the role (see the first test in this
        # class), so the raise above is the read failing rather than the role being unreachable.
        assert client.get_item_calls


@pytest.mark.unit
class TestBothMfaSitesGiveTheSameAnswer:
    """The assigned-role filter and the default-role check must agree, shape for shape.

    They ask the same question about the same attribute in the same file, and while they asked
    it in two spellings they disagreed: ``mfaRequired`` stored as a number deserializes to
    ``Decimal("0")``, which ``not in (None, False)`` reports as "no MFA needed" (because
    ``Decimal("0") == False``) while ``is None or is False`` reports the opposite. A stored
    JSON null split them the other way. Driving one stored shape through BOTH paths is what
    keeps a later edit to one of them from re-opening the gap.
    """

    def _assigned_role_is_active(self, stored_flag):
        role = {"roleName": {"S": "roleA"}}
        role.update(stored_flag)
        client = _FakeDynamoClient(
            user_role_pages=[{"Items": [_assignment("u1", "roleA")]}],
            roles={"roleA": role},
        )
        svc = _service(mfa=False)
        with patch.object(authz, "_dynamodb_client", client):
            return _roles_granted(svc._create_policy_text_helper()) == ["roleA"]

    def _default_role_is_active(self, stored_flag):
        role = {"roleName": {"S": "roleA"}}
        role.update(stored_flag)
        # No assignments at all, which is the only situation the default role applies to.
        client = _FakeDynamoClient(user_role_pages=[{"Items": []}], roles={"roleA": role})
        svc = _service(mfa=False)
        with patch.object(authz, "_dynamodb_client", client), \
             patch.object(authz, "DEFAULT_ROLE_NAME", "roleA"):
            return _roles_granted(svc._create_policy_text_helper()) == ["roleA"]

    @pytest.mark.parametrize("stored_flag,granted", MFA_FLAG_SHAPES)
    def test_the_two_sites_and_the_replaced_filter_all_agree(self, stored_flag, granted):
        assigned = self._assigned_role_is_active(stored_flag)
        default = self._default_role_is_active(stored_flag)

        assert assigned is granted
        assert default is granted, (
            f"the default-role check disagrees with the assigned-role filter on {stored_flag}")
        assert granted is _old_scan_filter_admits(stored_flag)

    def test_an_mfa_session_is_unaffected_by_the_stored_shape(self):
        """Positive control on the whole table above: the filter is what excludes these roles.

        With MFA presented, every shape is active — so the exclusions above come from the MFA
        predicate rather than from a role that could never be granted at all.
        """
        for stored_flag, _ in MFA_FLAG_SHAPES:
            role = {"roleName": {"S": "roleA"}}
            role.update(stored_flag)
            client = _FakeDynamoClient(
                user_role_pages=[{"Items": [_assignment("u1", "roleA")]}],
                roles={"roleA": role},
            )
            svc = _service(mfa=True)
            with patch.object(authz, "_dynamodb_client", client):
                granted = _roles_granted(svc._create_policy_text_helper())
            assert granted == ["roleA"], stored_flag

    def test_a_failed_default_role_read_propagates_like_the_assigned_one(self):
        """The throttled-read behaviour is now the same at both sites."""
        class _FailingRoleRead(_FakeDynamoClient):
            def get_item(self, **kwargs):
                raise RuntimeError("throttled")

        client = _FailingRoleRead(user_role_pages=[{"Items": []}])
        svc = _service(mfa=False)

        with patch.object(authz, "_dynamodb_client", client), \
             patch.object(authz, "DEFAULT_ROLE_NAME", "roleA"):
            with pytest.raises(RuntimeError):
                svc._create_policy_text_helper()


@pytest.fixture
def _clean_policy_cache():
    """Remove this test's user from the module-level policy map, before and after.

    The map has no per-test reset; scoping the cleanup to the ids these tests use keeps that
    unchanged for every other authz test.
    """
    ids = ["unprovisioned-user", "failing-read-user"]
    for user_id in ids:
        authz.casbin_user_policy_map.pop(user_id, None)
    yield
    for user_id in ids:
        authz.casbin_user_policy_map.pop(user_id, None)


@pytest.mark.unit
class TestEmptyPolicyTextIsNotRetried:
    def test_no_effective_roles_yields_deny_all_without_sleeping(self, _clean_policy_cache):
        svc = _service(user_id="unprovisioned-user")
        svc._create_policy_text_helper = MagicMock(return_value="")

        with patch.object(authz.time, "sleep") as slept:
            policy_text = svc._create_policy_text()

        assert policy_text == authz.POLICY_TEXT_DENY_ALL
        assert slept.call_args_list == []

    def test_the_deny_all_result_is_cached_like_any_other(self, _clean_policy_cache):
        svc = _service(user_id="unprovisioned-user")
        svc._create_policy_text_helper = MagicMock(return_value="")

        with patch.object(authz.time, "sleep"):
            svc._create_policy_text()

        assert authz.casbin_user_policy_map["unprovisioned-user"] == authz.POLICY_TEXT_DENY_ALL

    def test_a_deny_all_enforcer_denies(self, _clean_policy_cache):
        """The result is not merely labelled deny-all: Casbin loads it and refuses."""
        svc = _service(user_id="unprovisioned-user")
        svc._create_policy_text_helper = MagicMock(return_value="")

        with patch.object(authz.time, "sleep"):
            svc._create_casbin_enforcer(svc._create_policy_text())

        assert svc.enforce({"object__type": "database", "databaseId": "db1"}, "GET") is False

    def test_a_failed_read_still_retries_and_still_denies(self, _clean_policy_cache):
        """Control for both assertions above.

        A read failure is a different case from "no permissions" and must keep its retries,
        so removing the sleep for the empty case cannot have removed it for a transient
        DynamoDB error.
        """
        svc = _service(user_id="failing-read-user")
        svc._create_policy_text_helper = MagicMock(side_effect=RuntimeError("throttled"))

        with patch.object(authz.time, "sleep") as slept:
            policy_text = svc._create_policy_text()

        assert policy_text == authz.POLICY_TEXT_DENY_ALL
        assert len(slept.call_args_list) >= 1
        # Nothing waits after the final attempt.
        assert len(slept.call_args_list) <= authz.CASBIN_GET_POLICY_RETRY_ATTEMPTS - 1
        # A failed read must not be cached as this user's policy.
        assert "failing-read-user" not in authz.casbin_user_policy_map
