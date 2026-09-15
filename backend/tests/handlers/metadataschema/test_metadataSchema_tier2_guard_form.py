# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""S2-BACKEND-143, structural arm: the Tier-2 guard SHAPE in metadataSchemaService.

`test_metadataSchema_tier2_fail_closed.py` covers the four single-resource sites that exist today by
calling them with an empty token list. That is the property, but it is enumerated -- a fifth
single-resource `enforce()` added to this module later can reintroduce the forbidden shape without
turning any of those tests red, because no test names it.

This file asserts the shape instead of the site, over the module's own AST:

* No `enforce()` in a denying form (`if not ...enforce(...)`) sits inside an
  `if len(claims_and_roles["tokens"]) > 0:` wrapper. With no denying `else` that is the shape
  backend/CLAUDE.md Rule 4 forbids by name: with an empty token list the whole block is skipped and
  execution falls through to the mutation or the response.
* Each of the four gated functions carries an explicit `len(claims_and_roles["tokens"]) == 0` denial,
  and it comes BEFORE the `enforce()` it protects. A denial placed after the check satisfies a
  presence test while leaving the hole open.

The first check does not look at an `else` branch, so `if len(tokens) > 0: ...enforce()... else:
deny` is reported as well. That is deliberate and stricter than Rule 4's literal prohibition: the
positive shape the Tier-2 single-resource bullet prescribes is the separate pre-check, not a wrapper
carrying a denying `else`, and the second check requires the pre-check of these four functions in any
case. `test_the_check_also_reports_a_wrapper_with_a_denying_else` pins that, so a future edit reading
a red result knows the reported shape is the wrapper itself.

Rule 4's documented exception -- list filtering, which APPENDS an item only when `enforce()` passes
and so yields an empty page rather than an unfiltered one -- reads the enforce verdict in its
positive form, so it is outside what the first check looks at. `test_the_check_spares_the_list_filtering_shape`
pins that, and pins that the module really does contain such sites, so the exemption is not vacuous.

Every check here is paired with a synthetic source fragment proving the check can fire. A structural
assertion that cannot fail is the failure mode of this kind of test: it reports a clean module
whether or not it is capable of reporting anything at all.
"""

import ast
import inspect
import re

import pytest

from backend.backend.handlers.metadataschema import metadataSchemaService as svc

# `ast.unparse` normalizes quoting and whitespace, so these match the canonical rendering of the
# guard rather than one of its spellings: claims_and_roles["tokens"] arrives here as
# claims_and_roles['tokens'] whichever quotes the source used.
NON_EMPTY_TOKEN_GUARD = re.compile(r"^len\(claims_and_roles\['tokens'\]\)\s*(>|>=|!=)\s")
EMPTY_TOKEN_DENIAL = re.compile(r"^len\(claims_and_roles\['tokens'\]\)\s*==\s*0$")
DENYING_ENFORCE = re.compile(r"^not\s+\S*\.?enforce\(")

GATED_FUNCTIONS = [
    "create_metadata_schema",
    "update_metadata_schema",
    "delete_metadata_schema",
    "handle_get_request",
]


def _module_tree():
    source = inspect.getsource(svc)
    return ast.parse(source)


def _function(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name} is not defined in metadataSchemaService")


def _enforce_wrapped_in_token_guard(tree):
    """Every denying-form `enforce()` that sits inside a non-empty-token wrapper, as (line, test)."""
    wrapped = []
    for outer in ast.walk(tree):
        if not isinstance(outer, ast.If):
            continue
        if not NON_EMPTY_TOKEN_GUARD.match(ast.unparse(outer.test)):
            continue
        for inner in ast.walk(outer):
            if inner is outer or not isinstance(inner, ast.If):
                continue
            if DENYING_ENFORCE.match(ast.unparse(inner.test)):
                wrapped.append((inner.lineno, ast.unparse(inner.test)))
    return wrapped


def _list_filtering_sites(tree):
    """Sites reading the enforce verdict in its positive form inside a token wrapper (Rule 4's
    exception -- these append on success, so empty tokens yield an empty result)."""
    sites = []
    for outer in ast.walk(tree):
        if not isinstance(outer, ast.If):
            continue
        if not NON_EMPTY_TOKEN_GUARD.match(ast.unparse(outer.test)):
            continue
        for inner in ast.walk(outer):
            if inner is outer or not isinstance(inner, ast.If):
                continue
            test = ast.unparse(inner.test)
            if ".enforce(" in test and not DENYING_ENFORCE.match(test):
                sites.append(inner.lineno)
    return sites


def _lines_of(node, pattern):
    return [
        child.lineno
        for child in ast.walk(node)
        if isinstance(child, ast.If) and pattern.match(ast.unparse(child.test))
    ]


# Source fragments the checks are calibrated against. Each is the shape a future edit could
# reintroduce, written out so a check that has stopped working shows up as a green control.
FORBIDDEN_WRAPPER = """
def save_thing(claims_and_roles, item):
    if len(claims_and_roles["tokens"]) > 0:
        casbin_enforcer = CasbinEnforcer(claims_and_roles)
        if not casbin_enforcer.enforce(item, "POST"):
            return authorization_error()

    thing_table.put_item(Item=item)
    return success(body=item)
"""

WRAPPER_WITH_DENYING_ELSE = """
def save_thing(claims_and_roles, item):
    if len(claims_and_roles["tokens"]) > 0:
        casbin_enforcer = CasbinEnforcer(claims_and_roles)
        if not casbin_enforcer.enforce(item, "POST"):
            return authorization_error()
    else:
        return authorization_error()

    thing_table.put_item(Item=item)
    return success(body=item)
"""

LIST_FILTERING = """
def list_things(claims_and_roles, items):
    allowed = []
    for item in items:
        if len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if casbin_enforcer.enforce(item, "GET"):
                allowed.append(item)
    return allowed
"""

NO_DENIAL = """
def save_thing(claims_and_roles, item):
    casbin_enforcer = CasbinEnforcer(claims_and_roles)
    if not casbin_enforcer.enforce(item, "POST"):
        return authorization_error()

    thing_table.put_item(Item=item)
    return success(body=item)
"""

DENIAL_AFTER_ENFORCE = """
def save_thing(claims_and_roles, item):
    casbin_enforcer = CasbinEnforcer(claims_and_roles)
    if not casbin_enforcer.enforce(item, "POST"):
        return authorization_error()
    if len(claims_and_roles["tokens"]) == 0:
        return authorization_error()

    thing_table.put_item(Item=item)
    return success(body=item)
"""


@pytest.mark.unit
class TestNoTokenGuardWrapsASingleResourceEnforce:
    def test_module_has_no_wrapped_enforce_site(self):
        wrapped = _enforce_wrapped_in_token_guard(_module_tree())

        assert wrapped == [], (
            "a single-resource enforce() is gated on a non-empty token list with no denying else: "
            f"{wrapped}"
        )

    def test_the_check_fires_on_the_forbidden_shape(self):
        """Positive control: the exact shape Rule 4 names must be reported."""
        wrapped = _enforce_wrapped_in_token_guard(ast.parse(FORBIDDEN_WRAPPER))

        assert len(wrapped) == 1

    def test_the_check_also_reports_a_wrapper_with_a_denying_else(self):
        """The wrapper is reported whether or not it carries a denying `else`: the prescribed shape is
        the separate pre-check, so a red result names the wrapper, not the missing else."""
        wrapped = _enforce_wrapped_in_token_guard(ast.parse(WRAPPER_WITH_DENYING_ELSE))

        assert len(wrapped) == 1

    def test_the_check_spares_the_list_filtering_shape(self):
        """Rule 4's exception must not be reported -- and the module must really contain it, or the
        exemption is asserting nothing."""
        assert _enforce_wrapped_in_token_guard(ast.parse(LIST_FILTERING)) == []
        assert _list_filtering_sites(_module_tree()), (
            "no list-filtering site found; the exemption above no longer covers live code"
        )


@pytest.mark.unit
class TestEveryGatedSiteDeniesOnEmptyTokens:
    @pytest.mark.parametrize("function_name", GATED_FUNCTIONS)
    def test_function_denies_before_it_enforces(self, function_name):
        function = _function(_module_tree(), function_name)
        denials = _lines_of(function, EMPTY_TOKEN_DENIAL)
        enforcements = _lines_of(function, DENYING_ENFORCE)

        assert enforcements, f"{function_name} no longer runs a Tier-2 enforce() at all"
        assert denials, f"{function_name} has no explicit empty-token denial"
        # Order matters: a denial after the check is unreachable for the request it should stop.
        assert min(denials) < min(enforcements), (
            f"{function_name} denies on empty tokens only after enforce() has already run"
        )

    def test_the_check_fires_when_the_denial_is_absent(self):
        """Positive control: an enforce() with no empty-token denial at all."""
        function = _function(ast.parse(NO_DENIAL), "save_thing")

        assert _lines_of(function, DENYING_ENFORCE)
        assert _lines_of(function, EMPTY_TOKEN_DENIAL) == []

    def test_the_check_fires_when_the_denial_trails_the_enforce(self):
        """Positive control for the ordering arm, which a presence-only check would pass."""
        function = _function(ast.parse(DENIAL_AFTER_ENFORCE), "save_thing")
        denials = _lines_of(function, EMPTY_TOKEN_DENIAL)
        enforcements = _lines_of(function, DENYING_ENFORCE)

        assert denials and enforcements
        assert min(denials) > min(enforcements)
