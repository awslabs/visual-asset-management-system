#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Every openPipeline handler that collects responses must detect a failure by STATUS CODE.

Ten of these handlers accumulate per-input responses in a `responses` list and then scan it for a
failure before returning success. The scan was written as `if "error" in response['body']`, and no
failure route anywhere in any of them appends a body containing an `"error"` key -- each appends
`{"message": ...}` beside a 4xx/5xx `statusCode`. So the scan matched nothing, the loop was dead code,
and execution fell through to the success return, which dereferences `sfn_response`. That name is
unbound whenever the failure happened before `sfn.start_execution` assigned it, so the handler raised
`UnboundLocalError` in place of returning the 500 it had already built.

The consequence is a wrong DIAGNOSTIC rather than a hung workflow: `abort_external_workflow` has
already reported the task token by that point, and the raise sets `FunctionError`, which makes the
nested caller report it a second time (harmlessly -- a duplicate against a failed token raises
`TaskDoesNotExist`, which is logged). What an operator reads is `UnboundLocalError: sfn_response`
instead of the intended cause.

Written as one cross-pipeline rule rather than ten per-pipeline tests because these handlers are
copies of one another: the defect spread by copying, so the guard has to cover a pipeline that has not
been written yet. This is source analysis rather than invocation because each handler needs a different
event shape and a different set of module-level AWS clients, and the property is structural.
"""

import ast
import glob
import io
import os

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Every openPipeline handler in the tree. Discovered rather than listed, because a NEW pipeline is
# exactly the case this rule exists for -- but the counts below are asserted, so a glob that stops
# matching fails instead of reporting that all pipelines pass.
_HANDLERS = sorted(
    p.replace("\\", "/")
    for p in glob.glob(
        os.path.join(_REPO_ROOT, "backendPipelines", "**", "lambda", "openPipeline.py"),
        recursive=True,
    )
)

# The number in the tree today, and the number that use the accumulate-then-scan shape. The remaining
# four return their failure response directly and have no loop to get wrong.
_EXPECTED_HANDLERS = 14
_EXPECTED_WITH_SCAN = 10


def _rel(path):
    return path.replace("\\", "/").split("backendPipelines/", 1)[-1]


def _source(path):
    return io.open(path, encoding="utf-8").read()


def _response_scan_tests(tree):
    """The `if` test of every `for ... in responses:` loop in the module."""
    tests = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.For):
            continue
        if not (isinstance(node.iter, ast.Name) and node.iter.id == "responses"):
            continue
        for inner in node.body:
            if isinstance(inner, ast.If):
                tests.append(inner.test)
    return tests


_WITH_SCAN = [p for p in _HANDLERS if _response_scan_tests(ast.parse(_source(p)))]


def test_the_handler_set_was_found():
    # Control for every parametrized case below: each is driven off one of these lists, so an empty or
    # shrunken list would report success having checked nothing. Both counts are asserted because they
    # move for different reasons -- a new pipeline changes the first, a rewritten failure route the
    # second.
    assert len(_HANDLERS) == _EXPECTED_HANDLERS, [_rel(p) for p in _HANDLERS]
    assert len(_WITH_SCAN) == _EXPECTED_WITH_SCAN, [_rel(p) for p in _WITH_SCAN]


@pytest.mark.parametrize("path", _WITH_SCAN, ids=_rel)
def test_the_response_scan_keys_on_the_status_code(path):
    tests = _response_scan_tests(ast.parse(_source(path)))
    assert tests, _rel(path)
    for test in tests:
        rendered = ast.unparse(test)
        assert "statusCode" in rendered, (
            "%s scans responses with %r, which does not read the status code. Every failure route "
            "appends a body carrying only \"message\", so any other predicate matches nothing and "
            "the success return runs on the failure path." % (_rel(path), rendered)
        )


@pytest.mark.parametrize("path", _HANDLERS, ids=_rel)
def test_no_handler_tests_for_an_error_key_in_the_body(path):
    # The specific dead predicate, forbidden by name. Not redundant with the test above: a handler
    # could satisfy that one with a status-code loop while still carrying a second key-membership
    # test elsewhere, and a handler with no loop at all is not covered by it.
    source = _source(path)
    assert '"error" in response' not in source, _rel(path)
    assert "'error' in response" not in source, _rel(path)


@pytest.mark.parametrize("path", _WITH_SCAN, ids=_rel)
def test_the_scan_has_a_failure_response_to_find(path):
    # Justifies the rule rather than restating it: the loop is only worth keying on the status code
    # because a failure route really does append a 4xx/5xx into this list. A handler whose failure
    # routes all returned directly would make the loop vacuous, and the assertion above would then be
    # guarding nothing.
    tree = ast.parse(_source(path))
    appended_error_codes = []
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "append"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "responses"
        ):
            continue
        for arg in node.args:
            if not isinstance(arg, ast.Dict):
                continue
            for key, value in zip(arg.keys, arg.values):
                if (
                    isinstance(key, ast.Constant)
                    and key.value == "statusCode"
                    and isinstance(value, ast.Constant)
                    and isinstance(value.value, int)
                    and value.value >= 400
                ):
                    appended_error_codes.append(value.value)
    assert appended_error_codes, (
        "%s scans `responses` for a failure but appends no 4xx/5xx into it, so the scan cannot fire."
        % _rel(path)
    )


@pytest.mark.parametrize("path", _WITH_SCAN, ids=_rel)
def test_the_appended_failure_body_carries_no_error_key(path):
    # The measured fact the whole rule rests on, asserted rather than assumed. If a failure body ever
    # did carry an "error" key, the original predicate would have worked and this rule would be
    # unnecessary -- so this is what makes the status-code form required rather than merely tidier.
    tree = ast.parse(_source(path))
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "append"
        ):
            continue
        for arg in node.args:
            if not isinstance(arg, ast.Dict):
                continue
            codes = [
                v.value
                for k, v in zip(arg.keys, arg.values)
                if isinstance(k, ast.Constant)
                and k.value == "statusCode"
                and isinstance(v, ast.Constant)
            ]
            if not any(isinstance(c, int) and c >= 400 for c in codes):
                continue
            for key, value in zip(arg.keys, arg.values):
                if isinstance(key, ast.Constant) and key.value == "body":
                    assert isinstance(value, ast.Dict)
                    body_keys = [
                        k.value for k in value.keys if isinstance(k, ast.Constant)
                    ]
                    assert "error" not in body_keys, (
                        "%s appends a failure body containing an \"error\" key (%r). The rule above "
                        "assumes none does." % (_rel(path), body_keys)
                    )
