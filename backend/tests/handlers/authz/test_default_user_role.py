# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for the optional default user role (``DEFAULT_ROLE_NAME``).

The feature grants a baseline role to an authenticated identity that has NO role assignments —
typically a federated IdP login that was never provisioned into the user-roles table. Because it is
the only path that grants access to an unprovisioned user, every guard around it is security
relevant:

* Disabled (empty) by default, so a deployment that does not opt in keeps deny-by-default.
* Applies only when the user has no assignments AT ALL, judged on the unfiltered assignment list —
  a user whose roles were removed by the MFA filter is provisioned and must not get baseline access
  in a non-MFA session.
* A role name that does not exist is dropped with a logged error rather than handed to Casbin as a
  grouping line with no policy.
* A default role that itself requires MFA is not applied to a non-MFA session.
"""

from unittest.mock import MagicMock, patch

import pytest

from backend.backend.handlers.authz import CasbinEnforcerService
import backend.backend.handlers.authz as authz


def _service(user_id="fed-user", mfa=False):
    """A CasbinEnforcerService without __init__ (no DynamoDB), wired for policy-text building."""
    svc = CasbinEnforcerService.__new__(CasbinEnforcerService)
    svc._user_id = user_id
    svc._mfaEnabled = mfa
    svc._roles_table_name = "roles-table"
    svc._user_roles_table_name = "user-roles-table"
    return svc


def _wire(svc, assigned=None, roles_in_table=None, non_mfa_role_names=None):
    """Stub the three table reads the policy builder performs.

    assigned            -> the user's own role assignments (unfiltered)
    roles_in_table      -> {roleName: roleRecord} for the by-name role lookup
    non_mfa_role_names  -> role names that do not require MFA
    """
    roles_in_table = roles_in_table or {}
    svc._read_current_user_roles_from_table = MagicMock(return_value=list(assigned or []))
    svc._read_mfaNotRequired_roles_from_table = MagicMock(
        return_value=[{"roleName": name} for name in (non_mfa_role_names or [])]
    )
    svc._read_role_from_table = MagicMock(side_effect=lambda name: roles_in_table.get(name))
    # No policies: the test asserts on the role grouping lines, not on the constraint rules.
    svc._read_policies_batch_optimized = MagicMock(return_value=[])
    return svc


def _roles_granted(policy_text):
    """Role names appearing as Casbin grouping lines in the generated policy."""
    names = []
    for line in policy_text.splitlines():
        if line.startswith("g, "):
            names.append(line.split("'role::")[1].rstrip("'"))
    return names


@pytest.mark.unit
class TestDisabledByDefault:
    def test_no_default_role_when_unset(self):
        # The shipped default is an empty string: an unprovisioned user gets no roles, which leaves
        # the caller with the deny-all policy.
        svc = _wire(_service(), assigned=[], roles_in_table={"basicReadOnly": {"roleName": "basicReadOnly"}})
        with patch.object(authz, "DEFAULT_ROLE_NAME", ""):
            policy = svc._create_policy_text_helper()
        assert _roles_granted(policy) == []

    def test_empty_value_is_not_matched_against_role_names(self):
        # A blank name must not be looked up or matched; the guard is on the value, not the table.
        svc = _wire(_service(), assigned=[], roles_in_table={"": {"roleName": ""}})
        with patch.object(authz, "DEFAULT_ROLE_NAME", ""):
            policy = svc._create_policy_text_helper()
        assert _roles_granted(policy) == []
        svc._read_role_from_table.assert_not_called()


@pytest.mark.unit
class TestAppliedOnlyToUnprovisionedUsers:
    def test_granted_when_the_user_has_no_assignments(self):
        svc = _wire(
            _service(),
            assigned=[],
            roles_in_table={"basicReadOnly": {"roleName": "basicReadOnly"}},
            non_mfa_role_names=["basicReadOnly"],
        )
        with patch.object(authz, "DEFAULT_ROLE_NAME", "basicReadOnly"):
            policy = svc._create_policy_text_helper()
        assert _roles_granted(policy) == ["basicReadOnly"]

    def test_not_added_alongside_an_existing_assignment(self):
        # A provisioned user's own roles decide; the default may only ever widen an unprovisioned
        # identity's access, never an assigned user's.
        svc = _wire(
            _service(),
            assigned=[{"userId": "fed-user", "roleName": "assetViewer"}],
            roles_in_table={"basicReadOnly": {"roleName": "basicReadOnly"}},
            non_mfa_role_names=["assetViewer", "basicReadOnly"],
        )
        with patch.object(authz, "DEFAULT_ROLE_NAME", "basicReadOnly"):
            policy = svc._create_policy_text_helper()
        assert _roles_granted(policy) == ["assetViewer"]

    def test_not_granted_when_the_users_roles_were_filtered_out_by_mfa(self):
        # The regression this guards: the user IS provisioned, but every assigned role requires MFA
        # and the session has none. Deciding "has no roles" from the FILTERED list would hand them
        # baseline access here, defeating mfaRequired on the role they actually hold.
        svc = _wire(
            _service(mfa=False),
            assigned=[{"userId": "fed-user", "roleName": "adminMfa"}],
            roles_in_table={"basicReadOnly": {"roleName": "basicReadOnly"}},
            non_mfa_role_names=["basicReadOnly"],  # adminMfa requires MFA, so it is absent
        )
        with patch.object(authz, "DEFAULT_ROLE_NAME", "basicReadOnly"):
            policy = svc._create_policy_text_helper()
        assert _roles_granted(policy) == []


@pytest.mark.unit
class TestMissingRoleIsDroppedNotFabricated:
    def test_nonexistent_role_is_dropped_with_an_error(self):
        svc = _wire(_service(), assigned=[], roles_in_table={}, non_mfa_role_names=[])
        with patch.object(authz, "DEFAULT_ROLE_NAME", "typoRole"), patch.object(
            authz.logger, "error"
        ) as logged_error:
            policy = svc._create_policy_text_helper()

        assert _roles_granted(policy) == []
        assert "typoRole" not in policy
        logged_error.assert_called_once()
        assert "typoRole" in logged_error.call_args.args[0]

    def test_nonexistent_role_is_dropped_for_an_mfa_session_too(self):
        # The MFA path performed no existence check at all, so this is the case that would have put
        # a role with no policy into Casbin.
        svc = _wire(_service(mfa=True), assigned=[], roles_in_table={})
        with patch.object(authz, "DEFAULT_ROLE_NAME", "typoRole"), patch.object(
            authz.logger, "error"
        ) as logged_error:
            policy = svc._create_policy_text_helper()

        assert _roles_granted(policy) == []
        logged_error.assert_called_once()

    def test_a_read_failure_drops_the_role_rather_than_granting_it(self):
        svc = _service()
        svc._read_current_user_roles_from_table = MagicMock(return_value=[])
        svc._read_mfaNotRequired_roles_from_table = MagicMock(return_value=[])
        svc._read_policies_batch_optimized = MagicMock(return_value=[])
        svc._read_role_from_table = MagicMock(side_effect=Exception("throttled"))

        with patch.object(authz, "DEFAULT_ROLE_NAME", "basicReadOnly"):
            with pytest.raises(Exception):
                # The lookup itself raising is a failure of the enforcer, not a silent grant.
                svc._create_policy_text_helper()


@pytest.mark.unit
class TestMfaRequirementOnTheDefaultRole:
    def test_mfa_required_default_role_is_not_applied_without_mfa(self):
        svc = _wire(
            _service(mfa=False),
            assigned=[],
            roles_in_table={"privileged": {"roleName": "privileged", "mfaRequired": True}},
            non_mfa_role_names=[],
        )
        with patch.object(authz, "DEFAULT_ROLE_NAME", "privileged"), patch.object(
            authz.logger, "warning"
        ) as logged_warning:
            policy = svc._create_policy_text_helper()

        assert _roles_granted(policy) == []
        # The builder warns about other things too (e.g. an empty policy set), so assert on the
        # message that matters rather than the call count.
        messages = [str(call.args[0]) for call in logged_warning.call_args_list if call.args]
        assert any("privileged" in m for m in messages), messages

    def test_mfa_required_default_role_is_applied_with_mfa(self):
        svc = _wire(
            _service(mfa=True),
            assigned=[],
            roles_in_table={"privileged": {"roleName": "privileged", "mfaRequired": True}},
        )
        with patch.object(authz, "DEFAULT_ROLE_NAME", "privileged"):
            policy = svc._create_policy_text_helper()
        assert _roles_granted(policy) == ["privileged"]

    def test_absent_mfa_flag_counts_as_not_required(self):
        # The roles-table filter treats a missing mfaRequired as "not required"; the default-role
        # check must agree, or a plain role would be rejected for a plain session.
        svc = _wire(
            _service(mfa=False),
            assigned=[],
            roles_in_table={"basicReadOnly": {"roleName": "basicReadOnly"}},
            non_mfa_role_names=["basicReadOnly"],
        )
        with patch.object(authz, "DEFAULT_ROLE_NAME", "basicReadOnly"):
            policy = svc._create_policy_text_helper()
        assert _roles_granted(policy) == ["basicReadOnly"]

    def test_explicit_false_mfa_flag_counts_as_not_required(self):
        svc = _wire(
            _service(mfa=False),
            assigned=[],
            roles_in_table={"basicReadOnly": {"roleName": "basicReadOnly", "mfaRequired": False}},
            non_mfa_role_names=["basicReadOnly"],
        )
        with patch.object(authz, "DEFAULT_ROLE_NAME", "basicReadOnly"):
            policy = svc._create_policy_text_helper()
        assert _roles_granted(policy) == ["basicReadOnly"]
