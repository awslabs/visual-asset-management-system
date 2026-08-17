# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests that assetService.update_asset validates edited tags against the
asset's own database + GLOBAL scope, mirroring the create path.

An asset in database A may be edited to carry A's tags or GLOBAL tags, but an
edit that sets another database's tag (or a nonexistent tag) must be rejected —
closing the namespacing gap on the PUT path.
"""

import importlib.util
import os
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

# Reuse the real createAsset loader and composite-key moto table fixtures so
# update_asset exercises the real scoped-validation functions it imports.
from tests.handlers.assets.test_createAsset_tag_scope import (
    _load as _load_create_asset,
    _make_tag_table,
    _make_tag_type_table,
)

os.environ.setdefault("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "test-buckets-table")
os.environ.setdefault("ASSET_STORAGE_TABLE_NAME", "test-asset-table")
os.environ.setdefault("DATABASE_STORAGE_TABLE_NAME", "test-db-table")
os.environ.setdefault("S3_ASSET_AUXILIARY_BUCKET", "test-aux-bucket")
os.environ.setdefault("SUBSCRIPTIONS_STORAGE_TABLE_NAME", "test-subs-table")
os.environ.setdefault("SEND_EMAIL_FUNCTION_NAME", "test-email-fn")
os.environ.setdefault("ASSET_FILE_VERSION_HISTORY_STORAGE_TABLE_NAME", "test-history-table")
os.environ.setdefault("ASSET_HISTORY_STORAGE_TABLE_NAME", "test-asset-history-table")
os.environ.setdefault("ASSET_UPLOAD_TABLE_NAME", "test-upload-table")
os.environ.setdefault("ASSET_LINKS_STORAGE_TABLE_NAME", "test-links-table")
os.environ.setdefault("ASSET_LINKS_METADATA_STORAGE_TABLE_NAME", "test-links-meta-table")
os.environ.setdefault("ASSET_FILE_METADATA_STORAGE_TABLE_NAME", "test-file-meta-table")
os.environ.setdefault("FILE_ATTRIBUTE_STORAGE_TABLE_NAME", "test-file-attr-table")
os.environ.setdefault("ASSET_VERSIONS_STORAGE_TABLE_NAME", "test-versions-table")
os.environ.setdefault("ASSET_FILE_VERSIONS_STORAGE_TABLE_NAME", "test-file-versions-table")
os.environ.setdefault("ASSET_FILE_METADATA_VERSIONS_STORAGE_TABLE_NAME", "test-file-meta-versions-table")
os.environ.setdefault("COMMENT_STORAGE_TABLE_NAME", "test-comment-table")

_ASSET_SERVICE_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "backend", "handlers", "assets", "assetService.py"
)

_cached_module = None


def _load_asset_service():
    """Load the real assetService module by file path with boto3 stubbed."""
    global _cached_module
    if _cached_module is not None:
        return _cached_module

    stub_names = (
        "handlers.assets.assetCount", "handlers.assets.assetFiles",
        "handlers.authz", "handlers.auth",
    )
    saved = {name: sys.modules.get(name) for name in stub_names}

    count_stub = types.ModuleType("handlers.assets.assetCount")
    count_stub.update_asset_count = MagicMock()
    sys.modules["handlers.assets.assetCount"] = count_stub

    files_stub = types.ModuleType("handlers.assets.assetFiles")
    files_stub.delete_s3_prefix_all_versions = MagicMock()
    files_stub.aux_bucket_asset_file_base = (
        lambda db, key: f"{(db or '').strip('/')}/{(key or '').strip('/')}/"
    )
    sys.modules["handlers.assets.assetFiles"] = files_stub

    authz_stub = types.ModuleType("handlers.authz")
    authz_stub.CasbinEnforcer = MagicMock()
    sys.modules["handlers.authz"] = authz_stub

    auth_stub = types.ModuleType("handlers.auth")
    auth_stub.request_to_claims = MagicMock(return_value={"tokens": ["tester"]})
    sys.modules["handlers.auth"] = auth_stub

    dynamodb_mod = sys.modules.get("common.dynamodb")
    added_attrs = []
    if dynamodb_mod is not None and not hasattr(dynamodb_mod, "validate_pagination_info"):
        dynamodb_mod.validate_pagination_info = MagicMock()
        added_attrs.append("validate_pagination_info")

    try:
        with patch("boto3.client", return_value=MagicMock()), patch(
            "boto3.resource", return_value=MagicMock()
        ):
            spec = importlib.util.spec_from_file_location(
                "assetService_update_tag_scope_under_test", os.path.abspath(_ASSET_SERVICE_PATH)
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
            if dynamodb_mod is not None:
                delattr(dynamodb_mod, attr)
    _cached_module = module
    return module


def _wire_create_for_scope(ca, tag_table, tag_type_table):
    """Point the real createAsset module at the moto tag tables and register it
    so update_asset's lazy `from handlers.assets.createAsset import ...` resolves
    to these real, scoped-validation functions."""
    ca.tag_table = tag_table
    ca.tag_type_table = tag_type_table
    sys.modules["handlers.assets.createAsset"] = ca


def _wire_update(m, existing_asset):
    m.get_asset_details = MagicMock(return_value=dict(existing_asset))
    m.asset_table = MagicMock()
    m.write_asset_history_record = MagicMock()
    m.send_subscription_email = MagicMock()


def _existing_asset(db="db-a"):
    return {
        "databaseId": db, "assetId": "asset-1", "assetName": "N1",
        "description": "d1", "isDistributable": True, "tags": [],
        "bucketId": "b1", "assetLocation": {"Key": "asset-1/"},
    }


@pytest.mark.unit
class TestAssetUpdateTagScope:
    def _saved_create_module(self):
        return sys.modules.get("handlers.assets.createAsset")

    def test_update_accepts_own_db_and_global_tags(self, ddb_resource):
        saved = self._saved_create_module()
        try:
            ca = _load_create_asset()
            tag_table = _make_tag_table(ddb_resource)
            tag_type_table = _make_tag_type_table(ddb_resource)
            tag_table.put_item(Item={"databaseId": "db-a", "tagName": "priority",
                                     "tagTypeName": "Custom", "description": "d"})
            tag_table.put_item(Item={"databaseId": "GLOBAL", "tagName": "reviewed",
                                     "tagTypeName": "System", "description": "d"})
            _wire_create_for_scope(ca, tag_table, tag_type_table)

            m = _load_asset_service()
            _wire_update(m, _existing_asset("db-a"))

            result = m.update_asset(
                "db-a", "asset-1",
                {"tags": ["priority", "reviewed"]},
                {"tokens": ["u1"]},
            )
            assert result.success is True
            m.asset_table.put_item.assert_called_once()
            saved_item = m.asset_table.put_item.call_args.kwargs["Item"]
            assert sorted(saved_item["tags"]) == ["priority", "reviewed"]
        finally:
            if saved is not None:
                sys.modules["handlers.assets.createAsset"] = saved
            else:
                sys.modules.pop("handlers.assets.createAsset", None)

    def test_update_rejects_other_db_tag(self, ddb_resource):
        saved = self._saved_create_module()
        try:
            ca = _load_create_asset()
            tag_table = _make_tag_table(ddb_resource)
            tag_type_table = _make_tag_type_table(ddb_resource)
            # Seed the tag only in another database's partition.
            tag_table.put_item(Item={"databaseId": "db-b", "tagName": "secret",
                                     "tagTypeName": "Custom", "description": "d"})
            _wire_create_for_scope(ca, tag_table, tag_type_table)

            m = _load_asset_service()
            _wire_update(m, _existing_asset("db-a"))

            # A rejected tag must surface as a 400 (VAMSGeneralErrorResponse), not a 500.
            # Matched by name: the exception class is only importable from the lazily
            # loaded module, and the mock hierarchy shadows a module-level import.
            with pytest.raises(Exception) as rejected:
                m.update_asset(
                    "db-a", "asset-1",
                    {"tags": ["secret"]},
                    {"tokens": ["u1"]},
                )
            assert type(rejected.value).__name__ == "VAMSGeneralErrorResponse"
            m.asset_table.put_item.assert_not_called()
        finally:
            if saved is not None:
                sys.modules["handlers.assets.createAsset"] = saved
            else:
                sys.modules.pop("handlers.assets.createAsset", None)


@pytest.mark.unit
class TestUpdateKeepsDeletedTags:
    """A tag deleted after it was applied stays on the asset and must not block editing.

    Every edit resubmits the asset's whole tag list, including an edit that only changes the
    description, so validating the full list against what currently exists would make such an asset
    permanently un-editable. Only the tags an edit ADDS are validated.
    """

    def _saved_create_module(self):
        return sys.modules.get("handlers.assets.createAsset")

    def _scoped(self, ddb_resource, existing_tags):
        """createAsset wired to moto tag tables holding one GLOBAL tag, plus an asset carrying
        `existing_tags` (which may include a tag that no longer exists anywhere)."""
        ca = _load_create_asset()
        tag_table = _make_tag_table(ddb_resource)
        tag_type_table = _make_tag_type_table(ddb_resource)
        tag_table.put_item(Item={"databaseId": "GLOBAL", "tagName": "reviewed",
                                "tagTypeName": "Custom", "description": "d"})
        _wire_create_for_scope(ca, tag_table, tag_type_table)

        m = _load_asset_service()
        asset = _existing_asset("db-a")
        asset["tags"] = list(existing_tags)
        _wire_update(m, asset)
        return m

    def test_resubmitting_a_deleted_tag_still_saves(self, ddb_resource):
        saved = self._saved_create_module()
        try:
            # 'goneTag' exists on the asset but in no tag table — it was deleted after being applied.
            m = self._scoped(ddb_resource, ["reviewed", "goneTag"])

            result = m.update_asset(
                "db-a", "asset-1",
                {"description": "edited", "tags": ["reviewed", "goneTag"]},
                {"tokens": ["u1"]},
            )

            assert result.success is True
            saved_item = m.asset_table.put_item.call_args.kwargs["Item"]
            # The deleted tag is retained rather than silently dropped.
            assert sorted(saved_item["tags"]) == ["goneTag", "reviewed"]
        finally:
            if saved is not None:
                sys.modules["handlers.assets.createAsset"] = saved

    def test_removing_a_deleted_tag_is_allowed(self, ddb_resource):
        saved = self._saved_create_module()
        try:
            m = self._scoped(ddb_resource, ["reviewed", "goneTag"])

            result = m.update_asset(
                "db-a", "asset-1",
                {"tags": ["reviewed"]},
                {"tokens": ["u1"]},
            )

            assert result.success is True
            saved_item = m.asset_table.put_item.call_args.kwargs["Item"]
            assert saved_item["tags"] == ["reviewed"]
        finally:
            if saved is not None:
                sys.modules["handlers.assets.createAsset"] = saved

    def test_re_adding_a_deleted_tag_is_rejected(self, ddb_resource):
        saved = self._saved_create_module()
        try:
            # Once removed, the tag is no longer 'existing', so submitting it counts as an addition
            # and is rejected — the user cannot add a deleted tag back.
            m = self._scoped(ddb_resource, ["reviewed"])

            # A rejected tag must surface as a 400 (VAMSGeneralErrorResponse), not a 500.
            # Matched by name: the exception class is only importable from the lazily
            # loaded module, and the mock hierarchy shadows a module-level import.
            with pytest.raises(Exception) as rejected:
                m.update_asset(
                    "db-a", "asset-1",
                    {"tags": ["reviewed", "goneTag"]},
                    {"tokens": ["u1"]},
                )
            assert type(rejected.value).__name__ == "VAMSGeneralErrorResponse"
            m.asset_table.put_item.assert_not_called()
        finally:
            if saved is not None:
                sys.modules["handlers.assets.createAsset"] = saved
