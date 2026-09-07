# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Every nested RequestResponse invoke must inspect `FunctionError` (owner question 78, option A).

A `RequestResponse` invoke of a Lambda that RAISED still returns HTTP `StatusCode` 200 -- the failure
is reported out of band, in the `FunctionError` field of the invoke response. A caller that checks only
`StatusCode` therefore reads a failed launch as a successful one, never reports the Step Functions task
token, and the callback task sits until its `taskTimeout`. On the GPU pipelines that timeout is hours,
so a run that failed in its first second displays RUNNING for hours while holding a GPU reservation.

`S4-PIPELINES-064` fixed the five callers someone had listed. Re-derived from source there were
fourteen, and the other nine were disproportionately the GPU pipelines -- the ones where the
consequence is worst. This file is the ratchet that keeps a fifteenth from shipping without it.

**Asserted as a property, not as text.** Thirteen callers carry a byte-identical block, but
`rapidPipelineEKS` inspects `FunctionError` through a richer path: it reads the response `Payload` and
parses the error details out of it. That is a superset of the canonical check and must keep passing, so
matching on the canonical wording would fail a caller that does MORE. What matters is that the
response's `FunctionError` is read on the invoke path.

**Why this lives in `backend/tests/`** rather than beside the pipelines: `backendPipelines/` has no
pytest configuration and is not referenced by any CI workflow, so a ratchet placed there would not run.
The backend suite does run, and this file only reads source text -- it imports nothing from the
pipeline trees, so it carries none of their import-time AWS requirements.
"""

import ast
import os

import pytest

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
_PIPELINES_DIR = os.path.join(_REPO_ROOT, "backendPipelines")


def _pipeline_sources():
    """Every non-test pipeline Python module."""
    out = []
    for dirpath, dirnames, filenames in os.walk(_PIPELINES_DIR):
        dirnames[:] = [d for d in dirnames
                       if d not in ("__pycache__", ".pytest_cache", "tests", "src", "node_modules")]
        for name in filenames:
            if name.endswith(".py"):
                out.append(os.path.join(dirpath, name))
    return sorted(out)


def _performs_synchronous_invoke(tree) -> bool:
    """True when the module CALLS something with `InvocationType='RequestResponse'`.

    Detected from the AST rather than from the text. A substring search for `RequestResponse` also
    matches PROSE: a comment in `rapidPipeline/lambda/constructPipeline.py` explains the contrast with a
    nested `RequestResponse` invoke, and that alone made a file which performs no invoke at all look
    like an unguarded invoker. Measured -- it is what first failed this file's own prose guard.

    A module that relied on boto3's default invocation type without naming it would be missed. Recorded
    rather than guarded: every current call site names it explicitly, and inferring the default would
    classify every asynchronous invoke as synchronous too.
    """
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for kw in node.keywords:
            if kw.arg == "InvocationType" and isinstance(kw.value, ast.Constant) \
                    and kw.value.value == "RequestResponse":
                return True
    return False


def _request_response_invokers():
    """Modules that perform a synchronous Lambda invoke."""
    found = []
    for path in _pipeline_sources():
        with open(path, encoding="utf-8", errors="replace") as handle:
            source = handle.read()
        if "RequestResponse" not in source:
            continue  # cheap pre-filter; the AST check below is what decides
        try:
            tree = ast.parse(source)
        except SyntaxError:  # pragma: no cover - a parse failure is its own, louder problem
            continue
        if _performs_synchronous_invoke(tree):
            found.append((path, source))
    return found


@pytest.mark.unit
class TestRequestResponseInvokersReportAFailedLaunch:
    def test_the_scan_finds_the_invokers_at_all(self):
        """Control. An empty set would make the rule below pass without examining anything.

        The count is deliberately asserted as a floor rather than an exact number: a new pipeline
        legitimately adds one, and pinning the exact total would turn that into a failure here instead
        of in the rule that matters.
        """
        invokers = _request_response_invokers()
        assert len(invokers) >= 14, (
            f"expected at least the 14 known RequestResponse invokers under backendPipelines/, "
            f"found {len(invokers)} -- the walk or the detector is broken, not the product"
        )

    def test_every_request_response_invoker_inspects_function_error(self):
        offenders = []
        for path, source in _request_response_invokers():
            if "FunctionError" not in source:
                offenders.append(os.path.relpath(path, _REPO_ROOT).replace(os.sep, "/"))

        assert offenders == [], (
            "a pipeline performs a synchronous Lambda invoke without inspecting `FunctionError`. The "
            "invoked function can RAISE and the invoke still returns StatusCode 200, so this reads a "
            "failed launch as a success: no task-token failure is sent and the workflow's callback "
            "task blocks until taskTimeout -- hours, on the GPU pipelines. Copy the guard from "
            "backendPipelines/multi/modelOps/lambda/vamsExecuteModelOps.py:\n  " + "\n  ".join(offenders)
        )

    def test_the_check_is_on_the_invoke_result_and_not_merely_the_word(self):
        """Guards against the rule above being satisfied by a comment or a log line.

        `FunctionError` appearing anywhere in the file would pass the substring rule, including in
        prose. This asserts the stronger shape for every invoker: the name is read off a subscript or a
        `.get(...)` of a response object, which is the only way to actually consult it.
        """
        weak = []
        for path, source in _request_response_invokers():
            try:
                tree = ast.parse(source)
            except SyntaxError:  # pragma: no cover - a parse failure is its own, louder problem
                weak.append(f"{os.path.relpath(path, _REPO_ROOT)} (does not parse)")
                continue
            consulted = False
            for node in ast.walk(tree):
                # response['FunctionError'] / response.get('FunctionError') / 'FunctionError' in response
                if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant) \
                        and node.slice.value == "FunctionError":
                    consulted = True
                elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                        and node.func.attr == "get" and node.args \
                        and isinstance(node.args[0], ast.Constant) \
                        and node.args[0].value == "FunctionError":
                    consulted = True
                elif isinstance(node, ast.Compare) and isinstance(node.left, ast.Constant) \
                        and node.left.value == "FunctionError" \
                        and any(isinstance(op, ast.In) for op in node.ops):
                    consulted = True
                if consulted:
                    break
            if not consulted:
                weak.append(os.path.relpath(path, _REPO_ROOT).replace(os.sep, "/"))

        assert weak == [], (
            "`FunctionError` appears in these files but is never read off the invoke response, so the "
            "failed-launch case is still unhandled:\n  " + "\n  ".join(weak)
        )
