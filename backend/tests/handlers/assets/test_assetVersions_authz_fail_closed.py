# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""FIX-004: assetVersions.py must fail CLOSED when the request carries no identity.

The seven asset-version routes gate Tier-1 authorization as
``if len(claims_and_roles["tokens"]) > 0: ... enforceAPI() ...`` with no ``else``,
and the module's own copy of ``get_asset_with_permissions`` (line 189) wraps the
Tier-2 ``enforce()`` the same way. ``request_to_claims`` returns an empty token
list when the event has no ``requestContext.authorizer``, or when the authorizer
context carries none of the six principal claim keys, so such a request creates,
reverts, archives and unarchives asset versions with no authorization evaluated.

assetVersions is wired only to API routes -- there is no cross-lambda invoker --
so failing closed cannot break a `SYSTEM_USER` cross-call (which always carries
one token). The over-tightening risk is the permitted path, which is asserted for
every route alongside each denial.
"""

import ast
import importlib.util
import json
import os
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

# Env vars assetVersions requires at import time.
os.environ.setdefault("ASSET_STORAGE_TABLE_NAME", "test-asset-table")
os.environ.setdefault("ASSET_VERSIONS_STORAGE_TABLE_NAME", "test-asset-versions-table")
os.environ.setdefault("ASSET_FILE_VERSIONS_STORAGE_TABLE_NAME", "test-asset-file-versions-table")
os.environ.setdefault("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "test-s3-buckets-table")
os.environ.setdefault("S3_ASSET_AUXILIARY_BUCKET", "test-aux-bucket")

_ASSET_VERSIONS_SOURCE = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "backend", "handlers", "assets", "assetVersions.py"
)
_ASSET_FILES_SOURCE = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "backend", "handlers", "assets", "assetFiles.py"
)
_ASSET_EXPORT_SOURCE = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "backend", "handlers", "assets",
    "assetExportService.py"
)

# Reuse the fail-open source detector and its controls from the assetFiles suite so
# both CRITICAL fail-open findings are measured by the same rule.
from tests.handlers.assets.test_assetFiles_authz_fail_closed import (  # noqa: E402
    _FAIL_OPEN_SNIPPET,
    _fail_open_authz_functions,
    _real_request_to_claims,
    _rest_event,
)


#######################################################################################
# Rule 4's sanctioned exception, which the base detector does not know about
#######################################################################################
#
# backend/CLAUDE.md Rule 4 names one shape that gates authorization on a non-empty token
# list and is nevertheless fail-closed: LIST FILTERING that appends an item only when
# enforce() passes. The canonical spelling builds the enforcer, or None, from the token
# count and then guards each call with the enforcer itself::
#
#     casbin_enforcer = CasbinEnforcer(claims_and_roles) if len(claims_and_roles["tokens"]) > 0 else None
#     ...
#     if casbin_enforcer and casbin_enforcer.enforce(asset, "GET"):
#         authorized.append(asset)
#
# With no tokens the name is None, the `and` short-circuits, and nothing is appended --
# fail-closed by construction, and the result set is empty rather than unfiltered.
#
# The base detector reports it as an offender, because all it sees is `len(tokens) > 0`
# and `.enforce(` with no explicit empty-token deny. That false positive is what kept the
# xfail on `test_assetexportservice_has_no_fail_open_authz_function` red long after its
# stated subject -- the duplicated fail-open `get_asset_with_permissions` -- was fixed.
#
# HOW A FUTURE READER NOTICES THIS CLASS OF STALENESS. `xfail_strict = true` forces a
# marker off when its fix lands, but only by catching XPASS. A marker whose stated subject
# is fixed while the test still fails FOR AN UNRELATED REASON never XPASSes, so the ratchet
# is structurally blind to it and the marker sits forever asserting a closed defect. The
# only thing that finds it is reading the marker's reason against the source it names. When
# a marker's reason cites a specific function or line, re-read that function before trusting
# the marker; a detector-based assertion is the likeliest place for the reason and the
# failure to have come apart.


def _functions_by_key(source):
    return {
        (node.name, node.lineno): node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _nullable_enforcer_names(func):
    """Names bound to ``Enforcer(...) if len(claims_and_roles["tokens"]) > 0 else None``."""
    names = set()
    for stmt in ast.walk(func):
        if not isinstance(stmt, ast.Assign) or not isinstance(stmt.value, ast.IfExp):
            continue
        if_exp = stmt.value
        if not (isinstance(if_exp.orelse, ast.Constant) and if_exp.orelse.value is None):
            continue
        test_src = ast.unparse(if_exp.test).replace("'", '"').replace(" ", "")
        if 'len(claims_and_roles["tokens"])>0' not in test_src:
            continue
        for target in stmt.targets:
            if isinstance(target, ast.Name):
                names.add(target.id)
    return names


def _enforce_calls(func):
    """Every ``.enforce(...)`` / ``.enforceAPI(...)`` call node in `func`."""
    return [
        node for node in ast.walk(func)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in ("enforce", "enforceAPI")
    ]


def _calls_short_circuited_by(func, names):
    """Call nodes sitting inside an ``and`` whose operands include a bare name from `names`.

    Identity-keyed rather than matched by source text, so two structurally identical calls in
    different branches are told apart.
    """
    protected = set()
    for node in ast.walk(func):
        if not (isinstance(node, ast.BoolOp) and isinstance(node.op, ast.And)):
            continue
        if not any(isinstance(v, ast.Name) and v.id in names for v in node.values):
            continue
        for value in node.values:
            for sub in ast.walk(value):
                if isinstance(sub, ast.Call):
                    protected.add(id(sub))
    return protected


def _uses_fail_closed_list_filtering(func):
    """True when every enforce in `func` is short-circuited by an enforcer-or-None name.

    Deliberately narrow. The presence of the ternary alone is NOT enough -- that would be a
    blanket amnesty for any function that happens to build its enforcer that way while calling
    enforce unguarded somewhere else. Every enforce call in the function has to be both made on
    a nullable enforcer name and guarded by that name, so a single unguarded call keeps the
    function an offender.
    """
    names = _nullable_enforcer_names(func)
    if not names:
        return False

    calls = _enforce_calls(func)
    if not calls:
        return False

    protected = _calls_short_circuited_by(func, names)
    for call in calls:
        receiver = call.func.value
        if not (isinstance(receiver, ast.Name) and receiver.id in names):
            return False
        if id(call) not in protected:
            return False
    return True


def _fail_open_authz_functions_rule4_aware(source):
    """The shared detector, minus Rule 4's sanctioned list-filtering exception.

    A refinement layered on the shared detector rather than an edit to it: the base rule is
    imported by two suites and is correct for the single-resource shape it was written for.
    """
    functions = _functions_by_key(source)
    return [
        (name, lineno)
        for (name, lineno) in _fail_open_authz_functions(source)
        if not _uses_fail_closed_list_filtering(functions[(name, lineno)])
    ]


_LIST_FILTERING_SNIPPET = (
    'def h(items):\n'
    '    casbin_enforcer = CasbinEnforcer(claims_and_roles) '
    'if len(claims_and_roles["tokens"]) > 0 else None\n'
    '    allowed = []\n'
    '    for item in items:\n'
    '        item["object__type"] = "asset"\n'
    '        if casbin_enforcer and casbin_enforcer.enforce(item, "GET"):\n'
    '            allowed.append(item)\n'
    '    return allowed\n'
)
_UNGUARDED_TERNARY_SNIPPET = (
    'def h(items):\n'
    '    casbin_enforcer = CasbinEnforcer(claims_and_roles) '
    'if len(claims_and_roles["tokens"]) > 0 else None\n'
    '    allowed = []\n'
    '    for item in items:\n'
    '        if casbin_enforcer.enforce(item, "GET"):\n'
    '            allowed.append(item)\n'
    '    return allowed\n'
)
_MIXED_SNIPPET = (
    'def h(items, single):\n'
    '    casbin_enforcer = CasbinEnforcer(claims_and_roles) '
    'if len(claims_and_roles["tokens"]) > 0 else None\n'
    '    allowed = []\n'
    '    for item in items:\n'
    '        if casbin_enforcer and casbin_enforcer.enforce(item, "GET"):\n'
    '            allowed.append(item)\n'
    '    if len(claims_and_roles["tokens"]) > 0:\n'
    '        if not CasbinEnforcer(claims_and_roles).enforce(single, "PUT"):\n'
    '            return authorization_error()\n'
    '    return allowed\n'
)

_DB = "db1"
_ASSET = "asset-1"
_VERSION = "2"
_BASE = f"/database/{_DB}/assets/{_ASSET}"

_cached_module = None


def _load_real_common_dynamodb():
    """The real common.dynamodb, loaded by path (it is pure: boto3 types only)."""
    path = os.path.join(
        os.path.dirname(__file__), "..", "..", "..", "backend", "common", "dynamodb.py"
    )
    spec = importlib.util.spec_from_file_location(
        "real_common_dynamodb_for_assetversions", os.path.abspath(path)
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_asset_versions():
    """Load the real assetVersions module by file path with boto3 stubbed.

    The mock `handlers` packages the root conftest registers shadow the real
    package, so the module is loaded from its file path instead.
    """
    global _cached_module
    if _cached_module is not None:
        return _cached_module

    stub_names = ("handlers.authz", "handlers.auth")
    saved = {name: sys.modules.get(name) for name in stub_names}
    authz_stub = types.ModuleType("handlers.authz")
    authz_stub.CasbinEnforcer = MagicMock()
    sys.modules["handlers.authz"] = authz_stub
    auth_stub = types.ModuleType("handlers.auth")
    auth_stub.request_to_claims = MagicMock(return_value={"tokens": ["tester"], "roles": []})
    sys.modules["handlers.auth"] = auth_stub

    # The mock common.dynamodb the root conftest installs does not define the
    # helpers assetVersions imports at module level. Add them for the load only,
    # bound to the real implementations so nothing behaviourally relevant is faked.
    dynamodb_mod = sys.modules.get("common.dynamodb")
    added_attrs = []
    if dynamodb_mod is not None:
        real_dynamodb = _load_real_common_dynamodb()
        for attr in ("to_update_expr", "query_all_items"):
            if not hasattr(dynamodb_mod, attr):
                setattr(dynamodb_mod, attr, getattr(real_dynamodb, attr))
                added_attrs.append(attr)

    try:
        with patch("boto3.client", return_value=MagicMock()), patch(
            "boto3.resource", return_value=MagicMock()
        ):
            spec = importlib.util.spec_from_file_location(
                "assetVersions_fail_closed_under_test", os.path.abspath(_ASSET_VERSIONS_SOURCE)
            )
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
    finally:
        for name, mod in saved.items():
            if mod is not None:
                sys.modules[name] = mod
            else:
                sys.modules.pop(name, None)
        for attr in added_attrs:
            delattr(dynamodb_mod, attr)
    _cached_module = module
    return module


def _asset():
    return {
        "databaseId": _DB, "assetId": _ASSET, "assetName": "N",
        "bucketId": "bucket-1", "assetLocation": {"Key": f"{_ASSET}/"},
        "currentVersionId": "1",
    }


# (id, method, path, pathParameters, body, query, business function attr or None)
# A None business function means the route mutates asset_versions_table directly;
# for those the spy is asset_versions_table.update_item.
ROUTES = [
    ("createVersion", "POST", f"{_BASE}/createVersion",
     {"databaseId": _DB, "assetId": _ASSET},
     {"useLatestFiles": True, "comment": "c"}, None, "create_asset_version"),
    ("revertAssetVersion", "POST", f"{_BASE}/revertAssetVersion/{_VERSION}",
     {"databaseId": _DB, "assetId": _ASSET, "assetVersionId": _VERSION},
     {"comment": "c"}, None, "revert_asset_version"),
    ("getVersions", "GET", f"{_BASE}/getVersions",
     {"databaseId": _DB, "assetId": _ASSET}, None, {}, "get_asset_versions"),
    ("getVersion", "GET", f"{_BASE}/getVersion/{_VERSION}",
     {"databaseId": _DB, "assetId": _ASSET, "assetVersionId": _VERSION}, None, None,
     "get_asset_version_details"),
    ("updateAssetVersion", "PUT", f"{_BASE}/assetversions/{_VERSION}",
     {"databaseId": _DB, "assetId": _ASSET, "assetVersionId": _VERSION},
     {"comment": "edited"}, None, None),
    ("archiveAssetVersion", "POST", f"{_BASE}/assetversions/{_VERSION}/archive",
     {"databaseId": _DB, "assetId": _ASSET, "assetVersionId": _VERSION}, None, None, None),
    ("unarchiveAssetVersion", "POST", f"{_BASE}/assetversions/{_VERSION}/unarchive",
     {"databaseId": _DB, "assetId": _ASSET, "assetVersionId": _VERSION}, None, None, None),
]

_ROUTE_IDS = [r[0] for r in ROUTES]


def _invoke(route, authorizer=None, enforcer=None):
    """Invoke lambda_handler for one route with every write path stubbed.

    Returns (response, spy). The spy stands in for the route's write: either its
    business function or the versions table's update_item, so "not called" proves
    nothing was mutated rather than merely that a status code was returned.
    """
    _id, method, path, path_params, body, query, business_attr = route
    m = _load_asset_versions()
    event = _rest_event(method, path, path_params, body=body, query=query, authorizer=authorizer)

    versions_table = MagicMock()
    business_stub = MagicMock()
    business_stub.return_value.dict.return_value = {"success": True}

    patches = [
        patch.object(m, "request_to_claims", _real_request_to_claims()),
        # The suite's mock safeLogger has no .debug(); lambda_handler calls it, which
        # would surface as an unrelated 500. Test-harness only.
        patch.object(m, "logger", MagicMock()),
        patch.object(m, "asset_versions_table", versions_table),
        patch.object(m, "get_asset_with_permissions", MagicMock(return_value=_asset())),
        patch.object(m, "validate_asset_version_exists", MagicMock(return_value=True)),
        patch.object(m, "to_update_expr", MagicMock(return_value=({}, {}, "SET a = :a"))),
    ]
    if enforcer is not None:
        patches.append(patch.object(m, "CasbinEnforcer", enforcer))
    if business_attr is not None:
        patches.append(patch.object(m, business_attr, business_stub))

    for p in patches:
        p.start()
    try:
        response = m.lambda_handler(event, MagicMock())
    finally:
        for p in reversed(patches):
            p.stop()

    spy = business_stub if business_attr is not None else versions_table.update_item
    return response, spy


@pytest.mark.unit
class TestTier1FailsClosedOnEmptyTokens:
    """FIX-004 -- the tokenless request must be refused on all 7 version routes."""

    @pytest.mark.parametrize("route", ROUTES, ids=_ROUTE_IDS)
    def test_no_authorizer_is_denied_and_does_not_mutate(self, route):
        """FIX-004: no authorizer context -> 403, and the route's write never runs."""
        response, spy = _invoke(route, authorizer=None)

        assert response["statusCode"] == 403, (
            f"route {route[0]} returned {response['statusCode']} for a tokenless request: "
            f"{response.get('body')}"
        )
        spy.assert_not_called()

    @pytest.mark.parametrize("route", ROUTES, ids=_ROUTE_IDS)
    def test_authorizer_without_principal_claim_is_denied(self, route):
        """FIX-004: the external-IdP shape -- authorizer present, no recognized principal."""
        response, spy = _invoke(route, authorizer={"principalId": "abc",
                                                   "custom:someOtherClaim": "alice"})

        assert response["statusCode"] == 403, (
            f"route {route[0]} returned {response['statusCode']} for a request whose "
            f"authorizer context carries no principal claim"
        )
        spy.assert_not_called()

    @pytest.mark.parametrize("route", ROUTES, ids=_ROUTE_IDS)
    def test_authorized_request_still_served(self, route):
        """Control: an authorized principal must still be served on every route.

        The over-tightening catcher -- a pre-set flag left `False`, or placed before
        the wrong block, turns all seven routes into 403s.
        """
        response, spy = _invoke(route, authorizer={"cognito:username": "alice"})

        assert response["statusCode"] == 200, (
            f"route {route[0]} returned {response['statusCode']} for an authorized "
            f"request: {response.get('body')}"
        )
        spy.assert_called_once()

    @pytest.mark.parametrize("route", ROUTES, ids=_ROUTE_IDS)
    def test_tier1_enforceapi_denial_still_403(self, route):
        """Control: the pre-existing enforceAPI deny must stay a 403.

        Distinguishes the new empty-token deny from the enforceAPI deny; with only
        one 403 assertion you cannot tell which branch fired.
        """
        denying = MagicMock()
        denying.return_value.enforceAPI.return_value = False
        response, spy = _invoke(route, authorizer={"cognito:username": "alice"},
                                enforcer=denying)

        assert response["statusCode"] == 403
        spy.assert_not_called()


@pytest.mark.unit
class TestTier2GetAssetWithPermissions:
    """FIX-004 -- the module's own copy of the Tier-2 helper (assetVersions.py:189)."""

    def test_empty_tokens_denied(self):
        """FIX-004: an empty token list must deny rather than return the asset."""
        m = _load_asset_versions()
        table = MagicMock()
        table.get_item.return_value = {"Item": _asset()}

        with patch.object(m, "asset_table", table):
            with pytest.raises(Exception):
                m.get_asset_with_permissions(_DB, _ASSET, "GET", {"tokens": []})

    def test_permitted_caller_gets_unchanged_asset_shape(self):
        """Control: the allowed path must keep returning the full asset dict.

        get_asset_s3_location reads bucketId/assetLocation off this return value, so
        the permitted return shape is part of the contract.
        """
        m = _load_asset_versions()
        table = MagicMock()
        table.get_item.return_value = {"Item": _asset()}
        allowing = MagicMock()
        allowing.return_value.enforce.return_value = True

        with patch.object(m, "asset_table", table), \
                patch.object(m, "CasbinEnforcer", allowing):
            result = m.get_asset_with_permissions(_DB, _ASSET, "GET", {"tokens": ["alice"]})

        assert result["bucketId"] == "bucket-1"
        assert result["assetLocation"] == {"Key": f"{_ASSET}/"}
        assert result["object__type"] == "asset"

    def test_denying_enforcer_still_raises(self):
        """Control: the pre-existing deny path must keep raising."""
        m = _load_asset_versions()
        table = MagicMock()
        table.get_item.return_value = {"Item": _asset()}
        denying = MagicMock()
        denying.return_value.enforce.return_value = False

        with patch.object(m, "asset_table", table), \
                patch.object(m, "CasbinEnforcer", denying):
            with pytest.raises(Exception):
                m.get_asset_with_permissions(_DB, _ASSET, "GET", {"tokens": ["alice"]})


def _invoke_with_real_tier2(enforcer, authorizer):
    """Invoke the archive route with the REAL get_asset_with_permissions in place.

    The shared ``_invoke`` stubs the Tier-2 helper out, so it can never observe how
    a Tier-2 denial is surfaced to the caller. Here only the storage layer is faked,
    which makes the helper's raise travel the real lambda_handler error path.
    """
    m = _load_asset_versions()
    route = next(r for r in ROUTES if r[0] == "archiveAssetVersion")
    _id, method, path, path_params, body, query, _business = route
    event = _rest_event(method, path, path_params, body=body, query=query,
                        authorizer=authorizer)

    asset_table = MagicMock()
    asset_table.get_item.return_value = {"Item": _asset()}
    versions_table = MagicMock()

    patches = [
        patch.object(m, "request_to_claims", _real_request_to_claims()),
        patch.object(m, "logger", MagicMock()),
        patch.object(m, "asset_table", asset_table),
        patch.object(m, "asset_versions_table", versions_table),
        patch.object(m, "validate_asset_version_exists", MagicMock(return_value=True)),
        patch.object(m, "CasbinEnforcer", enforcer),
    ]
    for p in patches:
        p.start()
    try:
        response = m.lambda_handler(event, MagicMock())
    finally:
        for p in reversed(patches):
            p.stop()
    return response, versions_table.update_item


@pytest.mark.unit
class TestTier2StatusCodeThroughRoute:
    """FIX-004 status-code pin -- what a Tier-2 denial looks like to the caller.

    The empty-token guard added to ``get_asset_with_permissions`` raises the same
    ``VAMSGeneralErrorResponse`` the pre-existing Casbin denial raises, so a Tier-2
    denial stays a 400 and no CLI/web consumer has to change. Pinning it here means
    a later refactor to 403 cannot land silently.
    """

    def test_tier2_denial_is_400_and_does_not_mutate(self):
        """A Tier-2 denial surfaces as 400 via general_error, with no write."""
        denying = MagicMock()
        denying.return_value.enforceAPI.return_value = True
        denying.return_value.enforce.return_value = False

        response, update_item = _invoke_with_real_tier2(
            denying, authorizer={"cognito:username": "alice"})

        assert response["statusCode"] == 400, (
            f"Tier-2 denial returned {response['statusCode']}; if this fix intentionally "
            f"moved to 403, update the VamsCLI asset-version tests to match"
        )
        update_item.assert_not_called()

    def test_permitted_caller_passes_through_real_tier2(self):
        """Positive control: both tiers allowing -> 200 and the write runs.

        This is the over-tightening catcher for the Tier-2 half specifically -- the
        empty-token guard sits directly above the ``enforce()`` call, so a guard
        written against the wrong condition 400s every authorized caller here while
        the denial test above still passes.
        """
        allowing = MagicMock()
        allowing.return_value.enforceAPI.return_value = True
        allowing.return_value.enforce.return_value = True

        response, update_item = _invoke_with_real_tier2(
            allowing, authorizer={"cognito:username": "alice"})

        assert response["statusCode"] == 200, (
            f"an authorized caller returned {response['statusCode']} through the real "
            f"Tier-2 helper: {response.get('body')}"
        )
        update_item.assert_called_once()


@pytest.mark.unit
class TestNoFailOpenTokenGuardsRemain:
    """FIX-004 shape guard, plus the sibling-copy exposure the fix's scope must state.

    The identical fail-open ``get_asset_with_permissions`` is duplicated in
    assetFiles.py and assetExportService.py. Fixing only one module leaves the
    others open, and a test written against the wrong module object passes
    vacuously -- so each file is asserted by name.
    """

    def test_assetversions_has_no_fail_open_authz_function(self):
        """FIX-004: pinned at zero so converting 6 of 7 sites still fails."""
        with open(os.path.abspath(_ASSET_VERSIONS_SOURCE), "r", encoding="utf-8") as handle:
            offenders = _fail_open_authz_functions(handle.read())
        assert offenders == [], (
            f"assetVersions.py functions {offenders} gate authorization on a non-empty "
            f"token list with no empty-token deny"
        )

    def test_assetexportservice_has_no_fail_open_authz_function(self):
        """FIX-004 sibling copy. The xfail this carried was stale in substance.

        Its stated subject -- a duplicated fail-open ``get_asset_with_permissions`` in
        assetExportService.py -- is fixed: that function now denies on an empty token list
        (assetExportService.py around 167-170). The test kept failing because the BASE detector
        false-positives on ``process_asset_batch``, which uses Rule 4's sanctioned list-filtering
        idiom. Measured with the Rule 4-aware detector, which is what the assertion always meant.
        """
        with open(os.path.abspath(_ASSET_EXPORT_SOURCE), "r", encoding="utf-8") as handle:
            offenders = _fail_open_authz_functions_rule4_aware(handle.read())
        assert offenders == [], (
            f"assetExportService.py functions {offenders} gate authorization on a "
            f"non-empty token list with no empty-token deny"
        )

    def test_assetversions_has_no_fail_open_authz_function_under_the_refined_rule_either(self):
        """The refinement must not have loosened the file the fix is actually about."""
        with open(os.path.abspath(_ASSET_VERSIONS_SOURCE), "r", encoding="utf-8") as handle:
            offenders = _fail_open_authz_functions_rule4_aware(handle.read())
        assert offenders == []

    def test_detector_is_not_vacuous_on_these_files(self):
        """Control: the detector must actually parse and inspect these modules.

        Without this, a path typo or a parse failure would make the assertions above
        look like clean results.
        """
        for source_path in (_ASSET_VERSIONS_SOURCE, _ASSET_FILES_SOURCE, _ASSET_EXPORT_SOURCE):
            with open(os.path.abspath(source_path), "r", encoding="utf-8") as handle:
                tree = ast.parse(handle.read())
            functions = [n for n in ast.walk(tree)
                         if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
            assert functions, f"{source_path} parsed to zero functions"


@pytest.mark.unit
class TestTheRule4AwareRefinement:
    """The refinement's own controls: it must exempt exactly the sanctioned idiom.

    A refinement that exempted anything more would silence the finding it is layered on, and a
    refinement that exempted nothing would leave the marker's failure in place.
    """

    def test_the_base_detector_flags_the_sanctioned_idiom(self):
        """The premise: this is a FALSE POSITIVE, not an absent report.

        If the base rule ever learns the idiom, the refinement becomes a no-op and this test is
        the one that says so -- rather than the exemption quietly covering for a real fail-open.
        """
        assert len(_fail_open_authz_functions(_LIST_FILTERING_SNIPPET)) == 1

    def test_the_refined_detector_accepts_the_sanctioned_idiom(self):
        assert _fail_open_authz_functions_rule4_aware(_LIST_FILTERING_SNIPPET) == []

    def test_the_refined_detector_still_flags_a_real_fail_open(self):
        """The exemption must not be a blanket amnesty."""
        assert len(_fail_open_authz_functions_rule4_aware(_FAIL_OPEN_SNIPPET)) == 1

    def test_an_unguarded_call_on_a_nullable_enforcer_is_still_flagged(self):
        """The ternary alone is not the property; the short-circuit guard is.

        Without the guard the empty-token case raises AttributeError on None rather than
        denying -- a 500, not a refusal -- so it is not the sanctioned shape.
        """
        assert len(_fail_open_authz_functions_rule4_aware(_UNGUARDED_TERNARY_SNIPPET)) == 1

    def test_a_function_that_mixes_the_idiom_with_a_fail_open_single_check_is_flagged(self):
        """The realistic regression: list filtering plus one un-guarded single-resource check.

        Exempting a function because SOME of its enforces are guarded is how a real fail-open
        would hide behind this refinement.
        """
        assert len(_fail_open_authz_functions_rule4_aware(_MIXED_SNIPPET)) == 1

    def test_the_refinement_is_what_clears_assetexportservice(self):
        """Attribution: the marker came off because of this rule, not because of a rewrite.

        The base detector must still report the export module, and the refined one must not, and
        the difference must be exactly the list-filtering function. Pinned by NAME rather than by
        count so the fix cannot be credited to some other function disappearing.
        """
        with open(os.path.abspath(_ASSET_EXPORT_SOURCE), "r", encoding="utf-8") as handle:
            source = handle.read()

        base = _fail_open_authz_functions(source)
        refined = _fail_open_authz_functions_rule4_aware(source)
        exempted = {name for name, _ in base} - {name for name, _ in refined}

        assert exempted == {"process_asset_batch"}, (
            f"the refinement exempts {exempted or 'nothing'} in assetExportService.py; it is "
            f"meant to exempt exactly the list-filtering function. Base report: {base}"
        )

    def test_the_marker_subject_really_is_fixed(self):
        """The substance of the stale reason, read off the source it named.

        The marker said the duplicated fail-open ``get_asset_with_permissions`` still lived in
        assetExportService.py. It denies on an empty token list now, so the reason was false;
        this asserts that directly rather than through the detector, because the detector is the
        thing that was wrong.
        """
        with open(os.path.abspath(_ASSET_EXPORT_SOURCE), "r", encoding="utf-8") as handle:
            source = handle.read()
        func = next(
            node for node in ast.walk(ast.parse(source))
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "get_asset_with_permissions"
        )
        body = ast.unparse(func).replace("'", '"').replace(" ", "")
        assert 'len(claims_and_roles["tokens"])==0' in body, (
            "assetExportService.get_asset_with_permissions no longer carries an explicit "
            "empty-token deny; the removed xfail was describing a live defect after all"
        )
        assert not _uses_fail_closed_list_filtering(func), (
            "this function is not the list-filtering shape, so the assertion above is the "
            "right way to measure it"
        )
