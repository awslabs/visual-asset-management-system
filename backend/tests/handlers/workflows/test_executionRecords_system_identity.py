# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The execution record's system-attribution fallback is the reserved identity, not a variant.

Guards S2-BACKEND-154 (and its duplicate S2-BACKEND-175): the main execution row fell back to the
literal ``"system"`` when no user id was supplied. Handlers compare against the exact string
``SYSTEM_USER``, which is what is seeded into the user and user-roles tables, so ``"system"`` matches
no user, carries no admin role, and is invisible to the ``triggeredByUserId`` filter -- while the
record's own model documents the correct value as the default.

The fallback is unreachable through the API today (Tier-1 auth denies an empty token list), which is
exactly why a unit assertion is the right instrument: there is no request that can demonstrate it.
"""

import pytest

from backend.backend.common.workflows import executionRecords as er

SYSTEM_USER = "SYSTEM_USER"


def _record(triggered_by_user_id):
    return er.build_workflow_execution_record(
        execution_id="E1", workflow_database_id="wdb", workflow_id="wf",
        workflow_arn="arn:aws:states:us-east-1:111122223333:stateMachine:vams-wf",
        workflow_execution_arn="arn:aws:states:us-east-1:111122223333:execution:vams-wf:E1",
        execution_start_date="2026-01-01T00:00:00Z", execution_status="RUNNING",
        triggered_by_user_id=triggered_by_user_id, trigger_type="manual",
        execution_log_group_arn="")


@pytest.mark.unit
class TestTriggeredByUserIdFallback:

    @pytest.mark.parametrize("unresolved", ["", None])
    def test_an_unresolved_user_falls_back_to_the_reserved_identity(self, unresolved):
        assert _record(unresolved)["triggeredByUserId"] == SYSTEM_USER

    def test_the_forbidden_variant_is_never_written(self):
        assert _record("")["triggeredByUserId"] != "system"

    def test_a_supplied_user_is_preserved(self):
        """Negative control: the fallback must not overwrite a real caller."""
        assert _record("user@example.com")["triggeredByUserId"] == "user@example.com"

    def test_the_fallback_matches_the_models_documented_default(self):
        """The row builder and the model must agree, or a filter written against one misses rows
        written by the other."""
        from backend.backend.models.executions import WorkflowExecutionRecord
        declared = WorkflowExecutionRecord.__fields__["triggeredByUserId"].default
        assert declared == SYSTEM_USER == _record("")["triggeredByUserId"]
