# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The short-policy warning in ``_create_policy_text_helper`` records the length, not the policy.

A user whose roles carry no constraint rules gets a policy made only of
``g, user::<id>, 'role::<name>'`` grouping lines, which is under the warning's 100-character
threshold. The warning goes to the function log, so it must not carry the caller id or the role
names.
"""

from unittest.mock import MagicMock, patch

import pytest

from backend.backend.handlers.authz import CasbinEnforcerService
import backend.backend.handlers.authz as authz

USER_ID = "short-policy-user"
ROLE_NAME = "role-without-rules"


def _service():
    """A CasbinEnforcerService without __init__ (no DynamoDB): one role, no constraint rules."""
    svc = CasbinEnforcerService.__new__(CasbinEnforcerService)
    svc._user_id = USER_ID
    svc._mfaEnabled = True
    svc._read_current_user_roles_from_table = MagicMock(
        return_value=[{"userId": USER_ID, "roleName": ROLE_NAME}]
    )
    svc._read_policies_batch_optimized = MagicMock(return_value=[])
    return svc


@pytest.mark.unit
class TestShortPolicyWarning:
    def test_warning_names_neither_the_user_nor_the_role(self):
        mock_logger = MagicMock()
        with patch.object(authz, "logger", mock_logger):
            policy_text = _service()._create_policy_text_helper()

        # Premise: the policy is the one grouping line, short enough to trigger the warning
        assert policy_text == f"g, user::{USER_ID}, 'role::{ROLE_NAME}'"
        assert mock_logger.warning.called

        messages = " ".join(str(call) for call in mock_logger.warning.call_args_list)
        assert USER_ID not in messages
        assert ROLE_NAME not in messages
        assert f"{len(policy_text)} characters" in messages
