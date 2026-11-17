#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0
"""The zip service's reading of the execute handler's launch answers stays tied to that handler.

zipDownloadService removes a job without a FAILED row only for answers the execute handler gives
before start_execution. These tests pin each such text, prefix and status to executeWorkflow.py, so a
reworded refusal there falls back to the safe "a run may exist" branch only after this test is
updated on purpose, and so an answer the handler can also give after the start is never listed.
"""

import ast
import os

import pytest

from backend.tests.handlers.zipDownloads import zipHarness as h

pytestmark = pytest.mark.unit

EXECUTE_WORKFLOW = os.path.join(h.BACKEND, "handlers", "workflows", "executeWorkflow.py")


@pytest.fixture(scope="module")
def svc():
    return h.load_handlers().svc


@pytest.fixture(scope="module")
def tree():
    with open(EXECUTE_WORKFLOW, encoding="utf-8") as f:
        return ast.parse(f.read())


def _function(tree, name):
    return next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name)


def _string_constants(node):
    """Every string literal under node, with implicitly concatenated literals already joined."""
    return {n.value for n in ast.walk(node) if isinstance(n, ast.Constant) and isinstance(n.value, str)}


def _early_return_messages(tree, *function_names):
    """The literal "message" values of the returns in the named functions."""
    found = set()
    for name in function_names:
        for ret in (n for n in ast.walk(_function(tree, name)) if isinstance(n, ast.Return)):
            for d in (n for n in ast.walk(ret) if isinstance(n, ast.Dict)):
                for key, value in zip(d.keys, d.values):
                    if isinstance(key, ast.Constant) and key.value == "message" \
                            and isinstance(value, ast.Constant) and isinstance(value.value, str):
                        found.add(value.value)
    return found


def test_every_listed_pre_start_text_is_an_early_return_of_the_execute_handler(svc, tree):
    early = _early_return_messages(tree, "execute_workflow", "_resolve_and_authorize_pipelines")
    from common.workflows import executionLocks as el
    from common.workflows import systemPipelines as sp
    shared = {sp.SYSTEM_EXECUTION_INVALID_MESSAGE, el.LOCK_CONFLICT_MESSAGE,
              *el.LOCK_CONFLICT_MESSAGE_BY_SCOPE.values()}
    missing = {m for m in svc.PRE_START_REFUSAL_MESSAGES if m not in early and m not in shared}
    assert not missing, f"no longer an early return of execute_workflow: {missing}"


def test_no_listed_pre_start_text_is_also_an_answer_of_the_exception_mapping(svc, tree):
    """lambda_handler's exception arms run after a run may have started: none of their texts may be
    read as 'no run exists'."""
    mapped = _string_constants(_function(tree, "lambda_handler"))
    assert not (svc.PRE_START_REFUSAL_MESSAGES & mapped)
    for prefix in svc.PRE_START_RETRYABLE_PREFIXES + svc.MAY_HAVE_STARTED_RETRYABLE_PREFIXES:
        assert any(text.startswith(prefix) for text in mapped), prefix


def test_the_execute_handler_gives_409_only_for_a_stale_deployment(tree):
    """409 is read as 'nothing started, retry': it must stay the stale-deployment answer, given
    before the start (_ensure_current_deployment) or when Step Functions refused a pruned version."""
    source = ast.unparse(tree)
    assert source.count("status_code=409") == 2
    assert "StateMachineDoesNotExist" in source


def test_the_validation_envelopes_are_early_returns(svc, tree):
    texts = _string_constants(_function(tree, "execute_workflow"))
    for envelope in svc.PRE_START_REFUSAL_ENVELOPES:
        assert envelope in texts
