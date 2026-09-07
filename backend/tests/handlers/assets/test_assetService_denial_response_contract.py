# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""assetService: a Tier-2 denial is a 403 at the entry point, and the return-type contract
that makes it one is guarded structurally rather than site by site.

`test_assetService_authz_fail_closed.py` pins the same six Tier-2 sites by calling
`handle_get_request` / `handle_put_request` / `handle_delete_request` directly. Two things that
leaves open, and both reproduce the original defect exactly.

**The denial is never carried across `lambda_handler`.** `AuthorizationDenied` is translated by
the two mutation request handlers; `lambda_handler`'s own except chain has no clause for it, so
a denial that reached it would fall into `except Exception` and be reported as a 500 with an
audit entry typed `internal`. The chain holds today only because every denial-raising call sits
inside a request handler's `try` block — a property nothing asserts, so hoisting one call, or
adding a denial-raising call to `lambda_handler` itself, restores the 500 with the
direct-invocation suite green. These cases drive `lambda_handler`, which is what API Gateway
calls.

**The return-type contract is only checked at the six sites that exist today.** The defect was
structural, not local: a business function signalled refusal by *returning*
`authorization_error()` — an already-finished API response — to a caller that then read
`result.dict()`, and a `dict` has no `.dict()`. A seventh site added later is invisible to a
per-site test list. The guard below reads the module and fails on a returned API response
anywhere other than a request handler, where returning a response is the function's whole job.
Every response builder is covered, not `authorization_error()` alone: the caller reads
`result.dict()` whatever the reason for the early return, so a `general_error()` or a
`validation_error()` returned from the same place is the same `AttributeError` and the same 500.
"""

import ast
import json
import os

import pytest
from unittest.mock import MagicMock, patch

from tests.handlers.assets.test_assetService_authz_fail_closed import (
    _SITE_IDS,
    _SITES,
    _event,
    _wire,
)
from tests.handlers.assets.test_assetService_tag_mutation_authz import (
    _tag_existence_validation_stubbed,
)
from tests.handlers.assets.test_assetService_update_tag_scope import (
    _ASSET_SERVICE_PATH,
    _written_attributes,
)


# Functions whose contract IS an API response, so returning one is correct. Everything else in
# the module returns its response model and must raise instead.
_REQUEST_HANDLERS = frozenset({
    "lambda_handler",
    "handle_get_request",
    "handle_put_request",
    "handle_delete_request",
})

# The response builders in models/common.py. Each returns a finished APIGatewayProxyResponseV2,
# which is a TypedDict -- a plain dict at runtime, with no .dict().
_RESPONSE_BUILDERS = frozenset({
    "authorization_error",
    "validation_error",
    "general_error",
    "internal_error",
    "success",
})


def _invoke_via_lambda_handler(m, method, path_suffix, body, tokens=("u1",)):
    """Drive the module entry point, so a denial must survive lambda_handler's except chain.

    lambda_handler reassigns the module-global claims_and_roles from request_to_claims, so the
    identity is injected there rather than on the module attribute.
    """
    with _tag_existence_validation_stubbed():
        with patch.object(
            m, "request_to_claims", MagicMock(return_value={"tokens": list(tokens)})
        ):
            return m.lambda_handler(_event(method, path_suffix, body), MagicMock())


def _returns_of_an_api_response():
    """Every returned API response in assetService.py as (enclosing function, builder, line).

    Attribution is to the innermost enclosing function, so a nested definition is not credited
    to the function around it.
    """
    source_path = os.path.abspath(_ASSET_SERVICE_PATH)
    with open(source_path, "r", encoding="utf-8") as handle:
        tree = ast.parse(handle.read(), filename=source_path)

    found = []

    def visit(node, enclosing):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                visit(child, child.name)
                continue
            if (
                isinstance(child, ast.Return)
                and isinstance(child.value, ast.Call)
                and isinstance(child.value.func, ast.Name)
                and child.value.func.id in _RESPONSE_BUILDERS
            ):
                found.append((enclosing, child.value.func.id, child.lineno))
            visit(child, enclosing)

    visit(tree, "<module>")
    return found


@pytest.mark.unit
class TestDenialReaches403AtTheEntryPoint:
    """The whole production chain, not just the request handler in the middle of it."""

    @pytest.mark.parametrize(
        "site,handler_name,method,path_suffix,body,archived,action",
        _SITES,
        ids=_SITE_IDS,
    )
    def test_a_tier2_denial_is_403_through_lambda_handler(
        self, site, handler_name, method, path_suffix, body, archived, action
    ):
        m, spy, undo = _wire(archived=archived, denied_actions=(action,))
        try:
            response = _invoke_via_lambda_handler(m, method, path_suffix, body)
        finally:
            undo()

        assert response["statusCode"] == 403, (
            f"{site}: a Tier-2 denial arrived at the caller as {response['statusCode']}. "
            f"AuthorizationDenied derives from Exception, so a denial that escapes its "
            f"request handler is caught by lambda_handler's `except Exception` and reported "
            f"as an internal error: {response}"
        )
        assert json.loads(response["body"])["message"] == "Not Authorized"
        m.asset_table.update_item.assert_not_called()
        m.asset_table.delete_item.assert_not_called()

    @pytest.mark.parametrize(
        "site,handler_name,method,path_suffix,body,archived,action",
        _SITES,
        ids=_SITE_IDS,
    )
    def test_the_denial_was_reached_by_evaluating_the_asset(
        self, site, handler_name, method, path_suffix, body, archived, action
    ):
        """A 403 from Tier 1 would satisfy the case above without exercising Tier 2 at all."""
        m, spy, undo = _wire(archived=archived, denied_actions=(action,))
        try:
            _invoke_via_lambda_handler(m, method, path_suffix, body)
        finally:
            undo()

        assert spy.calls, (
            f"{site}: the request was refused with nothing enforced on the asset, so the 403 "
            f"came from Tier 1 rather than from the object-level check under test"
        )
        refused = spy.calls[-1]
        assert refused["action"] == action, (
            f"{site}: the refusal came from a {refused['action']} evaluation, expected "
            f"{action}. Actions evaluated: {[call['action'] for call in spy.calls]}"
        )
        assert refused["object"]["object__type"] == "asset"


@pytest.mark.unit
class TestPermittedCallerStillSucceedsAtTheEntryPoint:
    """Positive control: a handler that refused everything satisfies every case above."""

    @pytest.mark.parametrize(
        "site,handler_name,method,path_suffix,body,archived,action",
        _SITES,
        ids=_SITE_IDS,
    )
    def test_a_permitted_caller_gets_200_through_lambda_handler(
        self, site, handler_name, method, path_suffix, body, archived, action
    ):
        m, spy, undo = _wire(archived=archived)
        try:
            response = _invoke_via_lambda_handler(m, method, path_suffix, body)
        finally:
            undo()

        assert response["statusCode"] == 200, (
            f"{site}: an authorized request was refused at the entry point: {response}"
        )
        assert spy.calls, f"{site}: the request succeeded with nothing enforced at all"

    def test_a_permitted_mutation_still_writes(self):
        """The 403 cases assert put_item was not called; this shows the write path is live."""
        m, spy, undo = _wire()
        try:
            response = _invoke_via_lambda_handler(
                m, "PUT", "", {"description": "a changed description"}
            )
        finally:
            undo()

        assert response["statusCode"] == 200, response
        assert (
            _written_attributes(m.asset_table)["description"]
            == "a changed description"
        )


@pytest.mark.unit
class TestOnlyARequestHandlerReturnsAResponseForADenial:
    """The structural half: the contract holds for sites that do not exist yet."""

    def test_no_business_function_returns_an_api_response(self):
        offenders = [
            (function_name, builder, line)
            for function_name, builder, line in _returns_of_an_api_response()
            if function_name not in _REQUEST_HANDLERS
        ]
        assert offenders == [], (
            "assetService.py returns a finished API response from a function that is not a "
            "request handler: "
            + ", ".join(
                f"{name}() returns {builder}() at line {line}"
                for name, builder, line in offenders
            )
            + ". A business function's return value is its response model, and its caller "
            "reads result.dict() on it — a response dict has no .dict(), so the early return "
            "becomes an AttributeError, falls into the broad `except Exception` and reaches "
            "the client as a 500 with an audit entry typed `internal`. Raise "
            "AuthorizationDenied for a refusal, or VAMSGeneralErrorResponse otherwise, and "
            "let the request handler translate it."
        )

    def test_the_scan_reads_the_module_it_claims_to_guard(self):
        """Without this, an empty or misdirected scan makes the guard above vacuous."""
        found = _returns_of_an_api_response()
        functions = {name for name, _builder, _line in found}
        builders = {builder for _name, builder, _line in found}
        assert "authorization_error" in builders, (
            f"no `return authorization_error()` was found anywhere in "
            f"{os.path.abspath(_ASSET_SERVICE_PATH)}; the guard is not reading the module"
        )
        assert "lambda_handler" in functions, (
            "lambda_handler does not return a response; it is the outermost function, so its "
            "Tier-1 refusal has nowhere to be translated and must be a returned response. Its "
            "absence means the scan is resolving the wrong module."
        )
        assert "<module>" not in functions, (
            "a returned response was attributed to module scope, so the enclosing-function "
            "attribution the guard relies on is broken"
        )
