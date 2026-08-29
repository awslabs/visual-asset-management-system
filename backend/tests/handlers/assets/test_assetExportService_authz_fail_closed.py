# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""FIX-004 sibling copy: assetExportService.py must fail CLOSED with no identity.

``get_asset_with_permissions`` (assetExportService.py:146) is the third copy of the
shared Tier-2 helper -- the same function body lives in assetVersions.py and
assetFiles.py. ``request_to_claims`` returns an empty token list when the event has
no ``requestContext.authorizer``, or when the authorizer context carries none of the
six principal claim keys, and the copy in this module gates its ``enforce()`` call
behind an explicit empty-token deny.

Both halves are asserted. The tokenless call must be refused while the enforcer is
driven to ALLOW, so nothing but the guard itself can produce the denial; and the same
call with a token must still return the asset dict unchanged, because
``handle_post_export`` uses this helper as the root-asset gate for every export and a
guard written against the wrong condition turns the whole export route into an error
for every real user.

``process_asset_batch`` (assetExportService.py:707) is covered as a regression guard
rather than a defect. Its shape --
``casbin_enforcer = CasbinEnforcer(...) if len(tokens) > 0 else None`` followed by
``if casbin_enforcer and casbin_enforcer.enforce(...)`` -- is the list-filtering
exception in backend/CLAUDE.md Rule 4: fail-closed by construction, because an empty
token list leaves the enforcer ``None`` and the ``and`` short-circuits every asset
into the unauthorized bucket. The tests below pin that behaviour so a later cleanup
that drops the ``and casbin_enforcer`` guard, or the ``else None`` ternary, is caught.
"""

import importlib.util
import os
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

# Env vars assetExportService requires at import time. Seeded so every resource name
# resolves from an override and the module load issues no SSM call.
os.environ.setdefault("ASSET_STORAGE_TABLE_NAME", "test-asset-table")
os.environ.setdefault("ASSET_VERSIONS_STORAGE_TABLE_NAME", "test-asset-versions-table")
os.environ.setdefault("ASSET_FILE_VERSIONS_STORAGE_TABLE_NAME", "test-asset-file-versions-table")
os.environ.setdefault("ASSET_FILE_METADATA_STORAGE_TABLE_NAME", "test-asset-file-metadata-table")
os.environ.setdefault("FILE_ATTRIBUTE_STORAGE_TABLE_NAME", "test-file-attribute-table")
os.environ.setdefault("ASSET_LINKS_STORAGE_TABLE_V2_NAME", "test-asset-links-v2-table")
os.environ.setdefault("ASSET_LINKS_METADATA_STORAGE_TABLE_NAME", "test-asset-links-metadata-table")
os.environ.setdefault("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "test-s3-buckets-table")
os.environ.setdefault("ASSET_LINKS_FUNCTION_NAME", "test-asset-links-function")
os.environ.setdefault("PRESIGNED_URL_TIMEOUT_SECONDS", "3600")

_ASSET_EXPORT_SOURCE = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "backend", "handlers", "assets",
    "assetExportService.py"
)

# Reuse the real claims resolver and the REST event builder from the assetFiles suite so
# all three copies of the fail-open finding are driven through the same event shape and
# the same request_to_claims implementation the deployed Lambda runs.
from tests.handlers.assets.test_assetFiles_authz_fail_closed import (  # noqa: E402
    _real_request_to_claims,
    _rest_event,
)

_DB = "db1"
_ASSET = "asset-1"
_OTHER_ASSET = "asset-2"
_EXPORT_PATH = f"/database/{_DB}/assets/{_ASSET}/export"

_cached_module = None


def _load_real_common_dynamodb():
    """The real common.dynamodb, loaded by path (it is pure: boto3 types only)."""
    path = os.path.join(
        os.path.dirname(__file__), "..", "..", "..", "backend", "common", "dynamodb.py"
    )
    spec = importlib.util.spec_from_file_location(
        "real_common_dynamodb_for_assetexport", os.path.abspath(path)
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_asset_export_service():
    """Load the real assetExportService module by file path with boto3 stubbed.

    The mock `handlers` package the root conftest registers shadows the real package,
    so the module is loaded from its file path instead.
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

    # The mock common.dynamodb the root conftest installs does not define the helper
    # assetExportService imports at module level. Add it for the load only, bound to the
    # real implementation so nothing behaviourally relevant is faked.
    dynamodb_mod = sys.modules.get("common.dynamodb")
    added_attrs = []
    if dynamodb_mod is not None:
        real_dynamodb = _load_real_common_dynamodb()
        for attr in ("query_all_items",):
            if not hasattr(dynamodb_mod, attr):
                setattr(dynamodb_mod, attr, getattr(real_dynamodb, attr))
                added_attrs.append(attr)

    try:
        with patch("boto3.client", return_value=MagicMock()), patch(
            "boto3.resource", return_value=MagicMock()
        ):
            spec = importlib.util.spec_from_file_location(
                "assetExportService_fail_closed_under_test", os.path.abspath(_ASSET_EXPORT_SOURCE)
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


def _asset(asset_id=_ASSET):
    return {
        "databaseId": _DB, "assetId": asset_id, "assetName": "N",
        "bucketId": "bucket-1", "assetLocation": {"Key": f"{asset_id}/"},
        "currentVersionId": "1",
    }


def _enforcer(api_allowed=True, object_allowed=True, enforce_side_effect=None):
    """A CasbinEnforcer stand-in with both tiers set independently."""
    enforcer = MagicMock()
    enforcer.return_value.enforceAPI.return_value = api_allowed
    if enforce_side_effect is not None:
        enforcer.return_value.enforce.side_effect = enforce_side_effect
    else:
        enforcer.return_value.enforce.return_value = object_allowed
    return enforcer


@pytest.mark.unit
class TestTier2GetAssetWithPermissions:
    """FIX-004 sibling copy -- the Tier-2 helper at assetExportService.py:146."""

    def test_empty_tokens_denied_even_when_the_enforcer_would_allow(self):
        """FIX-004: an empty token list must deny rather than return the asset.

        The enforcer is driven to ALLOW and asserted never to be constructed, so the
        only thing that can produce this denial is the empty-token guard itself. With
        the guard absent, the permissive enforcer returns the asset and this fails.
        """
        m = _load_asset_export_service()
        table = MagicMock()
        table.get_item.return_value = {"Item": _asset()}
        allowing = _enforcer()

        with patch.object(m, "asset_table", table), \
                patch.object(m, "CasbinEnforcer", allowing):
            with pytest.raises(m.VAMSGeneralErrorResponse):
                m.get_asset_with_permissions(_DB, _ASSET, "GET", {"tokens": []})

        allowing.assert_not_called()

    def test_permitted_caller_gets_unchanged_asset_shape(self):
        """Control: the allowed path must keep returning the full asset dict.

        handle_post_export uses this helper as the root-asset gate for the export
        route, and process_asset_batch reads bucketId/assetLocation off the same
        record, so the permitted return shape is part of the contract. "Denies
        correctly" is satisfied by a function that denies everyone.
        """
        m = _load_asset_export_service()
        table = MagicMock()
        table.get_item.return_value = {"Item": _asset()}
        allowing = _enforcer()

        with patch.object(m, "asset_table", table), \
                patch.object(m, "CasbinEnforcer", allowing):
            result = m.get_asset_with_permissions(_DB, _ASSET, "GET", {"tokens": ["alice"]})

        assert result["assetId"] == _ASSET
        assert result["bucketId"] == "bucket-1"
        assert result["assetLocation"] == {"Key": f"{_ASSET}/"}
        assert result["object__type"] == "asset"
        allowing.return_value.enforce.assert_called_once()
        # The object type annotation must reach the enforcer: the ABAC rules key on it,
        # so an enforce() call on an unannotated record evaluates the wrong policy.
        assert allowing.return_value.enforce.call_args[0][0]["object__type"] == "asset"

    def test_denying_enforcer_still_raises(self):
        """Control: the pre-existing Casbin deny path must keep raising.

        Distinguishes the new empty-token deny from the object-level deny; with only
        one raise assertion you cannot tell which branch fired.
        """
        m = _load_asset_export_service()
        table = MagicMock()
        table.get_item.return_value = {"Item": _asset()}
        denying = _enforcer(object_allowed=False)

        with patch.object(m, "asset_table", table), \
                patch.object(m, "CasbinEnforcer", denying):
            with pytest.raises(m.VAMSGeneralErrorResponse):
                m.get_asset_with_permissions(_DB, _ASSET, "GET", {"tokens": ["alice"]})

        denying.return_value.enforce.assert_called_once()

    def test_missing_asset_raises_before_any_enforcement(self):
        """Control: a missing asset raises without consulting an enforcer.

        All three denial paths in this helper raise the same exception type, so
        without this the not-found case is indistinguishable from a denial and a
        regression that answered "unauthorized" for every missing asset would pass.
        """
        m = _load_asset_export_service()
        table = MagicMock()
        table.get_item.return_value = {}
        allowing = _enforcer()

        with patch.object(m, "asset_table", table), \
                patch.object(m, "CasbinEnforcer", allowing):
            with pytest.raises(m.VAMSGeneralErrorResponse):
                m.get_asset_with_permissions(_DB, _ASSET, "GET", {"tokens": ["alice"]})

        allowing.assert_not_called()


def _run_process_asset_batch(identifiers, claims_and_roles, enforcer):
    """Run process_asset_batch offline. Returns (result, spies).

    Only batch_get_assets and the per-asset I/O helpers are stubbed, so the
    authorization filter under test is the module's own code. The spies stand in for
    the data gathering an authorized asset triggers, so "not called" proves no asset
    content was read rather than merely that a list came back.
    """
    m = _load_asset_export_service()
    details = {f"{a['databaseId']}:{a['assetId']}": _asset(a["assetId"]) for a in identifiers}
    bucket_spy = MagicMock(return_value={
        "bucketId": "bucket-1", "bucketName": "bucket-name", "baseAssetsPrefix": "prefix/"})
    files_spy = MagicMock(return_value=[])
    metadata_spy = MagicMock(return_value={})

    with patch.object(m, "batch_get_assets", MagicMock(return_value=details)), \
            patch.object(m, "CasbinEnforcer", enforcer), \
            patch.object(m, "get_default_bucket_details", bucket_spy), \
            patch.object(m, "list_s3_files", files_spy), \
            patch.object(m, "get_asset_metadata", metadata_spy), \
            patch.object(m, "get_asset_version_info", MagicMock(return_value=None)), \
            patch.object(m, "get_asset_file_versions", MagicMock(return_value=None)):
        result = m.process_asset_batch(identifiers, m.AssetExportRequestModel(), claims_and_roles)

    return result, {"bucket": bucket_spy, "files": files_spy, "metadata": metadata_spy}


def _by_asset_id(result):
    """Index the batch result by asset id -- the thread pool does not preserve order."""
    return {entry.get("assetid", entry.get("assetId")): entry for entry in result}


@pytest.mark.unit
class TestProcessAssetBatchFailsClosedByConstruction:
    """Regression guard for the list-filtering shape at assetExportService.py:707.

    Not a defect: the ``and casbin_enforcer`` guard makes an empty token list deny
    every asset. These tests fail if that guard or the ``else None`` ternary is
    removed -- dropping the guard raises AttributeError on ``None.enforce``, and
    constructing the enforcer unconditionally authorizes the tokenless caller.
    """

    def test_empty_tokens_authorize_no_asset(self):
        """An empty token list must yield only unauthorized markers and read nothing.

        The result is not an empty list: each denied asset contributes an
        ``unauthorizedAsset`` stub carrying its identifiers and no content. What must
        be absent is asset data -- files, metadata, bucket details.
        """
        identifiers = [{"databaseId": _DB, "assetId": _ASSET, "isRoot": True}]
        allowing = _enforcer()

        result, spies = _run_process_asset_batch(identifiers, {"tokens": []}, allowing)

        assert len(result) == 1
        assert result[0] == {"assetId": _ASSET, "databaseId": _DB, "unauthorizedAsset": True}
        # The enforcer is never constructed, which is what the `else None` ternary buys.
        allowing.assert_not_called()
        spies["bucket"].assert_not_called()
        spies["files"].assert_not_called()
        spies["metadata"].assert_not_called()

    def test_permitted_caller_still_gets_asset_content(self):
        """Control: a token plus a permitting enforcer must still export the asset.

        The over-tightening catcher for the batch path -- a filter that denies
        everyone satisfies the test above while returning an empty export.
        """
        identifiers = [{"databaseId": _DB, "assetId": _ASSET, "isRoot": True}]
        allowing = _enforcer()

        result, spies = _run_process_asset_batch(identifiers, {"tokens": ["alice"]}, allowing)

        assert len(result) == 1, f"expected one exported asset, got {result}"
        assert result[0]["assetid"] == _ASSET
        assert "unauthorizedAsset" not in result[0]
        assert result[0]["files"] == []
        assert result[0]["is_root_lookup_asset"] is True
        spies["bucket"].assert_called_once_with("bucket-1")
        spies["files"].assert_called_once()

    def test_filter_discriminates_per_asset(self):
        """Sensitivity control: the filter must be per-asset, not all-or-nothing.

        With only the all-allow and all-deny cases above, a function keyed on a single
        global decision would pass both.
        """
        identifiers = [
            {"databaseId": _DB, "assetId": _ASSET, "isRoot": True},
            {"databaseId": _DB, "assetId": _OTHER_ASSET, "isRoot": False},
        ]
        selective = _enforcer(
            enforce_side_effect=lambda asset, action: asset["assetId"] == _ASSET)

        result, _ = _run_process_asset_batch(identifiers, {"tokens": ["alice"]}, selective)

        entries = _by_asset_id(result)
        assert set(entries) == {_ASSET, _OTHER_ASSET}
        assert "unauthorizedAsset" not in entries[_ASSET]
        assert entries[_ASSET]["files"] == []
        assert entries[_OTHER_ASSET]["unauthorizedAsset"] is True
        assert "files" not in entries[_OTHER_ASSET]


def _invoke_export(authorizer=None, enforcer=None, asset_item=None, stub_tier2=True):
    """Invoke lambda_handler for POST /export. Returns (response, export_spy).

    ``export_spy`` stands in for every read the export performs, so "not called"
    proves no asset content left the Lambda rather than merely that a status code was
    returned. With ``stub_tier2`` False the real get_asset_with_permissions runs
    against a stubbed asset table, which is what makes a Tier-2 denial travel the real
    lambda_handler error path.
    """
    m = _load_asset_export_service()
    event = _rest_event(
        "POST", _EXPORT_PATH, {"databaseId": _DB, "assetId": _ASSET},
        body={}, authorizer=authorizer,
    )

    asset_table = MagicMock()
    asset_table.get_item.return_value = {"Item": asset_item or _asset()}
    export_spy = MagicMock(return_value={
        "assets": [], "relationships": None, "NextToken": None,
        "totalAssetsInTree": 1, "assetsInThisPage": 0,
    })

    patches = [
        patch.object(m, "request_to_claims", _real_request_to_claims()),
        patch.object(m, "asset_table", asset_table),
        patch.object(m, "export_assets", export_spy),
    ]
    if enforcer is not None:
        patches.append(patch.object(m, "CasbinEnforcer", enforcer))
    if stub_tier2:
        patches.append(patch.object(
            m, "get_asset_with_permissions", MagicMock(return_value=_asset())))

    for p in patches:
        p.start()
    try:
        response = m.lambda_handler(event, MagicMock())
    finally:
        for p in reversed(patches):
            p.stop()
    return response, export_spy


@pytest.mark.unit
class TestExportRouteAuthorization:
    """The export route's Tier-1 gate and the status code a Tier-2 denial carries."""

    def test_no_authorizer_is_denied_and_does_not_export(self):
        """A tokenless request must be refused, and the export never runs."""
        response, export_spy = _invoke_export(authorizer=None, enforcer=_enforcer())

        assert response["statusCode"] == 403, (
            f"tokenless export returned {response['statusCode']}: {response.get('body')}")
        export_spy.assert_not_called()

    def test_authorizer_without_principal_claim_is_denied(self):
        """The external-IdP shape: authorizer present, no recognized principal claim."""
        response, export_spy = _invoke_export(
            authorizer={"principalId": "abc", "custom:someOtherClaim": "alice"},
            enforcer=_enforcer())

        assert response["statusCode"] == 403, (
            f"export with no principal claim returned {response['statusCode']}")
        export_spy.assert_not_called()

    def test_authorized_request_still_served(self):
        """Control: an authorized principal must still reach the export."""
        response, export_spy = _invoke_export(
            authorizer={"cognito:username": "alice"}, enforcer=_enforcer())

        assert response["statusCode"] == 200, (
            f"authorized export returned {response['statusCode']}: {response.get('body')}")
        export_spy.assert_called_once()

    def test_tier1_enforceapi_denial_still_403(self):
        """Control: the pre-existing enforceAPI deny must stay a 403.

        Separates the empty-token deny from the route deny; with one 403 assertion you
        cannot tell which branch fired.
        """
        response, export_spy = _invoke_export(
            authorizer={"cognito:username": "alice"}, enforcer=_enforcer(api_allowed=False))

        assert response["statusCode"] == 403
        export_spy.assert_not_called()

    def test_tier2_denial_is_400_and_does_not_export(self):
        """A Tier-2 denial through the real helper surfaces as 400, with no export.

        The empty-token guard raises the same VAMSGeneralErrorResponse the Casbin
        denial raises, so a Tier-2 denial stays a 400 and no CLI or web consumer has
        to change. Pinning it means a later move to 403 cannot land silently.
        """
        response, export_spy = _invoke_export(
            authorizer={"cognito:username": "alice"},
            enforcer=_enforcer(object_allowed=False),
            stub_tier2=False)

        assert response["statusCode"] == 400, (
            f"Tier-2 export denial returned {response['statusCode']}; if this moved to "
            f"403, update the VamsCLI asset export tests to match")
        export_spy.assert_not_called()

    def test_permitted_caller_passes_through_real_tier2(self):
        """Control: both tiers allowing must reach the export through the real helper.

        The over-tightening catcher for the Tier-2 half -- the empty-token guard sits
        directly above the enforce() call, so a guard written against the wrong
        condition 400s every authorized caller here while the denial tests still pass.
        """
        response, export_spy = _invoke_export(
            authorizer={"cognito:username": "alice"},
            enforcer=_enforcer(),
            stub_tier2=False)

        assert response["statusCode"] == 200, (
            f"authorized export returned {response['statusCode']} through the real "
            f"Tier-2 helper: {response.get('body')}")
        export_spy.assert_called_once()
