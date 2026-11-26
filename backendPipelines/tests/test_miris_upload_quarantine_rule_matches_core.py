# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""The Miris upload pipeline's quarantine check is the core quarantine block's rule.

A pipeline function cannot import ``backend/``, so ``vamsExecuteMirisUpload.is_quarantined`` repeats
the condition ``check_quarantine_block`` in ``backend/backend/common/compliance/quarantineGuard.py``
raises on. This test compares the two conditions, with the row's variable renamed, so a change to
the core rule that is not made in the pipeline too fails here.
"""

import ast
import io
import os

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_CORE = os.path.join(_REPO_ROOT, "backend", "backend", "common", "compliance", "quarantineGuard.py")
_PIPELINE = os.path.join(
    _REPO_ROOT, "backendPipelines", "addon", "conversion", "mirisUpload", "lambda", "vamsExecuteMirisUpload.py"
)


def _function(path, name):
    tree = ast.parse(io.open(path, encoding="utf-8").read())
    found = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name]
    assert len(found) == 1, f"{name} not found once in {path}"
    return found[0]


class _RenameRow(ast.NodeTransformer):
    """Renames the compliance row's variable, whatever each module calls it."""

    def __init__(self, name):
        self.name = name

    def visit_Name(self, node):
        return ast.copy_location(ast.Name(id="row", ctx=node.ctx), node) if node.id == self.name else node


def _normalized(expression, row_name):
    return ast.unparse(_RenameRow(row_name).visit(expression))


def _core_condition():
    """The test of the `if` in check_quarantine_block whose body raises the quarantine refusal."""
    function = _function(_CORE, "check_quarantine_block")
    for node in ast.walk(function):
        if isinstance(node, ast.If) and any(
            isinstance(stmt, ast.Raise)
            and isinstance(stmt.exc, ast.Call)
            and getattr(stmt.exc.func, "id", "") == "QuarantineBlockedResponse"
            for stmt in node.body
        ):
            return node.test
    raise AssertionError("check_quarantine_block raises QuarantineBlockedResponse under no `if`")


def _pipeline_condition():
    """The expression is_quarantined returns, and the name of its parameter."""
    function = _function(_PIPELINE, "is_quarantined")
    returns = [n for n in ast.walk(function) if isinstance(n, ast.Return)]
    assert len(returns) == 1, "is_quarantined returns more than one expression"
    expression = returns[0].value
    # A `bool(...)` wrapper only makes the result a bool; the rule is its argument.
    if isinstance(expression, ast.Call) and getattr(expression.func, "id", "") == "bool":
        expression = expression.args[0]
    return expression, function.args.args[0].arg


def test_the_pipeline_quarantine_check_is_the_core_rule():
    core = _core_condition()
    core_row = sorted({n.id for n in ast.walk(core) if isinstance(n, ast.Name)})
    assert len(core_row) == 1, f"the core condition names more than the row: {core_row}"
    pipeline, pipeline_row = _pipeline_condition()

    assert _normalized(pipeline, pipeline_row) == _normalized(core, core_row[0])


def test_control_the_core_condition_is_the_compliance_state_rule():
    # A refactor that moves the rule out of check_quarantine_block should fail here with a clear
    # reason, not compare two unrelated expressions.
    text = ast.unparse(_core_condition())
    assert "complianceState" in text and "quarantined" in text and "exceptionGranted" in text
