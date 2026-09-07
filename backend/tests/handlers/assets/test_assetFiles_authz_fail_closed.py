# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""FIX-003: assetFiles.py must fail CLOSED when the request carries no identity.

All twelve file routes gated Tier-1 authorization as
``if len(claims_and_roles["tokens"]) > 0: ... enforceAPI() ...`` with no ``else``,
and both Tier-2 helpers (``get_asset_with_permissions``,
``validate_cross_asset_permissions``) did the same. ``request_to_claims`` yields an
empty token list whenever the event has no ``requestContext.authorizer`` -- or has
one whose claim map carries none of the six principal keys, which is what happens
on an external-IdP deployment with an unexpected claim shape. Such a request skipped
both tiers entirely and reached ``handle_delete_file`` / ``handle_archive_file`` /
``handle_move_file`` with no authorization at all.

Both halves are asserted for every route: the tokenless request must be refused
AND must not reach the business function, while the same request with a valid
authorizer context must still be served. A fix that denies on empty ``roles``
rather than empty ``tokens`` satisfies the first half and 403s every real user.

FIX-043 also lands in ``get_file_info`` in this module and is covered at the end of
this file: the ``isFolder`` guard on the only block that computes
``currentAssetVersionFileVersionMismatch`` was inverted, so the flag stayed at its
model default of ``None`` for every file and only folders (which carry no version
snapshot) could reach the comparison. With that block live, its DynamoDB snapshot
lookup key has to be the stored ``fileKey`` form -- relative to the asset prefix,
no leading slash -- and not the caller's raw path, which the file-operation APIs
accept in several spellings (``/dir/f.txt``, ``dir/f.txt``, ``assetId/dir/f.txt``).
"""

import ast
import importlib.util
import json
import os
from unittest.mock import MagicMock, patch

import pytest

# Env vars assetFiles requires at import time.
os.environ.setdefault("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "test-s3-buckets-table")
os.environ.setdefault("ASSET_STORAGE_TABLE_NAME", "test-asset-table")
os.environ.setdefault("ASSET_FILE_VERSIONS_STORAGE_TABLE_NAME", "test-asset-file-versions-table")

# Module-level import ensures the real backend.backend.handlers.assets package is
# populated in sys.modules before the root conftest's autouse fixture runs.
from backend.backend.handlers.assets import assetFiles  # noqa: F401,E402

_ASSET_FILES_SOURCE = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "backend", "handlers", "assets", "assetFiles.py"
)
_REAL_AUTH_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "backend", "handlers", "auth", "__init__.py"
)

_DB = "db1"
_ASSET = "asset-1"
_BASE = f"/database/{_DB}/assets/{_ASSET}"


def _load_asset_files():
    from backend.backend.handlers.assets import assetFiles as af
    return af


def _real_request_to_claims():
    """The real request_to_claims, loaded by path.

    The mock ``handlers.auth`` the root conftest installs always returns a
    populated token list, so the empty-token branch is unreachable through it.
    Loading the real resolver keeps the test honest: the empty token list is
    produced by the same code the deployed Lambda runs.
    """
    spec = importlib.util.spec_from_file_location(
        "real_handlers_auth_for_assetfiles", os.path.abspath(_REAL_AUTH_PATH)
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.request_to_claims


def _rest_event(method, path, path_params, body=None, query=None, authorizer=None):
    """A REST (v1) proxy event. Omitting `authorizer` is the tokenless case."""
    request_context = {"identity": {"sourceIp": "10.0.0.1"}}
    if authorizer is not None:
        request_context["authorizer"] = authorizer
    event = {
        "path": path,
        "httpMethod": method,
        "requestContext": request_context,
        "pathParameters": dict(path_params),
        "queryStringParameters": dict(query) if query else None,
        "headers": {},
    }
    if body is not None:
        event["body"] = json.dumps(body)
    return event


# (id, method, path, pathParameters, body, queryStringParameters, business function attr)
ROUTES = [
    ("listFiles", "GET", f"{_BASE}/listFiles",
     {"databaseId": _DB, "assetId": _ASSET}, None, None, "list_asset_files"),
    ("fileInfo", "GET", f"{_BASE}/fileInfo",
     {"databaseId": _DB, "assetId": _ASSET}, None, {"filePath": "/f.txt"}, "get_file_info"),
    ("moveFile", "POST", f"{_BASE}/moveFile",
     {"databaseId": _DB, "assetId": _ASSET},
     {"sourcePath": "/a.txt", "destinationPath": "/b.txt"}, None, "move_file"),
    ("copyFile", "POST", f"{_BASE}/copyFile",
     {"databaseId": _DB, "assetId": _ASSET},
     {"sourcePath": "/a.txt", "destinationPath": "/b.txt"}, None, "copy_file"),
    ("unarchiveFile", "POST", f"{_BASE}/unarchiveFile",
     {"databaseId": _DB, "assetId": _ASSET}, {"filePath": "/a.txt"}, None, "unarchive_file"),
    ("createFolder", "POST", f"{_BASE}/createFolder",
     {"databaseId": _DB, "assetId": _ASSET}, {"relativeKey": "newdir/"}, None, "create_folder"),
    ("archiveFile", "DELETE", f"{_BASE}/archiveFile",
     {"databaseId": _DB, "assetId": _ASSET}, {"filePath": "/a.txt"}, None, "archive_file"),
    ("deleteFile", "DELETE", f"{_BASE}/deleteFile",
     {"databaseId": _DB, "assetId": _ASSET},
     {"filePath": "/a.txt", "confirmPermanentDelete": True}, None, "delete_file"),
    ("deleteAssetPreview", "DELETE", f"{_BASE}/deleteAssetPreview",
     {"databaseId": _DB, "assetId": _ASSET}, None, None, "delete_asset_preview"),
    ("deleteAuxiliaryPreviewAssetFiles", "DELETE", f"{_BASE}/deleteAuxiliaryPreviewAssetFiles",
     {"databaseId": _DB, "assetId": _ASSET}, {"filePath": "/a.txt"}, None,
     "delete_auxiliary_preview_asset_files"),
    ("revertFileVersion", "POST", f"{_BASE}/revertFileVersion/v1",
     {"databaseId": _DB, "assetId": _ASSET, "versionId": "v1"},
     {"filePath": "/a.txt"}, None, "revert_file_version"),
    ("setPrimaryFile", "PUT", f"{_BASE}/setPrimaryFile",
     {"databaseId": _DB, "assetId": _ASSET},
     {"filePath": "/a.txt", "primaryType": "primary"}, None, "set_primary_file"),
]

_ROUTE_IDS = [r[0] for r in ROUTES]


def _invoke(route, authorizer=None):
    """Invoke lambda_handler for one route with the business function stubbed.

    Returns (response, business_function_mock). Stubbing the business function is
    what makes the assertion meaningful: it stands in for every S3/DynamoDB write
    the route would perform, so "not called" proves nothing was mutated.
    """
    _id, method, path, path_params, body, query, business_attr = route
    af = _load_asset_files()
    event = _rest_event(method, path, path_params, body=body, query=query, authorizer=authorizer)

    stub = MagicMock()
    stub.return_value.dict.return_value = {"success": True}
    with patch.object(af, "request_to_claims", _real_request_to_claims()), \
            patch.object(af, business_attr, stub):
        response = af.lambda_handler(event, MagicMock())
    return response, stub


@pytest.mark.unit
class TestTier1FailsClosedOnEmptyTokens:
    """FIX-003 -- the tokenless request must be refused on every file route."""

    @pytest.mark.parametrize("route", ROUTES, ids=_ROUTE_IDS)
    def test_no_authorizer_is_denied_and_does_not_mutate(self, route):
        """FIX-003: no authorizer context -> 403, and the route body never runs."""
        response, business = _invoke(route, authorizer=None)

        assert response["statusCode"] == 403, (
            f"route {route[0]} returned {response['statusCode']} for a tokenless request: "
            f"{response.get('body')}"
        )
        business.assert_not_called()

    @pytest.mark.parametrize("route", ROUTES, ids=_ROUTE_IDS)
    def test_authorizer_without_principal_claim_is_denied(self, route):
        """FIX-003: the external-IdP shape -- an authorizer with no recognized principal.

        This is the branch a federated deployment actually hits: the authorizer
        context exists but carries none of vams:tokens / cognito:username /
        username / sub / upn / email.
        """
        response, business = _invoke(route, authorizer={"principalId": "abc",
                                                        "custom:someOtherClaim": "alice"})

        assert response["statusCode"] == 403, (
            f"route {route[0]} returned {response['statusCode']} for a request whose "
            f"authorizer context carries no principal claim"
        )
        business.assert_not_called()

    @pytest.mark.parametrize("route", ROUTES, ids=_ROUTE_IDS)
    def test_authorized_request_still_served(self, route):
        """Control: a real principal with permissive constraints must still be served.

        This is the over-tightening catcher. A fix that denies on an empty `roles`
        list (rather than an empty `tokens` list) passes both denial tests above
        and 403s every user whose roles are resolved from DynamoDB instead of the
        token, which is every user on a Cognito deployment.
        """
        response, business = _invoke(route, authorizer={"cognito:username": "alice"})

        assert response["statusCode"] == 200, (
            f"route {route[0]} returned {response['statusCode']} for an authorized "
            f"request: {response.get('body')}"
        )
        business.assert_called_once()

    @pytest.mark.parametrize("route", ROUTES[:3], ids=_ROUTE_IDS[:3])
    def test_tier1_enforceapi_denial_still_403(self, route):
        """Control: distinguishes the new empty-token deny from the existing enforceAPI deny.

        With a real principal and an enforcer that refuses the API route, the
        response must already be 403 today. Without this, a single 403 assertion
        cannot tell which branch fired.
        """
        _id, method, path, path_params, body, query, business_attr = route
        af = _load_asset_files()
        event = _rest_event(method, path, path_params, body=body, query=query,
                            authorizer={"cognito:username": "alice"})

        denying_enforcer = MagicMock()
        denying_enforcer.return_value.enforceAPI.return_value = False
        stub = MagicMock()
        stub.return_value.dict.return_value = {"success": True}
        with patch.object(af, "request_to_claims", _real_request_to_claims()), \
                patch.object(af, "CasbinEnforcer", denying_enforcer), \
                patch.object(af, business_attr, stub):
            response = af.lambda_handler(event, MagicMock())

        assert response["statusCode"] == 403
        stub.assert_not_called()


def _asset():
    return {
        "databaseId": _DB, "assetId": _ASSET, "assetName": "N",
        "bucketId": "bucket-1", "assetLocation": {"Key": f"{_ASSET}/"},
    }


@pytest.mark.unit
class TestTier2GetAssetWithPermissions:
    """FIX-003 -- the shared Tier-2 helper (assetFiles.py:235)."""

    def test_empty_tokens_denied(self):
        """FIX-003: an empty token list must deny, not return the asset."""
        af = _load_asset_files()
        table = MagicMock()
        table.get_item.return_value = {"Item": _asset()}

        with patch.object(af, "asset_table", table):
            with pytest.raises(Exception):
                af.get_asset_with_permissions(_DB, _ASSET, "GET", {"tokens": []})

    def test_permitted_caller_gets_unchanged_asset_shape(self):
        """Control: the allowed path must keep returning the full asset dict.

        Downstream helpers (get_asset_s3_location) read bucketId/assetLocation off
        this return value, so "denies correctly" is not sufficient -- the permitted
        return shape has to be pinned too.
        """
        af = _load_asset_files()
        table = MagicMock()
        table.get_item.return_value = {"Item": _asset()}
        allowing = MagicMock()
        allowing.return_value.enforce.return_value = True

        with patch.object(af, "asset_table", table), \
                patch.object(af, "CasbinEnforcer", allowing):
            result = af.get_asset_with_permissions(_DB, _ASSET, "GET", {"tokens": ["alice"]})

        assert result["bucketId"] == "bucket-1"
        assert result["assetLocation"] == {"Key": f"{_ASSET}/"}
        assert result["object__type"] == "asset"

    def test_denying_enforcer_still_raises(self):
        """Control: the pre-existing deny path must keep raising."""
        af = _load_asset_files()
        table = MagicMock()
        table.get_item.return_value = {"Item": _asset()}
        denying = MagicMock()
        denying.return_value.enforce.return_value = False

        with patch.object(af, "asset_table", table), \
                patch.object(af, "CasbinEnforcer", denying):
            with pytest.raises(Exception):
                af.get_asset_with_permissions(_DB, _ASSET, "GET", {"tokens": ["alice"]})


@pytest.mark.unit
class TestTier2CrossAssetPermissions:
    """FIX-003 -- validate_cross_asset_permissions (assetFiles.py:878) and its caller.

    The helper RETURNS False on empty tokens rather than raising, and copy_file
    discarded that return value, so the copy proceeded. Fixing only the helper
    without checking the caller would have left cross-asset copy unauthorized, so
    both the helper's result and the caller's handling of it are asserted here.
    """

    def test_helper_returns_false_on_empty_tokens(self):
        """Baseline: the helper's own empty-token result, so the caller test is unambiguous."""
        af = _load_asset_files()
        assert af.validate_cross_asset_permissions(_asset(), _asset(), {"tokens": []}) is False

    def test_caller_treats_cross_asset_denial_as_deny(self):
        """FIX-003: copy_file must act on a False from validate_cross_asset_permissions.

        Driven with a real principal so the assertion isolates the discarded return
        value. (With an empty token list the deny comes from the Tier-2 helper
        ``get_asset_with_permissions`` further up copy_file, which would satisfy the
        assertion without the caller ever acting on the cross-asset result -- adding
        a raise inside the helper without checking this caller would leave the deny
        path still not enforced.)
        """
        af = _load_asset_files()
        copy_spy = MagicMock(return_value=True)

        with patch.object(af, "get_asset_with_permissions", side_effect=lambda *a, **k: _asset()), \
                patch.object(af, "validate_cross_asset_permissions", return_value=False), \
                patch.object(af, "get_asset_s3_location",
                             return_value=("asset-bucket", f"{_ASSET}/")), \
                patch.object(af, "s3_client", MagicMock()), \
                patch.object(af, "check_destination_file_exists", return_value=False), \
                patch.object(af, "copy_s3_object", copy_spy), \
                patch.object(af, "process_preview_files", return_value=[]), \
                patch.object(af, "copy_auxiliary_files", MagicMock()), \
                patch.object(af, "_copy_file_metadata_to_destination", return_value=0), \
                patch.object(af, "send_subscription_email", MagicMock()):
            with pytest.raises(Exception):
                af.copy_file(_DB, _ASSET, "/a.txt", "/a.txt", "other-asset",
                             claims_and_roles={"tokens": ["alice"]})

        copy_spy.assert_not_called()

    def test_caller_proceeds_when_cross_asset_check_allows(self):
        """Control: a True from the helper must still let the copy through.

        Without this, a fix that unconditionally refuses cross-asset copies would
        satisfy the test above.
        """
        af = _load_asset_files()
        copy_spy = MagicMock(return_value=True)

        with patch.object(af, "get_asset_with_permissions", side_effect=lambda *a, **k: _asset()), \
                patch.object(af, "validate_cross_asset_permissions", return_value=True), \
                patch.object(af, "get_asset_s3_location",
                             return_value=("asset-bucket", f"{_ASSET}/")), \
                patch.object(af, "s3_client", MagicMock()), \
                patch.object(af, "check_destination_file_exists", return_value=False), \
                patch.object(af, "copy_s3_object", copy_spy), \
                patch.object(af, "process_preview_files", return_value=[]), \
                patch.object(af, "copy_auxiliary_files", MagicMock()), \
                patch.object(af, "_copy_file_metadata_to_destination", return_value=0), \
                patch.object(af, "send_subscription_email", MagicMock()):
            response = af.copy_file(_DB, _ASSET, "/a.txt", "/a.txt", "other-asset",
                                    claims_and_roles={"tokens": ["alice"]})

        assert response.success is True
        copy_spy.assert_called_once()

    def test_cross_asset_copy_with_valid_tokens_still_copies(self):
        """Control: the authorized cross-asset copy must keep working."""
        af = _load_asset_files()
        copy_spy = MagicMock(return_value=True)
        allowing = MagicMock()
        allowing.return_value.enforce.return_value = True

        with patch.object(af, "CasbinEnforcer", allowing), \
                patch.object(af, "get_asset_with_permissions", side_effect=lambda *a, **k: _asset()), \
                patch.object(af, "get_asset_s3_location",
                             return_value=("asset-bucket", f"{_ASSET}/")), \
                patch.object(af, "s3_client", MagicMock()), \
                patch.object(af, "check_destination_file_exists", return_value=False), \
                patch.object(af, "copy_s3_object", copy_spy), \
                patch.object(af, "process_preview_files", return_value=[]), \
                patch.object(af, "copy_auxiliary_files", MagicMock()), \
                patch.object(af, "_copy_file_metadata_to_destination", return_value=0), \
                patch.object(af, "send_subscription_email", MagicMock()):
            response = af.copy_file(_DB, _ASSET, "/a.txt", "/a.txt", "other-asset",
                                    claims_and_roles={"tokens": ["alice"]})

        assert response.success is True
        copy_spy.assert_called_once()


# Spellings that constitute an explicit empty-token denial, whitespace- and
# quote-normalized. Any of them in a function means the function fails closed.
_EMPTY_TOKEN_DENY_SPELLINGS = (
    'len(claims_and_roles["tokens"])==0',
    'len(claims_and_roles["tokens"])<1',
    'notclaims_and_roles["tokens"]',
    'claims_and_roles["tokens"]==[]',
)


def _fail_open_authz_functions(source):
    """Functions that gate an enforce call on a non-empty token list and never deny
    when the list is empty.

    A function is fail-closed if it either (a) denies explicitly on an empty token
    list, in any of the spellings above, or (b) uses the Gold Standard pre-set flag
    pattern -- a name assigned ``False`` before the guarded block plus an
    ``if not <name>:`` deny after it. Anything else that runs ``enforce`` /
    ``enforceAPI`` only inside ``if len(claims_and_roles["tokens"]) > 0`` falls
    through with no authorization when the list is empty.

    Keyed on the literal ``claims_and_roles["tokens"]`` subscript, which is how
    every handler in this package spells it.
    """
    tree = ast.parse(source)
    offenders = []
    for func in [n for n in ast.walk(tree)
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
        func_src = ast.unparse(func).replace("'", '"').replace(" ", "")
        if 'len(claims_and_roles["tokens"])>0' not in func_src:
            continue
        if ".enforce(" not in func_src and ".enforceAPI(" not in func_src:
            continue
        if any(spelling in func_src for spelling in _EMPTY_TOKEN_DENY_SPELLINGS):
            continue
        preset_false = {
            target.id
            for stmt in ast.walk(func) if isinstance(stmt, ast.Assign)
            for target in stmt.targets
            if isinstance(target, ast.Name)
            and isinstance(stmt.value, ast.Constant) and stmt.value.value is False
        }
        has_preset_deny = any(
            isinstance(node, ast.If) and isinstance(node.test, ast.UnaryOp)
            and isinstance(node.test.op, ast.Not)
            and isinstance(node.test.operand, ast.Name)
            and node.test.operand.id in preset_false
            for node in ast.walk(func)
        )
        if has_preset_deny:
            continue
        offenders.append((func.name, func.lineno))
    return offenders


_FAIL_OPEN_SNIPPET = (
    'def h(event):\n'
    '    if len(claims_and_roles["tokens"]) > 0:\n'
    '        if not CasbinEnforcer(claims_and_roles).enforceAPI(event):\n'
    '            return authorization_error()\n'
    '    return do_the_thing()\n'
)
_PRESET_FLAG_SNIPPET = (
    'def h(event):\n'
    '    method_allowed_on_api = False\n'
    '    if len(claims_and_roles["tokens"]) > 0:\n'
    '        if CasbinEnforcer(claims_and_roles).enforceAPI(event):\n'
    '            method_allowed_on_api = True\n'
    '    if not method_allowed_on_api:\n'
    '        return authorization_error()\n'
    '    return do_the_thing()\n'
)
_EXPLICIT_DENY_SNIPPET = (
    'def h(event, asset):\n'
    '    if len(claims_and_roles["tokens"]) == 0:\n'
    '        return authorization_error()\n'
    '    if len(claims_and_roles["tokens"]) > 0:\n'
    '        if not CasbinEnforcer(claims_and_roles).enforce(asset, "GET"):\n'
    '            return authorization_error()\n'
    '    return asset\n'
)


@pytest.mark.unit
class TestNoFailOpenTokenGuardsRemain:
    """FIX-003 shape guard -- prevents an 11-of-12 partial fix from looking complete."""

    def test_detector_flags_the_fail_open_shape(self):
        """Positive control: the detector must find the shape it is meant to find."""
        assert len(_fail_open_authz_functions(_FAIL_OPEN_SNIPPET)) == 1

    def test_detector_accepts_the_preset_flag_shape(self):
        """Negative control: the Gold Standard fix must read as clean.

        Without this, a detector that flags every `len(tokens) > 0` test would keep
        reporting offenders after the fix and the assertion below could never pass.
        """
        assert _fail_open_authz_functions(_PRESET_FLAG_SNIPPET) == []

    def test_detector_accepts_an_explicit_empty_token_deny(self):
        """Negative control: the Tier-2 fix shape must read as clean."""
        assert _fail_open_authz_functions(_EXPLICIT_DENY_SNIPPET) == []

    def test_assetfiles_has_no_fail_open_authz_function(self):
        """FIX-003: no function in assetFiles.py may skip authorization on empty tokens.

        Pinned at zero rather than at a count, so converting 11 of 12 sites still
        fails.
        """
        with open(os.path.abspath(_ASSET_FILES_SOURCE), "r", encoding="utf-8") as handle:
            source = handle.read()
        offenders = _fail_open_authz_functions(source)
        assert offenders == [], (
            f"assetFiles.py functions {offenders} gate authorization on a non-empty "
            f"token list with no empty-token deny"
        )


# --------------------------------------------------------------------------- #
# FIX-043 -- get_file_info must compute currentAssetVersionFileVersionMismatch
# --------------------------------------------------------------------------- #

_FILE_KEY = f"{_ASSET}/model.glb"
_FOLDER_KEY = f"{_ASSET}/subdir/"


def _versioned_asset():
    """An asset pinned to asset version '2', which is what arms the comparison."""
    asset = _asset()
    asset["currentVersionId"] = "2"
    return asset


def _s3_versions(latest_archived=False):
    """Two S3 versions of one object, newest first, as get_s3_object_metadata orders them."""
    return [
        {"versionId": "sv2", "lastModified": "2026-02-02T00:00:00", "size": 12,
         "isLatest": True, "storageClass": "STANDARD", "etag": "etag-new",
         "isArchived": latest_archived},
        {"versionId": "sv1", "lastModified": "2026-01-01T00:00:00", "size": 10,
         "isLatest": False, "storageClass": "STANDARD", "etag": "etag-old",
         "isArchived": False},
    ]


def _object_metadata(versions, key=_FILE_KEY, is_folder=False):
    """The get_s3_object_metadata return shape for one object."""
    return {
        "fileName": os.path.basename(key.rstrip("/")),
        "key": key,
        "relativePath": "/" + key.split("/", 1)[1],
        "isFolder": is_folder,
        "size": 12,
        "contentType": "model/gltf-binary",
        "lastModified": "2026-02-02T00:00:00",
        "etag": "etag-new",
        "storageClass": "STANDARD",
        "isArchived": False,
        "primaryType": None,
        "changeSource": None,
        "changeUserId": None,
        "versions": versions,
    }


def _version_snapshot(pinned_s3_version_id, relative_key="model.glb"):
    """The get_asset_file_versions shape: asset version '2' pins one S3 versionId."""
    return {
        "assetId": _ASSET,
        "assetVersionId": "2",
        "files": [{"relativeKey": relative_key, "versionId": pinned_s3_version_id,
                   "size": 12, "lastModified": "2026-01-01T00:00:00", "etag": "etag-old"}],
        "createdAt": "2026-01-01T00:00:00",
    }


def _list_item(version_id="sv2", is_archived=False):
    """One list_s3_objects_with_archive_status item for the same file."""
    return {
        "fileName": "model.glb",
        "key": _FILE_KEY,
        "relativePath": "/model.glb",
        "isFolder": False,
        "size": 12,
        "dateCreatedCurrentVersion": "2026-02-02T00:00:00",
        "versionId": version_id,
        "etag": "etag-new",
        "storageClass": "STANDARD",
        "isArchived": is_archived,
    }


def _snapshot_only_for(stored_file_key, snapshot):
    """A get_asset_file_versions stub that answers only for the exact stored fileKey.

    ``fileKey`` is the table sort key, so a lookup built from a different spelling of
    the path returns nothing rather than an error -- which is why a stub that ignores
    its arguments cannot see a wrong key.
    """
    def _lookup(databaseId, assetId, assetVersionId, relativeFileKey):
        return snapshot if relativeFileKey == stored_file_key else None

    return _lookup


def _call_get_file_info(metadata, snapshot, file_path="/model.glb", snapshot_side_effect=None):
    """Invoke get_file_info offline. Returns (response, get_asset_file_versions mock).

    Everything that would touch S3 or DynamoDB is stubbed, so the only thing the
    assertions can be reading is the mismatch computation itself. Pass
    ``snapshot_side_effect`` to make the snapshot lookup key-sensitive.
    """
    af = _load_asset_files()
    snapshot_stub = (MagicMock(side_effect=snapshot_side_effect) if snapshot_side_effect
                     else MagicMock(return_value=snapshot))
    version_files_table = MagicMock()
    version_files_table.query.return_value = {"Items": []}

    with patch.object(af, "get_asset_with_permissions", return_value=_versioned_asset()), \
            patch.object(af, "get_asset_s3_location",
                         return_value=("asset-bucket", f"{_ASSET}/")), \
            patch.object(af, "get_s3_object_metadata", return_value=metadata), \
            patch.object(af, "get_asset_file_versions", snapshot_stub), \
            patch.object(af, "asset_version_files_table", version_files_table), \
            patch.object(af, "query_asset_version_history_map", return_value={}), \
            patch.object(af, "get_all_asset_versions", return_value=[]), \
            patch.object(af, "find_preview_files_for_base", return_value=[]):
        response = af.get_file_info(_DB, _ASSET, file_path, True, {"tokens": ["alice"]})
    return response, snapshot_stub


def _call_list_asset_files(list_item, snapshot):
    """Invoke list_asset_files offline for the same file, for the cross-path comparison."""
    af = _load_asset_files()
    with patch.object(af, "get_asset_with_permissions", return_value=_versioned_asset()), \
            patch.object(af, "get_asset_s3_location",
                         return_value=("asset-bucket", f"{_ASSET}/")), \
            patch.object(af, "list_s3_objects_with_archive_status",
                         return_value={"items": [dict(list_item)], "NextToken": None}), \
            patch.object(af, "get_asset_file_versions", return_value=snapshot):
        return af.list_asset_files(_DB, _ASSET, {}, {"tokens": ["alice"]})


def _latest(response):
    return next(v for v in response.versions if v.isLatest)


@pytest.mark.unit
class TestFileInfoAssetVersionMismatchFlag:
    """FIX-043 -- the flag must be a real bool for files, and absent for folders."""

    def test_flag_is_a_bool_not_none_for_a_file(self):
        """FIX-043: the regression assertion -- `None` is falsy, so identity is asserted.

        An ``assertFalse``-style check passes on the unfixed code because the model
        default is ``None``; only ``is not None`` plus ``isinstance(..., bool)``
        distinguishes a computed answer from a default.
        """
        response, _ = _call_get_file_info(
            _object_metadata(_s3_versions()), _version_snapshot("sv2"))

        latest = _latest(response)
        assert latest.currentAssetVersionFileVersionMismatch is not None
        assert isinstance(latest.currentAssetVersionFileVersionMismatch, bool)

    def test_latest_version_matching_the_snapshot_is_false(self):
        """FIX-043: the snapshot pins the latest S3 version -> in sync."""
        response, _ = _call_get_file_info(
            _object_metadata(_s3_versions()), _version_snapshot("sv2"))

        assert _latest(response).currentAssetVersionFileVersionMismatch is False

    def test_latest_version_newer_than_the_snapshot_is_true(self):
        """FIX-043: the snapshot pins the OLDER S3 version -> drifted.

        Paired with the False case above; a single truth value cannot tell a working
        comparison from a hardcoded constant.
        """
        response, _ = _call_get_file_info(
            _object_metadata(_s3_versions()), _version_snapshot("sv1"))

        assert _latest(response).currentAssetVersionFileVersionMismatch is True

    def test_archived_latest_version_is_true_even_when_pinned(self):
        """FIX-043: an archived file is a mismatch regardless of the pinned versionId."""
        response, _ = _call_get_file_info(
            _object_metadata(_s3_versions(latest_archived=True)), _version_snapshot("sv2"))

        assert _latest(response).currentAssetVersionFileVersionMismatch is True

    def test_non_latest_versions_keep_the_model_default(self):
        """The verifier's narrowing: only the latest version carries the flag.

        Pinned so a later change that starts flagging every version is a visible
        behaviour change rather than a silent one.
        """
        response, _ = _call_get_file_info(
            _object_metadata(_s3_versions()), _version_snapshot("sv2"))

        older = [v for v in response.versions if not v.isLatest]
        assert older, "fixture must supply a non-latest version"
        assert all(v.currentAssetVersionFileVersionMismatch is None for v in older)

    def test_folder_entry_does_not_reach_the_comparison(self):
        """Negative control: the behaviour the inverted condition accidentally had.

        A folder has no asset-version membership, so the block must not run and no
        snapshot lookup may be issued for it.
        """
        response, snapshot_stub = _call_get_file_info(
            _object_metadata(_s3_versions(), key=_FOLDER_KEY, is_folder=True),
            _version_snapshot("sv2"),
            file_path="/subdir/",
        )

        assert response.isFolder is True
        assert all(v.currentAssetVersionFileVersionMismatch is None
                   for v in response.versions)
        snapshot_stub.assert_not_called()

    def test_snapshot_is_read_once_per_request_not_once_per_version(self):
        """The lookup sits outside the version loop and must stay there.

        A refactor that moved it inside would be invisible to a value-only assertion
        but would add one DynamoDB read per version.
        """
        metadata = _object_metadata(_s3_versions())
        assert len(metadata["versions"]) > 1, "fixture must supply multiple versions"

        _, snapshot_stub = _call_get_file_info(metadata, _version_snapshot("sv2"))

        assert snapshot_stub.call_count == 1

    @pytest.mark.parametrize(
        "pinned_version_id,latest_archived,expected",
        [("sv2", False, False), ("sv1", False, True), ("sv2", True, True)],
        ids=["in-sync", "drifted", "archived"],
    )
    def test_file_info_and_list_path_agree(self, pinned_version_id, latest_archived, expected):
        """Cross-path agreement: get_file_info and list_asset_files must report the same.

        The two paths compute the flag independently (assetFiles.py get_file_info vs
        list_asset_files_current), and the archived branch is where they can diverge.
        Both are driven from one stubbed snapshot -- the DynamoDB key condition that
        narrows it to this file is not exercised here, only the comparison.
        """
        info_response, _ = _call_get_file_info(
            _object_metadata(_s3_versions(latest_archived=latest_archived)),
            _version_snapshot(pinned_version_id),
        )
        list_response = _call_list_asset_files(
            _list_item(version_id="sv2", is_archived=latest_archived),
            _version_snapshot(pinned_version_id),
        )

        assert len(list_response.items) == 1
        info_flag = _latest(info_response).currentAssetVersionFileVersionMismatch
        list_flag = list_response.items[0].currentAssetVersionFileVersionMismatch
        assert info_flag is expected
        assert list_flag is expected
        assert info_flag is list_flag


@pytest.mark.unit
class TestFileInfoSnapshotLookupKey:
    """FIX-043 -- the snapshot lookup key must be the stored ``fileKey`` form.

    ``get_asset_file_versions`` narrows on ``fileKey``, the sort key of the asset
    version file records, which is written relative to the asset prefix with no
    leading slash ("model.glb", "subdir/model.glb"). The file APIs accept the caller's
    path in several spellings (``models/assetsV3.py`` validate_asset_file_path), so the
    key has to come from the resolved S3 key and not from the raw request path: a key
    built from the raw path matches no row, and the flag then reports every file as
    drifted instead of comparing anything.

    S3 metadata is stubbed here, so these cases assert the DynamoDB key derivation
    only -- whether a given spelling addresses the intended S3 object is
    ``resolve_asset_file_path``'s concern.
    """

    @pytest.mark.parametrize(
        "caller_path,stored_file_key",
        [
            ("/model.glb", "model.glb"),
            ("model.glb", "model.glb"),
            ("//model.glb", "model.glb"),
            (f"{_ASSET}/model.glb", "model.glb"),
            (f"/{_ASSET}/model.glb", "model.glb"),
            ("/subdir/model.glb", "subdir/model.glb"),
            ("subdir/model.glb", "subdir/model.glb"),
            (f"{_ASSET}/subdir/model.glb", "subdir/model.glb"),
        ],
        ids=["leading-slash", "no-leading-slash", "doubled-slash", "asset-prefixed",
             "asset-prefixed-leading-slash", "nested", "nested-no-leading-slash",
             "nested-asset-prefixed"],
    )
    def test_lookup_key_is_the_stored_file_key(self, caller_path, stored_file_key):
        """FIX-043: every accepted caller spelling must resolve to the stored fileKey."""
        response, snapshot_stub = _call_get_file_info(
            _object_metadata(_s3_versions(), key=f"{_ASSET}/{stored_file_key}"),
            None,
            file_path=caller_path,
            snapshot_side_effect=_snapshot_only_for(
                stored_file_key, _version_snapshot("sv2", relative_key=stored_file_key)),
        )

        assert snapshot_stub.call_args[0][3] == stored_file_key
        # The row was found, so the pinned latest version reads as in sync rather than
        # falling through to the "no snapshot record" answer of drifted.
        assert _latest(response).currentAssetVersionFileVersionMismatch is False

    def test_a_lookup_key_that_misses_the_row_reports_drift(self):
        """Sensitivity control: the keyed stub must actually discriminate.

        A stub that answered regardless of the key -- which is what a plain
        ``return_value`` does -- would let the assertions above pass for a lookup built
        from the raw caller path too. Here the stored key is the asset-prefixed
        spelling, so the correct lookup key misses, and the flag reads as drifted:
        the exact wrong answer a raw-path key produces.
        """
        response, snapshot_stub = _call_get_file_info(
            _object_metadata(_s3_versions()),
            None,
            file_path="/model.glb",
            snapshot_side_effect=_snapshot_only_for(
                f"{_ASSET}/model.glb", _version_snapshot("sv2")),
        )

        assert snapshot_stub.call_args[0][3] == "model.glb"
        assert _latest(response).currentAssetVersionFileVersionMismatch is True
