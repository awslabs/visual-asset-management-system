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

import ast
import inspect
import re

import pytest

from backend.backend.common.workflows import executionRecords as er

SYSTEM_USER = "SYSTEM_USER"

# A record key carrying a user identity, e.g. triggeredByUserId / changeUserId.
_USER_ID_KEY = re.compile(r"UserId$", re.IGNORECASE)


def _user_id_entries():
    """Every ``"...UserId": <value>`` entry the module's record builders declare, as (key, value node)
    pairs. Read from the AST rather than the text so a comment that mentions a variant is not mistaken
    for one, and so a builder added later is covered without listing it here."""
    entries = []
    for node in ast.walk(ast.parse(inspect.getsource(er))):
        if not isinstance(node, ast.Dict):
            continue
        for key, value in zip(node.keys, node.values):
            if not (isinstance(key, ast.Constant) and isinstance(key.value, str)):
                continue
            if _USER_ID_KEY.search(key.value):
                entries.append((key.value, value))
    return entries


def _as_identity(node):
    """The identity a fallback expression resolves to, or None when it is not a fixed string -- a
    helper call cannot be read off the source, and is left to the behavioural assertions."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        resolved = getattr(er, node.id, None)
        return resolved if isinstance(resolved, str) else None
    return None


def _user_id_fallbacks():
    """The identity each user-id entry falls back to for an empty value, as (key, identity) pairs.
    Covers the ``x or FALLBACK`` and ``x if x else FALLBACK`` spellings and resolves a module-level
    constant, so restating a fallback a different way is still checked rather than skipped."""
    fallbacks = []
    for key, value in _user_id_entries():
        if isinstance(value, ast.BoolOp) and isinstance(value.op, ast.Or):
            node = value.values[-1]
        elif isinstance(value, ast.IfExp):
            node = value.orelse
        else:
            continue
        identity = _as_identity(node)
        if identity is not None:
            fallbacks.append((key, identity))
    return fallbacks


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


@pytest.mark.unit
class TestNoRecordBuilderFallsBackToAVariant:
    """Stated over the module rather than over one call, so a user-identity fallback added to another
    record builder is held to the same identity without this file being revisited."""

    def test_the_module_declares_a_user_id_entry(self):
        """Positive control: the walk finds the entries it is meant to police, so a green result below
        means the identities are right rather than that nothing was inspected. Keyed on the entry
        rather than on the fallback's shape, so writing a correct fallback a different way does not
        read as a violation."""
        assert _user_id_entries(), "found no user-identity record entry to check"

    def test_every_user_id_fallback_is_the_reserved_identity(self):
        wrong = [(k, v) for k, v in _user_id_fallbacks() if v != SYSTEM_USER]
        assert not wrong, f"user-identity fallbacks must be {SYSTEM_USER!r}: {wrong}"

    def test_the_declared_fallback_is_the_one_the_builder_writes(self):
        """Ties the reading to behaviour: the identity read off the source is the identity the built
        record carries, so the assertion above cannot pass on a declaration the code does not use."""
        declared = dict(_user_id_fallbacks())
        if "triggeredByUserId" not in declared:
            pytest.skip("the fallback is not a fixed string; the behavioural assertions cover it")
        assert declared["triggeredByUserId"] == _record("")["triggeredByUserId"]
