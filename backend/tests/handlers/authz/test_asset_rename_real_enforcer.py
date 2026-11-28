# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""An asset rename is decided by the real policy built from a stored constraint row (#369).

``tests/handlers/assets/test_assetService_rename_authz.py`` covers which records ``update_asset``
hands to ``CasbinEnforcer.enforce`` for a rename, with a predicate standing in for the enforcer. This
file runs the same rename through the real policy generation and Casbin evaluation for one constraint
row, asset ``GET`` and ``PUT`` where ``assetName starts_with PROJ-``: a rename out of that scope is
refused and writes nothing, and a rename inside it is written.

Only the role and constraint reads and the asset-table reads and writes are stubbed. assetService.py is
loaded by path, with ``handlers.authz`` pointing at the real authz module that this directory's
conftest makes importable.
"""

import contextlib
import importlib.util
import os
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

import backend.backend.handlers.authz as authz_module

for _name, _value in (
    ("ASSET_STORAGE_TABLE_NAME", "test-asset-table"),
    ("DATABASE_STORAGE_TABLE_NAME", "test-db-table"),
    ("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "test-buckets-table"),
    ("S3_ASSET_AUXILIARY_BUCKET", "test-aux-bucket"),
    ("SUBSCRIPTIONS_STORAGE_TABLE_NAME", "test-subs-table"),
    ("SEND_EMAIL_FUNCTION_NAME", "test-email-fn"),
    ("ASSET_FILE_VERSION_HISTORY_STORAGE_TABLE_NAME", "test-history-table"),
    ("ASSET_HISTORY_STORAGE_TABLE_NAME", "test-asset-history-table"),
    ("ASSET_UPLOAD_TABLE_NAME", "test-upload-table"),
    ("ASSET_LINKS_STORAGE_TABLE_NAME", "test-links-table"),
    ("ASSET_LINKS_METADATA_STORAGE_TABLE_NAME", "test-links-meta-table"),
    ("ASSET_FILE_METADATA_STORAGE_TABLE_NAME", "test-file-meta-table"),
    ("FILE_ATTRIBUTE_STORAGE_TABLE_NAME", "test-file-attr-table"),
    ("ASSET_VERSIONS_STORAGE_TABLE_NAME", "test-versions-table"),
    ("ASSET_FILE_VERSIONS_STORAGE_TABLE_NAME", "test-file-versions-table"),
    ("ASSET_FILE_METADATA_VERSIONS_STORAGE_TABLE_NAME", "test-file-meta-versions-table"),
    ("COMMENT_STORAGE_TABLE_NAME", "test-comment-table"),
):
    os.environ.setdefault(_name, _value)

_ASSET_SERVICE_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "backend", "handlers", "assets", "assetService.py"
)

USER_ID = "rename-user"
ROLE = "proj-editor"
CLAIMS = {"tokens": [USER_ID], "roles": [ROLE], "mfaEnabled": True}
DATABASE_ID = "db-a"
ASSET_ID = "asset-1"

PROJ_NAMES_ONLY = [
    {
        "constraintId": "proj-names-only",
        "name": "proj-names-only",
        "objectType": "asset",
        "criteriaAnd": [{"field": "assetName", "operator": "starts_with", "value": "PROJ-"}],
        "groupPermissions": [
            {"groupId": ROLE, "permission": "GET", "permissionType": "allow"},
            {"groupId": ROLE, "permission": "PUT", "permissionType": "allow"},
        ],
    }
]


@pytest.fixture
def asset_service():
    """assetService.py loaded by path against the real authz module."""
    names = ("handlers.auth", "handlers.authz", "handlers.assets.assetCount", "handlers.assets.assetFiles")
    saved = {name: sys.modules.get(name) for name in names}

    sys.modules["handlers.authz"] = authz_module
    auth_stub = types.ModuleType("handlers.auth")
    auth_stub.request_to_claims = MagicMock(return_value=CLAIMS)
    sys.modules["handlers.auth"] = auth_stub
    count_stub = types.ModuleType("handlers.assets.assetCount")
    count_stub.update_asset_count = MagicMock()
    sys.modules["handlers.assets.assetCount"] = count_stub
    files_stub = types.ModuleType("handlers.assets.assetFiles")
    files_stub.delete_s3_prefix_all_versions = MagicMock()
    files_stub.S3PrefixDeleteIncomplete = type("S3PrefixDeleteIncomplete", (Exception,), {})
    files_stub.delete_files_metadata = MagicMock()
    files_stub.delete_auxiliary_files_of_keys = MagicMock()
    files_stub.delete_file_version_records = MagicMock()
    files_stub.record_invocation_context = MagicMock()
    files_stub.aux_bucket_asset_file_base = (
        lambda db, key: f"{(db or '').strip('/')}/{(key or '').strip('/')}/"
    )
    sys.modules["handlers.assets.assetFiles"] = files_stub

    try:
        with patch("boto3.client", return_value=MagicMock()), patch(
            "boto3.resource", return_value=MagicMock()
        ):
            spec = importlib.util.spec_from_file_location(
                "assetService_under_test_rename_real_enforcer", os.path.abspath(_ASSET_SERVICE_PATH)
            )
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
    finally:
        for name, mod in saved.items():
            if mod is not None:
                sys.modules[name] = mod
            else:
                sys.modules.pop(name, None)

    module.get_asset_details = MagicMock(return_value={
        "databaseId": DATABASE_ID,
        "assetId": ASSET_ID,
        "assetName": "PROJ-1",
        "assetType": ".glb",
        "description": "d1",
        "isDistributable": True,
        "tags": [],
    })
    module.asset_table = MagicMock()
    module.write_asset_history_record = MagicMock()
    module.send_subscription_email = MagicMock()
    return module


@contextlib.contextmanager
def _policy(constraints):
    """The real enforcer for USER_ID in ROLE, with its policy built from `constraints`."""
    authz_module.casbin_user_policy_map = {}
    authz_module.casbin_user_enforcer_map = {}
    try:
        with patch.object(
            authz_module.CasbinEnforcerService,
            "_read_current_user_roles_from_table",
            return_value=[{"userId": USER_ID, "roleName": ROLE}],
        ), patch.object(
            authz_module.CasbinEnforcerService,
            "_read_policies_batch_optimized",
            return_value=constraints,
        ):
            yield
    finally:
        authz_module.casbin_user_policy_map = {}
        authz_module.casbin_user_enforcer_map = {}


def _update(module, update_data):
    try:
        return module.update_asset(DATABASE_ID, ASSET_ID, update_data, CLAIMS)
    except Exception as exc:  # noqa: BLE001 - a refusal is raised as AuthorizationDenied
        return exc


@pytest.mark.unit
class TestRenameDecidedByTheRealPolicy:
    def test_a_rename_out_of_the_constraint_is_refused_and_not_written(self, asset_service):
        with _policy(PROJ_NAMES_ONLY):
            # Control: the policy grants the stored asset, so the refusal comes from the new name.
            stored = {**asset_service.get_asset_details.return_value, "object__type": "asset"}
            assert authz_module.CasbinEnforcer(CLAIMS).enforce(stored, "PUT") is True
            result = _update(asset_service, {"assetName": "OTHER-1"})

        assert isinstance(result, asset_service.AuthorizationDenied), (
            f"a rename out of the role's name scope was not refused: {result!r}"
        )
        asset_service.asset_table.update_item.assert_not_called()
        asset_service.write_asset_history_record.assert_not_called()

    def test_a_rename_inside_the_constraint_is_written(self, asset_service):
        with _policy(PROJ_NAMES_ONLY):
            result = _update(asset_service, {"assetName": "PROJ-2"})

        assert getattr(result, "success", None) is True, f"an allowed rename was refused: {result!r}"
        asset_service.asset_table.update_item.assert_called_once()
        values = asset_service.asset_table.update_item.call_args.kwargs["ExpressionAttributeValues"]
        assert "PROJ-2" in values.values()
